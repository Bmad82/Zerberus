"""
Patch 125 - Bibel-Fibel Prompt-Kompressor.

Wendet Token-Optimierungsregeln aus der Bibel-Fibel auf Backend-System-Prompts an.

WICHTIG:
- Werkzeug, kein automatischer Prozess - wird manuell aufgerufen.
- Niemals auf user-facing Text anwenden (Ton muss erhalten bleiben).
- Mit preserve_sentiment=True werden emotionale Marker nicht angefasst.
"""
from __future__ import annotations

import re
from typing import List

# Artikel, die bedenkenlos entfernt werden können, wenn der Satz sonst klar bleibt.
_ARTICLES = {
    "der", "die", "das", "den", "dem", "des",
    "ein", "eine", "einen", "einem", "eines", "einer",
}

# Füllwörter/Stoppwörter die selten Bedeutung tragen.
_STOPWORDS = {
    "bitte", "dann", "also", "nun", "eigentlich", "ja",
    "quasi", "irgendwie", "halt", "jedenfalls", "grundsätzlich",
    "sozusagen", "wirklich", "eben", "ganz",
}

# Emotional/sentimental bedeutsame Wörter, die bei preserve_sentiment=True bleiben.
_SENTIMENT_MARKERS = {
    "liebevoll", "warm", "empathisch", "freundlich", "lieb",
    "sanft", "ruhig", "humorvoll", "herzlich", "aufmerksam",
    "nala", "rosa", "huginn", "chris",
}

# Verb-Kürzung: "du musst sicherstellen dass" → "Sicherstellen:"
_VERB_COMPRESSIONS: List[tuple[str, str]] = [
    (r"\bdu musst sicherstellen,?\s+dass\b", "Sicherstellen:"),
    (r"\bdu sollst sicherstellen,?\s+dass\b", "Sicherstellen:"),
    (r"\bdu solltest\b", "Soll:"),
    (r"\bdu sollst\b", "Soll:"),
    (r"\bdu musst\b", "Muss:"),
    (r"\bstelle sicher,?\s+dass\b", "Sicherstellen:"),
    (r"\bbitte beachte,?\s+dass\b", "Beachte:"),
    (r"\bes ist wichtig,?\s+dass\b", "Wichtig:"),
    (r"\bbeachte,?\s+dass\b", "Beachte:"),
]


def _apply_verb_compressions(text: str) -> str:
    for pattern, replacement in _VERB_COMPRESSIONS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def _remove_articles(text: str, preserve_sentiment: bool = False) -> str:
    def repl(match: re.Match) -> str:
        word = match.group(0)
        if preserve_sentiment and word.lower() in _SENTIMENT_MARKERS:
            return word
        return ""
    pattern = r"\b(" + "|".join(_ARTICLES) + r")\b\s*"
    return re.sub(pattern, repl, text, flags=re.IGNORECASE)


def _remove_stopwords(text: str, preserve_sentiment: bool = False) -> str:
    def repl(match: re.Match) -> str:
        word = match.group(0)
        if preserve_sentiment and word.lower() in _SENTIMENT_MARKERS:
            return word
        return ""
    pattern = r"\b(" + "|".join(_STOPWORDS) + r")\b\s*"
    return re.sub(pattern, repl, text, flags=re.IGNORECASE)


def _list_to_pipes(text: str) -> str:
    """Wandelt 'Erstens X, zweitens Y, drittens Z' → 'X|Y|Z'."""
    # Aufzählungs-Marker raus
    text = re.sub(
        r"\b(erstens|zweitens|drittens|viertens|fünftens|sechstens|siebtens)\b[:\s,]*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # 'a) ... b) ...' → a b (Labels entfernen, damit Pipes konsistent)
    text = re.sub(r"\b[a-f]\)\s*", "", text)
    # Explizite Listen mit mind. 3 Items → Pipes (nur wenn keine Satzzeichen stören)
    list_pattern = re.compile(
        r"([A-Za-zäöüÄÖÜß\-]{3,}(?:\s*,\s*[A-Za-zäöüÄÖÜß\-]{3,}){2,}"
        r"(?:\s+(?:und|oder)\s+[A-Za-zäöüÄÖÜß\-]{3,})?)"
    )
    def replace_list(match: re.Match) -> str:
        chunk = match.group(1)
        chunk = re.sub(r"\s+(und|oder)\s+", ",", chunk, flags=re.IGNORECASE)
        parts = [p.strip() for p in chunk.split(",") if p.strip()]
        return "|".join(parts)
    text = list_pattern.sub(replace_list, text)
    return text


def _collapse_whitespace(text: str) -> str:
    # Mehrfach-Spaces zu einem, Mehrfach-Leerzeilen zu einer
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _remove_redundancy(text: str) -> str:
    """Entfernt direkt aufeinander folgende identische Saetze (case-insensitive)."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    seen: set[str] = set()
    out: list[str] = []
    for sent in sentences:
        key = sent.strip().lower()
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(sent)
    return " ".join(out)


def compress_prompt(prompt: str, preserve_sentiment: bool = False) -> str:
    """Komprimiert einen System-Prompt nach Bibel-Fibel Regeln.

    Args:
        prompt: Der zu komprimierende Prompt. Leerer String → leerer String.
        preserve_sentiment: Wenn True, werden emotionale Marker (liebevoll, warm,
            Nala, Rosa, Huginn, Chris, ...) nicht angefasst.

    Returns:
        Komprimierter Prompt. Niemals länger als das Original.
    """
    if not prompt or not prompt.strip():
        return ""

    text = prompt
    text = _apply_verb_compressions(text)
    text = _list_to_pipes(text)
    text = _remove_stopwords(text, preserve_sentiment=preserve_sentiment)
    text = _remove_articles(text, preserve_sentiment=preserve_sentiment)
    text = _remove_redundancy(text)
    text = _collapse_whitespace(text)

    # Sicherheitsnetz: falls die Kompression mehr weggenommen als sinnvoll, das
    # Original zurueckgeben. Nur wenn < 10% Text ueberig bleibt ist das verdaechtig.
    if text and len(text) < max(10, len(prompt) * 0.1):
        return prompt.strip()

    return text


def compression_stats(original: str, compressed: str) -> dict:
    """Gibt Before/After-Stats zurück."""
    o = len(original)
    c = len(compressed)
    if o == 0:
        saved_pct = 0.0
    else:
        saved_pct = round(100 * (o - c) / o, 1)
    return {
        "original_chars": o,
        "compressed_chars": c,
        "saved_chars": o - c,
        "saved_pct": saved_pct,
        "estimated_tokens_saved": round((o - c) / 4),  # grobe Faustregel
    }
