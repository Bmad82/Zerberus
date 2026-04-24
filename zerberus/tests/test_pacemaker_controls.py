"""
Patch 150 (B-024): Pacemaker-Prozess-Steuerung.

Tests:
- UI-Sektion für Pacemaker-Prozesse in Hel
- Master + Sync-Toggle
- Pro Prozess: Name, Aktiv-Checkbox, Status, Intervall-Slider, Device-Select
- Backend-Endpoints (GET/POST /hel/admin/pacemaker/processes)
- Default-Prozesse: sentiment, memory, db_dedup, whisper_ping
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hel_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "hel.py"
    return path.read_text(encoding="utf-8")


class TestPacemakerUi:
    def test_sektion_existiert(self, hel_src):
        assert "Pacemaker-Prozesse" in hel_src
        assert 'id="pacemakerProcesses"' in hel_src

    def test_master_checkbox(self, hel_src):
        assert 'id="pacemaker-master"' in hel_src

    def test_sync_checkbox(self, hel_src):
        assert 'id="pacemaker-sync"' in hel_src

    def test_render_funktion(self, hel_src):
        assert "function renderPacemakerProcesses" in hel_src

    def test_save_funktion(self, hel_src):
        assert "function savePacemakerProcesses" in hel_src

    def test_load_funktion(self, hel_src):
        assert "function loadPacemakerProcesses" in hel_src

    def test_default_prozesse(self, hel_src):
        assert "sentiment" in hel_src
        assert "memory" in hel_src
        assert "db_dedup" in hel_src or "DB-Deduplizierung" in hel_src

    def test_cpu_gpu_toggle(self, hel_src):
        render_block = hel_src.split("function renderPacemakerProcesses")[1][:5000]
        assert "cpu" in render_block
        assert "cuda" in render_block

    def test_interval_slider(self, hel_src):
        render_block = hel_src.split("function renderPacemakerProcesses")[1][:5000]
        assert "interval-slider" in render_block
        assert 'min = \'1\'' in render_block or "'1'" in render_block

    def test_lazy_load_beim_tab_wechsel(self, hel_src):
        assert "loadPacemakerProcesses()" in hel_src


class TestBackendEndpoints:
    def test_get_endpoint_registriert(self, hel_src):
        assert '@router.get("/admin/pacemaker/processes")' in hel_src
        assert "async def get_pacemaker_processes" in hel_src

    def test_post_endpoint_registriert(self, hel_src):
        assert '@router.post("/admin/pacemaker/processes")' in hel_src
        assert "async def post_pacemaker_processes" in hel_src

    def test_default_constants(self, hel_src):
        assert "PACEMAKER_DEFAULT_PROCESSES" in hel_src
        # Default-Liste muss Dicts mit key/name/interval_min/device enthalten
        defaults_block = hel_src.split("PACEMAKER_DEFAULT_PROCESSES = [")[1].split("]")[0]
        assert "interval_min" in defaults_block
        assert "device" in defaults_block

    def test_speichert_in_config_yaml(self, hel_src):
        post_block = hel_src.split("async def post_pacemaker_processes")[1].split("async def ")[0]
        assert "pacemaker_processes" in post_block
        assert "yaml.safe_dump" in post_block


class TestSyncLogic:
    def test_sync_synchronisiert_intervalle(self, hel_src):
        """Wenn Sync aktiv ist, wird ein Intervall-Change auf alle übertragen."""
        render_block = hel_src.split("function renderPacemakerProcesses")[1][:6000]
        assert "_pacemakerProcesses.sync" in render_block
        assert "forEach" in render_block
