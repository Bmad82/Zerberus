"""
Patch 148 (B-022): Dialekte-Tab Umbau — strukturiertes UI statt roher JSON-Blob.

Tests:
- renderDialectGroups rendert Gruppen als einzelne Divs
- Eingabefelder "Von" → "Nach" existieren
- Neuer Eintrag wird oben hinzugefügt (nicht unten)
- Lösch-Button pro Eintrag
- Suchfunktion filtert
- saveDialectStructured schreibt Objekt zurück
- Raw-JSON-Fallback bleibt verfügbar
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hel_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "hel.py"
    return path.read_text(encoding="utf-8")


class TestDialectUi:
    def test_strukturierter_editor_existiert(self, hel_src):
        assert 'id="dialectGroups"' in hel_src
        assert 'id="dialectSearch"' in hel_src

    def test_render_funktion(self, hel_src):
        assert "function renderDialectGroups" in hel_src

    def test_save_structured_funktion(self, hel_src):
        assert "function saveDialectStructured" in hel_src
        fn_block = hel_src.split("async function saveDialectStructured")[1].split("async function ")[0]
        assert "POST" in fn_block
        assert "/hel/admin/dialect" in fn_block

    def test_add_group_funktion(self, hel_src):
        assert "function addDialectGroup" in hel_src

    def test_von_nach_placeholder(self, hel_src):
        render_block = hel_src.split("function renderDialectGroups")[1][:8000]
        assert "'Von…'" in render_block or "'Von...'" in render_block or "Von…" in render_block
        assert "'Nach…'" in render_block or "'Nach...'" in render_block or "Nach…" in render_block

    def test_delete_button_pro_eintrag(self, hel_src):
        render_block = hel_src.split("function renderDialectGroups")[1][:8000]
        assert "delete-entry" in render_block
        assert "✕" in render_block

    def test_neuer_eintrag_oben(self, hel_src):
        """Der Add-Row wird VOR den bestehenden Einträgen angehängt."""
        render_block = hel_src.split("function renderDialectGroups")[1][:8000]
        # addRow wird vor der filteredKeys.forEach hinzugefügt
        add_idx = render_block.find("appendChild(addRow)")
        foreach_idx = render_block.find("filteredKeys.forEach")
        assert add_idx > 0 and foreach_idx > 0 and add_idx < foreach_idx, \
            "addRow muss vor forEach der bestehenden Einträge appended werden"

    def test_suche_filtert(self, hel_src):
        render_block = hel_src.split("function renderDialectGroups")[1][:8000]
        assert "dialectSearch" in render_block
        assert ".toLowerCase()" in render_block
        assert ".includes(q)" in render_block

    def test_raw_json_fallback_bleibt(self, hel_src):
        """Raw-JSON-Editor bleibt als aufklappbares Details-Element."""
        assert 'id="dialectEditor"' in hel_src
        assert "<details" in hel_src or "Raw JSON" in hel_src or "Raw-JSON" in hel_src

    def test_textarea_nicht_mehr_prominent(self, hel_src):
        """rows=20 war vorher (prominent); jetzt nur noch rows=10 (versteckt in details)."""
        # rows="20" für den Editor sollte nicht mehr vorhanden sein
        editor_attrs = hel_src.split('id="dialectEditor"')[1][:200]
        assert 'rows="20"' not in editor_attrs
