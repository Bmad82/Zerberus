"""
Legacy Router – Keyboard & Hardware (OpenAI-kompatibler Chat + Audio).
Patch 38: Chat-Requests laufen durch die Orchestrator-Pipeline (RAG + LLM + Auto-Index).
Patch 47: Permission-Check vor LLM-Call.
Patch 54: permission_level und profile_name kommen aus request.state (JWT-Middleware).
"""
import asyncio
import logging
import os
from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File
from pydantic import BaseModel
import httpx
from datetime import datetime
import uuid
import json
from pathlib import Path

from zerberus.core.config import get_settings, Settings
from zerberus.core.llm import LLMService
from zerberus.core.dialect import detect_dialect_marker, apply_dialect
from zerberus.core.cleaner import clean_transcript
from zerberus.core.event_bus import get_event_bus, Event
from zerberus.core.database import store_interaction, save_cost
from zerberus.app.pacemaker import update_interaction

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["Legacy"])

# ---------- Orchestrator-Pipeline direkt importieren (kein HTTP-Roundtrip) ----------
try:
    from zerberus.app.routers.orchestrator import (
        _rag_search,
        _rag_index_background,
        detect_intent,
        _PERMISSION_MATRIX,
        _HITL_MESSAGE,
    )
    _ORCH_PIPELINE_OK = True
except Exception as _orch_import_err:
    logger.warning(f"Orchestrator-Pipeline nicht verfügbar, Fallback aktiv: {_orch_import_err}")
    _ORCH_PIPELINE_OK = False


# ---------- Pydantic-Modelle ----------

