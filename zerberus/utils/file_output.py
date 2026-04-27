"""Patch 168 — Datei-Output-Logik fuer Huginn (Phase C).

Adressiert die Findings K5, P5, P6, D7 aus der Roadmap v2:

- **K5** Content-Review vor Datei-Versand: Der Datei-Inhalt muss durch den
  Guard, bevor er den User erreicht. Diese Modul stellt nur das Routing +
  die Dateiformat-Erkennung; der Guard-Call selbst sitzt im Router.
- **P5** Telegram-Limit: Antworten >2000 Zeichen sind auf dem Handy schlecht
  lesbar. Wir kuerzen die sichtbare Vorschau und schicken die volle
  Antwort als Datei.
- **P6** Fehlende Datei-Pipeline: FILE/CODE-Intents wurden in P164 erkannt
  aber Huginn konnte nichts ausser Text senden.
- **D7** MIME-Whitelist: Huginn darf nur Textformate erzeugen — kein .exe,
  kein .ps1, kein .bat. Schutz vor LLM, das eine ``.sh``-Endung in den
  Vorschlag schmuggelt.

Bewusst nicht hier:
- PDF-Generierung (Rosa-Phase, P6 dort eigenes Patch).
- DLP/Firmen-Header (Rosa-Phase).
- Bild-Kompression (D7-Mini-Patch).
- User→Huginn Datei-Upload (Phase D, Sandbox).
"""
from __future__ import annotations

import logging
import re
from typing import Tuple

logger = logging.getLogger("zerberus.huginn.file_output")


# ──────────────────────────────────────────────────────────────────────
#  Konstanten
# ──────────────────────────────────────────────────────────────────────


# Schwelle, ab der eine CHAT-Antwort als Datei statt als Text rausgeht.
# Telegram erlaubt 4096 Zeichen pro Nachricht; ab ~2000 wird die Lesbarkeit
# auf dem Handy schlecht — also schicken wir die volle Antwort als Datei
# und den User-Hinweis als Vorschau-Text.
CHAT_FILE_THRESHOLD = 2000

# Telegram Bot API erlaubt bis 50 MB pro sendDocument. 10 MB sind ein
# realistisches Maximum fuer Text-Output und schuetzen vor LLM-Halluzinationen
# („schreib mir den kompletten Linux-Kernel als Datei").
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Telegram-Caption-Limit fuer sendDocument.
MAX_CAPTION_LENGTH = 1024

# Erlaubte Dateiendungen. LLM kann keine Binaerdateien produzieren, aber die
# Endung muss trotzdem sauber sein — sonst kuendigt Huginn unwissentlich
# eine ``.exe`` an.
MIME_WHITELIST = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".sql": "text/x-sql",
    ".json": "application/json",
    ".yaml": "application/x-yaml",
    ".yml": "application/x-yaml",
    ".csv": "text/csv",
}

# Explizite Block-Liste fuer paranoide Mehrfach-Pruefung — wenn ein Bug
# in der Logik die Whitelist umgeht, faengt diese Liste nochmal die
# offensichtlich gefaehrlichen Endungen ab.
EXTENSION_BLOCKLIST = {
    ".exe", ".sh", ".bat", ".cmd", ".ps1", ".dll", ".so",
    ".dylib", ".scr", ".com", ".vbs", ".jar", ".msi",
}


# ──────────────────────────────────────────────────────────────────────
#  Sprach-/Format-Heuristiken
# ──────────────────────────────────────────────────────────────────────


# Bewusst einfache Heuristiken — kein AST-Parsing. Wir muessen die Endung
# raten, nicht den Code validieren. False-Negative landen als ``.txt``
# (Defensive Default) — nie schlimmer als die alte Text-Antwort.

_PYTHON_HINTS = re.compile(
    r"^(\s*)(def\s+\w+\s*\(|import\s+\w+|from\s+\w+\s+import|class\s+\w+\s*[:\(])",
    re.MULTILINE,
)
_JAVASCRIPT_HINTS = re.compile(
    r"(^|\W)(function\s+\w+\s*\(|const\s+\w+\s*=|let\s+\w+\s*=|=>\s*[\{\(]|"
    r"console\.log\s*\(|export\s+(default|const|function))",
    re.MULTILINE,
)
_SQL_HINTS = re.compile(
    r"(?im)^\s*(SELECT\s+|CREATE\s+TABLE|INSERT\s+INTO|UPDATE\s+\w+\s+SET|"
    r"DELETE\s+FROM|ALTER\s+TABLE|DROP\s+TABLE)\b",
)
# Markdown: Header (# ...), Listen-Marker (- foo), Fenced-Code-Blocks,
# Bold/Italic. Wenn nichts davon → reiner Text.
_MARKDOWN_HINTS = re.compile(
    r"(^#{1,6}\s+\S|^[-*+]\s+\S|^\d+\.\s+\S|```|^\>\s+\S|\*\*\S|__\S)",
    re.MULTILINE,
)


def _detect_python(content: str) -> bool:
    return bool(_PYTHON_HINTS.search(content))


def _detect_javascript(content: str) -> bool:
    return bool(_JAVASCRIPT_HINTS.search(content))


def _detect_sql(content: str) -> bool:
    return bool(_SQL_HINTS.search(content))


