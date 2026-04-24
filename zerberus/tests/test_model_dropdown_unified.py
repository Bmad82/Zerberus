"""
Patch 147 (B-019): Einheitliches Format in Nala/Vision/Huginn-Dropdowns.

Tests:
- formatModelLabel-Funktion existiert und produziert das erwartete Format
- renderModelSelect nutzt formatModelLabel
- Vision-Dropdown nutzt formatModelLabel + Sortierung nach Input-Preis
- Huginn-Dropdown nutzt formatModelLabel + Sortierung nach Input-Preis
- Kein "[Budget]"/"[Premium]"-Label mehr im Vision-Dropdown
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hel_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "hel.py"
    return path.read_text(encoding="utf-8")


class TestFormatterFunction:
    def test_formatter_existiert(self, hel_src):
        assert "function formatModelLabel" in hel_src

    def test_format_enthaelt_bindestrich(self, hel_src):
        fn_block = hel_src.split("function formatModelLabel")[1].split("function ")[0]
        # Format: "Name — $X/$Y/1M"
        assert "/1M" in fn_block
        assert "—" in fn_block or "-" in fn_block


class TestNalaModelDropdown:
    def test_render_nutzt_formatter(self, hel_src):
        fn_block = hel_src.split("function renderModelSelect")[1].split("function ")[0]
        assert "formatModelLabel" in fn_block

    def test_default_sort_nach_preis(self, hel_src):
        fn_block = hel_src.split("function renderModelSelect")[1].split("function ")[0]
        # Sortierung nach prompt-Preis
        assert "pricing?.prompt" in fn_block
        assert "Patch 147" in fn_block


class TestVisionDropdown:
    def test_vision_nutzt_formatter(self, hel_src):
        vr_block = hel_src.split("async function visionReload")[1].split("async function ")[0]
        assert "formatModelLabel" in vr_block

    def test_vision_sort_nach_input_price(self, hel_src):
        vr_block = hel_src.split("async function visionReload")[1].split("async function ")[0]
        assert "input_price" in vr_block
        assert ".sort(" in vr_block

    def test_kein_tier_label_mehr_in_text(self, hel_src):
        """Tier bleibt als data-attribute, aber nicht im sichtbaren Text."""
        vr_block = hel_src.split("async function visionReload")[1].split("async function ")[0]
        # Kein "[${m.tier}]" im sichtbaren Label (Textcontent)
        assert "[${m.tier}]" not in vr_block
        # data-tier als Attribut ist OK
        assert 'data-tier="${m.tier}"' in vr_block


class TestHuginnDropdown:
    def test_huginn_nutzt_formatter(self, hel_src):
        hr_block = hel_src.split("async function huginnReload")[1].split("async function ")[0]
        assert "formatModelLabel" in hr_block

    def test_huginn_sort_nach_preis(self, hel_src):
        hr_block = hel_src.split("async function huginnReload")[1].split("async function ")[0]
        assert ".sort(" in hr_block
        assert "pricing?.prompt" in hr_block
