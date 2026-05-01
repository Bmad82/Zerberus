"""
Patch 192 — Tests fuer Sentiment-Triptychon UI.

Coverage:
  - Emoji-Mapping (BERT, Prosodie, Konsens)
  - Inkongruenz-Erkennung (Text positiv, Stimme negativ → 🤔)
  - Mehrabian-Logik (hohe Prosodie-Confidence dominiert)
  - compute_consensus / build_sentiment_payload
  - Source-Audit fuer Frontend (HTML, CSS, JS)
  - Source-Audit fuer Backend (legacy.py sentiment-Feld)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from zerberus.utils.sentiment_display import (
    bert_emoji,
    prosody_emoji,
    consensus_emoji,
    compute_consensus,
    build_sentiment_payload,
)


ROOT = Path(__file__).resolve().parents[2]


# ====================================================================
# BERT-Emoji-Mapping
# ====================================================================

class TestBertEmoji:
    def test_sentiment_emoji_positive_high(self):
        """score > 0.7 → 😊."""
        assert bert_emoji("positive", 0.85) == "😊"

    def test_sentiment_emoji_positive_low(self):
        """score <= 0.7 → 🙂."""
        assert bert_emoji("positive", 0.6) == "🙂"
        assert bert_emoji("positive", 0.5) == "🙂"

    def test_sentiment_emoji_negative_high(self):
        """negative + score > 0.7 → 😟."""
        assert bert_emoji("negative", 0.9) == "😟"

    def test_sentiment_emoji_negative_low(self):
        """negative + score <= 0.7 → 😐."""
        assert bert_emoji("negative", 0.4) == "😐"

    def test_sentiment_emoji_neutral(self):
        """neutral → 😶."""
        assert bert_emoji("neutral", 0.99) == "😶"

    def test_sentiment_emoji_invalid_score(self):
        """nicht-numerischer Score → graceful fallback (😶/🙂/😐)."""
        out = bert_emoji("positive", "high")
        # invalid → score=0.0 → "positive" und 0.0 < 0.7 → 🙂
        assert out in ("🙂", "😶")


# ====================================================================
# Prosodie-Emoji-Mapping
# ====================================================================

class TestProsodyEmoji:
    @pytest.mark.parametrize("mood,expected", [
        ("happy", "😊"),
        ("excited", "🤩"),
        ("calm", "😌"),
        ("sad", "😢"),
        ("angry", "😠"),
        ("stressed", "😰"),
        ("tired", "😴"),
        ("anxious", "😬"),
        ("sarcastic", "😏"),
        ("neutral", "😶"),
    ])
    def test_prosody_emoji_mapping(self, mood, expected):
        """Alle 10 Moods → korrekte Emojis."""
        assert prosody_emoji({"mood": mood}) == expected

    def test_prosody_emoji_unknown_mood(self):
        """Unbekannter Mood → 😶 (default)."""
        assert prosody_emoji({"mood": "elated"}) == "😶"

    def test_prosody_emoji_none(self):
        """prosody=None → 😶."""
        assert prosody_emoji(None) == "😶"


# ====================================================================
# Konsens-Logik
# ====================================================================

class TestConsensus:
    def test_consensus_incongruent(self):
        """BERT positiv (>0.5) + prosody valence < -0.2 → 🤔."""
        prosody = {"mood": "stressed", "valence": -0.4, "confidence": 0.8}
        assert consensus_emoji("positive", 0.85, prosody) == "🤔"

    def test_consensus_prosody_dominates(self):
        """confidence > 0.5 → Prosodie-Emoji."""
        prosody = {"mood": "calm", "valence": 0.4, "confidence": 0.7}
        assert consensus_emoji("neutral", 0.5, prosody) == "😌"

    def test_consensus_bert_fallback_low_confidence(self):
        """confidence <= 0.5 → BERT-Emoji."""
        prosody = {"mood": "happy", "valence": 0.3, "confidence": 0.2}
        assert consensus_emoji("positive", 0.85, prosody) == "😊"

    def test_consensus_no_prosody(self):
        """prosody=None → BERT-Emoji."""
        assert consensus_emoji("positive", 0.6, None) == "🙂"

    def test_compute_consensus_returns_dict(self):
        """compute_consensus liefert Dict mit emoji+incongruent+source."""
        prosody = {"mood": "stressed", "valence": -0.5, "confidence": 0.8}
        out = compute_consensus("positive", 0.9, prosody)
        assert out["emoji"] == "🤔"
        assert out["incongruent"] is True
        assert out["source"] == "bert+prosody"

    def test_compute_consensus_bert_only(self):
        """Ohne Prosodie → source=bert_only, incongruent=False."""
        out = compute_consensus("positive", 0.6, None)
        assert out["incongruent"] is False
        assert out["source"] == "bert_only"


# ====================================================================
# build_sentiment_payload
# ====================================================================

class TestSentimentPayload:
    def test_payload_text_only(self):
        """Text-Input ohne Prosodie → bert + consensus, prosody=None."""
        out = build_sentiment_payload(
            "Mir geht's gut",
            prosody=None,
            bert_result={"label": "positive", "score": 0.8},
        )
        assert out["bert"]["label"] == "positive"
        assert out["bert"]["emoji"] == "😊"
        assert out["prosody"] is None
        assert out["consensus"]["source"] == "bert_only"

    def test_payload_with_prosody(self):
        """Audio-Input mit Prosodie → alle drei Felder gefuellt."""
        prosody = {
            "mood": "stressed", "tempo": "fast",
            "valence": -0.3, "arousal": 0.7,
            "confidence": 0.85, "source": "gemma_e2b",
        }
        out = build_sentiment_payload(
            "Mir geht's gut",
            prosody=prosody,
            bert_result={"label": "positive", "score": 0.82},
        )
        assert out["bert"] is not None
        assert out["prosody"] is not None
        assert out["prosody"]["emoji"] == "😰"
        assert out["consensus"]["incongruent"] is True
        assert out["consensus"]["emoji"] == "🤔"

    def test_payload_skips_stub_prosody(self):
        """Stub-Prosodie wird ignoriert (source != 'stub' Filter)."""
        out = build_sentiment_payload(
            "Hallo",
            prosody={"mood": "neutral", "source": "stub", "confidence": 0.0, "valence": 0.5},
            bert_result={"label": "neutral", "score": 0.5},
        )
        assert out["prosody"] is None


# ====================================================================
# Frontend-Source-Audit (Triptychon-HTML, CSS, JS)
# ====================================================================

class TestTriptychonFrontendSource:
    @pytest.fixture(scope="class")
    def nala_src(self):
        return (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")

    def test_triptych_html_class_exists(self, nala_src):
        """sentiment-triptych Klasse im Markup."""
        assert "sentiment-triptych" in nala_src

    def test_triptych_three_chips(self, nala_src):
        """Drei sent-chip Elemente: BERT, Prosodie, Konsens."""
        assert "sent-bert" in nala_src
        assert "sent-prosody" in nala_src
        assert "sent-consensus" in nala_src

    def test_triptych_user_side_flex_start(self, nala_src):
        """User-Bubbles: justify-content: flex-start (links)."""
        assert "user-wrapper .sentiment-triptych" in nala_src
        assert "flex-start" in nala_src

    def test_triptych_bot_side_flex_end(self, nala_src):
        """Bot-Bubbles: justify-content: flex-end (rechts) — Default fuer .msg-wrapper."""
        # Default-Selektor in CSS:
        assert ".msg-wrapper .sentiment-triptych" in nala_src
        assert "flex-end" in nala_src

    def test_triptych_inactive_prosody_class(self, nala_src):
        """sent-inactive Klasse fuer grauen Prosodie-Chip ohne Audio."""
        assert "sent-inactive" in nala_src

    def test_triptych_apply_function(self, nala_src):
        """JS: applySentimentToLastBubbles + _applyTriptychBlock vorhanden."""
        assert "applySentimentToLastBubbles" in nala_src
        assert "_applyTriptychBlock" in nala_src

    def test_triptych_consumes_data_sentiment(self, nala_src):
        """sendMessage liest data.sentiment aus chat-Response."""
        assert "data.sentiment" in nala_src

    def test_triptych_44px_touch_target(self, nala_src):
        """44px Touch-Target fuer Mobile."""
        # In sent-chip-Block oder media query
        assert "min-height: 44px" in nala_src or "min-height:44px" in nala_src
        assert "min-width: 44px" in nala_src or "min-width:44px" in nala_src

    def test_triptych_logging_tag(self, nala_src):
        """Patch-Logging-Tag 192 referenziert."""
        assert "Patch 192" in nala_src or "[SENTIMENT-192]" in nala_src

    def test_triptych_incongruent_marker(self, nala_src):
        """sent-incongruent Klasse fuer Konsens-Widerspruch."""
        assert "sent-incongruent" in nala_src


# ====================================================================
# Backend-Source-Audit (Sentiment-Feld in Response)
# ====================================================================

class TestTriptychonBackendSource:
    def test_legacy_chat_response_has_sentiment_field(self):
        """legacy.py ChatCompletionResponse hat sentiment-Feld."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        assert "sentiment: dict | None" in legacy_src or "sentiment:" in legacy_src
        assert "[SENTIMENT-192]" in legacy_src

    def test_legacy_uses_build_sentiment_payload(self):
        """legacy.py importiert build_sentiment_payload."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        assert "build_sentiment_payload" in legacy_src

    def test_sentiment_display_module_exists(self):
        """sentiment_display.py existiert in utils."""
        path = ROOT / "zerberus" / "utils" / "sentiment_display.py"
        assert path.exists()
