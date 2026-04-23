"""
Patch 126 - Automatische Spracherkennung fuer RAG-Dokumente.

Erkennt DE vs EN anhand der ersten Wörter eines Dokuments.
Keine externe Library: einfache Wortlisten-Frequenz-Analyse.

Filtert heraus:
- JSON-Wrapper / System-Prompt-Marker
- Code-Syntax (def/class/if/else/for/import/function/const/let/var)
- Metadaten-Header (# Key: value, --- ...)
"""
from __future__ import annotations

import re
from collections import Counter

_DE_MARKERS = {
    "der", "die", "das", "und", "ist", "nicht", "ein", "eine", "mit",
    "sich", "auf", "fuer", "für", "von", "den", "dem", "des",
    "als", "war", "werden", "wird", "haben", "hat", "bin", "bist",
    "auch", "nur", "noch", "aber", "oder", "wenn", "weil", "dass",
    "ich", "du", "er", "sie", "wir", "ihr", "mein", "dein", "sein",
    "ueber", "über", "mehr", "sehr", "schon", "immer", "doch",
}

_EN_MARKERS = {
    "the", "and", "is", "of", "to", "in", "that", "with", "for",
    "on", "as", "at", "by", "from", "or", "an", "be", "was", "were",
    "has", "have", "had", "not", "but", "if", "it", "this", "these",
    "those", "there", "their", "they", "you", "your", "would", "should",
    "could", "will", "can", "about", "when", "which", "who",
}

# Code-Tokens die wir ignorieren (damit .py-Dateien nicht als "English" erkannt werden)
_CODE_TOKENS = {
    "def", "class", "if", "else", "elif", "for", "while", "import",
    "from", "return", "pass", "yield", "raise", "try", "except",
    "function", "const", "let", "var", "async", "await", "export",
    "default", "interface", "type", "enum", "struct", "namespace",
    "true", "false", "none", "null", "undefined",
}

_WORD_RE = re.compile(r"[A-Za-zäöüÄÖÜß]{2,}")

# JSON-Wrapper / Frontmatter Pattern die vor der Analyse abgeschnitten werden
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_JSON_LEADING_RE = re.compile(r"^\s*\{[^{]{0,500}?\"(role|content|system)\"\s*:", re.IGNORECASE)


def _strip_wrappers(content: str) -> str:
    """Entfernt JSON/YAML-Frontmatter und System-Prompt-Wrapper."""
    if not content:
        return ""
    stripped = _FRONTMATTER_RE.sub("", content, count=1)
    return stripped


def detect_language(content: str, sample_chars: int = 500) -> str:
    """Erkennt die Sprache des Content.

    Args:
        content: Text dessen Sprache erkannt werden soll.
        sample_chars: Wieviel Zeichen zur Analyse benutzen (default 500).

    Returns:
        "de" für Deutsch, "en" für Englisch.
        Bei Gleichstand oder Unsicherheit → "de" (Default).
    """
    if not content or not content.strip():
        return "de"

    sample = _strip_wrappers(content)[:sample_chars]
    tokens = [w.lower() for w in _WORD_RE.findall(sample)]
    # Code-Tokens raus
    tokens = [t for t in tokens if t not in _CODE_TOKENS]

    if len(tokens) < 5:
        # Zu wenig Signal - Default
        return "de"

    counter = Counter(tokens)
    de_score = sum(counter[t] for t in _DE_MARKERS if t in counter)
    en_score = sum(counter[t] for t in _EN_MARKERS if t in counter)

    # Deutsch-Sonderzeichen sind ein starker Indikator
    if re.search(r"[äöüÄÖÜß]", sample):
        de_score += 3

    if de_score > en_score:
        return "de"
    if en_score > de_score:
        return "en"
    return "de"


def language_confidence(content: str, sample_chars: int = 500) -> dict:
    """Gibt Scores zurück (fuer Debug / Hel-Dashboard)."""
    if not content or not content.strip():
        return {"language": "de", "de_score": 0, "en_score": 0, "tokens": 0}

    sample = _strip_wrappers(content)[:sample_chars]
    tokens = [w.lower() for w in _WORD_RE.findall(sample)]
    tokens = [t for t in tokens if t not in _CODE_TOKENS]

    counter = Counter(tokens)
    de_score = sum(counter[t] for t in _DE_MARKERS if t in counter)
    en_score = sum(counter[t] for t in _EN_MARKERS if t in counter)
    if re.search(r"[äöüÄÖÜß]", sample):
        de_score += 3

    if de_score > en_score:
        lang = "de"
    elif en_score > de_score:
        lang = "en"
    else:
        lang = "de"

    return {
        "language": lang,
        "de_score": de_score,
        "en_score": en_score,
        "tokens": len(tokens),
    }
