"""Patch 165 — Tests fuer Pure-Functions in ``zerberus.core.database``.

Bisher nur indirekt via ``test_db_dedup.py`` und ``test_memory_store.py``
abgedeckt — die Metrik- und Sentiment-Helpers selbst waren ungetestet.

Fokus auf die Pure-Functions:
- ``compute_metrics`` (Wortzahl, TTR, Yule-K, Shannon-Entropy)
- ``_compute_sentiment`` (graceful Fallback bei fehlendem Sentiment-Modul)
"""
from __future__ import annotations

import math

from zerberus.core import database as db_mod
from zerberus.core.database import _compute_sentiment, compute_metrics


# ----- compute_metrics ------------------------------------------------------


class TestComputeMetricsBasic:
    def test_empty_text_returns_zeros(self):
        m = compute_metrics("")
        for key in [
            "word_count",
            "sentence_count",
            "character_count",
            "avg_word_length",
            "unique_word_count",
            "ttr",
            "hapax_count",
            "yule_k",
            "shannon_entropy",
            "vader_compound",
        ]:
            assert m[key] == 0

    def test_simple_sentence_word_count(self):
        m = compute_metrics("Hallo Welt das ist ein Test")
        assert m["word_count"] == 6
        assert m["sentence_count"] == 1
        assert m["unique_word_count"] == 6

    def test_repeated_words_lower_ttr(self):
        m = compute_metrics("foo foo foo bar")
        assert m["word_count"] == 4
        assert m["unique_word_count"] == 2
        assert m["ttr"] == round(2 / 4, 3)

    def test_multiple_sentences_counted(self):
        m = compute_metrics("Erster Satz. Zweiter Satz! Dritter Satz?")
        assert m["sentence_count"] == 3

    def test_character_count_includes_spaces(self):
        text = "Hallo Welt"
        m = compute_metrics(text)
        assert m["character_count"] == len(text)

    def test_hapax_count(self):
        # Drei Woerter genau einmal, eines doppelt → hapax=3.
        m = compute_metrics("foo bar baz foo qux qux qux")
        assert m["hapax_count"] == 2  # nur "bar" und "baz" einmal

    def test_avg_word_length_rounded(self):
        m = compute_metrics("abc defg")  # (3+4)/2 = 3.5
        assert m["avg_word_length"] == 3.5


class TestComputeMetricsAdvanced:
    def test_ttr_perfect_diversity(self):
        m = compute_metrics("eins zwei drei vier")
        assert m["ttr"] == 1.0

    def test_shannon_entropy_uniform_distribution(self):
        # 4 verschiedene Woerter, jeweils einmal → log2(4) = 2.0.
        m = compute_metrics("a b c d")
        assert m["shannon_entropy"] == 2.0

    def test_shannon_entropy_zero_for_single_word(self):
        m = compute_metrics("a a a a")
        assert m["shannon_entropy"] == 0.0

    def test_yule_k_finite(self):
        # Yule-K sollte fuer normalen Text einen endlichen Wert liefern.
        m = compute_metrics("der die das und ist auch noch eine")
        assert math.isfinite(m["yule_k"])


# ----- _compute_sentiment ---------------------------------------------------


class TestComputeSentiment:
    def test_returns_float(self):
        # Egal ob Sentiment-Modul verfuegbar oder nicht: Rueckgabe ist float.
        result = _compute_sentiment("Heute war ein wunderschoener Tag.")
        assert isinstance(result, float)

    def test_returns_zero_when_module_missing(self, monkeypatch):
        # Sentiment-Modul-Import explizit zerbrechen → Fallback 0.0.
        import sys

        monkeypatch.setitem(
            sys.modules,
            "zerberus.modules.sentiment.router",
            None,  # type: ignore[arg-type]
        )
        result = _compute_sentiment("egal was")
        assert result == 0.0

    def test_returns_zero_when_analyze_raises(self, monkeypatch):
        import sys
        import types

        fake = types.ModuleType("zerberus.modules.sentiment.router")

        def _boom(_text):
            raise RuntimeError("Sentiment-Modell nicht verfuegbar")

        fake.analyze_sentiment = _boom  # type: ignore[attr-defined]
        monkeypatch.setitem(
            sys.modules, "zerberus.modules.sentiment.router", fake
        )
        result = _compute_sentiment("text")
        assert result == 0.0

    def test_neutral_label_returns_zero(self, monkeypatch):
        import sys
        import types

        fake = types.ModuleType("zerberus.modules.sentiment.router")
        fake.analyze_sentiment = lambda _t: {"label": "neutral", "score": 0.99}
        monkeypatch.setitem(
            sys.modules, "zerberus.modules.sentiment.router", fake
        )
        assert _compute_sentiment("text") == 0.0

    def test_positive_label_returns_positive_dampened(self, monkeypatch):
        # P85-Daempfung: score 0.5 → 0.3 (untere Schranke).
        import sys
        import types

        fake = types.ModuleType("zerberus.modules.sentiment.router")
        fake.analyze_sentiment = lambda _t: {"label": "positive", "score": 0.5}
        monkeypatch.setitem(
            sys.modules, "zerberus.modules.sentiment.router", fake
        )
        result = _compute_sentiment("text")
        assert result == 0.3

    def test_negative_label_returns_negative(self, monkeypatch):
        import sys
        import types

        fake = types.ModuleType("zerberus.modules.sentiment.router")
        fake.analyze_sentiment = lambda _t: {"label": "negative", "score": 1.0}
        monkeypatch.setitem(
            sys.modules, "zerberus.modules.sentiment.router", fake
        )
        result = _compute_sentiment("text")
        assert result == -1.0

    def test_score_capped_at_one(self, monkeypatch):
        import sys
        import types

        fake = types.ModuleType("zerberus.modules.sentiment.router")
        # Pathologisch hoher Score → Daempfung kappt bei 1.0.
        fake.analyze_sentiment = lambda _t: {"label": "positive", "score": 5.0}
        monkeypatch.setitem(
            sys.modules, "zerberus.modules.sentiment.router", fake
        )
        result = _compute_sentiment("text")
        assert result == 1.0


# ----- Sanity: compute_metrics nutzt _compute_sentiment -------------------


def test_compute_metrics_includes_sentiment(monkeypatch):
    """Patch 165: Falls Sentiment-Modul fehlt, bleibt vader_compound auf 0.0
    statt einen Crash auszuloesen — wichtig fuer die Test-Suite ohne
    BERT-Modell."""
    import sys

    monkeypatch.setitem(
        sys.modules,
        "zerberus.modules.sentiment.router",
        None,  # type: ignore[arg-type]
    )
    m = compute_metrics("Egal, irgendein Text mit ein paar Woertern.")
    assert m["vader_compound"] == 0.0
    # Wortzahl muss trotzdem stimmen.
    assert m["word_count"] == 7


def test_module_exposes_metric_helpers():
    """Sanity: die Helpers sind oeffentlich am Modul (kein __all__-Bruch)."""
    assert callable(getattr(db_mod, "compute_metrics"))
    assert callable(getattr(db_mod, "_compute_sentiment"))
