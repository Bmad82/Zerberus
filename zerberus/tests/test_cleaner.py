"""
Patch 102 (B-01): Tests für Whisper Phrasen-Repetition-Filter.
Patch 113b (W-001b): Tests für Satz-Repetition-Filter.
"""
import pytest
from zerberus.core.cleaner import detect_phrase_repetition, detect_sentence_repetition


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


class TestSentenceRepetition:
    """Patch 113b (W-001b): Ganze Satz-Wiederholungen."""

    def test_klassische_satz_repetition(self):
        s = "Ich gehe nach Hause. Ich gehe nach Hause. Ich gehe nach Hause."
        assert detect_sentence_repetition(s) == "Ich gehe nach Hause."

    def test_zwei_identische_saetze(self):
        s = "Hallo Welt. Hallo Welt."
        assert detect_sentence_repetition(s) == "Hallo Welt."

    def test_nur_konsekutive_duplikate_entfernen(self):
        s = "Test. Anderer Satz. Test."
        result = detect_sentence_repetition(s)
        # Test → Anderer Satz → Test (nicht konsekutiv, bleibt)
        assert result.count("Test.") == 2
        assert "Anderer Satz" in result

    def test_gemischt_konsekutiv_und_nicht(self):
        s = "A. A. B. A."
        assert detect_sentence_repetition(s) == "A. B. A."

    def test_frage_ausruf_zaehlen(self):
        s = "Bist du da? Bist du da? Geh!"
        result = detect_sentence_repetition(s)
        assert result.count("Bist du da?") == 1
        assert "Geh!" in result

    def test_case_insensitive_match(self):
        s = "Hallo Welt. hallo welt."
        # Case-normalisierter Vergleich → wird als Duplikat erkannt
        assert detect_sentence_repetition(s) == "Hallo Welt."

    def test_whitespace_normalisierung(self):
        s = "Hallo Welt.   Hallo  Welt."
        assert detect_sentence_repetition(s) == "Hallo Welt."

    def test_einzelner_satz_bleibt(self):
        s = "Nur ein Satz ohne Punkt"
        assert detect_sentence_repetition(s) == s

    def test_leerer_string(self):
        assert detect_sentence_repetition("") == ""

    def test_verschiedene_saetze_bleiben(self):
        s = "Heute war es warm. Gestern hat es geregnet. Morgen soll es schneien."
        assert detect_sentence_repetition(s) == s
