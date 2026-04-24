"""
Patch 141 (B-002): Session-Liste — Fallback-Titel und Timestamp-Untertitel.

Tests:
- buildSessionItem fällt auf "Unbenannte Session" zurück, wenn first_message leer
- Titel wird auf ~50 Zeichen gekürzt
- Untertitel enthält Datum + Uhrzeit (HH:MM)
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def nala_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "nala.py"
    return path.read_text(encoding="utf-8")


def test_fallback_title_unbenannte_session(nala_src):
    build_block = nala_src.split("function buildSessionItem(")[1].split("function ")[0]
    assert "Unbenannte Session" in build_block


def test_laenge_50_zeichen(nala_src):
    """Kürzung bei 50 Zeichen, nicht 40."""
    build_block = nala_src.split("function buildSessionItem(")[1].split("function ")[0]
    assert "> 50" in build_block or ".slice(0, 50)" in build_block


def test_untertitel_mit_uhrzeit(nala_src):
    build_block = nala_src.split("function buildSessionItem(")[1].split("function ")[0]
    assert "toLocaleTimeString" in build_block


def test_datum_plus_zeit_als_ts(nala_src):
    build_block = nala_src.split("function buildSessionItem(")[1].split("function ")[0]
    # Kombiniert Datum und Uhrzeit in die ts-Variable
    assert "dateStr" in build_block and "timeStr" in build_block
