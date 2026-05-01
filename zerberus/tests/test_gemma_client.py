"""
Patch 189 — Tests für GemmaAudioClient + erweiterte ProsodyManager-Funktionen.

Alle Tests sind gemockt (subprocess + httpx) — kein echtes llama-cpp
nötig. Das ist wichtig weil Coda das Binary nicht hat.

Coverage:
  - Mode-Routing (none / cli / server)
  - JSON-Parsing (clean / markdown / incomplete / kaputt)
  - Stub-Default (alle 7 Pflichtfelder)
  - Analyse-Routing (Mock subprocess + Mock httpx)
  - Fehlerpfade (Timeout / FileNotFoundError / HTTP 500)
  - is_active Property + admin_status (P190/P191)
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zerberus.modules.prosody.gemma_client import GemmaAudioClient
from zerberus.modules.prosody.manager import (
    ProsodyConfig,
    ProsodyManager,
    reset_prosody_manager,
)
from zerberus.modules.prosody.prompts import PROSODY_ANALYSIS_PROMPT


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_prosody_manager()
    yield
    reset_prosody_manager()


# ====================================================================
# Mode-Routing
# ====================================================================

class TestGemmaClientMode:
    def test_gemma_client_mode_none(self):
        """Kein model_path / kein server_url → mode='none'."""
        client = GemmaAudioClient({})
        assert client.mode == "none"

    def test_gemma_client_mode_cli(self):
        """model_path + mmproj_path → mode='cli'."""
        client = GemmaAudioClient({
            "model_path": "/x/gemma.gguf",
            "mmproj_path": "/x/mmproj.gguf",
        })
        assert client.mode == "cli"

    def test_gemma_client_mode_server(self):
        """server_url gesetzt → mode='server' (auch wenn model_path leer)."""
        client = GemmaAudioClient({"server_url": "http://localhost:8003"})
        assert client.mode == "server"

    def test_gemma_client_mode_server_wins_over_cli(self):
        """Beide gesetzt → server gewinnt (Pfad B bevorzugt)."""
        client = GemmaAudioClient({
            "server_url": "http://localhost:8003",
            "model_path": "/x/gemma.gguf",
            "mmproj_path": "/x/mmproj.gguf",
        })
        assert client.mode == "server"


# ====================================================================
# JSON-Parsing
# ====================================================================

class TestParseGemmaOutput:
    def test_parse_gemma_output_valid_json(self):
        client = GemmaAudioClient({})
        text = json.dumps({
            "mood": "happy", "tempo": "fast", "confidence": 0.85,
            "valence": 0.7, "arousal": 0.6, "dominance": 0.5,
        })
        result = client._parse_gemma_output(text)
        assert result["mood"] == "happy"
        assert result["tempo"] == "fast"
        assert result["confidence"] == 0.85
        assert result["valence"] == 0.7
        assert result["source"] == "gemma_e2b"

    def test_parse_gemma_output_json_in_markdown(self):
        """Gemma packt manchmal in ```json ... ``` Wrapper."""
        client = GemmaAudioClient({})
        text = """Here is the analysis:
