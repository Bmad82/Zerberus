"""
Patch 142: Settings-Umbau (B-006, B-012, B-013, B-015, B-016).

Tests:
- Schraubenschlüssel-Icon 🔧 nicht mehr in Top-Bar (B-006)
- Zahnrad ⚙️ im Sidebar-Footer (B-013)
- Abmelden-Icon im Sidebar-Footer, NICHT neben "Neue Session" (B-013)
- Passwort-Button NICHT in Sidebar, sondern in Settings (B-013)
- "Mein Ton" in Settings Tab "Ausdruck", NICHT in Sidebar (B-012)
- 3 Tabs: Aussehen, Ausdruck, System (B-015)
- UI-Skalierung-Slider (B-016)
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def nala_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "nala.py"
    return path.read_text(encoding="utf-8")


def _find_main_header_html_block(src: str) -> str:
    """Findet den HTML-Block ab '<div class="header" id="main-header">' bis zu seinem </div>."""
    marker = '<div class="header" id="main-header">'
    start = src.find(marker)
    assert start > 0, "main-header Marker nicht gefunden"
    # Wir suchen das nächste "<!-- Status-Bar" als Ende — das kommt direkt nach dem header schließt.
    end = src.find("Status-Bar", start)
    return src[start:end]


def _find_sidebar_html_block(src: str) -> str:
    """Findet den HTML-Block des Sidebar-Divs (Patch 142 Kommentar als Anker)."""
    marker = "Patch 142 (B-013)"
    start = src.find(marker)
    assert start > 0, "Patch 142 (B-013) Sidebar-Anker fehlt"
    # Ende: <div class="overlay"
    end = src.find('class="overlay"', start)
    return src[start:end]


def _find_sidebar_actions_html(src: str) -> str:
    """Findet das HTML-Element mit class='sidebar-actions'."""
    sidebar = _find_sidebar_html_block(src)
    marker = 'class="sidebar-actions"'
    start = sidebar.find(marker)
    assert start > 0, "sidebar-actions Marker fehlt"
    end = sidebar.find("</div>", start)
    return sidebar[start:end]


class TestSchraubenschluesselWeg:
    def test_schraubenschluessel_nicht_in_topbar(self, nala_src):
        """B-006: 🔧-Button im main-header existiert nicht mehr."""
        header = _find_main_header_html_block(nala_src)
        # In der Top-Bar existiert kein openSettingsModal() mehr
        assert "openSettingsModal()" not in header
        # Export-Button ist noch da
        assert "openExportMenu()" in header


class TestSidebarFooter:
    def test_sidebar_footer_klasse_existiert(self, nala_src):
        assert "sidebar-footer" in nala_src

    def test_abmelden_im_footer(self, nala_src):
        sidebar = _find_sidebar_html_block(nala_src)
        # Footer enthält doLogout()
        footer_start = sidebar.find("sidebar-footer")
        assert footer_start > 0
        footer_block = sidebar[footer_start:]
        assert "doLogout()" in footer_block

    def test_settings_cog_im_footer(self, nala_src):
        sidebar = _find_sidebar_html_block(nala_src)
        footer_start = sidebar.find("sidebar-footer")
        footer_block = sidebar[footer_start:]
        assert "⚙" in footer_block or "sidebar-footer-cog" in footer_block
        assert "openSettingsModal()" in footer_block

    def test_abmelden_nicht_neben_neue_session(self, nala_src):
        """B-013: Abmelden steht NICHT mehr in sidebar-actions."""
        actions = _find_sidebar_actions_html(nala_src)
        assert "doLogout()" not in actions
        assert "newSession()" in actions


class TestKeinPasswordInSidebar:
    def test_kein_passwort_btn_in_sidebar(self, nala_src):
        """B-013: Passwort-Button gehört nicht mehr in sidebar-actions."""
        actions = _find_sidebar_actions_html(nala_src)
        assert "openPwModal()" not in actions
        assert "Passwort" not in actions


class TestMeinTonInSettings:
    def test_mein_ton_in_settings_tab_voice(self, nala_src):
        """B-012: 'Mein Ton' ist im Settings-Tab 'voice', nicht in Sidebar."""
        # Textarea id="my-prompt-area" muss IM settings-tab-voice Block sein
        voice_start = nala_src.find('id="settings-tab-voice"')
        assert voice_start > 0
        voice_block = nala_src[voice_start:voice_start + 3000]
        assert 'id="my-prompt-area"' in voice_block
        assert 'saveMyPrompt()' in voice_block

    def test_mein_ton_nicht_mehr_in_sidebar(self, nala_src):
        """Die alte Sidebar hat keinen my-prompt-area mehr außerhalb der Settings."""
        # my-prompt-area darf nicht mehr im sidebar-Block stehen
        sidebar_start = nala_src.find('<div class="sidebar"')
        sidebar_end = nala_src.find("</div>\n        <div class=\"overlay\"", sidebar_start)
        if sidebar_end == -1:
            sidebar_end = nala_src.find("overlay", sidebar_start)
        sidebar_block = nala_src[sidebar_start:sidebar_end]
        assert 'id="my-prompt-area"' not in sidebar_block


class TestSettingsTabs:
    def test_drei_tabs(self, nala_src):
        """B-015: 3 Tabs Aussehen/Ausdruck/System."""
        # HTML-Block der Tab-Buttons, nicht die CSS-Definition
        marker = '<div class="settings-tabs">'
        start = nala_src.find(marker)
        assert start > 0
        tab_block = nala_src[start:start + 1500]
        assert 'data-tab="look"' in tab_block
        assert 'data-tab="voice"' in tab_block
        assert 'data-tab="system"' in tab_block
        assert "Aussehen" in tab_block
        assert "Ausdruck" in tab_block
        assert "System" in tab_block

    def test_tab_panels_existieren(self, nala_src):
        assert 'id="settings-tab-look"' in nala_src
        assert 'id="settings-tab-voice"' in nala_src
        assert 'id="settings-tab-system"' in nala_src

    def test_switch_tab_funktion(self, nala_src):
        assert "function switchSettingsTab" in nala_src


class TestUiSkalierung:
    def test_ui_scale_slider_existiert(self, nala_src):
        assert 'id="ui-scale-slider"' in nala_src
        assert 'applyUiScale' in nala_src

    def test_ui_scale_range(self, nala_src):
        """Range 0.8 – 1.4, Schritt 0.05."""
        slider_block = nala_src.split('id="ui-scale-slider"')[1][:500]
        assert 'min="0.8"' in slider_block
        assert 'max="1.4"' in slider_block
        assert 'step="0.05"' in slider_block

    def test_apply_ui_scale_funktion(self, nala_src):
        assert "function applyUiScale" in nala_src
        fn_block = nala_src.split("function applyUiScale(")[1].split("function ")[0]
        # Setzt --ui-scale
        assert "--ui-scale" in fn_block
        # Speichert in localStorage
        assert "localStorage" in fn_block

    def test_css_variable_ui_scale(self, nala_src):
        assert "--ui-scale" in nala_src

    def test_reset_ui_scale_funktion(self, nala_src):
        assert "function resetUiScale" in nala_src

    def test_restore_ui_scale_beim_laden(self, nala_src):
        """Skalierung überlebt Page-Reload."""
        assert "restoreUiScale" in nala_src


class TestPasswortInSystemTab:
    def test_passwort_in_system_tab(self, nala_src):
        system_start = nala_src.find('id="settings-tab-system"')
        assert system_start > 0
        system_block = nala_src[system_start:system_start + 2000]
        assert "openPwModal()" in system_block
        assert "Passwort" in system_block


class TestAccountInfo:
    def test_account_info_existiert(self, nala_src):
        system_start = nala_src.find('id="settings-tab-system"')
        system_block = nala_src[system_start:system_start + 2000]
        assert "account-profile-name" in system_block
        assert "account-permission" in system_block
