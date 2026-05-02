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

    try:
        await store_interaction("user", last_user_msg, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
        await store_interaction("assistant", answer, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
        await update_interaction()
    except Exception as e:
        logger.warning(f"⚠️ store_interaction fehlgeschlagen (non-fatal): {e}")

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
    return ChatCompletionResponse(
        created=int(datetime.now().timestamp()),
        model=model,
        choices=[Choice(index=0, message=Message(role="assistant", content=answer), finish_reason="stop")],
        sentiment=sentiment_payload,
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