```json
{"mood": "stressed", "tempo": "rushed", "confidence": 0.9, "valence": -0.4, "arousal": 0.8, "dominance": 0.3}
```
End."""
        result = client._parse_gemma_output(text)
        assert result["mood"] == "stressed"
        assert result["tempo"] == "rushed"
        assert result["source"] == "gemma_e2b"

    def test_parse_gemma_output_no_json(self):
        """Freitext ohne JSON → Stub-Fallback."""
        client = GemmaAudioClient({})
        result = client._parse_gemma_output("This audio sounds calm and relaxed.")
        assert result["source"] == "stub"
        assert result["confidence"] == 0.0

    def test_parse_gemma_output_incomplete_json(self):
        """JSON ohne 'mood'-Feld → fallback auf Default 'neutral'."""
        client = GemmaAudioClient({})
        text = '{"tempo": "fast", "confidence": 0.8}'
        result = client._parse_gemma_output(text)
        assert result["mood"] == "neutral"
        assert result["tempo"] == "fast"
        assert result["source"] == "gemma_e2b"

    def test_parse_gemma_output_empty_string(self):
        client = GemmaAudioClient({})
        result = client._parse_gemma_output("")
        assert result["source"] == "stub"

    def test_parse_gemma_output_broken_json(self):
        """Invalides JSON → Stub-Fallback."""
        client = GemmaAudioClient({})
        result = client._parse_gemma_output('{"mood": "happy", "tempo":')
        assert result["source"] == "stub"

    def test_parse_gemma_output_non_dict_json(self):
        """JSON ist Liste, nicht Dict → Stub."""
        client = GemmaAudioClient({})
        result = client._parse_gemma_output('["happy", "fast"]')
        assert result["source"] == "stub"


# ====================================================================
# Stub-Defaults
# ====================================================================

class TestStubResult:
    def test_stub_result_has_all_fields(self):
        result = GemmaAudioClient._stub_result()
        for f in ("mood", "tempo", "confidence", "valence", "arousal", "dominance", "source"):
            assert f in result, f"Stub fehlt Feld {f!r}"

    def test_stub_result_source_is_stub(self):
        result = GemmaAudioClient._stub_result()
        assert result["source"] == "stub"
        assert result["confidence"] == 0.0


# ====================================================================
# Analyse-Routing
# ====================================================================

class TestAnalyzeAudioRouting:
    def test_analyze_audio_routes_to_stub(self):
        """mode=none → Stub direkt, kein Backend-Call."""
        client = GemmaAudioClient({})  # nichts konfiguriert
        result = asyncio.run(client.analyze_audio(b"audio", "prompt"))
        assert result["source"] == "stub"

    def test_analyze_audio_routes_to_cli(self, tmp_path):
        """CLI-Pfad: subprocess wird aufgerufen, JSON-Output → geparst."""
        client = GemmaAudioClient({
            "model_path": str(tmp_path / "g.gguf"),
            "mmproj_path": str(tmp_path / "p.gguf"),
        })

        # Subprocess-Mock liefert JSON-Antwort
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(
            b'{"mood": "happy", "tempo": "fast", "confidence": 0.9, "valence": 0.7, "arousal": 0.6, "dominance": 0.5}',
            b"",
        ))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
            result = asyncio.run(client.analyze_audio(b"audio", "prompt"))

        assert result["mood"] == "happy"
        assert result["source"] == "gemma_e2b"

    def test_analyze_audio_routes_to_server(self):
        """Server-Pfad: httpx.AsyncClient.post wird aufgerufen."""
        client = GemmaAudioClient({"server_url": "http://localhost:8003"})

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json = MagicMock(return_value={
            "choices": [{"message": {"content": '{"mood": "calm", "tempo": "slow", "confidence": 0.7, "valence": 0.3, "arousal": 0.2, "dominance": 0.5}'}}]
        })

        async def _fake_post(*args, **kwargs):
            return fake_response

        fake_async_client = MagicMock()
        fake_async_client.__aenter__ = AsyncMock(return_value=fake_async_client)
        fake_async_client.__aexit__ = AsyncMock(return_value=None)
        fake_async_client.post = AsyncMock(return_value=fake_response)

        with patch("zerberus.modules.prosody.gemma_client.httpx.AsyncClient", return_value=fake_async_client):
            result = asyncio.run(client.analyze_audio(b"audio", "prompt"))

        assert result["mood"] == "calm"
        assert result["source"] == "gemma_e2b"


# ====================================================================
# Fehlerpfade — alle führen zum Stub (graceful degradation)
# ====================================================================

class TestErrorPaths:
    def test_cli_timeout_returns_stub(self, tmp_path):
        client = GemmaAudioClient({
            "model_path": str(tmp_path / "g.gguf"),
            "mmproj_path": str(tmp_path / "p.gguf"),
            "timeout_seconds": 1,
        })

        async def _hanging_communicate():
            await asyncio.sleep(10)

        fake_proc = MagicMock()
        fake_proc.communicate = _hanging_communicate

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
            result = asyncio.run(client.analyze_audio(b"audio", "prompt"))

        assert result["source"] == "stub"

    def test_cli_not_found_returns_stub(self, tmp_path):
        """Binary fehlt → FileNotFoundError → Stub."""
        client = GemmaAudioClient({
            "model_path": str(tmp_path / "g.gguf"),
            "mmproj_path": str(tmp_path / "p.gguf"),
            "llama_cli_path": "this-binary-does-not-exist-12345",
        })

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("not found")):
            result = asyncio.run(client.analyze_audio(b"audio", "prompt"))

        assert result["source"] == "stub"

    def test_cli_nonzero_returncode_returns_stub(self, tmp_path):
        """rc != 0 → Stub."""
        client = GemmaAudioClient({
            "model_path": str(tmp_path / "g.gguf"),
            "mmproj_path": str(tmp_path / "p.gguf"),
        })
        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.communicate = AsyncMock(return_value=(b"", b"some error"))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
            result = asyncio.run(client.analyze_audio(b"audio", "prompt"))

        assert result["source"] == "stub"

    def test_server_error_returns_stub(self):
        """HTTP-Fehler → Stub (graceful)."""
        client = GemmaAudioClient({"server_url": "http://localhost:8003"})

        fake_async_client = MagicMock()
        fake_async_client.__aenter__ = AsyncMock(return_value=fake_async_client)
        fake_async_client.__aexit__ = AsyncMock(return_value=None)
        fake_async_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with patch("zerberus.modules.prosody.gemma_client.httpx.AsyncClient", return_value=fake_async_client):
            result = asyncio.run(client.analyze_audio(b"audio", "prompt"))

        assert result["source"] == "stub"

    def test_cli_tmp_file_cleanup(self, tmp_path):
        """tmp-Datei wird nach Analyse gelöscht (Defense-in-Depth)."""
        import os
        import tempfile as _tf
        before = set(os.listdir(_tf.gettempdir()))

        client = GemmaAudioClient({
            "model_path": str(tmp_path / "g.gguf"),
            "mmproj_path": str(tmp_path / "p.gguf"),
        })
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b'{"mood": "neutral"}', b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
            asyncio.run(client.analyze_audio(b"audio", "prompt"))

        after = set(os.listdir(_tf.gettempdir()))
        # Es darf keine NEUE .wav-Datei übrig sein
        new_files = [f for f in (after - before) if f.endswith(".wav")]
        assert not new_files, f"tmp-Audio-Datei wurde nicht gelöscht: {new_files}"


# ====================================================================
# ProsodyConfig P189-Erweiterungen
# ====================================================================

class TestProsodyConfigP189:
    def test_p189_fields_in_defaults(self):
        cfg = ProsodyConfig()
        assert cfg.mmproj_path == ""
        assert cfg.server_url == ""
        assert cfg.llama_cli_path == "llama-mtmd-cli"
        assert cfg.n_gpu_layers == 99
        assert cfg.timeout_seconds == 30

    def test_p189_fields_from_dict(self):
        cfg = ProsodyConfig.from_dict({
            "mmproj_path": "/x/mmproj.gguf",
            "server_url": "http://localhost:8003",
            "llama_cli_path": "/usr/bin/llama-mtmd-cli",
            "n_gpu_layers": 50,
            "timeout_seconds": 60,
        })
        assert cfg.mmproj_path == "/x/mmproj.gguf"
        assert cfg.server_url == "http://localhost:8003"
        assert cfg.llama_cli_path == "/usr/bin/llama-mtmd-cli"
        assert cfg.n_gpu_layers == 50
        assert cfg.timeout_seconds == 60

    def test_to_client_settings_maps_all_fields(self):
        cfg = ProsodyConfig(
            model_path="/x/g.gguf", mmproj_path="/x/p.gguf",
            server_url="http://x", llama_cli_path="cli",
            device="cpu", n_gpu_layers=10, timeout_seconds=20,
        )
        s = cfg.to_client_settings()
        assert s["model_path"] == "/x/g.gguf"
        assert s["mmproj_path"] == "/x/p.gguf"
        assert s["server_url"] == "http://x"
        assert s["llama_cli_path"] == "cli"
        assert s["device"] == "cpu"
        assert s["n_gpu_layers"] == 10
        assert s["timeout_seconds"] == 20


# ====================================================================
# ProsodyManager P189/P190 — analyze() routing + is_active
# ====================================================================

class TestProsodyManagerP189:
    def test_analyze_disabled_returns_stub(self):
        """enabled=False → Stub direkt, kein client.analyze_audio Call."""
        mgr = ProsodyManager(ProsodyConfig(enabled=False))
        result = asyncio.run(mgr.analyze(b"audio"))
        assert result["source"] == "stub"

    def test_analyze_mode_none_returns_stub(self):
        """enabled=True aber mode=none (kein model_path) → Stub."""
        mgr = ProsodyManager(ProsodyConfig(enabled=True, model_path=""))
        result = asyncio.run(mgr.analyze(b"audio"))
        assert result["source"] == "stub"

    def test_analyze_routes_to_client(self, tmp_path):
        """enabled=True + Pfade gesetzt → client.analyze_audio wird gerufen."""
        cfg = ProsodyConfig(
            enabled=True,
            model_path=str(tmp_path / "g.gguf"),
            mmproj_path=str(tmp_path / "p.gguf"),
        )
        mgr = ProsodyManager(cfg)

        async def _fake_analyze(audio, prompt):
            assert audio == b"audio"
            assert prompt == PROSODY_ANALYSIS_PROMPT
            return {"mood": "happy", "source": "gemma_e2b", "confidence": 0.9,
                    "tempo": "fast", "valence": 0.5, "arousal": 0.5, "dominance": 0.5}

        mgr._client.analyze_audio = _fake_analyze
        result = asyncio.run(mgr.analyze(b"audio"))
        assert result["mood"] == "happy"
        assert result["source"] == "gemma_e2b"
        assert mgr._success_count == 1

    def test_analyze_client_exception_returns_stub(self, tmp_path):
        """Client wirft Exception → Stub + error_count++."""
        cfg = ProsodyConfig(
            enabled=True,
            model_path=str(tmp_path / "g.gguf"),
            mmproj_path=str(tmp_path / "p.gguf"),
        )
        mgr = ProsodyManager(cfg)

        async def _fail(audio, prompt):
            raise RuntimeError("boom")

        mgr._client.analyze_audio = _fail
        result = asyncio.run(mgr.analyze(b"audio"))
        assert result["source"] == "stub"
        assert mgr._error_count == 1


class TestIsActiveProperty:
    def test_is_active_true(self, tmp_path):
        cfg = ProsodyConfig(
            enabled=True,
            model_path=str(tmp_path / "g.gguf"),
            mmproj_path=str(tmp_path / "p.gguf"),
        )
        mgr = ProsodyManager(cfg)
        assert mgr.is_active is True

    def test_is_active_false_when_disabled(self, tmp_path):
        cfg = ProsodyConfig(
            enabled=False,
            model_path=str(tmp_path / "g.gguf"),
            mmproj_path=str(tmp_path / "p.gguf"),
        )
        mgr = ProsodyManager(cfg)
        assert mgr.is_active is False

    def test_is_active_false_when_mode_none(self):
        cfg = ProsodyConfig(enabled=True)  # model_path leer
        mgr = ProsodyManager(cfg)
        assert mgr.is_active is False

    def test_client_mode_property_passthrough(self):
        mgr = ProsodyManager(ProsodyConfig())
        assert mgr.client_mode == "none"
        mgr2 = ProsodyManager(ProsodyConfig(server_url="http://x"))
        assert mgr2.client_mode == "server"


# ====================================================================
# Source-Audit
# ====================================================================

class TestProsodyP189SourceAudit:
    def test_log_tag_p189_in_gemma_client(self):
        """Source-Audit: [PROSODY-189] muss in gemma_client.py existieren."""
        from pathlib import Path as _P
        src = (_P(__file__).resolve().parents[1] / "modules" / "prosody" / "gemma_client.py").read_text(encoding="utf-8")
        assert "[PROSODY-189]" in src

    def test_prompts_module_exports_constant(self):
        from zerberus.modules.prosody.prompts import PROSODY_ANALYSIS_PROMPT
        assert "JSON" in PROSODY_ANALYSIS_PROMPT
        assert "mood" in PROSODY_ANALYSIS_PROMPT
        assert "valence" in PROSODY_ANALYSIS_PROMPT