def _has_markdown_syntax(content: str) -> bool:
    return bool(_MARKDOWN_HINTS.search(content))


# ──────────────────────────────────────────────────────────────────────
#  Public API
# ──────────────────────────────────────────────────────────────────────


def determine_file_format(intent: str, content: str) -> Tuple[str, str]:
    """Liefert ``(filename, mime_type)`` fuer einen LLM-Output.

    Mappings (siehe Patch 168 Block 1):
        FILE + Markdown   → huginn_antwort.md  (text/markdown)
        FILE + reiner Text → huginn_antwort.txt (text/plain)
        CODE + Python     → huginn_code.py     (text/x-python)
        CODE + JavaScript → huginn_code.js     (text/javascript)
        CODE + SQL        → huginn_code.sql    (text/x-sql)
        CODE + sonstig    → huginn_code.txt    (text/plain)
        CHAT (Fallback)   → huginn_antwort.md  (text/markdown)

    Defensive Default: Unbekannter Intent verhaelt sich wie CHAT-Fallback.
    """
    intent_upper = (intent or "").upper()
    text = content or ""

    if intent_upper == "CODE":
        if _detect_python(text):
            return "huginn_code.py", MIME_WHITELIST[".py"]
        if _detect_javascript(text):
            return "huginn_code.js", MIME_WHITELIST[".js"]
        if _detect_sql(text):
            return "huginn_code.sql", MIME_WHITELIST[".sql"]
        return "huginn_code.txt", MIME_WHITELIST[".txt"]

    if intent_upper == "FILE":
        if _has_markdown_syntax(text):
            return "huginn_antwort.md", MIME_WHITELIST[".md"]
        return "huginn_antwort.txt", MIME_WHITELIST[".txt"]

    # CHAT-Fallback (oder unbekannter Intent): Markdown, weil LLM-Antworten
    # i. d. R. Headings/Listen enthalten und damit besser lesbar sind.
    return "huginn_antwort.md", MIME_WHITELIST[".md"]


def should_send_as_file(
    intent: str,
    content_length: int,
    threshold: int = CHAT_FILE_THRESHOLD,
) -> bool:
    """Routing-Entscheidung Datei vs. Text.

    - Intent FILE oder CODE → IMMER Datei.
    - Intent CHAT mit ``content_length > threshold`` → Datei (Fallback).
    - Sonst → Text.

    Andere Intents (SEARCH, IMAGE, ADMIN) liefern False — diese werden
    ueber den klassischen Text-Pfad ausgespielt; bei IMAGE waere ein
    Bild-Output Aufgabe von Phase D.
    """
    intent_upper = (intent or "").upper()
    if intent_upper in ("FILE", "CODE"):
        return True
    if intent_upper == "CHAT" and content_length > threshold:
        return True
    return False


def validate_file_size(content_bytes: bytes) -> bool:
    """``True`` wenn die Datei innerhalb des 10-MB-Limits liegt."""
    return len(content_bytes) <= MAX_FILE_SIZE_BYTES


def is_extension_allowed(filename: str) -> bool:
    """``True`` wenn die Datei-Endung in der MIME-Whitelist steht.

    Wird VOR dem Versand geprueft — wenn unsere ``determine_file_format``
    durch einen Bug eine ``.exe``-Endung produziert, blockt diese
    Funktion. Belt-and-suspenders.
    """
    if not filename:
        return False
    lower = filename.lower()
    for blocked in EXTENSION_BLOCKLIST:
        if lower.endswith(blocked):
            return False
    for allowed in MIME_WHITELIST:
        if lower.endswith(allowed):
            return True
    return False


def build_file_caption(intent: str, content: str, filename: str) -> str:
    """Baut die Caption-Vorschau fuer den Datei-Versand.

    Regeln:
    - CODE: ``"📄 \\`huginn_code.py\\` — N Zeilen Python"``
    - FILE: ``"📄 Hier ist dein Dokument (N Zeilen)"`` (oder ``Markdown``
      / ``Text`` je nach Endung).
    - CHAT-Fallback: ``"Die Antwort war zu lang fuer eine Nachricht.
      Hier als Datei:"``

    Caption ist auf 1024 Zeichen begrenzt (Telegram-Limit fuer
    sendDocument).
    """
    intent_upper = (intent or "").upper()
    line_count = len(content.splitlines()) if content else 0
    text_kind = "Text"
    if filename.endswith(".py"):
        text_kind = "Python"
    elif filename.endswith(".js"):
        text_kind = "JavaScript"
    elif filename.endswith(".ts"):
        text_kind = "TypeScript"
    elif filename.endswith(".sql"):
        text_kind = "SQL"
    elif filename.endswith(".md"):
        text_kind = "Markdown"

    if intent_upper == "CODE":
        caption = f"📄 `{filename}` — {line_count} Zeilen {text_kind}"
    elif intent_upper == "FILE":
        caption = (
            f"📄 Hier ist dein Dokument: `{filename}` "
            f"({line_count} Zeilen {text_kind})"
        )
    else:
        caption = (
            "Die Antwort war zu lang fuer eine Nachricht. Hier als Datei:\n"
            f"📄 `{filename}` ({line_count} Zeilen {text_kind})"
        )

    return caption[:MAX_CAPTION_LENGTH]
