"""Patch 203d-2 (Phase 5a #5) — Output-Synthese fuer Sandbox-Code-Execution
im Chat-Endpunkt.

Zweiter LLM-Call der den ``code_execution``-Payload aus P203d-1 in eine
menschenlesbare Antwort verwandelt: Original-Prompt + Code + stdout/stderr
werden in einen Synthese-Prompt gegossen, das LLM erklaert das Ergebnis
ohne den Code stumpf zu wiederholen.

Aufteilung:

- Pure-Function-Schicht (testbar ohne LLM, ohne Container):
  * ``should_synthesize(payload)`` — Trigger-Gate (kein Output und kein
    Crash → skip; Crash oder stdout-Output → synthese).
  * ``_truncate(text, limit)`` — Bytes-genau truncaten + Marker.
  * ``build_synthesis_messages(user_prompt, payload)`` — System+User-Msgs.

- Async-Wrapper:
  * ``synthesize_code_output(user_prompt, payload, llm_service, session_id)``
    ruft das LLM via ``LLMService.call(...)`` und gibt den Synthese-Text
    zurueck. Fail-open: jeder Fehler → ``None``, Caller behaelt den
    urspruenglichen ``answer``.

Was P203d-2 bewusst NICHT tut:

- Kein UI-Render (das ist P203d-3, Nala-Frontend-Patch).
- Kein zweiter ``store_interaction``-Eintrag — die ``interactions``-Tabelle
  bekommt nur den finalen ``answer``, der den Original-Output ueberschreibt.
- Keine Token-Cost-Aggregation in ``interactions.cost`` fuer den Synthese-
  Call (eigene Schuld; HANDOVER-Vermerk).
- Keine Streaming-SSE-Events.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Logging-Tag fuer Operations-Logs. Disjunkt von [SANDBOX-203d] (P203d-1),
# damit Filter den Synthese-Pfad isoliert beobachten koennen.
SYNTH_LOG_TAG = "[SYNTH-203d-2]"

# Truncate-Limit fuer stdout/stderr im Synthese-Prompt — 5 KB pro Stream.
# Schuetzt das Kontext-Fenster wenn Code Mega-Output produziert; reicht
# fuer typische Print-Outputs, Stack-Traces, JSON-Dumps.
SYNTH_MAX_OUTPUT_BYTES = 5_000

# Truncate-Marker (ASCII, faengt sicher an einer Byte-Grenze an, damit
# UTF-8-Decoder beim ``errors='ignore'``-Truncate nicht ueber den Marker
# stolpern.
TRUNCATED_MARKER = "\n…[gekuerzt]"


def should_synthesize(payload: Any) -> bool:
    """Trigger-Gate fuer den zweiten LLM-Call.

    Synthese laeuft wenn:
      - payload ist ein Dict mit ``exit_code`` (P203d-1-Pfad gelaufen) UND
      - ``exit_code != 0`` (Crash → Erklaerung von stderr noetig) ODER
      - ``exit_code == 0`` UND ``stdout`` nicht leer (Output → Aufbereitung).

    Skip wenn:
      - payload is None oder kein Dict
      - ``exit_code`` fehlt oder ist None
      - ``exit_code == 0`` UND ``stdout`` leer (nichts zu sagen).
    """
    if not isinstance(payload, dict):
        return False
    exit_code = payload.get("exit_code")
    if exit_code is None:
        return False
    if exit_code != 0:
        return True
    return bool((payload.get("stdout") or "").strip())


def _truncate(text: str, limit: int = SYNTH_MAX_OUTPUT_BYTES) -> str:
    """Bytes-genau truncaten + Marker.

    Encoded-Vergleich, nicht ``len(text)``: ein 2000-Zeichen-Output mit
    Multi-Byte-Symbolen koennte ueber dem Limit liegen, ein 5000-Zeichen-
    ASCII-Output exakt darunter. Decoder nimmt ``errors='ignore'`` damit
    ein abgeschnittenes Multi-Byte-Symbol nicht crasht.
    """
    if not text:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore") + TRUNCATED_MARKER


def build_synthesis_messages(
    user_prompt: str,
    payload: dict,
) -> list[dict]:
    """Baut die Messages fuer den Synthese-LLM-Call.

    Pure Function — keine I/O, keine Settings-Reads, deterministisch.

    System-Prompt: faktisch, "wiederhole den Code nicht stumpf, erklaere
    Fehler, beziehe dich auf die urspruengliche Frage".
    User-Message: Original-Frage + Code-Block + stdout/stderr —
    Markdown-fenced damit das LLM die Struktur erkennt.

    Beide Streams werden bei Bedarf via ``_truncate`` gekuerzt, der
    Marker ``[CODE-EXECUTION]`` ist substring-disjunkt zu den anderen
    LLM-Brueckenmarkern (``[PROJEKT-RAG]``, ``[PROJEKT-KONTEXT]``,
    ``[PROSODIE]``).
    """
    language = payload.get("language") or "?"
    code = payload.get("code") or ""
    exit_code = payload.get("exit_code")
    stdout = _truncate(payload.get("stdout") or "")
    stderr = _truncate(payload.get("stderr") or "")

    system_prompt = (
        "Du hast soeben Code in einer Sandbox ausgefuehrt. Fasse das "
        "Ergebnis menschenlesbar in Deutsch zusammen. "
        "Erklaere den Output knapp und beziehe dich auf die urspruengliche "
        "Frage. Wiederhole den Code NICHT stumpf, wenn er trivial war. "
        "Bei Fehlern (exit_code != 0): erklaere die Ursache aus stderr, "
        "schlage einen Fix vor. Antworte ohne Floskeln."
    )

    user_parts: list[str] = [
        f"Urspruengliche Frage des Users: {user_prompt}",
        "",
        f"[CODE-EXECUTION — Sprache: {language} | exit_code: {exit_code}]",
        f"```{language}",
        code,
        "```",
    ]
    if stdout:
        user_parts.extend(["", "stdout:", "```", stdout, "```"])
    if stderr:
        user_parts.extend(["", "stderr:", "```", stderr, "```"])
    user_parts.append("[/CODE-EXECUTION]")

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


async def synthesize_code_output(
    user_prompt: str,
    payload: dict,
    llm_service: Any,
    session_id: str | None,
) -> str | None:
    """Async-Wrapper: ruft LLM mit Prompt+Code+Output, liefert Synthese-Text.

    Args:
        user_prompt: Die letzte User-Nachricht aus dem Chat.
        payload: Das ``code_execution``-Dict aus P203d-1.
        llm_service: Eine ``LLMService``-Instanz mit ``async call(messages,
            session_id, ...)`` (returns 5-Tuple ``(answer, model, p_tok,
            c_tok, cost)``).
        session_id: Session-Schluessel fuer Logging/Cost-Tracking.

    Returns:
        Synthesized text (str), oder ``None`` wenn:
          - Trigger-Gate skipt (``should_synthesize`` → False)
          - LLM-Call crasht (fail-open → Caller behaelt Original-Answer)
          - LLM-Antwort ist leer/whitespace.
    """
    if not should_synthesize(payload):
        return None
    try:
        messages = build_synthesis_messages(user_prompt, payload)
        result = await llm_service.call(messages, session_id)
        if not isinstance(result, tuple) or not result:
            logger.warning(
                f"{SYNTH_LOG_TAG} unerwartetes LLM-Result-Format — fail-open"
            )
            return None
        synthesized = result[0]
        if not isinstance(synthesized, str) or not synthesized.strip():
            logger.warning(f"{SYNTH_LOG_TAG} LLM-Synthese leer — fail-open")
            return None
        out_len = len(payload.get("stdout") or "") + len(payload.get("stderr") or "")
        logger.info(
            f"{SYNTH_LOG_TAG} synthesized exit_code={payload.get('exit_code')} "
            f"raw_output_len={out_len} synth_len={len(synthesized)}"
        )
        return synthesized
    except Exception as err:
        logger.warning(f"{SYNTH_LOG_TAG} Synthese-LLM crashed (fail-open): {err}")
        return None
