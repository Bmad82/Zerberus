"""
Patch 125 - Tests fuer den Bibel-Fibel Prompt-Kompressor.
"""
import pytest

from zerberus.utils.prompt_compressor import compress_prompt, compression_stats


class TestCompressBasics:
    def test_empty_input_returns_empty(self):
        assert compress_prompt("") == ""
        assert compress_prompt("   \n\n") == ""

    def test_result_shorter_or_equal(self):
        original = (
            "Der User ist der Chef. Bitte beachte, dass der Server der Produktion dient. "
            "Du musst sicherstellen, dass die Datenbank immer erreichbar ist."
        )
        compressed = compress_prompt(original)
        assert len(compressed) <= len(original)

    def test_articles_get_removed(self):
        original = "Der Server ist die Zentrale."
        compressed = compress_prompt(original)
        assert "der" not in compressed.lower().split()
        assert "die" not in compressed.lower().split()

    def test_stopwords_get_removed(self):
        original = "Bitte beachte, dann also ja die Regel."
        compressed = compress_prompt(original)
        lower_words = compressed.lower().split()
        for word in ("bitte", "dann", "also", "ja"):
            assert word not in lower_words


class TestVerbCompression:
    def test_du_musst_sicherstellen(self):
        original = "Du musst sicherstellen, dass Logs rotieren."
        compressed = compress_prompt(original)
        assert "Sicherstellen:" in compressed

    def test_du_sollst(self):
        original = "Du sollst niemals die Datenbank löschen."
        compressed = compress_prompt(original)
        assert "Soll:" in compressed

    def test_bitte_beachte(self):
        original = "Bitte beachte, dass der Token vertraulich ist."
        compressed = compress_prompt(original)
        assert "Beachte:" in compressed


class TestListToPipes:
    def test_comma_list_becomes_pipes(self):
        original = "Unterstuetzt werden Python, JavaScript, TypeScript und Rust."
        compressed = compress_prompt(original)
        # Mindestens ein Pipe sollte erscheinen
        assert "|" in compressed

    def test_enumeration_markers_removed(self):
        original = "Erstens Log, zweitens Backup, drittens Alert."
        compressed = compress_prompt(original)
        assert "erstens" not in compressed.lower()
        assert "zweitens" not in compressed.lower()


class TestSentimentPreservation:
    def test_preserve_nala_marker(self):
        original = "Du bist Nala und warm und liebevoll."
        compressed = compress_prompt(original, preserve_sentiment=True)
        assert "Nala" in compressed or "nala" in compressed
        assert "warm" in compressed.lower()
        assert "liebevoll" in compressed.lower()


class TestRedundancy:
    def test_consecutive_duplicates_removed(self):
        original = "Der Token ist geheim. Der Token ist geheim. Prüfe das."
        compressed = compress_prompt(original)
        # "Token" sollte nur einmal auftauchen (Duplikat entfernt)
        assert compressed.lower().count("token") == 1


class TestIdempotence:
    def test_double_compression_stable(self):
        original = "Bitte beachte, dass du sicherstellen musst dass das System laeuft."
        first = compress_prompt(original)
        second = compress_prompt(first)
        # Zweifach-Kompression darf nicht viel mehr rausnehmen
        assert abs(len(first) - len(second)) <= 5 or len(second) >= len(first) * 0.8


class TestStats:
    def test_stats_zero_original(self):
        s = compression_stats("", "")
        assert s["saved_pct"] == 0.0

    def test_stats_positive_savings(self):
        s = compression_stats("bitte der ein test", "test")
        assert s["saved_chars"] > 0
        assert s["saved_pct"] > 0
        assert s["estimated_tokens_saved"] >= 0
