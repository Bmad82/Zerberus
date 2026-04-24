"""
Patch 151 (B-026 / L-001): Design-System & Konsistenz-Audit.

Tests:
- shared-design.css existiert und wird von Nala + Hel geladen
- Enthält Design-Tokens (Farben, Spacing, Radius, Touch)
- DESIGN.md existiert
- Touch-Targets >= 44px im Mobile-Media-Query
"""
from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def shared_css() -> str:
    return (ROOT / "zerberus" / "static" / "css" / "shared-design.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nala_src() -> str:
    return (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def hel_src() -> str:
    return (ROOT / "zerberus" / "app" / "routers" / "hel.py").read_text(encoding="utf-8")


class TestSharedCss:
    def test_datei_existiert(self, shared_css):
        assert len(shared_css) > 500

    def test_enthaelt_farben(self, shared_css):
        assert "--zb-primary" in shared_css
        assert "--zb-danger" in shared_css
        assert "--zb-border" in shared_css

    def test_enthaelt_spacing(self, shared_css):
        assert "--zb-space-xs" in shared_css
        assert "--zb-space-md" in shared_css
        assert "--zb-space-lg" in shared_css

    def test_enthaelt_radius(self, shared_css):
        assert "--zb-radius-sm" in shared_css
        assert "--zb-radius-md" in shared_css
        assert "--zb-radius-lg" in shared_css

    def test_touch_token_44px(self, shared_css):
        assert "--zb-touch-min: 44px" in shared_css

    def test_zb_btn_klasse(self, shared_css):
        assert ".zb-btn" in shared_css

    def test_zb_select_klasse(self, shared_css):
        assert ".zb-select" in shared_css

    def test_coarse_pointer_min_height(self, shared_css):
        # Auf Mobile (coarse pointer) kriegen alle klickbaren Elemente min-height.
        assert "hover: none" in shared_css
        assert "pointer: coarse" in shared_css
        assert "var(--zb-touch-min)" in shared_css


class TestNalaLink:
    def test_nala_laedt_shared_css(self, nala_src):
        assert '/static/css/shared-design.css' in nala_src


class TestHelLink:
    def test_hel_laedt_shared_css(self, hel_src):
        assert '/static/css/shared-design.css' in hel_src


class TestDesignDoc:
    def test_design_md_existiert(self):
        assert (ROOT / "docs" / "DESIGN.md").exists()

    def test_design_md_enthaelt_regel(self):
        content = (ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8")
        assert "Leitregel" in content or "projektübergreifend" in content
        assert "44" in content
        assert "shared-design.css" in content
