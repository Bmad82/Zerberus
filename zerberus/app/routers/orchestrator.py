"""
Orchestrator Router – Rosa (Hauptsteuerung).
Patch 36: RAG-Integration – Gedächtnis-Kontext vor LLM-Prompt,
          automatisches Indexieren nach jeder Antwort.
Patch 40: Intent-Erkennung (regelbasiert) – QUESTION / COMMAND / CONVERSATION.
Patch 43: Session-Kontext vollständig integriert – History, System-Prompt,
          store_interaction und save_cost direkt im Orchestrator.
Patch 46: SSE EventBus Streaming – Events mit session_id für Live-Status im Frontend.
Patch 47: Intent-Subtypen (COMMAND_TOOL / COMMAND_SAFE / QUESTION / CONVERSATION),
          Permission Layer (admin/user/guest), Modell-Override per Profil.
"""
import asyncio
import json
import logging
import re
from pathlib import Path
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zerberus.core.config import get_settings, Settings
from zerberus.core.llm import LLMService
from zerberus.core.event_bus import get_event_bus, Event
from zerberus.core.database import get_session_messages, store_interaction, save_cost

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/orchestrator", tags=["Orchestrator"])

# Sentiment-Modul (Patch 57: German BERT statt VADER)
try:
    from zerberus.modules.sentiment.router import analyze_sentiment as _analyze_sentiment
    _SENTIMENT_OK = True
except Exception:
    _analyze_sentiment = None
    _SENTIMENT_OK = False

# Nudge-Modul (graceful – kein Crash wenn Modul nicht verfügbar)
try:
    from zerberus.modules.nudge.router import evaluate_nudge as nudge_evaluate, NudgeRequest as _NudgeRequest
    _NUDGE_OK = True
except Exception:
    nudge_evaluate = None
    _NudgeRequest = None
    _NUDGE_OK = False

# RAG-Funktionen direkt importieren – kein HTTP-Roundtrip
try:
    from zerberus.modules.rag.router import (
        RAG_AVAILABLE,
        _ensure_init as rag_ensure_init,
        _encode as rag_encode,
        _search_index as rag_search,
        _add_to_index as rag_add,
    )
    _RAG_IMPORT_OK = True
except Exception:
    _RAG_IMPORT_OK = False
    RAG_AVAILABLE = False

# L2-Distanz-Schwellwert: nur Treffer unterhalb dieser Grenze gelten als relevant
_RAG_L2_THRESHOLD = 1.5
# Patch 101 (R-07): top_k von 3 auf 8 erhöht, damit Aggregat-Queries
# (Aufzählungen, Zusammenfassungen über mehrere Abschnitte) genug Kontext
# bekommen. Bei ~12 Chunks im Index ist das 2/3 des Korpus — für
# Multi-Chunk-Aggregation genau richtig.
_RAG_TOP_K = 8

llm = LLMService()

# ---------------------------------------------------------------------------
# Intent-Erkennung (regelbasiert, keine KI) – Patch 47: 4 Subtypen
# ---------------------------------------------------------------------------

# COMMAND_TOOL: Tool-Use, Agenten, externe Ressourcen, Dateisystem, teure Operationen
_COMMAND_TOOL_PHRASES = ["führe aus", "erstelle datei", "schreib in"]
_COMMAND_TOOL_WORDS = {
    "starte", "öffne", "lösche", "agent", "docker", "tool", "script",
    "automatisier", "automatisiere", "deploy", "installier", "installiere", "download",
}

# COMMAND_SAFE: harmlose Aktionen ohne externe Ressourcen
_COMMAND_SAFE_PHRASES = ["zeig mir", "liste auf", "gib mir", "lies vor"]
_COMMAND_SAFE_WORDS = {
    "exportier", "exportiere", "spiel", "spiele", "wiederhol", "wiederhole",
    "zusammenfass", "zusammenfasse", "übersetze", "formatier", "formatiere",
}

