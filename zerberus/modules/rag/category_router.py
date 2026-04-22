"""
Query-Router — Patch 111.

Keyword-basierte Heuristik, um aus einer User-Query eine Kategorie zu raten.
Ergebnis wird im Retrieval-Pipeline-Ende als Score-Bonus auf Chunks der
passenden Category angewendet (weiches Filtern, kein hartes Drop).

LLM-basierte Classification kommt in Phase 4.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Keyword-Signale pro Category. Lowercase-Match, einfache Substring-Suche.
# Die Listen sind bewusst kurz — false positives sind gefährlicher als
# false negatives (wenn keine Category erkannt wird, gibt's halt keinen Boost).
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "technical": (
        "code", "funktion", "api", "bug", "fehler", "python", "javascript",
        "config", "server", "endpoint", "import", "class ", "traceback",
        "exception", "regex", "json", "yaml",
    ),
    "narrative": (
        "geschichte", "kapitel", "charakter", "held", "heldin", "erzählung",
        "szene", "dialog", "protagonist", "antagonist", "plot", "handlung",
    ),
    "lore": (
        "welt", "universum", "mythologie", "legende", "rasse", "fraktion",
        "planet", "dimension", "magie", "götter", "lore", "kosmologie",
    ),
    "personal": (
        "tagebuch", "gestern", "heute", "ich habe", "mein tag", "notiz",
        "erinnerung", "mir ging es", "gefühlt",
    ),
    "reference": (
        "definition", "tabelle", "liste", "übersicht", "nachschlagen",
        "daten", "parameter", "wert", "spalte", "zeile",
    ),
}


def _match_keyword(query_lower: str, kw: str) -> bool:
    """Wortgrenzen-Matching, damit z.B. 'api' nicht in 'Kapitel' zündet.

    Multi-Wort-Keywords (enthalten Leerzeichen) sowie trailing-Space-Marker
    werden per einfachem Substring-Match abgehandelt — dort reicht Python's
    `in`, weil der Leerzeichen-Kontext bereits Wortgrenze erzwingt.
    """
    if " " in kw.strip():
        return kw in query_lower
    return re.search(rf"(?<!\w){re.escape(kw.strip())}(?!\w)", query_lower) is not None


def detect_query_category(query: str) -> str | None:
    """Rät die wahrscheinlichste Category aus der Query-Formulierung.

    Returns:
        Category-Name wenn mindestens ein Keyword matcht, sonst None.
        Bei Gleichstand: der erste Eintrag (Insertion-Order).
    """
    if not query:
        return None
    query_lower = query.lower()
    scores: dict[str, int] = {}
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if _match_keyword(query_lower, kw))
        if score > 0:
            scores[cat] = score
    if not scores:
        return None
    best = max(scores, key=lambda c: scores[c])
    logger.warning(
        f"[ROUTER-111] Query-Category: {best} (score={scores[best]}, "
        f"alle={scores})"
    )
    return best


def apply_category_boost(
    results: list[dict[str, Any]],
    query_category: str | None,
    boost: float = 0.1,
) -> list[dict[str, Any]]:
    """Gibt Chunks der passenden Category einen Score-Bonus.

    Bevorzugt `rerank_score` (Cross-Encoder, Patch 89), fällt auf `score`
    (L2-derived, ohne Reranker) zurück. Sortiert neu und gibt die Liste
    absteigend nach dem geboosteten Score zurück.

    Kein harter Filter — Chunks ohne Match bleiben im Ergebnis, nur die
    Reihenfolge verschiebt sich. So bleibt das Retrieval robust gegen
    Fehl-Klassifikationen der Heuristik.
    """
    if not query_category or not results:
        return results

    score_key = "rerank_score" if results and "rerank_score" in results[0] else "score"
    boosted_count = 0
    for r in results:
        if r.get("category") == query_category:
            current = float(r.get(score_key, 0.0))
            r[score_key] = current + boost
            r["category_boosted"] = True
            boosted_count += 1

    if boosted_count == 0:
        return results

    logger.warning(
        f"[ROUTER-111] Category-Boost: {boosted_count}/{len(results)} Chunks "
        f"der Category {query_category!r} um +{boost} auf {score_key} geboostet"
    )
    return sorted(results, key=lambda x: float(x.get(score_key, 0.0)), reverse=True)
