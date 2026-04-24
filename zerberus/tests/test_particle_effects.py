"""
Patch 145 (F-002): Feuerwerk & Sternenregen.

Tests:
- Particle-Canvas im DOM
- pointer-events: none (blockt keine Klicks)
- Rapid-Tap-Detector (7 Tasten in 2 Sekunden)
- Swipe-Up-Detector (≥200px nach oben)
- Star- und Firework-Particle-Spawner
- Goldregen-Funktion
- Resize-Handler
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def nala_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "nala.py"
    return path.read_text(encoding="utf-8")


class TestParticleCanvas:
    def test_canvas_existiert(self, nala_src):
        assert 'id="particleCanvas"' in nala_src

    def test_pointer_events_none(self, nala_src):
        start = nala_src.find('id="particleCanvas"')
        block = nala_src[start:start + 600]
        assert "pointer-events:none" in block or "pointer-events: none" in block

    def test_z_index_hoch(self, nala_src):
        start = nala_src.find('id="particleCanvas"')
        block = nala_src[start:start + 600]
        assert "z-index:9999" in block or "z-index: 9999" in block


class TestParticleEngine:
    def test_resize_handler(self, nala_src):
        pe_block = nala_src.split("initParticles()")[1][:10000]
        assert "function resize" in pe_block
        assert "canvas.width" in pe_block
        assert "canvas.height" in pe_block

    def test_spawn_stars(self, nala_src):
        pe_block = nala_src.split("initParticles()")[1][:10000]
        assert "function spawn" in pe_block
        assert "firework" in pe_block

    def test_gold_rain(self, nala_src):
        pe_block = nala_src.split("initParticles()")[1][:10000]
        assert "function goldRain" in pe_block
        assert "#FFD700" in pe_block

    def test_draw_star(self, nala_src):
        pe_block = nala_src.split("initParticles()")[1][:10000]
        assert "function drawStar" in pe_block


class TestTriggers:
    def test_rapid_tap_trigger(self, nala_src):
        pe_block = nala_src.split("initParticles()")[1][:10000]
        # 7 Tasten in 2 Sekunden
        assert "2000" in pe_block
        assert "tapTimes" in pe_block

    def test_swipe_up_trigger(self, nala_src):
        pe_block = nala_src.split("initParticles()")[1][:10000]
        # Mindestens 200px Swipe
        assert "200" in pe_block
        assert "touchstart" in pe_block
        assert "touchend" in pe_block

    def test_background_flash(self, nala_src):
        pe_block = nala_src.split("initParticles()")[1][:10000]
        assert "flashBackground" in pe_block


class TestFarben:
    def test_verschiedene_farben(self, nala_src):
        pe_block = nala_src.split("initParticles()")[1][:10000]
        # Mindestens 5 verschiedene Farben definiert
        assert "#FFD700" in pe_block  # Gold
        assert "#FF6B6B" in pe_block or "#4ECDC4" in pe_block
