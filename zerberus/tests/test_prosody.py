"""
Patch 188 — Tests für die Prosodie-Foundation.

Verifiziert:
  - ProsodyConfig Defaults + from_dict
  - ProsodyManager.analyze() liefert Stub-Werte solange kein Modell geladen
  - healthcheck() reagiert sauber auf disabled / no_model / model_not_found
  - Pipeline-Anker (Kommentar-Skelett) existiert in nala.py + legacy.py
  - main.py-Lifespan startet den Manager nicht bei enabled=False
  - Modul ist importierbar
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from zerberus.modules.prosody.manager import (
    ProsodyConfig,
    ProsodyManager,
    get_prosody_manager,
    reset_prosody_manager,
)


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_prosody_manager()
    yield
    reset_prosody_manager()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if not asyncio.iscoroutinefunction(coro) else asyncio.run(coro)


class TestProsodyConfig:
    def test_prosody_config_defaults(self):
        cfg = ProsodyConfig()
        assert cfg.enabled is False
        assert cfg.model_path == ""
        assert cfg.device == "cuda"
        assert cfg.vram_threshold_gb == 2.0
        assert cfg.output_format == "json"

    def test_prosody_config_from_dict_empty(self):
        cfg = ProsodyConfig.from_dict({})
        assert cfg.enabled is False
        assert cfg.device == "cuda"

    def test_prosody_config_from_dict_full(self):
        cfg = ProsodyConfig.from_dict({
            "enabled": True,
            "model_path": "/models/gemma.gguf",
            "device": "cpu",
            "vram_threshold_gb": 4.0,
            "output_format": "text",
        })
        assert cfg.enabled is True
        assert cfg.model_path == "/models/gemma.gguf"
        assert cfg.device == "cpu"
        assert cfg.vram_threshold_gb == 4.0
        assert cfg.output_format == "text"


class TestProsodyManagerStub:
    def test_prosody_manager_stub_returns_neutral(self):
        mgr = ProsodyManager(ProsodyConfig())
        result = asyncio.run(mgr.analyze(b"\x00\x01\x02"))
        assert result["mood"] == "neutral"
        assert result["tempo"] == "normal"
        assert result["source"] == "stub"

    def test_prosody_stub_has_all_fields(self):
        mgr = ProsodyManager(ProsodyConfig())
        result = asyncio.run(mgr.analyze(b""))
        for field in ("mood", "tempo", "confidence", "valence", "arousal", "dominance", "source"):
            assert field in result, f"Stub fehlt Feld {field!r}"

    def test_prosody_stub_confidence_is_zero(self):
        """Stub-Modus → confidence=0 damit Konsumenten merken: das ist Bullshit."""
        mgr = ProsodyManager(ProsodyConfig())
        result = asyncio.run(mgr.analyze(b""))
        assert result["confidence"] == 0.0


class TestProsodyHealthcheck:
    def test_prosody_healthcheck_disabled(self):
        mgr = ProsodyManager(ProsodyConfig(enabled=False))
        result = asyncio.run(mgr.healthcheck())
        assert result["ok"] is False
        assert result["reason"] == "disabled"

    def test_prosody_healthcheck_no_model_path(self):
        mgr = ProsodyManager(ProsodyConfig(enabled=True, model_path=""))
        result = asyncio.run(mgr.healthcheck())
        assert result["ok"] is False
        assert result["reason"] == "no_model"

    def test_prosody_healthcheck_model_not_found(self):
        mgr = ProsodyManager(ProsodyConfig(
            enabled=True, model_path="/nonexistent/path/to/gemma.gguf",
        ))
        result = asyncio.run(mgr.healthcheck())
        assert result["ok"] is False
        assert result["reason"] == "model_not_found"
        assert "/nonexistent/path/to/gemma.gguf" in result.get("path", "")

    def test_prosody_healthcheck_no_cuda(self, tmp_path):
        """enabled=True + Modell vorhanden + device=cuda + kein CUDA → no_cuda."""
        fake_model = tmp_path / "fake.gguf"
        fake_model.write_bytes(b"\x00")
        mgr = ProsodyManager(ProsodyConfig(
            enabled=True, model_path=str(fake_model), device="cuda", vram_threshold_gb=2.0,
        ))
        with patch("zerberus.modules.rag.device._cuda_state", return_value=(False, 0.0, 0.0, "")):
            result = asyncio.run(mgr.healthcheck())
        assert result["ok"] is False
        assert result["reason"] == "no_cuda"

    def test_prosody_healthcheck_not_enough_vram(self, tmp_path):
        fake_model = tmp_path / "fake.gguf"
        fake_model.write_bytes(b"\x00")
        mgr = ProsodyManager(ProsodyConfig(
            enabled=True, model_path=str(fake_model), device="cuda", vram_threshold_gb=4.0,
        ))
        # CUDA verfügbar, aber nur 1 GB frei < 4 GB Schwelle
        with patch("zerberus.modules.rag.device._cuda_state",
                   return_value=(True, 1.0, 8.0, "RTX 4070")):
            result = asyncio.run(mgr.healthcheck())
        assert result["ok"] is False
        assert result["reason"] == "not_enough_vram"
        assert result["vram_free_gb"] == 1.0
        assert result["vram_threshold_gb"] == 4.0

    def test_prosody_healthcheck_ok_with_cpu(self, tmp_path):
        """device=cpu → kein VRAM-Check, healthy wenn Modell vorhanden."""
        fake_model = tmp_path / "fake.gguf"
        fake_model.write_bytes(b"\x00")
        mgr = ProsodyManager(ProsodyConfig(
            enabled=True, model_path=str(fake_model), device="cpu",
        ))
        result = asyncio.run(mgr.healthcheck())
        assert result["ok"] is True
        assert result["device"] == "cpu"
        assert result["loaded"] is False  # Lazy-Load — Modell noch nicht geladen


class TestProsodyPipelineAnchor:
    def test_prosody_pipeline_anchor_in_nala(self):
        """Kommentar-Skelett in nala.py voice_endpoint vorhanden."""
        nala_src = (ROOT / "zerberus" / "app" / "routers" / "nala.py").read_text(encoding="utf-8")
        # Anker-Marker
        assert "[PROSODY-188]" in nala_src or "Patch 188: Prosodie" in nala_src
        # get_prosody_manager als Kommentar im voice_endpoint-Block
        voice_idx = nala_src.find("async def voice_endpoint")
        assert voice_idx > 0
        voice_block = nala_src[voice_idx:voice_idx + 6000]
        assert "get_prosody_manager" in voice_block

    def test_prosody_pipeline_anchor_in_legacy(self):
        """Kommentar-Skelett in legacy.py audio_transcriptions vorhanden."""
        legacy_src = (ROOT / "zerberus" / "app" / "routers" / "legacy.py").read_text(encoding="utf-8")
        audio_idx = legacy_src.find("async def audio_transcriptions")
        assert audio_idx > 0
        audio_block = legacy_src[audio_idx:audio_idx + 4000]
        assert "Patch 188: Prosodie" in audio_block
        assert "get_prosody_manager" in audio_block


class TestProsodyModuleImport:
    def test_prosody_module_importable(self):
        from zerberus.modules.prosody.manager import ProsodyManager  # noqa: F401
        assert True

    def test_prosody_init_module_exists(self):
        init_path = ROOT / "zerberus" / "modules" / "prosody" / "__init__.py"
        assert init_path.exists()


class TestProsodyFactory:
    def test_singleton_returns_same_instance(self):
        a = get_prosody_manager()
        b = get_prosody_manager()
        assert a is b

    def test_reset_breaks_singleton(self):
        a = get_prosody_manager()
        reset_prosody_manager()
        b = get_prosody_manager()
        assert a is not b

    def test_factory_reads_settings_modules_prosody(self):
        """settings.modules.prosody-Block wird in ProsodyConfig übersetzt."""
        class FakeSettings:
            modules = {
                "prosody": {
                    "enabled": True,
                    "model_path": "/x/y.gguf",
                    "device": "cpu",
                }
            }
        mgr = get_prosody_manager(FakeSettings())
        assert mgr.config.enabled is True
        assert mgr.config.model_path == "/x/y.gguf"
        assert mgr.config.device == "cpu"

    def test_factory_handles_missing_prosody_block(self):
        """Settings ohne prosody → Defaults (disabled)."""
        class FakeSettings:
            modules = {}
        mgr = get_prosody_manager(FakeSettings())
        assert mgr.config.enabled is False


class TestMainStartupIntegration:
    def test_main_lifespan_imports_prosody(self):
        """main.py importiert get_prosody_manager im Lifespan."""
        main_src = (ROOT / "zerberus" / "main.py").read_text(encoding="utf-8")
        assert "get_prosody_manager" in main_src
        assert "Patch 188" in main_src

    def test_main_logs_prosody_status(self):
        """main.py loggt 'Prosodie' im Startup-Banner."""
        main_src = (ROOT / "zerberus" / "main.py").read_text(encoding="utf-8")
        assert "_log_item(\"Prosodie\"" in main_src

    def test_main_handles_disabled_prosody(self):
        """main.py erkennt reason='disabled' und loggt skip."""
        main_src = (ROOT / "zerberus" / "main.py").read_text(encoding="utf-8")
        # Der Disabled-Branch im Startup
        assert '"disabled"' in main_src and "Prosodie" in main_src


class TestConfigYamlExample:
    def test_config_example_documents_prosody(self):
        """config.yaml.example dokumentiert den prosody-Block mit Defaults."""
        example = (ROOT / "config.yaml.example").read_text(encoding="utf-8")
        assert "prosody:" in example
        assert "enabled: false" in example
        assert "vram_threshold_gb" in example
