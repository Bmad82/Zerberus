"""Patch 164 — Parser für den JSON-Header in LLM-Antworten.

Erwartetes Format::

    {"intent": "CHAT", "effort": 2, "needs_hitl": false}
    <eigentliche Antwort>

Der Header darf optional in einen ``json``-Code-Fence gewickelt sein, damit
Modelle, die per Default Markdown ausgeben, nicht stolpern::

    ```json
    {"intent": "CODE", "effort": 3, "needs_hitl": true}
    ```
    Hier ist der Code...

Robustheit-Garantien:

- Kein JSON-Header gefunden → Default ``(CHAT, effort=3, needs_hitl=False)``,
  ``body`` = der gesamte Original-Text.
- Kaputtes JSON → Default + Warning-Log; Body = Original-Text.
- Unbekannter Intent (``"BANANA"``) → CHAT.
- ``effort`` außerhalb 1–5 → in den Bereich geclampt.
- ``effort`` nicht-numerisch → Default 3.
- Empty / None Input → Default mit leerem Body.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from zerberus.core.intent import HuginnIntent

logger = logging.getLogger("zerberus.intent_parser")


# Optional umschließender Fence (```json ... ```), gefolgt von einem Block,
# der mit ``{`` beginnt. Den eigentlichen JSON-Block ziehen wir mit einem
# Brace-Counter, weil die LLM-Header oft Werte mit Sonderzeichen enthalten
# können — eine simple ``[^}]+``-Klasse wäre zu eng.
_FENCE_OPEN = re.compile(r"^\s*```\s*json\s*\n?", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```\s*\n?", re.IGNORECASE)


@dataclass
class ParsedResponse:
    """Geparste LLM-Antwort mit Intent-Metadaten."""

    intent: HuginnIntent
    effort: int                     # 1–5, Aufwandsschätzung des LLM
    needs_hitl: bool                # Ob HitL-Bestätigung nötig ist
    body: str                       # Die eigentliche Antwort (ohne JSON-Header)
    raw_header: Optional[dict]      # Der geparste JSON-Header (für Logging)


_DEFAULT = ParsedResponse(
    intent=HuginnIntent.CHAT,
    effort=3,
    needs_hitl=False,
    body="",
    raw_header=None,
)


def _extract_json_block(text: str) -> tuple[Optional[str], str]:
    """Holt den ersten ``{...}``-Block am Anfang von ``text``.

    Berücksichtigt Verschachtelung via einfachem Brace-Counter (kein
    String-State, weil unsere Header keine String-Werte mit ``{``/``}``
    enthalten — wenn doch, ist das ein Bug im LLM-Output, kein
    Robustheits-Pfad). Liefert ``(json_str, rest)`` oder ``(None, text)``
    wenn kein vollständiger Block am Anfang gefunden wurde.
    """
    if not text or not text.lstrip().startswith("{"):
        return None, text

    leading = len(text) - len(text.lstrip())
    body_start = leading
    depth = 0
    end = -1
    for i in range(leading, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None, text
    return text[body_start:end + 1], text[end + 1:]


def parse_llm_response(raw_response: str) -> ParsedResponse:
    """Parst LLM-Antwort in Intent-Header + Body.

    Siehe Modul-Docstring für Robustheit-Garantien.
    """
    if not raw_response:
        return _DEFAULT

    text = raw_response

    # Optional ```json ... ``` Fence abziehen
    fence_match = _FENCE_OPEN.match(text)
    fenced = bool(fence_match)
    if fenced:
        text = text[fence_match.end():]

    json_str, rest = _extract_json_block(text)
    if json_str is None:
        return ParsedResponse(
            intent=HuginnIntent.CHAT,
            effort=3,
            needs_hitl=False,
            body=raw_response.strip(),
            raw_header=None,
        )

    if fenced:
        # Schließenden Fence aus dem Body entfernen (typisch direkt nach
        # dem JSON, ggf. mit Newline davor).
        close_match = _FENCE_CLOSE.match(rest)
        if close_match:
            rest = rest[close_match.end():]

    try:
        header = json.loads(json_str)
        if not isinstance(header, dict):
            raise ValueError("JSON-Header ist kein Objekt")
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("[INTENT-164] JSON-Header Parse-Fehler: %s", e)
        return ParsedResponse(
            intent=HuginnIntent.CHAT,
            effort=3,
            needs_hitl=False,
            body=raw_response.strip(),
            raw_header=None,
        )

    intent = HuginnIntent.from_str(header.get("intent"))

    # effort: nicht-numerisch → 3, sonst auf [1, 5] clampen.
    effort_raw = header.get("effort", 3)
    try:
        effort = max(1, min(5, int(effort_raw)))
    except (TypeError, ValueError):
        effort = 3

    needs_hitl = bool(header.get("needs_hitl", False))

    body = rest.strip()

    logger.debug(
        "[INTENT-164] Parsed: intent=%s effort=%d hitl=%s body_len=%d",
        intent.value, effort, needs_hitl, len(body),
    )

    return ParsedResponse(
        intent=intent,
        effort=effort,
        needs_hitl=needs_hitl,
        body=body,
        raw_header=header,
    )