# QUESTION: Informationsanfragen
_QUESTION_STARTERS = {
    "was", "wie", "wann", "wo", "warum", "wer", "welche", "welcher", "welches",
    "erkläre", "erklär", "definier", "definiere",
    "what", "how", "when", "where", "who", "why", "which",
}

# Intent-Snippets: werden direkt vor der User-Message in den Kontext eingefügt
INTENT_SNIPPETS = {
    "QUESTION":      "[Modus: Informationsanfrage – präzise antworten, strukturiert, kein Bullshit]",
    "COMMAND_SAFE":  "[Modus: Aktion – kurz ausführen und knapp bestätigen]",
    "COMMAND_TOOL":  "[Modus: Tool-Anfrage – Permission-Check läuft]",
    "CONVERSATION":  "[Modus: Gespräch – locker, empathisch, keine Listen wenn nicht nötig]",
}

# ---------------------------------------------------------------------------
# Permission Layer – Patch 47
# ---------------------------------------------------------------------------

# Welche Intent-Typen sind pro Permission-Level erlaubt?
# Nicht erlaubte Intents → Human-in-the-Loop statt LLM-Call
_PERMISSION_MATRIX: dict[str, set[str]] = {
    "admin":  {"QUESTION", "COMMAND_SAFE", "COMMAND_TOOL", "CONVERSATION"},
    "user":   {"QUESTION", "COMMAND_SAFE", "CONVERSATION"},
    "guest":  {"QUESTION", "CONVERSATION"},
}

_HITL_MESSAGE = (
    "Das würde ich gern für dich erledigen – "
    "aber dafür brauche ich Chris' OK. Soll ich ihn fragen?"
)

# Patch 104: HITL-Guard greift NUR bei externen Bot-Channels.
# Nala-Frontend, Dictate (/v1/), Orchestrator-API laufen mit channel=None
# und überspringen den Permission-Block. Telegram/WhatsApp-Router müssen
# beim Aufruf von _run_pipeline() channel="telegram"/"whatsapp" setzen.
_HITL_PROTECTED_CHANNELS: set[str] = {"telegram", "whatsapp"}


def detect_intent(message: str) -> str:
    """
    Regelbasierte Intent-Erkennung (Patch 47).
    Prüfreihenfolge: COMMAND_TOOL → COMMAND_SAFE → QUESTION → CONVERSATION
    Rückgabe: "COMMAND_TOOL" | "COMMAND_SAFE" | "QUESTION" | "CONVERSATION"
    """
    text = message.strip()
    if not text:
        return "CONVERSATION"

    text_lower = text.lower()
    first_word = text.split()[0].lower().rstrip(",.!:;")
    words = {w.lower().rstrip(",.!:;") for w in text.split()}

    # 1. COMMAND_TOOL (höchste Priorität)
    for phrase in _COMMAND_TOOL_PHRASES:
        if phrase in text_lower:
            return "COMMAND_TOOL"
    if first_word in _COMMAND_TOOL_WORDS or words & _COMMAND_TOOL_WORDS:
        return "COMMAND_TOOL"

    # 2. COMMAND_SAFE
    for phrase in _COMMAND_SAFE_PHRASES:
        if phrase in text_lower:
            return "COMMAND_SAFE"
    if first_word in _COMMAND_SAFE_WORDS or words & _COMMAND_SAFE_WORDS:
        return "COMMAND_SAFE"

    # 3. QUESTION
    if text.endswith("?"):
        return "QUESTION"
    if first_word in _QUESTION_STARTERS:
        return "QUESTION"

    # 4. CONVERSATION (Fallback)
    return "CONVERSATION"


# ---------------------------------------------------------------------------
# System-Prompt laden (lokal, kein Import aus legacy.py wegen Zirkular-Import)
# ---------------------------------------------------------------------------

def _load_system_prompt(profile_name: str | None = None) -> str:
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


# ---------------------------------------------------------------------------
# Pydantic-Modelle
# ---------------------------------------------------------------------------

class OrchestratorRequest(BaseModel):
    message: str
    session_id: str = ""
    context: dict = {}


