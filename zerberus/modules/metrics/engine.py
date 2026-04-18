"""
Metric Engine – Patch 59/60
Berechnet linguistische Metriken für deutsche Texte.
Pure-Python-Metriken (kein spaCy nötig).
spaCy-basierte Metriken (hedging, self-reference, causal) sind graceful:
None wenn de_core_news_sm nicht installiert ist.
spaCy wird lazy geladen – kein Import beim Modulstart.
"""
import logging
import math
import re
from collections import Counter
from typing import Optional

logger = logging.getLogger(__name__)

_HEDGE_WORDS = {"vielleicht", "könnte", "eventuell", "möglicherweise", "wohl", "eigentlich"}
_SELF_REF_WORDS = {"ich", "mich", "mir", "mein", "meiner", "meins"}
_CAUSAL_WORDS = {"weil", "da", "daher", "deshalb", "denn", "also", "darum"}

_nlp = None  # lazy load


def _tokenize(text: str) -> list:
    return [w.lower() for w in re.findall(r'\b\w+\b', text)]


def _split_sentences(text: str) -> list:
    return [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]


# ---------------------------------------------------------------------------
# Pure-Python-Metriken
# ---------------------------------------------------------------------------

def compute_ttr(text: str) -> float:
    """Type-Token-Ratio: unique_words / total_words (lowercase-normalisiert)"""
    tokens = _tokenize(text)  # _tokenize lowercases bereits
    if not tokens:
        return 0.0
    unique = len(set(tokens))
    total = len(tokens)
    ttr = unique / total
    # Debug-Log bei TTR=1.0 (alle Tokens einmalig – typisch bei sehr kurzen Nachrichten)
    if ttr == 1.0:
        logger.debug(f"[TTR] tokens={total}, unique={unique}, ttr=1.0 — alle Tokens einmalig (kurze Nachricht oder nur 1-2 Wörter)")
    return ttr


def compute_mattr(text: str, window_size: int = 50) -> float:
    """Moving Average TTR über Fenster von 50 Wörtern"""
    tokens = _tokenize(text)
    if not tokens:
        return 0.0
    if len(tokens) < window_size:
        return compute_ttr(text)
    ratios = []
    for i in range(len(tokens) - window_size + 1):
        window = tokens[i:i + window_size]
        ratios.append(len(set(window)) / window_size)
    return sum(ratios) / len(ratios) if ratios else 0.0


def compute_hapax_ratio(text: str) -> float:
    """Hapax Legomena Ratio: Wörter die genau 1x vorkommen / total_words"""
    tokens = _tokenize(text)
    if not tokens:
        return 0.0
    counts = Counter(tokens)
    hapax = sum(1 for c in counts.values() if c == 1)
    return hapax / len(tokens)


def compute_avg_sentence_length(text: str) -> float:
    """Durchschnittliche Satzlänge in Wörtern (Split auf . ! ?)"""
    sentences = _split_sentences(text)
    if not sentences:
        return 0.0
    lengths = [len(_tokenize(s)) for s in sentences]
    return sum(lengths) / len(lengths)


def compute_shannon_entropy(text: str) -> float:
    """Shannon Entropy der Wortverteilung"""
    tokens = _tokenize(text)
    if not tokens:
        return 0.0
    total = len(tokens)
    counts = Counter(tokens)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


# ---------------------------------------------------------------------------
# spaCy-basierte Metriken (graceful – None wenn Modell nicht verfügbar)
# ---------------------------------------------------------------------------

def _get_nlp():
    global _nlp
    if _nlp is None:
        try:
            import spacy
            _nlp = spacy.load("de_core_news_sm")
        except Exception:
            _nlp = False
    return _nlp if _nlp else None


def compute_hedging_frequency(text: str) -> Optional[float]:
    """Anteil Tokens die Hedge-Wörter sind (vielleicht, könnte, eventuell, möglicherweise, wohl, eigentlich)"""
    nlp = _get_nlp()
    if nlp is None:
        return None
    doc = nlp(text)
    tokens = [t.text.lower() for t in doc if not t.is_space]
    if not tokens:
        return None
    return sum(1 for t in tokens if t in _HEDGE_WORDS) / len(tokens)


def compute_self_reference_frequency(text: str) -> Optional[float]:
    """Anteil Tokens die Selbstreferenz sind (ich, mich, mir, mein, meiner, meins)"""
    nlp = _get_nlp()
    if nlp is None:
        return None
    doc = nlp(text)
    tokens = [t.text.lower() for t in doc if not t.is_space]
    if not tokens:
        return None
    return sum(1 for t in tokens if t in _SELF_REF_WORDS) / len(tokens)


def compute_causal_ratio(text: str) -> Optional[float]:
    """Anteil Sätze mit Kausalkonnektoren (weil, da, daher, deshalb, denn, also, darum)"""
    nlp = _get_nlp()
    if nlp is None:
        return None
    doc = nlp(text)
    sentences = list(doc.sents)
    if not sentences:
        return None
    causal_count = sum(
        1 for sent in sentences
        if any(t.text.lower() in _CAUSAL_WORDS for t in sent)
    )
    return causal_count / len(sentences)
