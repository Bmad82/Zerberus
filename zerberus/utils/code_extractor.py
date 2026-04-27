"""Patch 171 — Code-Extraktion aus LLM-Markdown-Output (Phase D, Block 2).

Sucht in der LLM-Antwort nach Fenced-Code-Bloecken und liefert sie als
strukturierte Liste zurueck. Wird vom Sandbox-Pfad genutzt, um aus einer
gemischten CHAT-/CODE-Antwort den ausfuehrbaren Code zu isolieren.

Sprach-Aliase:
- ``python``, ``py``  → ``python``
- ``javascript``, ``js``, ``node`` → ``javascript``

Jede unbekannte Sprache landet als ``unknown`` — der Caller entscheidet
dann, ob er das ausfuehrt (i. d. R. nicht).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


_LANGUAGE_ALIASES = {
    "python": "python",
    "py": "python",
    "python3": "python",
    "javascript": "javascript",
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
}


_FENCED_BLOCK_RE = re.compile(
    r"```(?P<lang>[A-Za-z0-9_+\-]*)\s*\n(?P<code>.*?)```",
    re.DOTALL,
)


@dataclass
class CodeBlock:
    language: str
    code: str
    start_pos: int
    end_pos: int


def _normalize_language(raw: str) -> str:
    if not raw:
        return "unknown"
    return _LANGUAGE_ALIASES.get(raw.strip().lower(), raw.strip().lower())


def extract_code_blocks(text: str, fallback_language: Optional[str] = None) -> List[CodeBlock]:
    """Extrahiert Fenced-Code-Bloecke aus Markdown-/LLM-Output.

    Args:
        text: LLM-Antwort (kann beliebigen Inhalt + 0..N Code-Bloecke haben).
        fallback_language: Sprache, wenn KEIN Block gefunden wurde — dann
            wird der gesamte Text als Code-Block behandelt. ``None`` (Default)
            unterdrueckt den Fallback und liefert eine leere Liste.

    Returns:
        Liste von ``CodeBlock`` — sortiert nach Vorkommen im Text.
    """
    if not text:
        return []

    blocks: List[CodeBlock] = []
    for match in _FENCED_BLOCK_RE.finditer(text):
        language = _normalize_language(match.group("lang") or "")
        code = match.group("code")
        # ``re.DOTALL`` faengt das letzte newline mit ein — bewusst KEIN strip(),
        # weil signifikantes Whitespace (z.B. Python-Indent) erhalten bleiben muss.
        # Nur das eine trailing newline vor dem schliessenden ``` weghacken.
        if code.endswith("\n"):
            code = code[:-1]
        blocks.append(CodeBlock(
            language=language,
            code=code,
            start_pos=match.start(),
            end_pos=match.end(),
        ))

    if not blocks and fallback_language:
        return [CodeBlock(
            language=_normalize_language(fallback_language),
            code=text,
            start_pos=0,
            end_pos=len(text),
        )]

    return blocks


def first_executable_block(
    text: str,
    allowed_languages: List[str],
    fallback_language: Optional[str] = None,
) -> Optional[CodeBlock]:
    """Liefert den ERSTEN Block, dessen Sprache in ``allowed_languages`` ist.

    Praktisch fuer den Sandbox-Pfad: ``allowed_languages = ["python", "javascript"]``
    aus der Config — der erste Treffer wird ausgefuehrt, der Rest geht
    nur als Datei raus.
    """
    allowed_lower = {lang.strip().lower() for lang in allowed_languages}
    for block in extract_code_blocks(text, fallback_language=fallback_language):
        if block.language in allowed_lower:
            return block
    return None