class OrchestratorResponse(BaseModel):
    response: str
    modules_involved: list = []
    sentiment: float | None = None
    nudge: str | None = None


# ---------------------------------------------------------------------------
# Interne Hilfsfunktionen
# ---------------------------------------------------------------------------

async def _rag_search(query: str, settings: Settings) -> list[dict]:
    """
    Sucht im RAG-Index nach relevanten Einträgen.
    Gibt gefilterte Treffer (L2 < Threshold) zurück, oder [] bei Fehler/kein Index.

    Patch 97 (R-04): Wenn `query_expansion_enabled`, wird die Query vorab
    durch einen LLM-Call in 2-3 synonyme Varianten erweitert. Jede Variante
    läuft separat durch FAISS, die Kandidaten werden dedupliziert und
    zusammen durch den Reranker gejagt (mit der ORIGINAL-Query — so bleibt
    die Relevanz-Bewertung an der tatsächlichen User-Absicht verankert).
    """
    if not _RAG_IMPORT_OK or not RAG_AVAILABLE:
        return []

    rag_cfg = settings.modules.get("rag", {})
    if not rag_cfg.get("enabled", False):
        return []

    try:
        await rag_ensure_init(settings)
        min_words = int(rag_cfg.get("min_chunk_words", 0))
        rerank_enabled = bool(rag_cfg.get("rerank_enabled", False))
        rerank_model = str(rag_cfg.get("rerank_model", ""))
        rerank_multiplier = int(rag_cfg.get("rerank_multiplier", 4))
        expand_enabled = bool(rag_cfg.get("query_expansion_enabled", False))

        # Patch 97: Query Expansion (Fail-Safe: bei Fehler nur Original-Query)
        if expand_enabled:
            from zerberus.modules.rag.query_expander import expand_query
            queries = await expand_query(query, rag_cfg)
        else:
            queries = [query]

        # Per Sub-Query: raw FAISS-Hits (ohne inline-Rerank). Wir reranken
        # am Ende einmal über alle uniquen Kandidaten mit der Original-Query.
        per_query_k = _RAG_TOP_K * (rerank_multiplier if rerank_enabled else 1)
        all_candidates: list[dict] = []
        seen: set[str] = set()
        for q in queries:
            vec = await asyncio.to_thread(rag_encode, q)
            sub_hits = await asyncio.to_thread(
                rag_search,
                vec,
                per_query_k,
                min_words,
                q,
                False,          # rerank_enabled=False — wir reranken am Ende kombiniert
                "",
                rerank_multiplier,
            )
            for h in sub_hits:
                key = (h.get("text", "") or "")[:200]
                if key and key not in seen:
                    seen.add(key)
                    all_candidates.append(h)

        if expand_enabled:
            logger.warning(
                f"[EXPAND-97] Original: {query!r}, Expanded: {queries}, "
                f"per-query-k={per_query_k}, Post-dedup: {len(all_candidates)}"
            )

        # Finaler Rerank über den dedupe'ten Pool mit der ORIGINAL-Query
        if rerank_enabled and rerank_model and all_candidates:
            from zerberus.modules.rag.reranker import rerank as _rerank
            hits = await asyncio.to_thread(
                _rerank, query, all_candidates, rerank_model, _RAG_TOP_K
            )
            # Patch 105: Minimum-Reranker-Score — liegt der Top-Score unter
            # der Schwelle, ist KEIN Chunk wirklich relevant (typisch bei
            # Übersetzungs-/Umformulierungs-Aufgaben). RAG-Kontext komplett
            # verwerfen, statt 8 irrelevante Chunks ins Prompt zu pumpen.
            rerank_min_score = float(rag_cfg.get("rerank_min_score", 0.05))
            if hits and "rerank_score" in hits[0]:
                top_score = float(hits[0].get("rerank_score", 0.0))
                if top_score < rerank_min_score:
                    logger.warning(
                        "[THRESHOLD-105] RAG-Top-Score %.4f < Minimum %.4f — RAG-Kontext verworfen",
                        top_score, rerank_min_score,
                    )
                    return []
                logger.warning(
                    "[THRESHOLD-105] RAG-Top-Score %.4f >= Minimum %.4f — Kontext behalten (%d Chunks)",
                    top_score, rerank_min_score, len(hits),
                )
            filtered = hits
        else:
            filtered = [h for h in all_candidates if h.get("l2_distance", 999) < _RAG_L2_THRESHOLD][:_RAG_TOP_K]
            if all_candidates and not filtered:
                logger.warning(f"[DEBUG-85] RAG: {len(all_candidates)} Treffer gefunden, aber ALLE über L2-Threshold {_RAG_L2_THRESHOLD}! Nächster: {all_candidates[0].get('l2_distance', 'N/A'):.3f}")
        return filtered
    except Exception as e:
        logger.warning(f"RAG-Suche fehlgeschlagen (graceful fallback): {e}")
        return []


