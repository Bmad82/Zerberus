"""
Patch 102 (B-01): Tests für Whisper Phrasen-Repetition-Filter.
Patch 113b (W-001b): Tests für Satz-Repetition-Filter.
Patch 120 (W-001b Erweiterung): Tests für Long-Subsequence-Filter.
"""
import pytest
from zerberus.core.cleaner import (
    detect_phrase_repetition,
    detect_sentence_repetition,
    detect_long_subsequence_repetition,
)


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


class TestLongSubsequenceRepetition:
    """Patch 120 — lange Subsequenz-Loops ohne Interpunktion."""

    def test_mittagspause_beispiel(self):
        """Konkreter Bug aus Patch 120: 17-Woerter-Satz x3, ohne Punkte."""
        block = (
            "in der mittagspause wenn ich nach hause fahre wo ich frueh "
            "mitgekriegt habe oh was ist das neu cool"
        )
        s = f"{block} {block} {block}"
        result = detect_long_subsequence_repetition(s)
        # Nur eine vollstaendige Kopie darf uebrig bleiben
        assert result == block
        # Block-Laenge bleibt erhalten (19 Woerter)
        assert len(result.split()) == 19

    def test_drei_kopien_mit_rest(self):
        """Nach den Wiederholungen kommt noch Rest-Text — der bleibt erhalten."""
        block = "a b c d e f g h i"
        tail = "j k"
        s = f"{block} {block} {block} {tail}"
        result = detect_long_subsequence_repetition(s)
        assert result == f"{block} {tail}"

    def test_nur_zwei_kopien(self):
        """Zwei Kopien reichen — alle bis auf eine entfernen."""
        block = "eins zwei drei vier fuenf sechs sieben acht"
        s = f"{block} {block}"
        result = detect_long_subsequence_repetition(s)
        assert result == block

    def test_unter_min_len_bleibt(self):
        """Wiederholungen unter 8 Woertern gehen an Phrase-Filter, nicht hier."""
        s = "a b c a b c a b c"
        # min_len=8, also keine Verkuerzung
        assert detect_long_subsequence_repetition(s) == s

    def test_kein_loop_bleibt_unveraendert(self):
        s = "das ist ein ganz normaler satz ohne jede wiederholung von phrasen oder saetzen"
        assert detect_long_subsequence_repetition(s) == s

    def test_leerer_string(self):
        assert detect_long_subsequence_repetition("") == ""

    def test_single_word(self):
        assert detect_long_subsequence_repetition("hallo") == "hallo"

    def test_custom_min_len(self):
        """min_len=4 erlaubt kuerzere Loops."""
        block = "a b c d"
        s = f"{block} {block} {block}"
        result = detect_long_subsequence_repetition(s, min_len=4)
        assert result == block
