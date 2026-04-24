"""
Patch 144 (B-007 / F-001): Katzenpfoten-Indikator.

Tests:
- Pfoten-HTML im chat-screen
- CSS-Animation (pawWalk)
- JS-Funktionen showTypingIndicator/removeTypingIndicator benutzen _showPaws/_hidePaws
- setPawStatus mappt Backend-Events auf Status-Texte
- SSE-Handler ruft setPawStatus auf
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def nala_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "nala.py"
    return path.read_text(encoding="utf-8")


class TestPawsDom:
    def test_paw_indicator_div_existiert(self, nala_src):
        assert 'id="pawIndicator"' in nala_src
        assert 'id="pawStatus"' in nala_src

    def test_vier_pfoten(self, nala_src):
        # Zählen: mindestens 4 🐾 im paw-indicator Block
        start = nala_src.find('id="pawIndicator"')
        end = nala_src.find("</div>", start)
        block = nala_src[start:end]
        assert block.count("🐾") >= 4


class TestPawsCss:
    def test_paw_walk_keyframe(self, nala_src):
        assert "@keyframes pawWalk" in nala_src

    def test_animation_3s(self, nala_src):
        assert "pawWalk 3s" in nala_src

    def test_paw_print_klasse(self, nala_src):
        assert ".paw-print" in nala_src

    def test_position_fixed_ueber_input(self, nala_src):
        paw_block = nala_src.split(".paw-indicator {")[1].split("}")[0]
        assert "position: fixed" in paw_block
        assert "bottom:" in paw_block


class TestPawsJs:
    def test_show_paws_funktion(self, nala_src):
        assert "function _showPaws" in nala_src
        assert "function _hidePaws" in nala_src

    def test_show_typing_indicator_nutzt_paws(self, nala_src):
        sti_block = nala_src.split("function showTypingIndicator()")[1].split("function ")[0]
        assert "_showPaws" in sti_block

    def test_remove_typing_indicator_versteckt_paws(self, nala_src):
        rti_block = nala_src.split("function removeTypingIndicator()")[1].split("function ")[0]
        assert "_hidePaws" in rti_block

    def test_set_paw_status_mapping(self, nala_src):
        assert "function setPawStatus" in nala_src
        fn_block = nala_src.split("function setPawStatus(")[1].split("function ")[0]
        assert "rag_search" in fn_block
        assert "llm_start" in fn_block
        assert "generating" in fn_block

    def test_done_versteckt_paws(self, nala_src):
        # SSE-Handler: bei done → _hidePaws
        sse_block = nala_src.split("evtSource.onmessage")[1][:2000]
        assert "_hidePaws" in sse_block or "hidePaws" in sse_block

    def test_sse_ruft_set_paw_status(self, nala_src):
        sse_block = nala_src.split("evtSource.onmessage")[1][:2000]
        assert "setPawStatus" in sse_block


class TestAlterIndikatorWeg:
    def test_kein_fester_antwort_wird_generiert_block(self, nala_src):
        """Der alte bubble-indicator wird nicht mehr beim showTypingIndicator-Aufruf angelegt."""
        sti_block = nala_src.split("function showTypingIndicator()")[1].split("function ")[0]
        # Der Text "Antwort wird generiert…" darf im showTypingIndicator NICHT mehr kommen
        assert "Antwort wird generiert" not in sti_block