def _rag_index_sync(text: str, settings: Settings) -> None:
    """Synchrones Indexieren – läuft im Thread-Pool."""
    import asyncio as _asyncio
    import threading

    async def _run():
        try:
            await rag_ensure_init(settings)
            vec = await _asyncio.to_thread(rag_encode, text)
            total = await _asyncio.to_thread(rag_add, vec, text, {"source": "orchestrator"}, settings)
            logger.debug(f"RAG auto-indexed: {text[:60]}... (total={total})")
        except Exception as e:
            logger.warning(f"RAG auto-index fehlgeschlagen: {e}")

    # Neuer Event-Loop im Thread, damit das nicht mit dem Haupt-Loop kollidiert
    loop = _asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()


async def _rag_index_background(text: str, settings: Settings) -> None:
    """Startet das Indexieren im Hintergrund ohne den Response zu blockieren."""
    if not _RAG_IMPORT_OK or not RAG_AVAILABLE:
        return
    rag_cfg = settings.modules.get("rag", {})
    if not rag_cfg.get("enabled", False):
        return
    asyncio.get_event_loop().run_in_executor(None, _rag_index_sync, text, settings)


async def _run_pipeline(
    message: str,
    session_id: str,
    settings: Settings,
    profile_name: str | None = None,
    permission_level: str = "guest",
    allowed_model: str | None = None,
    temperature_override: float | None = None,
    channel: str | None = None,
) -> tuple[str, str, int, int, float, str, float | None, str | None]:
    """
    Vollständige Orchestrator-Pipeline mit Session-Kontext (Patch 43):
      Intent → RAG → Session-History + System-Prompt → LLM → Store → Auto-Index → Events

    Patch 46: Publiziert Events mit session_id für SSE-Streaming.
    Patch 47: Permission-Check vor LLM-Call, Intent-Snippets, Modell-Override.
    Patch 61: temperature_override – wenn gesetzt, überschreibt globale ai_temperature.

    Rückgabe: (answer, model, prompt_tokens, completion_tokens, cost, intent)
    Kann von anderen Routen direkt importiert werden (kein HTTP-Roundtrip).
    """
    message = message.strip()
    modules_used = []
    bus = get_event_bus()

    # ------------------------------------------------------------------
    # 0. Intent erkennen
    # ------------------------------------------------------------------
    intent = detect_intent(message)
    modules_used.append(f"intent:{intent}")
    logger.info(f"🎯 Intent erkannt: {intent} (Permission: {permission_level})")
    logger.warning(f"[DEBUG-80b] Intent: {intent} | Nachricht ({len(message.split())} Wörter): {message[:80]}")

    await bus.publish(Event(
        type="intent_detected",
        data={"intent": intent, "message": message[:50]},
        session_id=session_id,
    ))

    # ------------------------------------------------------------------
    # 0b. Permission-Check / HITL-Guard (Patch 47, scope-restricted Patch 104)
    # Greift nur bei externen Bot-Channels (Telegram/WhatsApp). Nala-Chat,
    # Dictate (/v1/) und die Orchestrator-API laufen mit channel=None und
    # überspringen den Block.
    # ------------------------------------------------------------------
    if channel in _HITL_PROTECTED_CHANNELS:
        allowed_intents = _PERMISSION_MATRIX.get(permission_level, _PERMISSION_MATRIX["guest"])
        if intent not in allowed_intents:
            logger.info(f"🔒 Permission-Block: '{permission_level}' darf '{intent}' nicht ausführen (channel={channel})")
            await bus.publish(Event(type="done", data={}, session_id=session_id))
            return _HITL_MESSAGE, "permission-block", 0, 0, 0.0, intent, None, None
    else:
        logger.warning("[HITL-104] Guard übersprungen – channel=%s, intent=%s, permission=%s", channel, intent, permission_level)

    # ------------------------------------------------------------------
    # 0c. Sandbox-Ausführung (Patch 52 – aktiv für COMMAND_TOOL + admin)
    # ------------------------------------------------------------------
    sandbox_context = ""
    if intent == "COMMAND_TOOL" and permission_level == "admin":
        try:
            from zerberus.main import _DOCKER_OK as _sb_docker_ok
        except ImportError:
            _sb_docker_ok = False

        if _sb_docker_ok:
            try:
                from zerberus.modules.sandbox.executor import execute_in_sandbox
                code_match = re.search(r"```(?:python)?\n?(.*?)```", message, re.DOTALL)
                code = code_match.group(1).strip() if code_match else message
                sandbox_result = await execute_in_sandbox(code)
                logger.info(f"[SANDBOX] exit_code={sandbox_result['exit_code']}, timed_out={sandbox_result['timed_out']}")
                if sandbox_result["timed_out"]:
                    sandbox_context = "[Sandbox-Output]: Timeout nach 10 Sekunden"
                elif sandbox_result["stdout"]:
                    sandbox_context = f"[Sandbox-Output]: {sandbox_result['stdout'].strip()}"
                elif sandbox_result["exit_code"] != 0:
                    sandbox_context = f"[Sandbox-Output Fehler]: {sandbox_result['stderr'].strip()}"
            except Exception as _sb_err:
                logger.warning(f"[SANDBOX] Ausführung fehlgeschlagen (graceful fallback): {_sb_err}")

        await bus.publish(Event(
            type="sandbox_pending",
            data={"session_id": session_id, "message": message[:100]},
            session_id=session_id,
        ))

    # ------------------------------------------------------------------
    # 1. RAG: Relevanten Kontext aus dem Gedächtnis holen
    #    Patch 78b: Skip bei CONVERSATION-Intent oder kurzen Eingaben ohne Fragezeichen
    # ------------------------------------------------------------------
    # Patch 85: RAG-Skip nur bei CONVERSATION + kurz + kein ?
    # Vorher: OR-Logik skippte auch QUESTION-Intent ohne "?" → RAG nie erreicht
    skip_rag = (
        intent == "CONVERSATION" and
        len(message.split()) < 15 and
        "?" not in message
    )

    logger.warning(f"[DEBUG-85] RAG-Skip: {skip_rag} | intent={intent}, wörter={len(message.split())}, '?'={'?' in message}")

    if skip_rag:
        logger.info(f"⏭️ RAG übersprungen (intent={intent}, words={len(message.split())}, '?'={'?' in message})")
        rag_hits = []
    else:
        await bus.publish(Event(
            type="rag_search",
            data={},
            session_id=session_id,
        ))
        rag_hits = await _rag_search(message, settings)
        logger.warning(f"[DEBUG-83] RAG results: {len(rag_hits) if rag_hits else 0} hits")
        if rag_hits:
            for i, r in enumerate(rag_hits[:3]):
                logger.warning(f"[DEBUG-83] RAG hit {i}: l2={r.get('l2_distance', 'N/A'):.3f} | text={r.get('text', '')[:80]}")

    # Intent-Snippet direkt vor der User-Message einfügen (Patch 47)
    snippet = INTENT_SNIPPETS.get(intent, "")

    sandbox_block = f"{sandbox_context}\n" if sandbox_context else ""

    if rag_hits:
        modules_used.append("rag")
        context_lines = "\n".join(f"[Gedächtnis]: {h['text']}" for h in rag_hits)
        # Patch 101 (R-07): Aggregation-Hint — bei Aufzählungs-/Listen-/
        # Zusammenfassungs-Fragen soll der LLM ALLE Kontext-Abschnitte nutzen,
        # nicht nur den ersten.
        agg_hint = (
            "\n\nWICHTIG: Wenn die Frage nach einer Aufzählung, Liste oder "
            "Zusammenfassung über MEHRERE Abschnitte fragt, nutze ALLE oben "
            "stehenden Kontext-Abschnitte. Zähle alle relevanten Treffer auf, "
            "nicht nur den ersten."
        )
        user_content = f"[Intent: {intent}]\n{context_lines}{agg_hint}\n\n{snippet}\n{sandbox_block}{message}" if snippet else f"[Intent: {intent}]\n{context_lines}{agg_hint}\n\n{sandbox_block}{message}"
        logger.warning(f"[AGG-101] Chunks in Prompt: {len(rag_hits)} | Aggregation-Hint: aktiv")
        logger.info(f"🧠 RAG lieferte {len(rag_hits)} relevante Treffer (L2 < {_RAG_L2_THRESHOLD})")
    else:
        user_content = f"[Intent: {intent}]\n{snippet}\n{sandbox_block}{message}" if snippet else f"[Intent: {intent}]\n{sandbox_block}{message}"

    # ------------------------------------------------------------------
    # 2. Session-History + System-Prompt laden
    # ------------------------------------------------------------------
    sys_prompt = _load_system_prompt(profile_name)
    # Patch 78b: Fallback-Permission für Allgemeinwissen + Smalltalk
    if sys_prompt:
        _lower = sys_prompt.lower()
        if not any(kw in _lower for kw in ("allgemein", "wissen", "smalltalk")):
            sys_prompt += (
                "\nWenn keine spezifischen Dokumentinformationen verfügbar sind, "
                "beantworte allgemeine Fragen aus deinem Allgemeinwissen und führe normale Gespräche."
            )
    messages = []
    if sys_prompt:
        messages.append({"role": "system", "content": sys_prompt})

    if session_id:
        history = await get_session_messages(session_id)
        history_lines = []
        for h in history:
            if h["role"] in ("user", "assistant"):
                history_lines.append(f"{h['role'].upper()}: {h['content']}")
        if history_lines:
            history_block = (
                "[VERGANGENE SESSION — nur Tonreferenz, nicht als aktuelles Gespräch behandeln]\n"
                + "\n".join(history_lines)
                + "\n[ENDE VERGANGENE SESSION]"
            )
            # Inject into system prompt so LLM treats it as context, not active conversation
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += "\n\n" + history_block
            else:
                messages.insert(0, {"role": "system", "content": history_block})

    messages.append({"role": "user", "content": user_content})

    # [DEBUG-80b] System-Prompt + RAG-Status loggen
    _sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
    logger.warning(f"[DEBUG-80b] System-Prompt (letzte 200 Zeichen): ...{_sys_content[-200:]}")
    logger.warning(f"[DEBUG-80b] RAG-Kontext vorhanden: {bool(rag_hits)} | Anzahl RAG-Hits: {len(rag_hits)}")

    # ------------------------------------------------------------------
    # 3. LLM-Aufruf (Patch 47: Modell-Override wenn Profil allowed_model gesetzt hat)
    # ------------------------------------------------------------------
    await bus.publish(Event(
        type="llm_start",
        data={},
        session_id=session_id,
    ))

    answer, model, p_tok, c_tok, cost = await llm.call(
        messages,
        session_id=session_id or None,
        model_override=allowed_model or None,
        temperature_override=temperature_override,
    )
    logger.info(f"LLM [{model}] {p_tok}+{c_tok} Tokens, ${cost:.6f}")
    modules_used.append("llm")

    # ------------------------------------------------------------------
    # 3b. BERT-Sentiment auf die User-Nachricht (Patch 57)
    # ------------------------------------------------------------------
    sentiment_score: float | None = None
    if _SENTIMENT_OK and _analyze_sentiment is not None:
        try:
            sent = _analyze_sentiment(message)
            # Konvertiere label → compound-ähnlichen Wert für EventBus-Kompatibilität
            _label_to_compound = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
            sentiment_score = _label_to_compound.get(sent["label"], 0.0)
            await bus.publish(Event(
                type="user_sentiment",
                data={"compound": sentiment_score, "label": sent["label"], "score": sent["score"]},
                session_id=session_id,
            ))
        except Exception as _sent_err:
            logger.warning(f"BERT-Sentiment fehlgeschlagen (graceful fallback): {_sent_err}")

    # ------------------------------------------------------------------
    # 3c. Nudge-Evaluierung (Patch 50)
    # ------------------------------------------------------------------
    nudge_text: str | None = None
    if _NUDGE_OK and nudge_evaluate is not None:
        try:
            nudge_cfg = settings.modules.get("nudge", {})
            if nudge_cfg.get("enabled", False):
                score = abs(sentiment_score) if sentiment_score is not None else 0.0
                nudge_req = _NudgeRequest(
                    event_type="conversation",
                    score=score,
                    context={"message": message[:100]},
                )
                nudge_result = await nudge_evaluate(nudge_req, settings)
                if nudge_result.should_nudge:
                    nudge_text = nudge_result.reason
                    await bus.publish(Event(
                        type="nudge_sent",
                        data={"session_id": session_id, "nudge_text": nudge_text},
                        session_id=session_id,
                    ))
        except Exception as _nudge_err:
            logger.warning(f"Nudge-Evaluierung fehlgeschlagen (graceful fallback): {_nudge_err}")

    # ------------------------------------------------------------------
    # 4. Interaktion + Kosten speichern
    # ------------------------------------------------------------------
    if session_id:
        try:
            await store_interaction("user", message, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
            await store_interaction("assistant", answer, session_id=session_id, profile_name=profile_name or "", profile_key=profile_name or None)
            await save_cost(session_id, model, p_tok, c_tok, cost)
        except Exception as e:
            logger.warning(f"⚠️ store_interaction fehlgeschlagen (non-fatal): {e}")

    # ------------------------------------------------------------------
    # 5. RAG auto-index deaktiviert (Patch 68: verhindert unerwünschte
    #    "orchestrator"-Chunks im RAG-Index nach manuellem Upload/Clear)
    # ------------------------------------------------------------------

    await bus.publish(Event(
        type="rag_indexed",
        data={},
        session_id=session_id,
    ))

    # ------------------------------------------------------------------
    # 6. Orchestrator-Event publishen + Done-Signal für SSE
    # ------------------------------------------------------------------
    await bus.publish(Event(
        type="orchestrator_process",
        data={"message": message[:100], "modules": modules_used, "rag_hits": len(rag_hits)}
    ))

    await bus.publish(Event(
        type="done",
        data={},
        session_id=session_id,
    ))

    return answer, model, p_tok, c_tok, cost, intent, sentiment_score, nudge_text


# ---------------------------------------------------------------------------
# Endpunkte
# ---------------------------------------------------------------------------

@router.post("/process", response_model=OrchestratorResponse)
async def process_message(
    req: OrchestratorRequest,
    settings: Settings = Depends(get_settings)
):
    logger.info(f"Orchestrator processing: {req.message[:60]}...")

    # session_id aus Request oder context dict
    session_id = req.session_id or req.context.get("session_id", "")

    answer, model, p_tok, c_tok, cost, intent, sentiment, nudge = await _run_pipeline(
        req.message, session_id, settings
    )

    return OrchestratorResponse(
        response=answer,
        modules_involved=[f"intent:{intent}", "rag", "llm"],
        sentiment=sentiment,
        nudge=nudge,
    )


@router.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "orchestrator",
        "rag_import": _RAG_IMPORT_OK,
        "rag_available": RAG_AVAILABLE,
    }
