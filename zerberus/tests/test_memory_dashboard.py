"""
Patch 152 (B-020): Memory-Dashboard in Hel.

Tests:
- Memory-Dashboard-Section im RAG-Tab
- Suchfeld, Kategorie-Filter, Confidence-Badges
- Manuelles Hinzufügen
- Löschen pro Zeile
- Statistik-Leiste
- Lazy-Load beim Tab-Wechsel
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hel_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "hel.py"
    return path.read_text(encoding="utf-8")


class TestMemoryDashboardUi:
    def test_sektion_existiert(self, hel_src):
        assert "Memory-Dashboard" in hel_src
        assert 'id="memoryTableHost"' in hel_src

    def test_suchfeld(self, hel_src):
        assert 'id="memorySearch"' in hel_src

    def test_kategorie_filter(self, hel_src):
        assert 'id="memoryCategoryFilter"' in hel_src
        # Alle Kategorien aus Patch 132
        for cat in ("PERSON", "PREFERENCE", "FACT", "EVENT", "SKILL", "EMOTION"):
            assert cat in hel_src

    def test_manueller_hinzufuege_block(self, hel_src):
        assert 'id="newMemorySubject"' in hel_src
        assert 'id="newMemoryFact"' in hel_src
        assert 'id="newMemoryCategory"' in hel_src
        assert "addMemoryManual" in hel_src


class TestMemoryJs:
    def test_load_funktion(self, hel_src):
        assert "async function loadMemoryDashboard" in hel_src

    def test_render_funktion(self, hel_src):
        assert "function renderMemoryTable" in hel_src

    def test_delete_funktion(self, hel_src):
        assert "async function deleteMemory" in hel_src
        fn = hel_src.split("async function deleteMemory")[1].split("async function ")[0]
        assert "DELETE" in fn
        assert "/hel/admin/memory/" in fn

    def test_add_funktion(self, hel_src):
        assert "async function addMemoryManual" in hel_src
        fn = hel_src.split("async function addMemoryManual")[1].split("async function ")[0]
        assert "/hel/admin/memory/add" in fn

    def test_confidence_badge(self, hel_src):
        assert "_confidenceBadge" in hel_src
        # 0.9 grün, 0.7 gelb, sonst rot
        fn = hel_src.split("function _confidenceBadge")[1].split("function ")[0]
        assert "0.9" in fn
        assert "0.7" in fn

    def test_statistik(self, hel_src):
        load_block = hel_src.split("async function loadMemoryDashboard")[1].split("async function ")[0]
        assert "memoryStats" in load_block
        assert "/hel/admin/memory/stats" in load_block

    def test_lazy_load_beim_tab_wechsel(self, hel_src):
        # gedaechtnis-Tab lädt jetzt auch das Memory-Dashboard
        assert "loadMemoryDashboard()" in hel_src
        # In activateTab: wenn id === 'gedaechtnis' → loadRagStatus + loadMemoryDashboard
        idx = hel_src.find("id === 'gedaechtnis'")
        assert idx > 0, "Lazy-Load-Anker für 'gedaechtnis' fehlt"
        block = hel_src[idx:idx + 200]
        assert "loadMemoryDashboard" in block
