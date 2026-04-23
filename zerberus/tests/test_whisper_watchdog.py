"""Unit-Tests fuer den Whisper-Watchdog — Patch 119.

Keine Abhaengigkeit auf pytest-asyncio (ist im venv nicht installiert),
async-Funktionen werden ueber asyncio.run in synchronen Tests getrieben.
"""
from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from zerberus import whisper_watchdog


class _DummyClient:
    """Replaces httpx.AsyncClient fuer Health-Check-Tests."""

    def __init__(self, status_code: int = 200, raise_exc: Exception | None = None):
        self._status = status_code
        self._raise = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        if self._raise is not None:
            raise self._raise
        return SimpleNamespace(status_code=self._status)


class TestCheckHealth:
    def test_returns_true_on_200(self):
        with patch("zerberus.whisper_watchdog.httpx.AsyncClient",
                   lambda *a, **kw: _DummyClient(200)):
            assert asyncio.run(whisper_watchdog.check_whisper_health()) is True

    def test_returns_false_on_500(self):
        with patch("zerberus.whisper_watchdog.httpx.AsyncClient",
                   lambda *a, **kw: _DummyClient(500)):
            assert asyncio.run(whisper_watchdog.check_whisper_health()) is False

    def test_returns_false_on_network_error(self):
        with patch("zerberus.whisper_watchdog.httpx.AsyncClient",
                   lambda *a, **kw: _DummyClient(raise_exc=OSError("connection refused"))):
            assert asyncio.run(whisper_watchdog.check_whisper_health()) is False


class TestRestartContainer:
    def test_restart_success_returns_true(self):
        with patch("zerberus.whisper_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            assert whisper_watchdog.restart_whisper_container() is True
            mock_run.assert_called_once()
            # Container-Name muss im Aufruf vorkommen
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "docker"
            assert call_args[1] == "restart"
            assert whisper_watchdog.WHISPER_CONTAINER_NAME in call_args

    def test_restart_failure_returns_false(self):
        with patch("zerberus.whisper_watchdog.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="no such container")
            assert whisper_watchdog.restart_whisper_container() is False

    def test_restart_timeout_returns_false(self):
        with patch("zerberus.whisper_watchdog.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=60)):
            assert whisper_watchdog.restart_whisper_container() is False

    def test_restart_docker_missing_returns_false(self):
        with patch("zerberus.whisper_watchdog.subprocess.run",
                   side_effect=FileNotFoundError("docker")):
            assert whisper_watchdog.restart_whisper_container() is False


class TestConstants:
    """Smoke-Test: Defaults muessen sinnvoll sein, sonst bootet der Server falsch."""

    def test_container_name_set(self):
        assert whisper_watchdog.WHISPER_CONTAINER_NAME
        assert "/" not in whisper_watchdog.WHISPER_CONTAINER_NAME  # kein image-Pfad aus Versehen

    def test_interval_is_hourly(self):
        assert whisper_watchdog.RESTART_INTERVAL_SECONDS == 3600

    def test_health_url_points_to_8002(self):
        assert "8002" in whisper_watchdog.WHISPER_HEALTH_URL
