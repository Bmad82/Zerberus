"""
Patch 193 — Tests fuer Whisper-Endpoint Prosodie/Sentiment-Enrichment.

Coverage:
  - Response-Schema /v1/audio/transcriptions: text bleibt IMMER (Backward-Compat),
    sentiment + prosody nur wenn vorhanden
  - Source-Audit fuer ENRICHMENT-193 Tag in legacy.py + nala.py
  - Source-Audit fuer SSE-Events `event: prosody` und `event: sentiment` in nala.py
  - Konsens-Konstruktion bei vorhandenem BERT + Prosodie
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def legacy_src():
    return (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nala_src():
    return (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")


# ====================================================================
# Logik-Tests (Reine Helper aus sentiment_display.py)
# ====================================================================

class TestEnrichmentLogic:
    def test_consensus_field_with_both_signals(self):
        """Wenn BERT + Prosodie → consensus-Dict mit Quelle 'bert+prosody'."""
        from zerberus.utils.sentiment_display import compute_consensus
        prosody = {"mood": "stressed", "valence": -0.4, "confidence": 0.7}
        out = compute_consensus("positive", 0.85, prosody)
        assert out["source"] == "bert+prosody"
        assert out["incongruent"] is True

    def test_no_consensus_text_only(self):
        """Reiner Text-Pfad → consensus.source = bert_only."""
        from zerberus.utils.sentiment_display import compute_consensus
        out = compute_consensus("neutral", 0.5, None)
        assert out["source"] == "bert_only"
        assert out["incongruent"] is False


# ====================================================================
# Source-Audit /v1/audio/transcriptions (legacy.py)
# ====================================================================

class TestWhisperResponseSchema:
    def test_response_always_has_text_field(self, legacy_src):
        """Audio-Endpunkt baut response = {"text": cleaned_transcript} immer."""
        # Defensives Audit: text-Feld wird IMMER initialisiert.
        assert 'response = {"text": cleaned_transcript}' in legacy_src

    def test_response_prosody_gated_by_stub_check(self, legacy_src):
        """Prosody-Feld nur wenn source != 'stub'."""
        # Backward-Compat: kein prosody bei Stub.
        assert 'source") != "stub"' in legacy_src

    def test_legacy_imports_analyze_sentiment_in_audio(self, legacy_src):
        """Audio-Endpoint ruft analyze_sentiment auf."""
        assert "analyze_sentiment" in legacy_src

    def test_legacy_uses_compute_consensus(self, legacy_src):
        """compute_consensus wird genutzt wenn Prosodie da."""
        assert "compute_consensus" in legacy_src

    def test_legacy_enrichment_tag_present(self, legacy_src):
        """[ENRICHMENT-193] Logging-Tag im legacy.py."""
        assert "[ENRICHMENT-193]" in legacy_src

    def test_legacy_sentiment_field_added_when_bert_runs(self, legacy_src):
        """Audio-Response erhaelt sentiment-Feld wenn BERT erfolgreich."""
        # Pattern-Check auf das additive Feld:
        assert 'response["sentiment"] = _sentiment_block' in legacy_src

    def test_backward_compat_text_only_client(self, legacy_src):
        """Clients die nur ['text'] lesen (Dictate) bleiben kompatibel —
        text-Feld wird stets vor jedem additiven Feld initialisiert."""
        # Audit-Pattern: text-Init kommt VOR sentiment-Init.
        text_idx = legacy_src.find('response = {"text": cleaned_transcript}')
        sentiment_idx = legacy_src.find('response["sentiment"] = _sentiment_block')
        assert text_idx > 0, "text-Init nicht gefunden"
        assert sentiment_idx > 0, "sentiment-Init nicht gefunden"
        assert text_idx < sentiment_idx, "text MUSS vor sentiment initialisiert werden (Backward-Compat)"


# ====================================================================
# Source-Audit /nala/voice + SSE-Events (nala.py)
# ====================================================================

class TestNalaVoiceEnrichment:
    def test_nala_voice_uses_analyze_sentiment(self, nala_src):
        """nala.py ruft analyze_sentiment im /voice-Pfad auf."""
        assert "analyze_sentiment" in nala_src

    def test_nala_voice_uses_compute_consensus(self, nala_src):
        """nala.py nutzt compute_consensus."""
        assert "compute_consensus" in nala_src

    def test_nala_enrichment_tag_present(self, nala_src):
        """[ENRICHMENT-193] Logging-Tag im nala.py."""
        assert "[ENRICHMENT-193]" in nala_src

    def test_sse_prosody_event_emitted(self, nala_src):
        """SSE-Stream emittiert `event: prosody` (Frontend-Listener-Quelle)."""
        assert "event: prosody" in nala_src

    def test_sse_sentiment_event_emitted(self, nala_src):
        """SSE-Stream emittiert `event: sentiment`."""
        assert "event: sentiment" in nala_src

    def test_voice_publishes_prosody_event(self, nala_src):
        """voice-Handler publisht type='prosody' an Event-Bus."""
        assert 'type="prosody"' in nala_src

    def test_voice_publishes_sentiment_event(self, nala_src):
        """voice-Handler publisht type='sentiment' an Event-Bus."""
        assert 'type="sentiment"' in nala_src
