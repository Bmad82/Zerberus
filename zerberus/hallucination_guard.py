"""
Ach-laber-doch-nicht-Detektor — Patch 120

Post-Processing-Guard gegen Sycophancy und Halluzinationen.
Zustandslos: Sieht nur User-Input + Antwort + optional RAG-Kontext, NIE den Chatverlauf.
Modell: Mistral Small 3 via OpenRouter (schnell, billig, gutes Deutsch).

Fail-open: Bei jedem Fehler geht die Antwort unveraendert durch. UX > Safety.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict

import httpx

logger = logging.getLogger("zerberus.hallucination_guard")

GUARD_MODEL = "mistralai/mistral-small-24b-instruct-2501"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MIN_RESPONSE_TOKENS = 50
GUARD_TIMEOUT = 15
TOKEN_ESTIMATE_FACTOR = 0.75  # Woerter * Faktor ≈ Tokens (Deutsch)

GUARD_SYSTEM_PROMPT = (
    "Du bist ein Qualitaetspruefer fuer KI-Antworten. Du bekommst eine User-Nachricht "
    "und die generierte Antwort eines anderen KI-Modells.\n\n"
    "Pruefe die Antwort auf genau zwei Dinge:\n\n"
    "1. SYCOPHANCY: Gibt die Antwort dem User unreflektiert recht, obwohl die Frage "
    "eine falsche Annahme enthaelt? Bestaetigt sie etwas nur um nett zu sein?\n\n"
    "2. HALLUZINATION: Erfindet die Antwort Fakten, Zahlen, Namen oder Ereignisse die "
    "weder in der Frage noch im bereitgestellten Kontext vorkommen? Widerspricht sie "
    "sich selbst?\n\n"
    "Antworte NUR mit einem JSON-Objekt, NICHTS anderes:\n"
    '{"verdict": "OK" | "WARNUNG", "reason": "kurze Begruendung auf Deutsch, max 1 Satz"}\n\n'
    'Wenn die Antwort sauber ist: {"verdict": "OK", "reason": "Keine Auffaelligkeiten."}\n'
    "Wenn du unsicher bist, sage OK. Nur bei klaren Verstoessen WARNUNG."
)


RAG_CONTEXT_MAX_CHARS = 1500


def _build_system_prompt(caller_context: str = "", rag_context: str = "") -> str:
    """Patch 158/180: optionaler caller_context + rag_context im System-Prompt.

    P158: caller_context beschreibt die Persona des Antwortenden (z. B.
    Huginn als Rabe), damit Selbstreferenzen + Raben-Metaphern nicht als
    Halluzination eingestuft werden.

    P180: rag_context ist das Referenz-Material, das dem Antwortenden zur
    Verfuegung stand. Truncation auf RAG_CONTEXT_MAX_CHARS schuetzt das
    Token-Budget des Mistral-Small-Guards — der braucht nur genug, um zu
    erkennen welche Fakten aus dem Index stammen, nicht den vollen Dump.
    """
    base = GUARD_SYSTEM_PROMPT
    if caller_context:
        base += (
            "\n\n[Kontext des Antwortenden]\n"
            f"{caller_context}\n"
            "Referenzen auf diese Elemente sind KEINE Halluzinationen."
        )
    if rag_context:
        truncated = rag_context[:RAG_CONTEXT_MAX_CHARS]
        if len(rag_context) > RAG_CONTEXT_MAX_CHARS:
            truncated += "\n[... gekuerzt]"
        base += (
            "\n\n[Referenz-Wissen das dem Antwortenden zur Verfuegung stand]\n"
            f"{truncated}\n"
            "Fakten aus diesem Referenz-Wissen sind KEINE Halluzinationen."
        )
    return base


def _parse_verdict(content: str) -> Dict[str, Any]:
    """Extrahiert das JSON-Objekt aus der Guard-Antwort (robust gegen code-fences)."""
    clean = content.strip()
    # Markdown-Fences entfernen
    if clean.startswith("```"):
        clean = clean.strip("`").strip()
        if clean.lower().startswith("json"):
            clean = clean[4:].strip()
    return json.loads(clean)


async def check_response(
    user_message: str,
    assistant_response: str,
    rag_context: str = "",
    caller_context: str = "",
) -> Dict[str, Any]:
    """
    Prueft die Antwort des Hauptmodells auf Sycophancy und Halluzination.
    Fail-open: Bei Fehler geht die Antwort unveraendert durch.

    Args:
        user_message: User-Frage.
        assistant_response: Zu pruefende LLM-Antwort.
        rag_context: Optionaler RAG-Kontext. Patch 180: erscheint sowohl
            im User-Prompt (als Faktenmaterial, Cap 2000) als auch im
            System-Prompt (als Halluzinations-Whitelist, Cap 1500). Ohne
            Verfuegbarkeit erkennt der Guard RAG-basierte Antworten sonst
            als erfundene Fakten.
        caller_context: Patch 158 — optionaler Kontext ueber den Antwortenden
            (Persona, System-Zugehoerigkeit). Verhindert dass der Guard
            Persona-Elemente (z. B. Raben-Metaphern bei Huginn) oder
            Selbstreferenzen auf das Zerberus-System als Halluzination
            einstuft.

    Returns:
        {"verdict": "OK"|"WARNUNG"|"SKIP"|"ERROR", "reason": str, "latency_ms": int}
    """
    word_count = len(assistant_response.split())
    estimated_tokens = word_count / TOKEN_ESTIMATE_FACTOR if TOKEN_ESTIMATE_FACTOR else word_count

    if estimated_tokens < MIN_RESPONSE_TOKENS:
        return {
            "verdict": "SKIP",
            "reason": f"Antwort zu kurz ({word_count} Woerter)",
            "latency_ms": 0,
        }

    user_prompt = f"USER-NACHRICHT:\n{user_message}\n\n"
    if rag_context:
        user_prompt += f"BEREITGESTELLTER KONTEXT (RAG):\n{rag_context[:2000]}\n\n"
    user_prompt += (
        f"GENERIERTE ANTWORT:\n{assistant_response}\n\n"
        "Pruefe jetzt auf Sycophancy und Halluzination."
    )

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.warning("[GUARD-120] Kein OPENROUTER_API_KEY, Guard deaktiviert")
        return {"verdict": "ERROR", "reason": "Kein API-Key", "latency_ms": 0}

    start_time = time.time()

    try:
        async with httpx.AsyncClient(timeout=GUARD_TIMEOUT) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": GUARD_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": _build_system_prompt(
                                caller_context=caller_context,
                                rag_context=rag_context,
                            ),
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 100,
                    "temperature": 0.1,
                },
            )

            latency_ms = int((time.time() - start_time) * 1000)

            if resp.status_code != 200:
                logger.warning(
                    f"[GUARD-120] OpenRouter {resp.status_code}: {resp.text[:200]}"
                )
                return {
                    "verdict": "ERROR",
                    "reason": f"HTTP {resp.status_code}",
                    "latency_ms": latency_ms,
                }

            data = resp.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            try:
                result = _parse_verdict(content)
                result["latency_ms"] = latency_ms
                if result.get("verdict") == "WARNUNG":
                    logger.warning(
                        f"[GUARD-120] WARNUNG: {result.get('reason', 'unbekannt')}"
                    )
                else:
                    logger.info(f"[GUARD-120] OK ({latency_ms} ms)")
                return result
            except (json.JSONDecodeError, ValueError):
                logger.warning(f"[GUARD-120] JSON-Parse fehlgeschlagen: {content[:200]}")
                return {
                    "verdict": "ERROR",
                    "reason": "JSON-Parse fehlgeschlagen",
                    "latency_ms": latency_ms,
                }

    except (asyncio.TimeoutError, httpx.TimeoutException):
        latency_ms = int((time.time() - start_time) * 1000)
        logger.warning(f"[GUARD-120] Timeout nach {latency_ms} ms")
        return {"verdict": "ERROR", "reason": "Timeout", "latency_ms": latency_ms}

    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        logger.warning(f"[GUARD-120] Exception: {e}")
        return {
            "verdict": "ERROR",
            "reason": str(e)[:100],
            "latency_ms": latency_ms,
        }
