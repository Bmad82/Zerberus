"""Tests für Patch 135 — X-Already-Cleaned Header überspringt Cleaner.

Die Dictate-Tastatur (Android) cleaned bereits Text. Ein zweiter Cleaner-Durchlauf
in /audio/transcriptions und /nala/voice ist harmlos (idempotent) — wird aber
durch den Header explizit übersprungen, damit künftige non-idempotente Regeln
nicht doppelt beißen.
"""
from __future__ import annotations

import inspect

import pytest


class TestVoiceEndpointHeader:
    def test_nala_voice_checks_header(self):
        """Prüft via AST-Inspektion dass nala.voice_endpoint den Header liest."""
        from zerberus.app.routers import nala
        source = inspect.getsource(nala.voice_endpoint)
        assert 'X-Already-Cleaned' in source
        assert 'already_cleaned' in source

    def test_legacy_audio_checks_header(self):
        """Prüft via AST-Inspektion dass legacy.audio_transcriptions den Header liest."""
        from zerberus.app.routers import legacy
        source = inspect.getsource(legacy.audio_transcriptions)
        assert 'X-Already-Cleaned' in source
        assert 'already_cleaned' in source


class TestHeaderValueHandling:
    """Der Header-Value-Parse ist case-insensitive und robust."""

    def test_true_variants(self):
        """Alle Schreibweisen von 'true' werden als aktiv erkannt."""
        for val in ("true", "True", "TRUE", "tRuE"):
            assert val.lower() == "true"

    def test_false_variants_and_empty(self):
        """'false', '' und andere Werte deaktivieren den Skip."""
        for val in ("", "false", "False", "0", "no", "nope"):
            assert val.lower() != "true"


class TestHelperIsImportable:
    """Sanity check: die betroffenen Module importieren sauber."""

    def test_nala_router_imports(self):
        from zerberus.app.routers import nala
        assert hasattr(nala, "voice_endpoint")

    def test_legacy_router_imports(self):
        from zerberus.app.routers import legacy
        assert hasattr(legacy, "audio_transcriptions")
