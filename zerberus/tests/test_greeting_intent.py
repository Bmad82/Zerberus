"""
Patch 137 (B-001): GREETING Intent + RAG-Skip für Smalltalk.

Tests:
- Reine Grüße werden als GREETING klassifiziert (Hi, Hallo, Moin, Guten Morgen, ...)
- Grüße mit inhaltlicher Frage bleiben QUESTION ("Hallo, wer ist Anne?")
- GREETING-Intent triggert RAG-Skip (kein Noise-Context bei Smalltalk)
- rerank_min_score ist auf 0.15 angehoben (war 0.05)
"""
from __future__ import annotations

import pytest

from zerberus.app.routers.orchestrator import detect_intent, INTENT_SNIPPETS, _PERMISSION_MATRIX


class TestGreetingIntent:
    def test_guten_morgen_ist_greeting(self):
        assert detect_intent("Guten Morgen") == "GREETING"

    def test_hi_ist_greeting(self):
        assert detect_intent("Hi") == "GREETING"

    def test_hallo_ist_greeting(self):
        assert detect_intent("Hallo") == "GREETING"

    def test_moin_ist_greeting(self):
        assert detect_intent("Moin") == "GREETING"

    def test_servus_ist_greeting(self):
        assert detect_intent("Servus") == "GREETING"

    def test_na_ist_greeting_trotz_fragezeichen(self):
        assert detect_intent("Na?") == "GREETING"

    def test_wie_gehts_ist_greeting(self):
        assert detect_intent("Wie geht es dir?") == "GREETING"

    def test_tschuess_ist_greeting(self):
        assert detect_intent("Tschüss") == "GREETING"

    def test_danke_ist_greeting(self):
        assert detect_intent("Danke") == "GREETING"

    def test_gruss_mit_frage_ist_question(self):
        """Inhaltliche Frage gewinnt über Gruß-Pattern."""
        assert detect_intent("Hallo, wer ist Anne?") == "QUESTION"

    def test_gruss_mit_fragewort_ist_question(self):
        assert detect_intent("Guten Morgen, was steht in meinen Dokumenten?") == "QUESTION"

    def test_normale_frage_bleibt_question(self):
        assert detect_intent("Wer ist Anne?") == "QUESTION"

    def test_transform_bleibt_transform(self):
        assert detect_intent("Übersetze folgenden Text: Hello world") == "TRANSFORM"


class TestGreetingPermissions:
    def test_alle_level_duerfen_greeting(self):
        for lvl in ("admin", "user", "guest"):
            assert "GREETING" in _PERMISSION_MATRIX[lvl], f"{lvl} darf GREETING nicht?"


class TestGreetingSnippet:
    def test_greeting_snippet_existiert(self):
        assert "GREETING" in INTENT_SNIPPETS
        snippet = INTENT_SNIPPETS["GREETING"]
        assert "Smalltalk" in snippet or "smalltalk" in snippet.lower()


class TestRerankThreshold:
    def test_rerank_min_score_auf_015(self):
        """Patch 137 (B-001): Threshold von 0.05 auf 0.15 angehoben."""
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        rag = cfg["modules"]["rag"]
        assert rag["rerank_min_score"] >= 0.15, f"rerank_min_score={rag['rerank_min_score']}, erwartet >= 0.15"
