"""
Legacy Router – Keyboard & Hardware (OpenAI-kompatibler Chat + Audio).
Patch 38: Chat-Requests laufen durch die Orchestrator-Pipeline (RAG + LLM + Auto-Index).
Patch 47: Permission-Check vor LLM-Call.
Patch 54: permission_level und profile_name kommen aus request.state (JWT-Middleware).
"""
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

    # Patch 47: Permission-Check vor LLM-Call (nach Dialect-Kurzschluss)
    if _ORCH_PIPELINE_OK:
        intent = detect_intent(last_user_msg)
        allowed_intents = _PERMISSION_MATRIX.get(permission_level, _PERMISSION_MATRIX["guest"])
        if intent not in allowed_intents:
            logger.info(f"🔒 Permission-Block (legacy): '{permission_level}' darf '{intent}' nicht ausführen")
            try:
                await store_interaction("user", last_user_msg, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
                await store_interaction("assistant", _HITL_MESSAGE, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
                await update_interaction()
            except Exception:
                pass
            return ChatCompletionResponse(
                created=int(datetime.now().timestamp()),
                model="permission-block",
                choices=[Choice(index=0, message=Message(role="assistant", content=_HITL_MESSAGE), finish_reason="stop")]
            )

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
            skip_rag = (
                intent == "CONVERSATION" and
                len(last_user_msg.split()) < 15 and
                "?" not in last_user_msg
            )

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
                context_lines = "\n".join(f"[Gedächtnis]: {h['text']}" for h in rag_hits)
                # Patch 101 (R-07): Aggregation-Hint für Listen-/Aufzählungs-Fragen
                agg_hint = (
                    "\n\nWICHTIG: Wenn die Frage nach einer Aufzählung, Liste oder "
                    "Zusammenfassung über MEHRERE Abschnitte fragt, nutze ALLE oben "
                    "stehenden Kontext-Abschnitte. Zähle alle relevanten Treffer auf, "
                    "nicht nur den ersten."
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
            bus = get_event_bus()
            await bus.publish(Event(type="llm_start", data={}, session_id=session_id))
            answer, model, _, _, cost = await llm_service.call(messages_for_llm, session_id, temperature_override=temperature_override)
    else:
        # Fallback: direkter LLM-Call ohne RAG
        bus = get_event_bus()
        await bus.publish(Event(type="llm_start", data={}, session_id=session_id))
        answer, model, _, _, cost = await llm_service.call(messages_for_llm, session_id, temperature_override=temperature_override)

    try:
        await store_interaction("user", last_user_msg, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
        await store_interaction("assistant", answer, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
        await update_interaction()
    except Exception as e:
        logger.warning(f"⚠️ store_interaction fehlgeschlagen (non-fatal): {e}")

    # Response-Format bleibt exakt OpenAI-kompatibel (Nala-Weiche)
    return ChatCompletionResponse(
        created=int(datetime.now().timestamp()),
        model=model,
        choices=[Choice(index=0, message=Message(role="assistant", content=answer), finish_reason="stop")]
    )


# ---------- Audio-Endpunkt ----------

@router.post("/audio/transcriptions")
async def audio_transcriptions(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings)
):
    whisper_url = settings.legacy.urls.whisper_url

    try:
        audio_data = await file.read()
        files = {"file": (file.filename, audio_data, file.content_type)}
        data = {"model": "whisper-1"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(whisper_url, files=files, data=data)
            response.raise_for_status()
            whisper_result = response.json()

        raw_transcript = whisper_result.get("text", "")
        cleaned_transcript = clean_transcript(raw_transcript)

        logger.info(f"🎤 Transkript: '{raw_transcript}' -> '{cleaned_transcript}'")

        # Patch 83: Stille/leeres Transkript nach Cleaner abfangen
        if not cleaned_transcript.strip():
            logger.info("[DEBUG-83] Stille erkannt — leeres Transkript nach Cleaner")
            return {"text": "", "note": "silence_detected"}

        await store_interaction("whisper_input", raw_transcript, integrity=0.9)
        await update_interaction()

        return {"text": cleaned_transcript}

    except Exception as e:
        logger.exception("❌ Audio transcription failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "legacy_router"}
