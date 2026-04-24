"""
Patch 149: Hel Kleinigkeiten (B-021, B-023, B-025).

Tests:
- WhisperCleaner-Regeln UI deaktiviert, Pflege über Config
- Sysctl-Tab umbenannt zu "System"
- Hel hat eigene Settings mit Schrift-/UI-Scale-Slider
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hel_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "hel.py"
    return path.read_text(encoding="utf-8")


class TestWhisperCleanerUiDeaktiviert:
    def test_kein_regel_editor_mehr(self, hel_src):
        """B-021: id="cleanerList" ist nicht mehr im HTML des cleaner-sections."""
        body = hel_src.split('id="body-cleaner"')[1][:3000]
        assert 'id="cleanerList"' not in body

    def test_keine_add_regel_buttons(self, hel_src):
        body = hel_src.split('id="body-cleaner"')[1][:3000]
        assert "addCleanerRule()" not in body
        assert "addCleanerComment()" not in body

    def test_hinweis_auf_config_datei(self, hel_src):
        body = hel_src.split('id="body-cleaner"')[1][:3000]
        assert "whisper_cleaner.json" in body
        assert "Patch 149" in body or "UI deaktiviert" in body


class TestSysctlUmbenannt:
    def test_tab_heisst_system(self, hel_src):
        tab_line = [l for l in hel_src.splitlines() if 'data-tab="sysctl"' in l and 'hel-tab' in l]
        assert tab_line, "sysctl-Tab-Button nicht gefunden"
        # Text ist "System" (oder enthält es); nicht mehr "Sysctl"
        assert "System" in tab_line[0]
        assert "Sysctl" not in tab_line[0]


class TestHelEigeneSettings:
    def test_zahnrad_button(self, hel_src):
        assert 'id="helSettingsBtn"' in hel_src
        assert "toggleHelSettings()" in hel_src

    def test_hel_settings_panel(self, hel_src):
        assert 'id="helSettingsPanel"' in hel_src
        assert 'id="helUiScaleSlider"' in hel_src

    def test_slider_range(self, hel_src):
        """0.8 – 1.4 wie in Nala."""
        slider_block = hel_src.split('id="helUiScaleSlider"')[1][:500]
        assert 'min="0.8"' in slider_block
        assert 'max="1.4"' in slider_block

    def test_apply_hel_ui_scale_funktion(self, hel_src):
        assert "function applyHelUiScale" in hel_src
        fn = hel_src.split("function applyHelUiScale")[1].split("function ")[0]
        assert "--ui-scale" in fn
        assert "hel_font_size-base" in fn or "hel-font-size-base" in fn

    def test_restore_beim_laden(self, hel_src):
        assert "restoreHelUiScale" in hel_src

    def test_reset_funktion(self, hel_src):
        assert "function resetHelUiScale" in hel_src