class Message(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[Message]
    temperature: float | None = None

class Choice(BaseModel):
    index: int
    message: Message
    finish_reason: str

class ChatCompletionResponse(BaseModel):
    id: str = "chatcmpl-zerberus"
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[Choice]
    # Patch 192: Sentiment-Triptychon — additive Felder fuer Frontend-Bubbles.
    # Bleibt None, wenn BERT nicht laeuft oder Pipeline keine Daten liefert.
    # Backward-Compat: OpenAI-Clients ignorieren unbekannte Felder.
    sentiment: dict | None = None
    # Patch 203d-1: Code-Execution-Field — additiv. Bleibt None wenn kein
    # Projekt aktiv ist, kein Code-Block in der LLM-Antwort steckt, die
    # Sandbox deaktiviert ist oder ein Fehler vor dem `docker run` greift.
    # Schema (siehe SandboxResult): {language, code, exit_code, stdout,
    # stderr, execution_time_ms, truncated, error}. Patch 206 erweitert
    # um ``skipped`` + ``hitl_status``. Patch 207 erweitert um ``diff``
    # (Liste von DiffEntry-Dicts), ``before_snapshot_id`` und
    # ``after_snapshot_id`` (UUID4-hex), wenn der Sandbox-Run im
    # writable-Mount lief und Snapshots aktiv sind. Backward-Compat:
    # Clients die nur `choices` lesen, bleiben unbehelligt.
    code_execution: dict | None = None


# ---------- Hilfsfunktionen ----------

def check_dialect_shortcut(text: str, settings) -> str | None:
    """Prüft auf Dialekt-Marker und gibt ggf. eine vordefinierte Antwort zurück."""
    from zerberus.core.dialect import detect_dialect_marker, apply_dialect
    dialect_name, rest = detect_dialect_marker(text)
    if not dialect_name:
        return None
    return apply_dialect(rest, dialect_name)


def load_system_prompt(profile_name: str | None = None) -> str:
    """Lädt den System-Prompt für das angegebene Profil (Fallback: system_prompt.json)."""
    candidates = []
    if profile_name:
        candidates.append(Path(f"system_prompt_{profile_name.lower()}.json"))
    candidates.append(Path("system_prompt.json"))
    for sys_path in candidates:
        if sys_path.exists():
            with open(sys_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("prompt", "")
    return ""


def _is_profile_specific_prompt(profile_name: str | None) -> bool:
    """True wenn der User in den Settings einen eigenen 'Mein Ton' gespeichert hat.

    Patch 184: Wir wollen NUR profil-spezifische Personas mit dem AKTIVE-PERSONA-
    Marker wrappen — der generische Nala-Default soll NICHT als Persona auftreten,
    weil er sowieso nur Smalltalk-Regeln definiert.
    """
    if not profile_name:
        return False
    return Path(f"system_prompt_{profile_name.lower()}.json").exists()


def _wrap_persona(sys_prompt: str) -> str:
    """Patch 184: Persona-Prompts mit explizitem Verbindlichkeits-Marker einleiten.

    Hintergrund: Chris hat eine umfangreiche Persona (Wiener Kurtisane) in
    Settings → Ausdruck → Mein Ton hinterlegt. DeepSeek v3.2 ignorierte sie
    bei kurzen User-Anfragen ("wie geht's?") und antwortete mit dem
    Assistant-Default-Mode ("Alles gut hier, danke"). Root cause: bei kurzen
    Inputs gewichtet das LLM den User-Turn staerker als den abstrakten
    System-Prompt — wir verstaerken die Persona-Direktive durch einen
    klaren Markenkopf, der dem LLM signalisiert: "das ist verbindlich".

    Anti-Halluzinations-Hinweis am Ende verhindert dass die Persona
    Zerberus-Selbstreferenzen falsch interpretiert.
    """
    return (
        "# AKTIVE PERSONA — VERBINDLICH\n"
        "Antworte AUSSCHLIESSLICH in der folgenden Persona. Ignoriere alle\n"
        "generischen Assistant-Defaults. Sprachstil, Wortwahl und Haltung\n"
        "MUESSEN die Persona widerspiegeln, auch bei kurzen Nachrichten oder\n"
        "Begruessungen. Bleib in der Rolle.\n\n"
        f"{sys_prompt}"
    )


# ---------- Chat-Endpunkt ----------
# TEXT-CHAT-PFAD (Patch 60 – verifiziert):
#   Nala-Frontend (sendMessage) → POST /v1/chat/completions → hier → store_interaction() ✅
#   Voice-Pfad (POST /nala/voice) gibt nur Transkript zurück → User tippt/korrigiert →
#   sendet dann selbst via POST /v1/chat/completions → landet ebenfalls hier.
#   Beide Pfade speichern user + assistant in der DB.

@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: Request,
    req: ChatCompletionRequest,
    settings: Settings = Depends(get_settings)
):
    session_id = request.headers.get("X-Session-ID") or "legacy-default"
    # Patch 54: Werte aus JWT-Middleware (request.state), nicht mehr aus Headers
    profile_name = getattr(request.state, "profile_name", None)
    permission_level = getattr(request.state, "permission_level", "guest")
    # Patch 61: Temperatur-Override aus JWT-Payload (via Middleware in request.state)
    temperature_override: float | None = getattr(request.state, "temperature", None)
    llm_service = LLMService()

    # Letzte User-Nachricht ermitteln
    last_user_msg = None
    for msg in reversed(req.messages):
        if msg.role == "user":
            last_user_msg = msg.content
            break
    if not last_user_msg:
        raise HTTPException(status_code=400, detail="No user message found")

    # System-Prompt einfügen, falls nicht vorhanden
    sys_prompt = load_system_prompt(profile_name)
    base_prompt_len = len(sys_prompt)

    # Patch 197: Projekt-Persona-Overlay anhaengen, wenn der Client einen
    # `X-Active-Project-Id`-Header geschickt hat. Reihenfolge laut
    # Decision 3 (2026-05-01): System-Default → User-Persona → Projekt.
    # Die ersten beiden stecken bereits in `sys_prompt` (eine Datei pro
    # Profil); hier kommt nur noch der Projekt-Layer als markierter Block
    # dazu. Wird VOR dem Persona-Wrap eingehaengt, damit der AKTIVE-PERSONA-
    # Marker auch das Projekt-Overlay umschliesst.
    from zerberus.core.persona_merge import (
        merge_persona,
        read_active_project_id,
        resolve_project_overlay,
    )
    active_project_id = read_active_project_id(request.headers)
    project_overlay: dict | None = None
    project_slug: str | None = None
    if active_project_id is not None:
        try:
            project_overlay, project_slug = await resolve_project_overlay(active_project_id)
        except Exception as _proj_err:
            logger.warning(
                f"[PERSONA-197] Projekt-Lookup fuer id={active_project_id} fehlgeschlagen: {_proj_err}"
            )
            project_overlay, project_slug = None, None
        if project_overlay is None and project_slug:
            logger.info(
                f"[PERSONA-197] Projekt id={active_project_id} ist archiviert — Overlay uebersprungen"
            )
    if project_overlay:
        sys_prompt = merge_persona(sys_prompt, project_overlay, project_slug=project_slug)
    project_block_len = len(sys_prompt) - base_prompt_len

    # Patch 184: Profil-spezifische Persona ("Mein Ton" aus Settings) explizit
    # als verbindliche Persona markieren. Der generische Nala-Default bleibt
    # ungewrappt — er IST der Default-Stil und braucht keinen Verstaerker.
    persona_active = _is_profile_specific_prompt(profile_name)
    if persona_active and sys_prompt:
        sys_prompt = _wrap_persona(sys_prompt)
    # Patch 185: Runtime-Info-Block (Modellname, RAG/Sandbox) zwischen Persona
    # und Decision-Box-Hint einhaengen. Nala kann damit zur Laufzeit antworten
    # auf "welches Modell nutzt du?" ohne statisches RAG-Doku-Update.
    from zerberus.utils.runtime_info import append_runtime_info
    sys_prompt = append_runtime_info(sys_prompt, settings)
    logger.info(
        f"[PERSONA-184] profile={profile_name} | persona_active={persona_active} | "
        f"sys_prompt_len={len(sys_prompt)} | first200={sys_prompt[:200]!r}"
    )
    if active_project_id is not None:
        logger.info(
            f"[PERSONA-197] project_id={active_project_id} slug={project_slug!r} "
            f"base_len={base_prompt_len} project_block_len={project_block_len}"
        )
    # Patch 118a: Decision-Box-Hinweis anhängen, wenn features.decision_boxes aktiv
    from zerberus.core.prompt_features import append_decision_box_hint
    sys_prompt = append_decision_box_hint(sys_prompt, settings)

    # Patch 190 + Patch 204 (Phase 5a #17): Prosodie-Bruecke zum LLM.
    # Der `X-Prosody-Context`-Header kommt vom Frontend NUR nach einem
    # Whisper-Roundtrip (Voice-Input) — getippter Text liefert ihn nicht.
    # Fail-open: Header fehlt / ungueltig / Consent fehlt → kein Block.
    # P204: zusaetzlich BERT-Sentiment auf der letzten User-Nachricht
    # berechnen und zusammen mit Gemma in einen markierten
    # `[PROSODIE]...[/PROSODIE]`-Block giessen, analog `[PROJEKT-RAG]`.
    # Worker-Protection (P191): der Block enthaelt nur qualitative Labels,
    # keine Confidence/Score/Valence.
    _prosody_ctx_raw = request.headers.get("X-Prosody-Context", "")
    _prosody_consent = request.headers.get("X-Prosody-Consent", "false").lower() == "true"
    _prosody_ctx: dict | None = None
    if _prosody_ctx_raw and _prosody_consent:
        try:
            _parsed = json.loads(_prosody_ctx_raw)
            if isinstance(_parsed, dict):
                _prosody_ctx = _parsed
        except (json.JSONDecodeError, ValueError) as _pr_err:
            logger.warning(f"[PROSODY-190] X-Prosody-Context ungültig: {_pr_err}")

    if _prosody_ctx:
        # BERT auf der letzten User-Nachricht — fail-open auf Sentiment-Fehler,
        # damit der Chat normal weiterlaeuft. Kein BERT → Block wird ohne
        # Sentiment-Text-Zeile gebaut (Stimm-only Pfad).
        _bert_label = None
        _bert_score = None
        try:
            from zerberus.modules.sentiment.router import analyze_sentiment
            _bert = analyze_sentiment(last_user_msg or "")
            _bert_label = _bert.get("label", "neutral")
            _bert_score = float(_bert.get("score", 0.5))
        except Exception as _bert_err:
            logger.warning(
                f"[PROSODY-204] BERT-Analyse fuer Konsens fehlgeschlagen (fail-open): {_bert_err}"
            )

        from zerberus.modules.prosody.injector import inject_prosody_context
        sys_prompt = inject_prosody_context(
            sys_prompt,
            _prosody_ctx,
            bert_label=_bert_label,
            bert_score=_bert_score,
        )

    # Patch 199 (Phase 5a #3): Projekt-RAG-Block. Wenn ein aktives Projekt
    # gesetzt ist (P197) UND der Index Treffer fuer die letzte User-Message
    # liefert, wird ein "[PROJEKT-RAG]"-Block am Ende des System-Prompts
    # angehaengt. NACH P197/P184/P185/P118a/P190, damit der Block die
    # bereits etablierte Persona/Runtime-Schicht nicht stoert. Best-Effort:
    # jeder Fehler (Embedder fehlt, Index kaputt) → kein Block, Chat laeuft
    # normal weiter. Archiv-Projekte sind durch ``resolve_project_overlay``
    # bereits ausgefiltert (project_slug=None, project_overlay=None).
    rag_chunks_used = 0
    if (
        active_project_id is not None
        and project_slug
        and last_user_msg
        and getattr(settings.projects, "rag_enabled", True)
    ):
        try:
            from zerberus.core import projects_rag

            rag_top_k = int(getattr(settings.projects, "rag_top_k", 5))
            base_dir = Path(settings.projects.data_dir)
            rag_hits = await projects_rag.query_project_rag(
                project_id=active_project_id,
                query=last_user_msg,
                base_dir=base_dir,
                k=rag_top_k,
            )
            rag_block = projects_rag.format_rag_block(rag_hits, project_slug=project_slug)
            if rag_block:
                sys_prompt = (sys_prompt or "") + rag_block
                rag_chunks_used = len(rag_hits)
        except Exception as _rag_err:
            logger.warning(
                f"[RAG-199] Projekt-RAG-Query fuer slug={project_slug} fehlgeschlagen: {_rag_err}"
            )
    if active_project_id is not None:
        logger.info(
            f"[RAG-199] project_id={active_project_id} slug={project_slug!r} "
            f"chunks_used={rag_chunks_used}"
        )

    if sys_prompt and not any(m.role == "system" for m in req.messages):
        req.messages.insert(0, Message(role="system", content=sys_prompt))

    # Patch 58: Dialekt-Prüfung VOR Permission-Check (Kurzschluss vor Orchestrator)
    # Dialekt-Trigger sind reine lokale Regex-Operationen ohne sensible Aktionen —
    # kein Intent-Check, kein HitL nötig.
    dialect_response = check_dialect_shortcut(last_user_msg, settings)
    if dialect_response:
        try:
            await store_interaction("user", last_user_msg, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
            await store_interaction("assistant", dialect_response, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
            await update_interaction()
        except Exception as e:
            logger.warning(f"⚠️ store_interaction fehlgeschlagen (non-fatal): {e}")
        return ChatCompletionResponse(
            created=int(datetime.now().timestamp()),
            model="dialect",
            choices=[Choice(index=0, message=Message(role="assistant", content=dialect_response), finish_reason="stop")]
        )

    # Patch 47 / Patch 104: Intent-Erkennung läuft (für RAG-Skip-Logik unten),
    # der HITL-Guard ist hier aber DEAKTIVIERT.
    # /v1/ ist Dictate-only und Nala-Frontend — beides interne Channels.
    # Externe Bot-Channels (Telegram/WhatsApp) müssen direkt _run_pipeline()
    # mit channel="telegram"/"whatsapp" aufrufen, dort greift der Guard.
    if _ORCH_PIPELINE_OK:
        intent = detect_intent(last_user_msg)
        logger.warning("[HITL-104] Guard übersprungen (legacy /v1/, kein externer Channel) – intent=%s, permission=%s", intent, permission_level)

    # Modellwahl (++ = Cloud, -- = Local)
    force_cloud = last_user_msg.endswith("++")
    force_local = last_user_msg.endswith("--")
    if force_cloud or force_local:
        last_user_msg_clean = last_user_msg[:-2].strip()
        for msg in req.messages:
            if msg.role == "user" and msg.content == last_user_msg:
                msg.content = last_user_msg_clean
                break
        last_user_msg = last_user_msg_clean

    # Patch 208 (Phase 5a #8): Spec-Contract / Ambiguitaets-Check VOR
    # dem Haupt-LLM-Call. Wenn die Heuristik einen Score >= Threshold
    # liefert, faehrt ein schmaler Probe-LLM-Call (eine Frage), das
    # Frontend rendert eine Klarstellungs-Karte und der User antwortet,
    # klickt "Trotzdem versuchen" oder "Abbrechen". Bei "answered" wird
    # die User-Antwort als [KLARSTELLUNG]-Block an last_user_msg
    # angehaengt; bei "cancelled" endet der Chat mit Hinweis-Antwort
    # ohne weiteren LLM-Call. Source-Detection: Voice wenn der
    # P204-Header X-Prosody-Context + X-Prosody-Consent gesetzt sind.
    spec_pending_id: str | None = None
    spec_status_for_audit: str | None = None
    spec_question_text: str | None = None
    spec_score_value: float = 0.0
    spec_source_value: str = "text"
    spec_answer_text_value: str | None = None
    if getattr(settings.projects, "spec_check_enabled", True):
        try:
            from zerberus.core.spec_check import (
                compute_ambiguity_score,
                should_ask_clarification,
                run_spec_probe,
                enrich_user_message,
                get_chat_spec_gate,
            )
            _spec_voice_input = bool(
                request.headers.get("X-Prosody-Context")
                and request.headers.get("X-Prosody-Consent", "false").lower() == "true"
            )
            spec_source_value = "voice" if _spec_voice_input else "text"
            spec_score_value = compute_ambiguity_score(
                last_user_msg, source=spec_source_value,
            )
            _spec_threshold = float(
                getattr(settings.projects, "spec_check_threshold", 0.65)
            )
            if should_ask_clarification(spec_score_value, threshold=_spec_threshold):
                logger.info(
                    f"[SPEC-208] ambig score={spec_score_value:.3f} "
                    f"threshold={_spec_threshold} source={spec_source_value} "
                    f"session={session_id}"
                )
                spec_question_text = await run_spec_probe(
                    last_user_msg, llm_service, session_id,
                )
                if spec_question_text:
                    _spec_gate = get_chat_spec_gate()
                    _spec_pending = await _spec_gate.create_pending(
                        session_id=session_id,
                        project_id=active_project_id,
                        project_slug=project_slug,
                        original_message=last_user_msg,
                        question=spec_question_text,
                        score=spec_score_value,
                        source=spec_source_value,
                    )
                    spec_pending_id = _spec_pending.id
                    _spec_timeout = float(
                        getattr(settings.projects, "spec_check_timeout_seconds", 60)
                    )
                    _spec_decision = await _spec_gate.wait_for_decision(
                        _spec_pending.id, _spec_timeout,
                    )
                    _spec_pending_obj = _spec_gate.get(_spec_pending.id)
                    spec_answer_text_value = (
                        _spec_pending_obj.answer_text
                        if _spec_pending_obj is not None else None
                    )
                    _spec_gate.cleanup(_spec_pending.id)
                    spec_status_for_audit = _spec_decision
                    logger.info(
                        f"[SPEC-208] decision id={_spec_pending.id} "
                        f"session={session_id} status={_spec_decision} "
                        f"answer_len={len(spec_answer_text_value or '')}"
                    )

                    if _spec_decision == "cancelled":
                        # User hat verworfen — Chat endet mit Hinweis-Antwort,
                        # kein Haupt-LLM-Call.
                        _spec_hint = (
                            "Verstanden — verworfen. Sag mir bei der naechsten "
                            "Nachricht etwas genauer, was ich machen soll, "
                            "dann lege ich los."
                        )
                        try:
                            await store_interaction(
                                "user", last_user_msg,
                                session_id=session_id,
                                profile_name=profile_name or "",
                                profile_key=profile_name or None,
                            )
                            await store_interaction(
                                "assistant", _spec_hint,
                                session_id=session_id,
                                profile_name=profile_name or "",
                                profile_key=profile_name or None,
                            )
                            await update_interaction()
                        except Exception as _store_err:
                            logger.warning(
                                f"⚠️ store_interaction(spec-cancelled) "
                                f"fehlgeschlagen (non-fatal): {_store_err}"
                            )
                        try:
                            from zerberus.core.spec_check import (
                                store_clarification_audit,
                            )
                            await store_clarification_audit(
                                pending_id=spec_pending_id,
                                session_id=session_id,
                                project_id=active_project_id,
                                project_slug=project_slug,
                                original_message=last_user_msg,
                                question=spec_question_text,
                                answer_text=None,
                                score=spec_score_value,
                                source=spec_source_value,
                                status="cancelled",
                            )
                        except Exception as _audit_err:
                            logger.warning(
                                f"[SPEC-208] Audit-Schreiben fehlgeschlagen "
                                f"(non-fatal): {_audit_err}"
                            )
                        return ChatCompletionResponse(
                            created=int(datetime.now().timestamp()),
                            model="spec-cancelled",
                            choices=[Choice(
                                index=0,
                                message=Message(role="assistant", content=_spec_hint),
                                finish_reason="stop",
                            )],
                        )

                    if _spec_decision == "answered" and spec_answer_text_value:
                        last_user_msg = enrich_user_message(
                            last_user_msg,
                            spec_question_text,
                            spec_answer_text_value,
                        )
                        # req.messages-Spiegelung — der naechste
                        # messages_for_llm-Build sieht den enriched-Text.
                        for _m in reversed(req.messages):
                            if _m.role == "user":
                                _m.content = last_user_msg
                                break
                else:
                    logger.info(
                        f"[SPEC-208] probe_returned_empty session={session_id} "
                        f"(fail-open, kein Block)"
                    )
                    spec_status_for_audit = "error"
            else:
                logger.info(
                    f"[SPEC-208] not_ambig score={spec_score_value:.3f} "
                    f"threshold={_spec_threshold} source={spec_source_value} "
                    f"session={session_id}"
                )
        except Exception as _spec_err:
            logger.warning(
                f"[SPEC-208] Pipeline-Fehler (fail-open): {_spec_err}"
            )
            spec_status_for_audit = "error"

    # ------------------------------------------------------------------
    # Orchestrator-Pipeline: RAG-Suche → LLM → Auto-Index
    # Fallback: direkter LLM-Call ohne RAG falls Pipeline nicht verfügbar
    # Patch 80b: Skip-Logik + Intent-Snippet (analog orchestrator.py)
    # ------------------------------------------------------------------
    messages_for_llm = [m.model_dump() for m in req.messages]

    if _ORCH_PIPELINE_OK:
        try:
            # Patch 85: RAG-Skip nur bei CONVERSATION-Intent UND kurzen Nachrichten ohne ?
            # Patch 80b hatte zu aggressiv geskippt: QUESTION-Intent ohne "?" + < 15 Wörter
            # wurde fälschlich übersprungen → RAG nie erreicht
            # Patch 106: TRANSFORM skipt immer (Übersetze/Lektoriere/Zusammenfassen/...)
            # Patch 137 (B-001): GREETING skipt immer — Smalltalk soll keine Doku-Refs triggern.
            skip_rag_transform = intent == "TRANSFORM"
            skip_rag_greeting = intent == "GREETING"
            skip_rag_conversation = (
                intent == "CONVERSATION" and
                len(last_user_msg.split()) < 15 and
                "?" not in last_user_msg
            )
            skip_rag = skip_rag_transform or skip_rag_greeting or skip_rag_conversation

            if skip_rag_transform:
                logger.warning("[TRANSFORM-106] Intent=TRANSFORM erkannt — RAG und Query Expansion übersprungen")
            if skip_rag_greeting:
                logger.warning("[GREETING-137] Intent=GREETING erkannt — RAG übersprungen")

            logger.warning(f"[DEBUG-85] Intent: {intent} | Nachricht ({len(last_user_msg.split())} Wörter): {last_user_msg[:80]}")
            logger.warning(f"[DEBUG-85] RAG-Skip: {skip_rag} | intent={intent}, wörter={len(last_user_msg.split())}, '?'={'?' in last_user_msg}")

            if skip_rag:
                logger.info(f"⏭️ RAG übersprungen (intent={intent}, words={len(last_user_msg.split())}, '?'={'?' in last_user_msg})")
                rag_hits = []
            else:
                # 1. RAG-Suche auf die letzte User-Nachricht
                rag_hits = await _rag_search(last_user_msg, settings)
                logger.warning(f"[DEBUG-83] RAG results: {len(rag_hits) if rag_hits else 0} hits")
                if rag_hits:
                    for i, r in enumerate(rag_hits[:3]):
                        logger.warning(f"[DEBUG-83] RAG hit {i}: l2={r.get('l2_distance', 'N/A'):.3f} | text={r.get('text', '')[:80]}")

            # Patch 80b: Intent-Snippet einfügen (analog orchestrator.py)
            from zerberus.app.routers.orchestrator import INTENT_SNIPPETS
            snippet = INTENT_SNIPPETS.get(intent, "")

            if rag_hits:
                # Patch 108: Quelle + Kategorie + Score im Kontext-Header sichtbar
                def _fmt_hit(h: dict) -> str:
                    src = h.get("source") or "unbekannt"
                    cat = h.get("category") or "general"
                    score = h.get("score")
                    score_str = f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"
                    return f"[Quelle: {src} | Kategorie: {cat} | Score: {score_str}]\n{h['text']}"
                context_lines = "\n".join(_fmt_hit(h) for h in rag_hits)
                # Patch 101 (R-07): Aggregation-Hint für Listen-/Aufzählungs-Fragen
                agg_hint = (
                    "\n\nWICHTIG: Wenn die Frage nach einer Aufzählung, Liste oder "
                    "Zusammenfassung über MEHRERE Abschnitte fragt, nutze ALLE oben "
                    "stehenden Kontext-Abschnitte. Zähle alle relevanten Treffer auf, "
                    "nicht nur den ersten."
                )
                # Patch 111: Category-Hint bei gemischten Kategorien
                cats = {h.get("category") or "general" for h in rag_hits}
                if len(cats) > 1:
                    agg_hint += (
                        "\n\nDie Kontext-Abschnitte stammen aus verschiedenen Kategorien "
                        "(z.B. narrative, technical, lore). Bevorzuge Informationen aus "
                        "der zur Frage passenden Kategorie."
                    )
                enriched_content = f"[Intent: {intent}]\n{context_lines}{agg_hint}\n\n{snippet}\n{last_user_msg}" if snippet else f"[Intent: {intent}]\n{context_lines}{agg_hint}\n\n{last_user_msg}"
                # Letzte User-Nachricht in der Kopie anreichern
                for i in range(len(messages_for_llm) - 1, -1, -1):
                    if messages_for_llm[i]["role"] == "user":
                        messages_for_llm[i] = {"role": "user", "content": enriched_content}
                        break
                logger.warning(f"[AGG-101] Chunks in Prompt: {len(rag_hits)} | Aggregation-Hint: aktiv")
                logger.info(f"🧠 RAG lieferte {len(rag_hits)} Treffer für Legacy-Chat")
            elif snippet:
                # Auch ohne RAG: Intent-Snippet einfügen
                for i in range(len(messages_for_llm) - 1, -1, -1):
                    if messages_for_llm[i]["role"] == "user":
                        messages_for_llm[i] = {"role": "user", "content": f"[Intent: {intent}]\n{snippet}\n{last_user_msg}"}
                        break

            # Patch 80b: Fallback-Permission für Allgemeinwissen im System-Prompt
            for m in messages_for_llm:
                if m["role"] == "system":
                    _lower = m["content"].lower()
                    if not any(kw in _lower for kw in ("allgemein", "wissen", "smalltalk")):
                        m["content"] += (
                            "\nWenn keine spezifischen Dokumentinformationen verfügbar sind, "
                            "beantworte allgemeine Fragen aus deinem Allgemeinwissen und führe normale Gespräche."
                        )
                    break

            # Debug-Logging: System-Prompt-Ende + RAG-Status
            sys_content = next((m["content"] for m in messages_for_llm if m["role"] == "system"), "")
            logger.warning(f"[DEBUG-80b] System-Prompt (letzte 200 Zeichen): ...{sys_content[-200:]}")
            logger.warning(f"[DEBUG-80b] RAG-Kontext vorhanden: {bool(rag_hits)} | Anzahl RAG-Hits: {len(rag_hits)}")

            # Patch 85: llm_start Event für Typing-Bubble (war nur im Orchestrator, nicht im Legacy-Pfad)
            bus = get_event_bus()
            await bus.publish(Event(type="llm_start", data={}, session_id=session_id))

            # 2. LLM-Call mit angereichertem Kontext (Patch 61: temperature_override)
            answer, model, p_tok, c_tok, cost = await llm_service.call(messages_for_llm, session_id, temperature_override=temperature_override)
            logger.info(f"LLM [{model}] {p_tok}+{c_tok} Tokens, ${cost:.6f}")

        except Exception as e:
            logger.warning(f"⚠️ Orchestrator-Pipeline fehlgeschlagen, direkter LLM-Fallback: {e}")
            logger.warning(f"[FALLBACK-102] Direkter LLM-Fallback aktiviert (Cloud-Default-Modell), Grund: Orchestrator-Exception ({type(e).__name__})")
            bus = get_event_bus()
            await bus.publish(Event(type="llm_start", data={}, session_id=session_id))
            answer, model, _, _, cost = await llm_service.call(messages_for_llm, session_id, temperature_override=temperature_override)
    else:
        # Fallback: direkter LLM-Call ohne RAG
        logger.warning("[FALLBACK-102] Direkter LLM-Fallback aktiviert (Cloud-Default-Modell), Grund: Orchestrator nicht initialisiert")
        bus = get_event_bus()
        await bus.publish(Event(type="llm_start", data={}, session_id=session_id))
        answer, model, _, _, cost = await llm_service.call(messages_for_llm, session_id, temperature_override=temperature_override)

    # Patch 120: Ach-laber-doch-nicht Guard — fail-open, haengt bei WARNUNG einen Qualitaetshinweis an.
    if settings.features.get("hallucination_guard", True):
        try:
            from zerberus.hallucination_guard import check_response
            _rag_hits_local = locals().get("rag_hits") or []
            _guard_ctx = ""
            if _rag_hits_local:
                _guard_ctx = "\n".join(
                    f"[{h.get('source','?')}|{h.get('category','general')}] {h.get('text','')}"
                    for h in _rag_hits_local[:5]
                )
            # Patch 158: caller_context verhindert Halluzinations-False-Positives
            # auf Zerberus-Selbstreferenzen (Nala/Hel/Huginn/Chris).
            _nala_guard_context = (
                "Der Antwortende ist 'Nala', ein persoenlicher KI-Assistent im "
                "Zerberus-System. Referenzen auf Zerberus, Chris, Nala, Hel, "
                "Huginn und das Zerberus-Projekt sind keine Halluzinationen "
                "sondern korrekte Selbstreferenzen."
            )
            _guard = await check_response(
                user_message=last_user_msg,
                assistant_response=answer,
                rag_context=_guard_ctx,
                caller_context=_nala_guard_context,
            )
            if _guard.get("verdict") == "WARNUNG":
                answer = f"{answer}\n\n---\n⚠️ *Qualitaetshinweis: {_guard.get('reason', 'Guard hat angeschlagen.')}*"
        except Exception as _guard_err:
            logger.warning(f"[GUARD-120] fail-open, Ausnahme wurde ignoriert: {_guard_err}")

    # Patch 203d-2: User-Insert frueh (Eingabe ist endgueltig). Assistant-
    # Insert wird ans Ende verschoben (nach Synthese), damit der
    # gespeicherte Text der finale Output ist und nicht der Roh-Output mit
    # Code-Block. update_interaction wandert mit, damit der Pacemaker den
    # finalen State sieht.
    try:
        await store_interaction("user", last_user_msg, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
    except Exception as e:
        logger.warning(f"⚠️ store_interaction(user) fehlgeschlagen (non-fatal): {e}")

    # Patch 203d-1 (Phase 5a #5): Code-Detection + Sandbox-Roundtrip im
    # Workspace des aktiven Projekts. Wenn der LLM-Output einen Fenced-
    # Code-Block in einer erlaubten Sprache enthaelt UND ein aktives,
    # nicht-archiviertes Projekt gesetzt ist UND die Sandbox aktiv ist,
    # wird der Block in der projekt-spezifischen Workspace-Sandbox
    # ausgefuehrt (RO-Mount aus P203c). Result landet als additives
    # `code_execution`-Feld in der Response — kein zweiter LLM-Call,
    # keine Output-Synthese (das macht P203d-2). UI-Render ist P203d-3.
    #
    # Gate-Reihenfolge (alles fail-open ausser ein Gate verbietet's):
    # 1. `active_project_id` aus dem Header (P201) — sonst Datei-Fallback
    # 2. `project_slug` aus dem Persona-Resolver vorhanden
    # 3. `project_overlay is not None` — bei archiviertem Projekt liefert
    #    `resolve_project_overlay` (None, slug); aktive Projekte ohne
    #    Overlay haben ({}, slug). Wir blocken Code-Execution auf
    #    archivierten Projekten konservativ — der User hat das Projekt
    #    bewusst auf Eis gelegt.
    # 4. Sandbox-Feature (`settings.modules.sandbox.enabled`)
    # 5. Code-Block via `first_executable_block` in `allowed_languages`
    # 6. `execute_in_workspace` — None heisst Slug-Reject oder Disabled,
    #    SandboxResult heisst durchgelaufen (auch bei exit_code != 0).
    code_execution_payload: dict | None = None
    # Patch 206: HitL-Gate-Variablen — pending_id fuer Audit-Trail-Korrelation,
    # hitl_status fuer Synthese-Skip + Audit-Row.
    hitl_pending_id: str | None = None
    hitl_status_for_audit: str | None = None
    # Patch 209 (Phase 5a #7): Veto-Variablen — audit_id fuer Audit-Korrelation,
    # status fuer Audit-Row (pass/veto/skipped/error).
    veto_audit_id: str | None = None
    veto_status_for_audit: str | None = None
    veto_reason_for_audit: str | None = None
    veto_latency_for_audit: int | None = None
    veto_language_for_audit: str | None = None
    veto_code_for_audit: str | None = None
    if (
        active_project_id is not None
        and project_slug
        and project_overlay is not None
    ):
        try:
            from zerberus.modules.sandbox.manager import get_sandbox_manager
            from zerberus.utils.code_extractor import first_executable_block
            from zerberus.core.projects_workspace import execute_in_workspace

            _sandbox = get_sandbox_manager()
            if _sandbox.config.enabled:
                _block = first_executable_block(
                    answer,
                    list(_sandbox.config.allowed_languages),
                )
                if _block is not None:
                    # Patch 209 (Phase 5a #7): Veto-Probe VOR HitL-Gate.
                    # Ein zweites Modell beurteilt den Code: macht das,
                    # was der User wollte UND ist es sicher? Bei VETO
                    # ueberspringen wir HitL + Sandbox komplett und
                    # liefern einen Wandschlag-Payload mit Begruendung.
                    # Trigger-Gate ist eine Pure-Function (skipt triviale
                    # 1-Zeiler ohne Risk-Tokens). Fail-open: jeder Fehler
                    # im Probe-Pfad behandelt veto=False (weiter zum
                    # HitL-Gate). Default-aktiv via
                    # ``settings.projects.code_veto_enabled``.
                    _veto_skip_hitl_and_sandbox = False
                    _veto_payload: dict | None = None
                    veto_language_for_audit = _block.language
                    veto_code_for_audit = _block.code
                    if getattr(settings.projects, "code_veto_enabled", True):
                        try:
                            from zerberus.core.code_veto import (
                                should_run_veto,
                                run_veto,
                                new_audit_id,
                            )
                            if should_run_veto(_block.code, _block.language):
                                veto_audit_id = new_audit_id()
                                _veto_temp = float(
                                    getattr(
                                        settings.projects,
                                        "code_veto_temperature",
                                        0.1,
                                    )
                                )
                                _verdict = await run_veto(
                                    _block.code,
                                    _block.language,
                                    last_user_msg,
                                    llm_service,
                                    session_id,
                                    temperature=_veto_temp,
                                )
                                veto_latency_for_audit = _verdict.latency_ms
                                if _verdict.error:
                                    veto_status_for_audit = "error"
                                    veto_reason_for_audit = _verdict.error
                                elif _verdict.veto:
                                    veto_status_for_audit = "veto"
                                    veto_reason_for_audit = _verdict.reason
                                    _veto_skip_hitl_and_sandbox = True
                                    _veto_payload = {
                                        "language": _block.language,
                                        "code": _block.code,
                                        "exit_code": -1,
                                        "stdout": "",
                                        "stderr": "",
                                        "execution_time_ms": 0,
                                        "truncated": False,
                                        "error": (
                                            _verdict.reason
                                            or "Veto vom zweiten Modell"
                                        ),
                                        "skipped": True,
                                        "hitl_status": "vetoed",
                                        "veto": _verdict.to_payload_dict(),
                                    }
                                    logger.info(
                                        f"[VETO-209] blocked session={session_id} "
                                        f"language={_block.language} "
                                        f"reason_len={len(_verdict.reason or '')}"
                                    )
                                else:
                                    veto_status_for_audit = "pass"
                                    veto_reason_for_audit = None
                            else:
                                veto_status_for_audit = "skipped"
                                veto_reason_for_audit = None
                        except Exception as _veto_err:
                            logger.warning(
                                f"[VETO-209] Pipeline-Fehler (fail-open): {_veto_err}"
                            )
                            veto_status_for_audit = "error"
                            veto_reason_for_audit = str(_veto_err)
                    # Wenn code_veto_enabled=False, schreiben wir KEINEN
                    # Audit — der Veto-Pfad existiert in dem Fall faktisch
                    # nicht, weder im Hauptpfad noch im Audit-Trail.

                    if _veto_skip_hitl_and_sandbox and _veto_payload is not None:
                        code_execution_payload = _veto_payload
                        # Wir setzen hitl_status_for_audit NICHT, weil HitL
                        # nicht lief — die code_executions-Tabelle bleibt
                        # leer fuer diesen Run, der Audit landet
                        # ausschliesslich in code_vetoes.

                if _block is not None and not _veto_skip_hitl_and_sandbox:
                    # Patch 206 (Phase 5a #6): HitL-Gate VOR Sandbox-Run.
                    # Long-Poll innerhalb der Chat-Completions-Request:
                    # Pending wird angelegt, das Frontend pollt parallel
                    # ``GET /v1/hitl/poll`` und rendert die Confirm-Karte.
                    # User klickt → ``POST /v1/hitl/resolve`` → Event.set →
                    # wir wachen auf und fuehren aus (oder skippen). Bei
                    # ``hitl_enabled=False`` laeuft der alte P203d-1-Pfad
                    # (Status ``bypassed`` im Audit).
                    _hitl_decision: str
                    if getattr(settings.projects, "hitl_enabled", True):
                        from zerberus.core.hitl_chat import get_chat_hitl_gate
                        _gate = get_chat_hitl_gate()
                        _pending = await _gate.create_pending(
                            session_id=session_id,
                            project_id=active_project_id,
                            project_slug=project_slug,
                            code=_block.code,
                            language=_block.language,
                        )
                        hitl_pending_id = _pending.id
                        _timeout = float(
                            getattr(settings.projects, "hitl_timeout_seconds", 60)
                        )
                        _hitl_decision = await _gate.wait_for_decision(
                            _pending.id, _timeout,
                        )
                        # Cleanup nach Auswertung — sonst waechst der
                        # In-Memory-Store monoton bei jedem Code-Block.
                        _gate.cleanup(_pending.id)
                        logger.info(
                            f"[HITL-206] decision id={_pending.id} "
                            f"session={session_id} status={_hitl_decision}"
                        )
                    else:
                        _hitl_decision = "bypassed"
                        logger.info(
                            f"[HITL-206] bypassed session={session_id} "
                            f"(hitl_enabled=False)"
                        )
                    hitl_status_for_audit = _hitl_decision

                    if _hitl_decision in ("approved", "bypassed"):
                        _base_dir = Path(settings.projects.data_dir)
                        # Patch 207: writable-Mount-Steuerung + Snapshot-
                        # Schicht. Wenn ``sandbox_writable=True`` UND
                        # ``snapshots_enabled=True``, schiesst der
                        # Endpunkt einen ``before_run``-Snapshot, faehrt
                        # die Sandbox writable und schiesst danach einen
                        # ``after_run``-Snapshot. Die Diff-Liste plus
                        # beide ``snapshot_id``s landen additiv im
                        # ``code_execution``-Feld der Response. Default
                        # bleibt RO (sandbox_writable=False) — dann ist
                        # P207 ein No-Op und der Pfad verhaelt sich
                        # exakt wie P206.
                        _writable = bool(getattr(settings.projects, "sandbox_writable", False))
                        _snapshots_active = (
                            _writable
                            and bool(getattr(settings.projects, "snapshots_enabled", True))
                        )
                        _before_snap: dict | None = None
                        if _snapshots_active:
                            try:
                                from zerberus.core.projects_snapshots import (
                                    snapshot_workspace_async,
                                )
                                _before_snap = await snapshot_workspace_async(
                                    project_id=active_project_id,
                                    base_dir=_base_dir,
                                    label="before_run",
                                    pending_id=hitl_pending_id,
                                )
                            except Exception as _snap_err:
                                logger.warning(
                                    f"[SNAPSHOT-207] before_run fehlgeschlagen "
                                    f"(fail-open): {_snap_err}"
                                )
                                _before_snap = None

                        _result = await execute_in_workspace(
                            project_id=active_project_id,
                            code=_block.code,
                            language=_block.language,
                            base_dir=_base_dir,
                            writable=_writable,
                        )
                        if _result is not None:
                            code_execution_payload = {
                                "language": _block.language,
                                "code": _block.code,
                                "exit_code": _result.exit_code,
                                "stdout": _result.stdout,
                                "stderr": _result.stderr,
                                "execution_time_ms": _result.execution_time_ms,
                                "truncated": _result.truncated,
                                "error": _result.error,
                                "skipped": False,
                                "hitl_status": _hitl_decision,
                            }
                            logger.info(
                                f"[SANDBOX-203d] project_id={active_project_id} slug={project_slug!r} "
                                f"language={_block.language} exit_code={_result.exit_code} "
                                f"stdout_len={len(_result.stdout)} stderr_len={len(_result.stderr)} "
                                f"time_ms={_result.execution_time_ms} truncated={_result.truncated} "
                                f"writable={_writable}"
                            )
                            # Patch 207: after_run-Snapshot + Diff. Nur
                            # wenn before_run erfolgreich war — sonst
                            # gibt's keine sinnvolle Vergleichsbasis.
                            # Diff/Snapshot-Felder bleiben None, wenn
                            # _writable False oder Snapshots deaktiviert.
                            if _snapshots_active and _before_snap is not None:
                                try:
                                    from zerberus.core.projects_snapshots import (
                                        snapshot_workspace_async,
                                        diff_snapshots,
                                    )
                                    _after_snap = await snapshot_workspace_async(
                                        project_id=active_project_id,
                                        base_dir=_base_dir,
                                        label="after_run",
                                        pending_id=hitl_pending_id,
                                        parent_snapshot_id=_before_snap["id"],
                                    )
                                    if _after_snap is not None:
                                        _diff = diff_snapshots(
                                            _before_snap["manifest"],
                                            _after_snap["manifest"],
                                        )
                                        code_execution_payload["diff"] = [
                                            d.to_public_dict() for d in _diff
                                        ]
                                        code_execution_payload["before_snapshot_id"] = _before_snap["id"]
                                        code_execution_payload["after_snapshot_id"] = _after_snap["id"]
                                        logger.info(
                                            f"[SNAPSHOT-207] diff project_id={active_project_id} "
                                            f"before={_before_snap['id']} after={_after_snap['id']} "
                                            f"changes={len(_diff)}"
                                        )
                                except Exception as _diff_err:
                                    logger.warning(
                                        f"[SNAPSHOT-207] after_run/diff fehlgeschlagen "
                                        f"(fail-open): {_diff_err}"
                                    )
                        else:
                            logger.info(
                                f"[SANDBOX-203d] execute_in_workspace returned None "
                                f"(slug_reject/disabled/missing) project_id={active_project_id}"
                            )
                    else:
                        # rejected | timeout — Skip-Payload mit Reason,
                        # damit Frontend die Code-Card mit Skip-Banner
                        # rendern kann (P203d-3 Renderer ist additiv).
                        _skip_reason = (
                            "Vom User abgebrochen"
                            if _hitl_decision == "rejected"
                            else "Keine User-Bestaetigung (Timeout)"
                        )
                        code_execution_payload = {
                            "language": _block.language,
                            "code": _block.code,
                            "exit_code": -1,
                            "stdout": "",
                            "stderr": "",
                            "execution_time_ms": 0,
                            "truncated": False,
                            "error": _skip_reason,
                            "skipped": True,
                            "hitl_status": _hitl_decision,
                        }
                        logger.info(
                            f"[HITL-206] skipped session={session_id} "
                            f"status={_hitl_decision} language={_block.language}"
                        )
                if _block is None:
                    logger.info(
                        f"[SANDBOX-203d] kein executable Code-Block project_id={active_project_id}"
                    )
            else:
                logger.info("[SANDBOX-203d] Sandbox disabled — Code-Detection uebersprungen")
        except Exception as _sandbox_err:
            logger.warning(
                f"[SANDBOX-203d] Pipeline-Fehler (fail-open): {_sandbox_err}"
            )
            hitl_status_for_audit = "error"

    # Patch 203d-2 (Phase 5a #5): Output-Synthese. Wenn der P203d-1-Pfad
    # ein ``code_execution_payload`` produziert hat UND der Trigger-Gate
    # zustimmt (exit_code != 0 ODER stdout nicht leer), ruft ein zweiter
    # LLM-Call die Synthese auf: Original-Frage + Code + stdout/stderr →
    # menschenlesbarer Antworttext, der den Roh-Output ersetzt.
    #
    # Fail-Open auf jeder Stufe — wenn die Synthese crasht oder leer
    # zurueckkommt, behaelt der Endpoint die Original-LLM-Antwort (mit
    # Code-Block); das ``code_execution``-Feld ist trotzdem in der
    # Response, damit das Frontend den Roh-Output ggf. selbst rendern
    # kann (P203d-3 UI-Render).
    # Patch 206: Skip-Synthese wenn HitL den Code-Block geblockt hat
    # (skipped=True). Es gibt nichts auszuwerten, und die Original-Antwort
    # zeigt dem User noch den Code-Block — die HitL-Skip-Begruendung kommt
    # im ``code_execution.error``-Feld plus Frontend-Skip-Banner.
    if code_execution_payload is not None and not code_execution_payload.get("skipped"):
        try:
            from zerberus.modules.sandbox.synthesis import synthesize_code_output

            synthesized = await synthesize_code_output(
                user_prompt=last_user_msg,
                payload=code_execution_payload,
                llm_service=llm_service,
                session_id=session_id,
            )
            if synthesized:
                answer = synthesized
        except Exception as _synth_err:
            logger.warning(
                f"[SYNTH-203d-2] Pipeline-Fehler (fail-open): {_synth_err}"
            )

    # Patch 203d-2: Assistant-Insert NACH der Synthese, damit ``answer`` der
    # finale Text ist. Falls Synthese skipte oder fehlschlug, ist es der
    # Original-LLM-Output (Backwards-Compat zu P203d-1).
    try:
        await store_interaction("assistant", answer, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
        await update_interaction()
    except Exception as e:
        logger.warning(f"⚠️ store_interaction(assistant) fehlgeschlagen (non-fatal): {e}")

    # Patch 206 (Phase 5a #6): Audit-Trail-Zeile in ``code_executions``.
    # Schreibt nur wenn ein Code-Block erkannt wurde (Payload ist nicht None
    # ODER ein hitl_status fuer den Pipeline-Pfad gesetzt wurde). Der
    # Helper schluckt jeden Fehler — Hauptpfad bleibt grün.
    if code_execution_payload is not None and hitl_status_for_audit is not None:
        try:
            from zerberus.core.hitl_chat import store_code_execution_audit
            await store_code_execution_audit(
                session_id=session_id,
                project_id=active_project_id,
                project_slug=project_slug,
                payload=code_execution_payload,
                pending_id=hitl_pending_id,
                hitl_status=hitl_status_for_audit,
            )
        except Exception as _audit_err:
            logger.warning(
                f"[HITL-206] Audit-Schreiben fehlgeschlagen (non-fatal): {_audit_err}"
            )

    # Patch 209 (Phase 5a #7): Audit-Trail-Zeile in ``code_vetoes``.
    # Schreibt fuer jeden Code-Block, fuer den der Veto-Pfad einen Status
    # gesetzt hat (pass/veto/skipped/error). Auch bei "skipped"/"pass"
    # auditieren wir — die Statistik braucht alle drei Werte fuer
    # System-Prompt-Tuning + Threshold-Anpassung.
    if veto_status_for_audit is not None:
        try:
            from zerberus.core.code_veto import store_veto_audit
            await store_veto_audit(
                audit_id=veto_audit_id,
                session_id=session_id,
                project_id=active_project_id,
                project_slug=project_slug,
                language=veto_language_for_audit,
                code_text=veto_code_for_audit,
                user_prompt=last_user_msg,
                verdict=veto_status_for_audit,
                reason=veto_reason_for_audit,
                latency_ms=veto_latency_for_audit,
            )
        except Exception as _audit_err:
            logger.warning(
                f"[VETO-209] Audit-Schreiben fehlgeschlagen (non-fatal): {_audit_err}"
            )

    # Patch 208 (Phase 5a #8): Audit-Trail-Zeile in ``clarifications``.
    # Schreibt nur wenn der Spec-Check-Pfad einen Status erzeugt hat
    # (Probe-Call lief). Bei "cancelled" haben wir oben schon early-
    # returned und dort geschrieben — der Block hier deckt
    # ``answered``/``bypassed``/``timeout``/``error`` ab.
    if spec_status_for_audit is not None and spec_status_for_audit != "cancelled":
        try:
            from zerberus.core.spec_check import store_clarification_audit
            await store_clarification_audit(
                pending_id=spec_pending_id,
                session_id=session_id,
                project_id=active_project_id,
                project_slug=project_slug,
                original_message=last_user_msg,
                question=spec_question_text,
                answer_text=spec_answer_text_value,
                score=spec_score_value,
                source=spec_source_value,
                status=spec_status_for_audit,
            )
        except Exception as _audit_err:
            logger.warning(
                f"[SPEC-208] Audit-Schreiben fehlgeschlagen (non-fatal): {_audit_err}"
            )

    # Patch 192: Sentiment-Triptychon — BERT-Analyse fuer User + Bot, Konsens
    # mit optionaler Prosodie aus dem X-Prosody-Context Header (one-shot).
    # Fail-open: jeder Fehler setzt sentiment_payload auf None; OpenAI-Schema
    # bleibt unveraendert.
    sentiment_payload: dict | None = None
    try:
        from zerberus.utils.sentiment_display import build_sentiment_payload
        _user_prosody = None
        if _prosody_ctx_raw and _prosody_consent:
            try:
                _user_prosody = json.loads(_prosody_ctx_raw)
            except (json.JSONDecodeError, ValueError):
                _user_prosody = None
        sentiment_payload = {
            "user": build_sentiment_payload(last_user_msg, prosody=_user_prosody),
            "bot": build_sentiment_payload(answer, prosody=None),
        }
        _u_emoji = (sentiment_payload["user"].get("consensus") or {}).get("emoji", "?")
        _b_emoji = (sentiment_payload["bot"].get("consensus") or {}).get("emoji", "?")
        logger.info(f"[SENTIMENT-192] user={_u_emoji} bot={_b_emoji}")
    except Exception as _sent_err:
        logger.warning(f"[SENTIMENT-192] Triptychon-Payload fehlgeschlagen (fail-open): {_sent_err}")
        sentiment_payload = None

    # Response-Format bleibt exakt OpenAI-kompatibel (Nala-Weiche).
    # Patch 192: zusaetzliches `sentiment`-Feld ist additiv — Clients die nur
    # `choices` lesen (Dictate, SillyTavern, OpenAI-SDK) bleiben kompatibel.
    # Patch 203d-1: zusaetzliches `code_execution`-Feld ist additiv —
    # bleibt None wenn kein Projekt aktiv, keine Sandbox oder kein Block.
    return ChatCompletionResponse(
        created=int(datetime.now().timestamp()),
        model=model,
        choices=[Choice(index=0, message=Message(role="assistant", content=answer), finish_reason="stop")],
        sentiment=sentiment_payload,
        code_execution=code_execution_payload,
    )


# ---------- Patch 206 — HitL-Gate-Endpoints fuer Chat-Code-Execution ----------
#
# Two-Endpoint-Pattern: Frontend pollt ``/v1/hitl/poll`` waehrend der Chat-
# Completions-Request long-pollt, rendert die Confirm-Karte sobald ein
# Pending zur Session existiert, und schickt die Entscheidung via
# ``/v1/hitl/resolve``. Beide Endpoints sind /v1/-auth-frei (Dictate-Lane-
# Invariante) — Ownership ueber session_id (poll) bzw. UUID4-pending_id +
# session_id-Match (resolve).


class HitlResolveRequest(BaseModel):
    pending_id: str
    decision: str  # "approved" | "rejected"
    session_id: str | None = None  # Defense-in-Depth: Cross-Session-Resolve blocken


class HitlPollResponse(BaseModel):
    pending: dict | None = None  # to_public_dict() oder None wenn keiner anliegt


class HitlResolveResponse(BaseModel):
    ok: bool
    decision: str | None = None


@router.get("/hitl/poll", response_model=HitlPollResponse)
async def hitl_poll(request: Request):
    """Liefert das aelteste pending HitL-Item dieser Session (oder None).

    Frontend pollt diesen Endpoint im Sekunden-Takt, solange der Chat-
    Completions-Request offen ist. Mehrere Pendings pro Session sind in
    der aktuellen Implementierung nicht erwartbar (ein Code-Block pro
    Chat-Turn), aber wir liefern bewusst nur den aeltesten — der Caller
    sieht ein FIFO-Verhalten.
    """
    session_id = request.headers.get("X-Session-ID") or "legacy-default"
    from zerberus.core.hitl_chat import get_chat_hitl_gate
    gate = get_chat_hitl_gate()
    pendings = gate.list_for_session(session_id)
    if not pendings:
        return HitlPollResponse(pending=None)
    pendings.sort(key=lambda p: p.created_at)
    return HitlPollResponse(pending=pendings[0].to_public_dict())


@router.post("/hitl/resolve", response_model=HitlResolveResponse)
async def hitl_resolve(req: HitlResolveRequest, request: Request):
    """Setzt die Entscheidung zu einem Pending. Idempotent.

    Resolve gilt nur wenn der Pending noch ``pending`` ist — Doppel-Klick
    aus dem UI macht keinen Schaden, gibt aber ``ok=False`` zurueck. Der
    optionale ``session_id``-Parameter (oder ``X-Session-ID``-Header) wird
    gegen den im Pending vermerkten verglichen — ein anderer Tab kann
    mein Pending nicht resolven.
    """
    session_id = req.session_id or request.headers.get("X-Session-ID") or None
    from zerberus.core.hitl_chat import get_chat_hitl_gate
    gate = get_chat_hitl_gate()
    ok = await gate.resolve(
        req.pending_id,
        req.decision,
        session_id=session_id,
    )
    return HitlResolveResponse(
        ok=ok,
        decision=req.decision if ok else None,
    )


# ---------- Patch 208 — Spec-Contract / Klarstellungs-Probes ----------
#
# Two-Endpoint-Pattern analog HitL (P206): Frontend pollt
# ``/v1/spec/poll`` waehrend der Chat-Completions-Request long-pollt,
# rendert die Klarstellungs-Karte sobald ein Pending zur Session
# existiert, und schickt die Entscheidung via ``/v1/spec/resolve``.
# Beide Endpoints sind /v1/-auth-frei (Dictate-Lane-Invariante) — Owner-
# ship ueber session_id (poll) bzw. UUID4-pending_id + session_id-Match
# (resolve). Decision-Werte: ``answered`` (mit ``answer_text``),
# ``bypassed`` ("Trotzdem versuchen"), ``cancelled`` (Chat verwerfen).


class SpecResolveRequest(BaseModel):
    pending_id: str
    decision: str  # "answered" | "bypassed" | "cancelled"
    session_id: str | None = None
    answer_text: str | None = None  # bei decision=answered Pflicht


class SpecPollResponse(BaseModel):
    pending: dict | None = None  # to_public_dict() oder None


class SpecResolveResponse(BaseModel):
    ok: bool
    decision: str | None = None


@router.get("/spec/poll", response_model=SpecPollResponse)
async def spec_poll(request: Request):
    """Liefert das aelteste pending Spec-Pending dieser Session (oder None).

    Frontend pollt diesen Endpoint im Sekunden-Takt waehrend der
    Chat-Completions-Request offen ist.
    """
    session_id = request.headers.get("X-Session-ID") or "legacy-default"
    from zerberus.core.spec_check import get_chat_spec_gate
    gate = get_chat_spec_gate()
    pendings = gate.list_for_session(session_id)
    if not pendings:
        return SpecPollResponse(pending=None)
    pendings.sort(key=lambda p: p.created_at)
    return SpecPollResponse(pending=pendings[0].to_public_dict())


@router.post("/spec/resolve", response_model=SpecResolveResponse)
async def spec_resolve(req: SpecResolveRequest, request: Request):
    """Setzt die Entscheidung zu einem Spec-Pending. Idempotent.

    - ``answered`` braucht non-empty ``answer_text``, sonst False.
    - Cross-Session-Resolve via ``session_id``-Mismatch wird geblockt.
    """
    session_id = req.session_id or request.headers.get("X-Session-ID") or None
    from zerberus.core.spec_check import get_chat_spec_gate
    gate = get_chat_spec_gate()
    ok = await gate.resolve(
        req.pending_id,
        req.decision,
        session_id=session_id,
        answer_text=req.answer_text,
    )
    return SpecResolveResponse(
        ok=ok,
        decision=req.decision if ok else None,
    )


# ---------- Patch 207 — Workspace-Rollback (Phase 5a #9 + #10) ----------
#
# Stellt den Workspace eines Projekts auf einen frueheren Snapshot zurueck.
# Auth-frei wie /v1/hitl/* (Dictate-Lane-Invariante). Defense-in-Depth:
# der Caller muss ``project_id`` mitschicken, das gegen den Snapshot-
# Eigentuemer verglichen wird — ein Snapshot aus Projekt A kann nicht
# ueber Projekt B angewendet werden.
#
# Das Frontend (Nala-Diff-Card) ruft das mit ``snapshot_id =
# code_execution.before_snapshot_id`` auf, sobald der User auf "↩️
# Aenderungen zurueckdrehen" klickt. Idempotent: zweites Rollback
# auf denselben Snapshot stellt den gleichen Stand wieder her, ist also
# ein No-Op (oder ein Re-Apply, falls inzwischen wieder Aenderungen
# entstanden sind).


class WorkspaceRollbackRequest(BaseModel):
    snapshot_id: str
    project_id: int


class WorkspaceRollbackResponse(BaseModel):
    ok: bool
    snapshot_id: str | None = None
    project_id: int | None = None
    project_slug: str | None = None
    file_count: int | None = None
    total_bytes: int | None = None
    error: str | None = None


@router.post("/workspace/rollback", response_model=WorkspaceRollbackResponse)
async def workspace_rollback(
    req: WorkspaceRollbackRequest,
    settings: Settings = Depends(get_settings),
):
    """Rollback eines Projekt-Workspaces auf einen Snapshot-Stand.

    Reject-Pfade (alle liefern ``ok=False`` mit ``error``-Reason):
        - Snapshot-ID nicht in DB (``unknown_snapshot``)
        - ``project_id`` mismatch zum Snapshot-Eigentuemer
          (``project_mismatch``)
        - Snapshot ohne ``project_slug`` in der DB (``missing_slug``)
        - Tar-Restore liefert None (``restore_failed``)
        - Snapshots-Feature deaktiviert (``snapshots_disabled``)
    """
    if not bool(getattr(settings.projects, "snapshots_enabled", True)):
        return WorkspaceRollbackResponse(
            ok=False,
            error="snapshots_disabled",
        )
    from zerberus.core.projects_snapshots import rollback_snapshot_async

    base_dir = Path(settings.projects.data_dir)
    try:
        result = await rollback_snapshot_async(
            snapshot_id=req.snapshot_id,
            base_dir=base_dir,
            expected_project_id=req.project_id,
        )
    except Exception as e:
        logger.warning(
            f"[SNAPSHOT-207] rollback_endpoint Pipeline-Fehler: {e}"
        )
        return WorkspaceRollbackResponse(
            ok=False,
            snapshot_id=req.snapshot_id,
            project_id=req.project_id,
            error="pipeline_error",
        )
    if result is None:
        return WorkspaceRollbackResponse(
            ok=False,
            snapshot_id=req.snapshot_id,
            project_id=req.project_id,
            error="restore_failed",
        )
    logger.info(
        f"[SNAPSHOT-207] rollback_endpoint snapshot_id={req.snapshot_id} "
        f"project_id={result['project_id']} slug={result['project_slug']!r} "
        f"file_count={result['file_count']}"
    )
    return WorkspaceRollbackResponse(
        ok=True,
        snapshot_id=result["snapshot_id"],
        project_id=result["project_id"],
        project_slug=result["project_slug"],
        file_count=result["file_count"],
        total_bytes=result["total_bytes"],
    )


# ---------- Audio-Endpunkt ----------

@router.post("/audio/transcriptions")
async def audio_transcriptions(
    request: Request,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings)
):
    whisper_url = settings.legacy.urls.whisper_url

    try:
        audio_data = await file.read()

        # Patch 160: Short-Audio-Guard + konfigurierbarer Timeout + Einmal-Retry
        # leben im zentralen whisper_client, damit legacy.py und nala.py
        # denselben Pfad teilen.
        from zerberus.utils.whisper_client import transcribe, WhisperSilenceGuard

        # Patch 188: Prosodie-Foundation + Patch 190: Pipeline aktiviert.
        # Patch 191: Consent-Header — Prosodie nur wenn User explizit zugestimmt.
        prosody_consent = request.headers.get("X-Prosody-Consent", "false").lower() == "true"

        # Patch 190: Wenn Prosodie aktiv UND Consent: parallel zu Whisper analysieren.
        # Bei Whisper-Fehler bricht der Endpoint ab (harter Fehler).
        # Bei Prosodie-Fehler läuft Whisper alleine weiter (weicher Fehler).
        from zerberus.modules.prosody.manager import get_prosody_manager
        _prosody_mgr = get_prosody_manager(settings)
        _prosody_active = _prosody_mgr.is_active and prosody_consent

        async def _whisper_call():
            return await transcribe(
                whisper_url=whisper_url,
                audio_data=audio_data,
                filename=file.filename,
                content_type=file.content_type,
                whisper_cfg=settings.whisper,
            )

        try:
            if _prosody_active:
                logger.info("[PROSODY-190] Whisper+Gemma parallel (Consent gegeben)")
                whisper_task = asyncio.create_task(_whisper_call())
                prosody_task = asyncio.create_task(_prosody_mgr.analyze(audio_data))
                whisper_result, prosody_outcome = await asyncio.gather(
                    whisper_task, prosody_task, return_exceptions=True,
                )
                if isinstance(whisper_result, Exception):
                    raise whisper_result
                if isinstance(prosody_outcome, Exception):
                    logger.warning(f"[PROSODY-190] Analyse fehlgeschlagen: {prosody_outcome}")
                    prosody_outcome = None
                else:
                    _src = (prosody_outcome or {}).get("source", "?")
                    _mood = (prosody_outcome or {}).get("mood", "?")
                    _conf = (prosody_outcome or {}).get("confidence", 0.0)
                    logger.info(f"[PROSODY-190] mood={_mood} confidence={_conf:.2f} source={_src}")
            else:
                whisper_result = await _whisper_call()
                prosody_outcome = None
        except WhisperSilenceGuard:
            # Short-Audio: OpenAI-kompatibles Leer-Transkript.
            return {"text": "", "note": "short_audio_skipped"}

        raw_transcript = whisper_result.get("text", "")

        # Patch 135: X-Already-Cleaned-Header überspringt Cleaning
        already_cleaned = request.headers.get("X-Already-Cleaned", "").lower() == "true"
        if already_cleaned:
            logger.info("[PIPELINE-135] Cleaner übersprungen (X-Already-Cleaned=true)")
            cleaned_transcript = raw_transcript
        else:
            cleaned_transcript = clean_transcript(raw_transcript)

        # P166: Vollen Text nicht ins Terminal fluten — Einzeiler reicht.
        logger.info(
            f"🎤 Audio-Transkript erfolgreich (raw={len(raw_transcript)} Zeichen, "
            f"clean={len(cleaned_transcript)} Zeichen)"
        )
        logger.debug(f"🎤 Transkript: '{raw_transcript}' -> '{cleaned_transcript}'")

        # Patch 83: Stille/leeres Transkript nach Cleaner abfangen
        if not cleaned_transcript.strip():
            logger.info("[DEBUG-83] Stille erkannt — leeres Transkript nach Cleaner")
            return {"text": "", "note": "silence_detected"}

        await store_interaction("whisper_input", raw_transcript, integrity=0.9)
        await update_interaction()

        # Patch 190: Prosodie-Result als optionales Feld in der Response.
        # Frontend reicht es als X-Prosody-Context-Header an /chat/completions
        # weiter. KEIN Schreiben in die DB (Worker-Protection P191).
        response = {"text": cleaned_transcript}
        _prosody_clean: dict | None = None
        if prosody_outcome and isinstance(prosody_outcome, dict) and prosody_outcome.get("source") != "stub":
            _prosody_clean = {
                "mood": prosody_outcome.get("mood"),
                "tempo": prosody_outcome.get("tempo"),
                "confidence": prosody_outcome.get("confidence"),
                "valence": prosody_outcome.get("valence"),
                "arousal": prosody_outcome.get("arousal"),
                "dominance": prosody_outcome.get("dominance"),
                "source": prosody_outcome.get("source"),
            }
            # Backward-Compat: prosody-Top-Level-Feld bleibt (P190-Schema).
            response["prosody"] = _prosody_clean

        # Patch 193: Whisper-Endpoint Enrichment — additives sentiment-Feld
        # mit BERT-Label/Score und optionalem Konsens (wenn Prosodie da).
        # text-Feld ist IMMER da; Clients die nur ["text"] lesen, bleiben
        # kompatibel. Fail-open bei BERT-Fehlern.
        try:
            from zerberus.modules.sentiment.router import analyze_sentiment
            from zerberus.utils.sentiment_display import compute_consensus
            _bert = analyze_sentiment(cleaned_transcript or "")
            _sentiment_block: dict = {
                "bert": {
                    "label": _bert.get("label", "neutral"),
                    "score": float(_bert.get("score", 0.5)),
                }
            }
            if _prosody_clean is not None:
                _sentiment_block["consensus"] = compute_consensus(
                    _bert.get("label", "neutral"),
                    float(_bert.get("score", 0.5)),
                    _prosody_clean,
                )
            response["sentiment"] = _sentiment_block
            logger.info(
                f"[ENRICHMENT-193] bert={_sentiment_block['bert']['label']}/"
                f"{_sentiment_block['bert']['score']:.2f} prosody={'yes' if _prosody_clean else 'no'}"
            )
        except Exception as _enrich_err:
            logger.warning(f"[ENRICHMENT-193] Sentiment-Enrichment fehlgeschlagen (fail-open): {_enrich_err}")

        return response

    except Exception as e:
        logger.exception("❌ Audio transcription failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "legacy_router"}
