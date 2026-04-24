"""
Patch 140 (B-003): Dark-Theme Kontrast-Fix.

Tests prüfen, dass der Auto-Kontrast-Code in nala.py vorhanden ist:
- getContrastColor-Funktion existiert
- applyAutoContrast wird bei Bubble-Farbwechsel aufgerufen
- Manuelle Text-Farbe deaktiviert Auto-Kontrast
- Init-Hook ruft Auto-Kontrast auf
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def nala_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "nala.py"
    return path.read_text(encoding="utf-8")


class TestAutoContrast:
    def test_get_contrast_color_existiert(self, nala_src):
        assert "function getContrastColor" in nala_src

    def test_apply_auto_contrast_existiert(self, nala_src):
        assert "function applyAutoContrast" in nala_src

    def test_wcag_luminanz_berechnung(self, nala_src):
        """WCAG-gewichtete Luminanz muss berechnet werden."""
        # 0.299, 0.587, 0.114 sind die WCAG-Gewichte für R/G/B
        assert "0.299" in nala_src
        assert "0.587" in nala_src
        assert "0.114" in nala_src

    def test_hell_oder_dunkel_waehlen(self, nala_src):
        """Bei heller Luminanz dunklen Text, bei dunkler hellen."""
        # Light/Dark switch: #1a1a1a (dunkel) / #f0f0f0 (hell)
        assert "#1a1a1a" in nala_src
        assert "#f0f0f0" in nala_src

    def test_bubble_preview_triggert_kontrast(self, nala_src):
        """bubblePreview() muss applyAutoContrast() aufrufen."""
        bp_block = nala_src.split("function bubblePreview()")[1].split("function ")[0]
        assert "applyAutoContrast" in bp_block

    def test_hsl_slider_triggert_kontrast(self, nala_src):
        """applyHsl() muss applyAutoContrast() aufrufen."""
        hsl_block = nala_src.split("function applyHsl(")[1].split("function ")[0]
        assert "applyAutoContrast" in hsl_block

    def test_manual_flag_beachten(self, nala_src):
        """Wenn User Text-Farbe manuell gesetzt hat, kein Override."""
        contrast_block = nala_src.split("function applyAutoContrast")[1].split("function ")[0]
        assert "manual" in contrast_block

    def test_bubble_text_preview_speichert_manual(self, nala_src):
        """bubbleTextPreview markiert localStorage 'nala_bubble_*_text_manual'."""
        btp = nala_src.split("function bubbleTextPreview(")[1].split("function ")[0]
        assert "_manual" in btp

    def test_init_ruft_auto_contrast_auf(self, nala_src):
        """showChatScreen ruft applyAutoContrast auf."""
        show_block = nala_src.split("function showChatScreen()")[1].split("function ")[0]
        assert "applyAutoContrast" in show_block
