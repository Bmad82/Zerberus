"""Patch 165 — Tests fuer ``zerberus.modules.rag.language_detector``.

Deckt die DE/EN-Erkennung fuer RAG-Dokumente (P126) ab:
- Code-Token-Filter (def/class/import) verhindert .py→EN-Fehlklassifikation
- Umlaut-Boost (+3) bevorzugt DE bei Misch-Inhalten
- Default-Fallback DE bei zu wenig Signal
- ``_strip_wrappers`` entfernt YAML-Frontmatter
"""
from __future__ import annotations

from zerberus.modules.rag.language_detector import (
    _strip_wrappers,
    detect_language,
    language_confidence,
)


# ----- detect_language ------------------------------------------------------


class TestDetectLanguageGerman:
    def test_clear_german_text(self):
        text = "Das ist ein deutscher Text mit vielen Wörtern und Umlauten."
        assert detect_language(text) == "de"

    def test_german_without_umlauts(self):
        text = "Das ist ein deutscher Text mit Woertern und nicht und der und ist."
        assert detect_language(text) == "de"

    def test_umlaut_boost_tips_balance(self):
        # Mischtext mit 1-2 Markern jeweils — Umlaut-Boost (+3) entscheidet.
        text = "Das is the and über"
        assert detect_language(text) == "de"


class TestDetectLanguageEnglish:
    def test_clear_english_text(self):
        text = "This is an English text with many words and the and is and that."
        assert detect_language(text) == "en"


class TestDetectLanguageEdgeCases:
    def test_empty_string_defaults_to_german(self):
        assert detect_language("") == "de"

    def test_whitespace_only_defaults_to_german(self):
        assert detect_language("    \n\n  ") == "de"

    def test_too_few_tokens_defaults_to_german(self):
        # < 5 ungefilterte Tokens → Default DE.
        assert detect_language("ok yes") == "de"

    def test_code_only_defaults_to_german(self):
        # Reiner Python-Code: Tokens werden gefiltert, < 5 → DE-Default.
        text = "def foo(): return None\nclass Bar: pass"
        assert detect_language(text) == "de"

    def test_code_with_german_strings_detects_german(self):
        text = (
            "def hallo():\n"
            "    return 'Das ist ein deutscher Kommentar mit der und ist'\n"
        )
        assert detect_language(text) == "de"

    def test_sample_chars_truncates_long_input(self):
        # Erste 10 Zeichen sind DE, der Rest EN — sample_chars=10 macht das DE.
        text = "der die das" + " the and is" * 50
        assert detect_language(text, sample_chars=11) == "de"


# ----- language_confidence --------------------------------------------------


class TestLanguageConfidence:
    def test_returns_dict_with_scores(self):
        result = language_confidence("der die das und ist")
        assert result["language"] == "de"
        assert result["de_score"] >= 5
        assert result["tokens"] >= 5

    def test_empty_returns_zero_scores(self):
        result = language_confidence("")
        assert result == {"language": "de", "de_score": 0, "en_score": 0, "tokens": 0}

    def test_english_text_scores_higher_en(self):
        result = language_confidence("the and is of to in that with for")
        assert result["language"] == "en"
        assert result["en_score"] > result["de_score"]


# ----- _strip_wrappers ------------------------------------------------------


class TestStripWrappers:
    def test_yaml_frontmatter_removed(self):
        text = "---\ntitle: Foo\nlang: de\n---\nDer eigentliche Text"
        stripped = _strip_wrappers(text)
        assert "title:" not in stripped
        assert stripped.startswith("Der eigentliche Text")

    def test_no_frontmatter_unchanged(self):
        text = "Plain content without frontmatter"
        assert _strip_wrappers(text) == text

    def test_empty_returns_empty(self):
        assert _strip_wrappers("") == ""

    def test_only_first_frontmatter_block_stripped(self):
        # Die Regex ist auf count=1 limitiert.
        text = "---\nfoo: 1\n---\nBody\n---\nbar: 2\n---\nMore"
        stripped = _strip_wrappers(text)
        assert "foo:" not in stripped
        # Zweiter Block bleibt drin.
        assert "bar:" in stripped
