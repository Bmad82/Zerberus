"""
Patch 139: Nala Bubble-Layout-Fixes (B-005, B-008, B-009, B-010, B-011).

Tests inspect the HTML/CSS/JS emitted by zerberus.app.routers.nala, ohne
Browser/Playwright. Wir prüfen, dass die Style-/Script-Blocks die richtigen
Regeln enthalten.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def nala_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "nala.py"
    return path.read_text(encoding="utf-8")


class TestShine:
    def test_radial_gradient_statt_linear(self, nala_src):
        """B-005: Shine ist radial-gradient, nicht linear-gradient."""
        assert "radial-gradient" in nala_src
        # im :before-Block muss radial-gradient statt linear-gradient stehen
        before_block = nala_src.split(".message::before")[1].split("}")[0]
        assert "radial-gradient" in before_block, "Shine-::before nutzt noch linear-gradient"

    def test_shine_oben_links(self, nala_src):
        """B-005: Lichtquelle in der Ecke (ellipse at 20% 20% oder ähnlich)."""
        assert "ellipse at 20% 20%" in nala_src


class TestBubbleBreite:
    def test_bubble_max_width_mobile(self, nala_src):
        """B-008: Bubbles max-width ≥ 90% auf Mobile."""
        assert "max-width: 92%" in nala_src, "Bubbles sollten mindestens 92% auf Mobile haben"

    def test_msg_wrapper_max_width(self, nala_src):
        """B-008: msg-wrapper (Action-Container) ebenfalls breit."""
        wrapper_block = nala_src.split(".msg-wrapper {")[1].split("}")[0]
        assert "92%" in wrapper_block


class TestActionButtons:
    def test_toolbar_initial_opacity_0(self, nala_src):
        """B-009: msg-toolbar startet mit opacity: 0."""
        toolbar_block = nala_src.split(".msg-toolbar {")[1].split("}")[0]
        assert "opacity: 0" in toolbar_block

    def test_pointer_events_none_wenn_unsichtbar(self, nala_src):
        """B-009: Toolbar ohne pointer-events wenn unsichtbar (blockt keine Klicks)."""
        toolbar_block = nala_src.split(".msg-toolbar {")[1].split("}")[0]
        assert "pointer-events: none" in toolbar_block

    def test_actions_visible_klasse_macht_sichtbar(self, nala_src):
        """B-009: .actions-visible Klasse schaltet Toolbar ein."""
        assert ".actions-visible" in nala_src or "actions-visible" in nala_src

    def test_attach_action_toggle_funktion_existiert(self, nala_src):
        """JS-Funktion für Tap-Toggle muss existieren."""
        assert "attachActionToggle" in nala_src

    def test_auto_hide_nach_5_sekunden(self, nala_src):
        """B-009: Nach 5000ms automatisch wieder ausblenden."""
        assert "5000" in nala_src

    def test_attach_wird_in_addmessage_aufgerufen(self, nala_src):
        addmsg_block = nala_src.split("function addMessage(")[1][:5000]
        assert "attachActionToggle(" in addmsg_block


class TestRepeatButton:
    def test_retry_btn_transparent(self, nala_src):
        """B-010: Repeat/Retry-Button hat transparenten Hintergrund."""
        # In der CSS steht ein Block für .retry-btn / [data-action="retry"]
        assert "retry-btn" in nala_src
        # Transparent ist gesetzt
        assert "background: transparent !important" in nala_src

    def test_retry_btn_klasse_im_js(self, nala_src):
        """Der Button muss die retry-btn Klasse bekommen."""
        assert "retry-btn" in nala_src
        # Plus dataset.action
        assert "dataset.action = 'retry'" in nala_src


class TestTitelzeile:
    def test_title_font_size_kleiner(self, nala_src):
        """B-011: .title hat font-size < 1em."""
        # Das .title block in der CSS
        title_blocks = [b for b in nala_src.split(".title {")[1:] if "flex:" in b[:200]]
        assert title_blocks, "kein .title Block gefunden"
        block = title_blocks[0].split("}")[0]
        assert "font-size:" in block
        # Muss < 1em sein (z.B. 0.95em)
        import re
        m = re.search(r"font-size:\s*(0\.\d+)em", block)
        assert m, f"font-size nicht kleiner als 1em: {block}"

    def test_header_font_size_kleiner(self, nala_src):
        """B-011: .header Gesamt-font-size unter 1.5em (war 1.5em)."""
        header_block = nala_src.split(".header {")[1].split("}")[0]
        assert "font-size: 1.05em" in header_block or "font-size: 1em" in header_block


class TestTouchTargets:
    def test_action_buttons_min_44px_mobile(self, nala_src):
        """Touch-Targets ≥ 44px auf Mobile."""
        # In der @media-Regel für coarse-pointer
        assert "min-width: 44px" in nala_src
        assert "min-height: 44px" in nala_src
