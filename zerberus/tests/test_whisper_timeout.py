"""Tests fuer Patch 160 — Whisper Timeout-Hardening + Short-Audio-Guard.

Testet den zentralen `zerberus.utils.whisper_client.transcribe()` und die
Integration in `legacy.py::audio_transcriptions` und `nala.py::voice_endpoint`.

Muster analog zu `test_hallucination_guard.py`: async-Aufrufe via `asyncio.run`,
httpx-Client via `unittest.mock.patch`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx
import pytest

from zerberus.core.config import WhisperConfig
from zerberus.utils import whisper_client as wc


# ---------------------------------------------------------------------------
# Test-Helper
# ---------------------------------------------------------------------------

class _MockResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any]):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "http://x/"),
                response=httpx.Response(self.status_code),
            )


@dataclass
class _MockClient:
    """Ersetzt httpx.AsyncClient. `responses` ist die Sequenz der Aufrufe:
    jede Entry ist entweder eine MockResponse oder eine Exception-Klasse/Instanz."""
    responses: List[Any]

    def __post_init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._idx = 0

    async def __aenter__(self) -> "_MockClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url, files=None, data=None, **kwargs):
        self.calls.append({"url": url, "files": files, "data": data})
        item = self.responses[self._idx]
        self._idx += 1
        if isinstance(item, Exception):
            raise item
        if isinstance(item, type) and issubclass(item, Exception):
            raise item("mock")
        return item


def _patch_client(client: _MockClient):
    """Patcht httpx.AsyncClient im whisper_client-Modul auf unseren Mock."""
    return patch.object(
        wc.httpx, "AsyncClient", lambda *a, **kw: client
    )


def _cfg(**overrides) -> WhisperConfig:
    defaults = dict(
        request_timeout_seconds=120.0,
        connect_timeout_seconds=10.0,
        min_audio_bytes=4096,
        timeout_retries=1,
        retry_backoff_seconds=0.0,  # Tests: kein echtes Sleep
    )
    defaults.update(overrides)
    return WhisperConfig(**defaults)


# ---------------------------------------------------------------------------
# Test 3 — Config-Key existiert und hat vernuenftige Defaults
# ---------------------------------------------------------------------------

class TestWhisperConfigExists:
    def test_config_key_has_sane_default(self):
        """`settings.whisper.request_timeout_seconds` muss existieren und >= 60 sein."""
        cfg = WhisperConfig()
        assert hasattr(cfg, "request_timeout_seconds")
        assert cfg.request_timeout_seconds >= 60, (
            "Default-Timeout < 60s ist zu knapp fuer Whisper auf einer RTX 3060"
        )

    def test_connect_timeout_is_short(self):
        """Connect-Timeout soll kurz sein — Docker down = schnell melden."""
        cfg = WhisperConfig()
        assert cfg.connect_timeout_seconds <= 30

    def test_min_audio_bytes_default(self):
        cfg = WhisperConfig()
        # 4KB ≈ 0.25s Audio → kleiner = garantiert Silence.
        assert cfg.min_audio_bytes >= 1024
        assert cfg.min_audio_bytes <= 8192

    def test_settings_integration(self):
        """Der Key muss in Settings als `settings.whisper.request_timeout_seconds`
        erreichbar sein."""
        from zerberus.core.config import Settings
        # Settings ist pydantic — wir pruefen nur, dass das Feld registriert ist.
        assert "whisper" in Settings.model_fields


# ---------------------------------------------------------------------------
# Test 1 — Short-Audio-Guard im Client
# ---------------------------------------------------------------------------

class TestShortAudioGuardClient:
    def test_guard_wirft_silence_exception(self):
        """< min_audio_bytes → WhisperSilenceGuard, KEIN httpx-Call."""
        client = _MockClient([_MockResponse(200, {"text": "ignored"})])
        with _patch_client(client):
            with pytest.raises(wc.WhisperSilenceGuard):
                asyncio.run(wc.transcribe(
                    whisper_url="http://whisper/",
                    audio_data=b"\x00" * 100,  # 100 Bytes < 4096
                    filename="a.wav",
                    content_type="audio/wav",
                    whisper_cfg=_cfg(),
                ))
        # Kein Client-Call darf passiert sein.
        assert client.calls == []

    def test_grenzfall_genau_an_der_schwelle(self):
        """Exakt min_audio_bytes → KEIN Guard (>= ist erlaubt)."""
        cfg = _cfg(min_audio_bytes=4096)
        client = _MockClient([_MockResponse(200, {"text": "ok"})])
        with _patch_client(client):
            result = asyncio.run(wc.transcribe(
                whisper_url="http://whisper/",
                audio_data=b"\x00" * 4096,
                filename="a.wav",
                content_type="audio/wav",
                whisper_cfg=cfg,
            ))
        assert result == {"text": "ok"}
        assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Test 4 — Timeout-Wert kommt aus Config (nicht hardcoded)
# ---------------------------------------------------------------------------

class TestTimeoutFromConfig:
    def test_custom_timeout_propagiert_in_httpx_client(self):
        """Wenn Config ein nicht-default Timeout setzt, muss httpx.AsyncClient
        mit genau diesem Timeout-Objekt gebaut werden."""
        cfg = _cfg(request_timeout_seconds=77.0, connect_timeout_seconds=3.0)
        captured: Dict[str, Any] = {}

        def _fake_client(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            captured["verify"] = kwargs.get("verify")
            return _MockClient([_MockResponse(200, {"text": "ok"})])

        with patch.object(wc.httpx, "AsyncClient", _fake_client):
            asyncio.run(wc.transcribe(
                whisper_url="http://whisper/",
                audio_data=b"\x00" * 5000,
                filename="a.wav",
                content_type="audio/wav",
                whisper_cfg=cfg,
            ))
        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        # httpx.Timeout speichert den read-Timeout; connect ist separat.
        assert timeout.read == 77.0
        assert timeout.connect == 3.0


# ---------------------------------------------------------------------------
# Test 5 — Retry bei Timeout
# ---------------------------------------------------------------------------

class TestRetryOnTimeout:
    def test_retry_nach_einem_timeout_erfolgt(self):
        """Erster Call → ReadTimeout, zweiter → 200 mit Text."""
        client = _MockClient([
            httpx.ReadTimeout("slow"),
            _MockResponse(200, {"text": "spaet aber da"}),
        ])
        with _patch_client(client):
            result = asyncio.run(wc.transcribe(
                whisper_url="http://whisper/",
                audio_data=b"\x00" * 5000,
                filename="a.wav",
                content_type="audio/wav",
                whisper_cfg=_cfg(timeout_retries=1, retry_backoff_seconds=0.0),
            ))
        assert result == {"text": "spaet aber da"}
        assert len(client.calls) == 2

    def test_retry_exhausted_raist_den_timeout_hoch(self):
        """Beide Calls → ReadTimeout → raise ReadTimeout. Aufrufer macht daraus 500."""
        client = _MockClient([
            httpx.ReadTimeout("first"),
            httpx.ReadTimeout("second"),
        ])
        with _patch_client(client):
            with pytest.raises(httpx.ReadTimeout):
                asyncio.run(wc.transcribe(
                    whisper_url="http://whisper/",
                    audio_data=b"\x00" * 5000,
                    filename="a.wav",
                    content_type="audio/wav",
                    whisper_cfg=_cfg(timeout_retries=1, retry_backoff_seconds=0.0),
                ))
        assert len(client.calls) == 2

    def test_kein_retry_wenn_disabled(self):
        """`timeout_retries=0` → nach erstem Timeout sofort raise, keine weiteren Calls."""
        client = _MockClient([httpx.ReadTimeout("one-shot")])
        with _patch_client(client):
            with pytest.raises(httpx.ReadTimeout):
                asyncio.run(wc.transcribe(
                    whisper_url="http://whisper/",
                    audio_data=b"\x00" * 5000,
                    filename="a.wav",
                    content_type="audio/wav",
                    whisper_cfg=_cfg(timeout_retries=0),
                ))
        assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# Test 1 + 2 — Endpoint-Integration (Short-Audio-Guard im Endpoint)
# ---------------------------------------------------------------------------

class _FakeUploadFile:
    """Minimaler Stand-in fuer starlette.UploadFile."""

    def __init__(self, data: bytes, filename: str = "audio.wav", ct: str = "audio/wav"):
        self._data = data
        self.filename = filename
        self.content_type = ct

    async def read(self) -> bytes:
        return self._data


class _FakeRequest:
    def __init__(self, headers: Optional[Dict[str, str]] = None):
        self.headers = headers or {}
        self.state = SimpleNamespace(profile_name=None)


def _minimal_settings(tmp_path=None) -> SimpleNamespace:
    """Erzeugt ein Settings-Substitut fuer die Endpoint-Tests — echte
    pydantic-Settings wuerden config.yaml einlesen, das wollen wir nicht."""
    return SimpleNamespace(
        legacy=SimpleNamespace(urls=SimpleNamespace(whisper_url="http://whisper/x")),
        whisper=_cfg(),
        features={"hallucination_guard": False},
    )


class TestShortAudioGuardInEndpoints:
    def test_legacy_audio_transcriptions_liefert_leeres_text_feld(self, monkeypatch):
        """< 4 KB Upload an /v1/audio/transcriptions → 200 mit `{"text": ""}`,
        KEIN Whisper-Call. Das ist OpenAI-kompatibel."""
        from zerberus.app.routers.legacy import audio_transcriptions

        # Harter Fail wenn httpx tatsaechlich aufgerufen wuerde.
        called = {"hits": 0}

        def _boom(*a, **kw):
            called["hits"] += 1
            raise AssertionError("httpx.AsyncClient darf nicht aufgerufen werden")

        monkeypatch.setattr(wc.httpx, "AsyncClient", _boom)

        short_file = _FakeUploadFile(b"\x00" * 100)
        request = _FakeRequest()
        settings = _minimal_settings()

        result = asyncio.run(audio_transcriptions(request, short_file, settings))
        assert result == {"text": "", "note": "short_audio_skipped"}
        assert called["hits"] == 0

    def test_nala_voice_liefert_leeres_transcript(self, monkeypatch):
        """< 4 KB Upload an /nala/voice → 200 mit nala-typischem Silence-Format,
        KEIN Whisper-Call."""
        from zerberus.app.routers.nala import voice_endpoint

        def _boom(*a, **kw):
            raise AssertionError("httpx.AsyncClient darf nicht aufgerufen werden")

        monkeypatch.setattr(wc.httpx, "AsyncClient", _boom)

        short_file = _FakeUploadFile(b"\x00" * 100)
        request = _FakeRequest()
        settings = _minimal_settings()

        result = asyncio.run(voice_endpoint(request, short_file, settings))
        assert result["transcript"] == ""
        assert result["response"] == ""
        assert result["note"] == "short_audio_skipped"


# ---------------------------------------------------------------------------
# Test 6 — Retry-erschoepft → 500 aus dem Endpoint
# ---------------------------------------------------------------------------

class TestEndpointRetryExhausted:
    def test_legacy_gibt_500_bei_timeout_nach_retry(self, monkeypatch):
        """Beide Whisper-Calls timeouten → legacy.audio_transcriptions wirft
        HTTPException(500). Der Endpoint-eigene Try/Except macht daraus 500."""
        from fastapi import HTTPException
        from zerberus.app.routers.legacy import audio_transcriptions

        client = _MockClient([
            httpx.ReadTimeout("1"),
            httpx.ReadTimeout("2"),
        ])
        monkeypatch.setattr(wc.httpx, "AsyncClient", lambda *a, **kw: client)

        file = _FakeUploadFile(b"\x00" * 5000)
        request = _FakeRequest()
        # Config mit 0 Backoff damit der Test schnell ist.
        settings = SimpleNamespace(
            legacy=SimpleNamespace(urls=SimpleNamespace(whisper_url="http://whisper/x")),
            whisper=_cfg(timeout_retries=1, retry_backoff_seconds=0.0),
        )

        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(audio_transcriptions(request, file, settings))
        assert excinfo.value.status_code == 500
        assert len(client.calls) == 2  # Erstversuch + 1 Retry


# ---------------------------------------------------------------------------
# Sanity: Module importieren sauber
# ---------------------------------------------------------------------------

class TestSanityImports:
    def test_whisper_client_importierbar(self):
        from zerberus.utils.whisper_client import transcribe, WhisperSilenceGuard
        assert callable(transcribe)
        assert issubclass(WhisperSilenceGuard, Exception)

    def test_whisper_config_importierbar(self):
        from zerberus.core.config import WhisperConfig
        cfg = WhisperConfig()
        assert cfg.request_timeout_seconds > 0
