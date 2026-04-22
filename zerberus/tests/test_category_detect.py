"""
Patch 111 — Auto-Category-Detection (Upload) und Query-Category-Detection.
"""
from __future__ import annotations

from zerberus.app.routers.hel import _detect_category
from zerberus.modules.rag.category_router import detect_query_category


class TestDetectCategoryUpload:
    """`_detect_category` entscheidet bei 'auto'/'general' anhand der Extension."""

    def test_auto_py_file_becomes_technical(self):
        cat, auto = _detect_category("script.py", "auto")
        assert cat == "technical"
        assert auto is True

    def test_auto_csv_becomes_reference(self):
        cat, auto = _detect_category("data.csv", "auto")
        assert cat == "reference"
        assert auto is True

    def test_auto_pdf_becomes_general(self):
        cat, auto = _detect_category("book.pdf", "auto")
        assert cat == "general"
        assert auto is True

    def test_auto_unknown_extension_becomes_general(self):
        cat, auto = _detect_category("weird.xyz", "auto")
        assert cat == "general"
        assert auto is True

    def test_user_override_is_respected(self):
        # Auch wenn .py auf 'technical' mappen würde, User-Wahl 'narrative' gewinnt
        cat, auto = _detect_category("story.py", "narrative")
        assert cat == "narrative"
        assert auto is False

    def test_general_triggers_detection(self):
        # Patch 111: "general" wird als Detection-Wunsch interpretiert
        cat, auto = _detect_category("config.yaml", "general")
        assert cat == "technical"
        assert auto is True

    def test_case_insensitive_extension(self):
        cat, auto = _detect_category("UPPER.JSON", "auto")
        assert cat == "technical"
        assert auto is True


class TestDetectQueryCategory:
    """Keyword-basierte Query-Classification."""

    def test_technical_query(self):
        assert detect_query_category("Wie schreibe ich eine Python-Funktion?") == "technical"

    def test_narrative_query(self):
        assert detect_query_category("Was passiert in Kapitel 3?") == "narrative"

    def test_lore_query(self):
        assert detect_query_category("Wie funktioniert die Magie in dieser Welt?") == "lore"

    def test_personal_query(self):
        assert detect_query_category("Was habe ich gestern in mein Tagebuch geschrieben?") == "personal"

    def test_reference_query(self):
        assert detect_query_category("Gib mir eine Tabelle mit allen Parametern.") == "reference"

    def test_empty_query_returns_none(self):
        assert detect_query_category("") is None

    def test_no_keyword_match_returns_none(self):
        assert detect_query_category("Hallo, wie geht es dir?") is None
