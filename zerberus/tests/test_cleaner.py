"""
Patch 102 (B-01): Tests für Whisper Phrasen-Repetition-Filter.
"""
import pytest
from zerberus.core.cleaner import detect_phrase_repetition


class TestPhraseRepetition:
    def test_klassischer_whisper_loop(self):
        s = "ein bisschen so ein bisschen so ein bisschen so ein bisschen so"
        assert detect_phrase_repetition(s) == "ein bisschen so"

    def test_zwei_wort_phrase(self):
        s = "und dann und dann und dann"
        assert detect_phrase_repetition(s) == "und dann"

    def test_normaler_text_bleibt(self):
        s = "Heute war ein schöner Tag im Park"
        assert detect_phrase_repetition(s) == s

    def test_leerer_string(self):
        assert detect_phrase_repetition("") == ""

    def test_eine_wiederholung_ueber_threshold(self):
        s = "guten Morgen guten Morgen"
        assert detect_phrase_repetition(s, max_repeats=3) == s

    def test_zwei_wiederholungen_werden_gekuerzt(self):
        s = "guten Morgen guten Morgen"
        assert detect_phrase_repetition(s, max_repeats=2) == "guten Morgen"

    def test_mit_text_drumherum(self):
        s = "Hallo ich sage ja ja ja ja und tschuess"
        result = detect_phrase_repetition(s)
        assert "ja ja ja ja" not in result
        assert result.startswith("Hallo ich sage")
        assert result.endswith("und tschuess")

    def test_dreiwort_phrase_in_satz(self):
        s = "Anfang ein bisschen so ein bisschen so ein bisschen so Ende"
        result = detect_phrase_repetition(s)
        assert "ein bisschen so ein bisschen so" not in result
        assert "Anfang" in result
        assert "Ende" in result
