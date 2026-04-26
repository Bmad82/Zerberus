"""Patch 166 — Tests fuer Huginn-Poll-Fehler-Eskalation (B4).

Hintergrund: bei kurzen Internet-Aussetzern hat der Telegram-Long-Poll-Loop
das Terminal mit `[HUGINN-155] getUpdates Exception: ...`-Zeilen geflutet.
Patch 166 stuft das auf DEBUG runter und zaehlt aufeinanderfolgende Fehler;
nach `_POLL_ERROR_WARN_THRESHOLD` (=5) gibt es genau EINE WARNING, danach
wieder still. Bei Erfolg → Counter-Reset und (falls vorher gewarnt) eine
INFO-Zeile „Verbindung wiederhergestellt".
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from zerberus.modules.telegram import bot as bot_module
from zerberus.modules.telegram.bot import (
    _POLL_ERROR_WARN_THRESHOLD,
    _reset_poll_error_counter_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_counter():
    """Test-Isolation: Counter vor/nach jedem Test zuruecksetzen."""
    _reset_poll_error_counter_for_tests()
    yield
    _reset_poll_error_counter_for_tests()


def _build_loop_runner(monkeypatch, tmp_path, fake_get_updates):
    """Setup-Helper: liefert eine Funktion ``run()`` die `long_polling_loop`
    bis zur ersten ``CancelledError`` ausfuehrt."""
    monkeypatch.setattr(bot_module, "OFFSET_FILE", tmp_path / "off.json")

    async def fake_deregister(token, timeout=10.0):
        return True

    monkeypatch.setattr(bot_module, "deregister_webhook", fake_deregister)
    monkeypatch.setattr(bot_module, "get_updates", fake_get_updates)

    async def handler(update):
        pass

    def run():
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(bot_module.long_polling_loop("TOKEN", handler))

    return run


class TestPollErrorEscalation:
    def test_single_error_does_not_warn(self, monkeypatch, tmp_path, caplog):
        """1 Fehler → kein WARNING."""
        calls = {"n": 0}

        async def fake_get_updates(token, offset=0, timeout=30, allowed_updates=None):
            calls["n"] += 1
            if calls["n"] == 1:
                # Fehlerzustand simulieren wie es ``get_updates`` selbst tun wuerde.
                bot_module._LAST_POLL_FAILED = True
                return []
            raise asyncio.CancelledError()

        run = _build_loop_runner(monkeypatch, tmp_path, fake_get_updates)
        caplog.set_level(logging.WARNING, logger="zerberus.huginn")
        run()

        warns = [r for r in caplog.records if "[HUGINN-166]" in r.getMessage()]
        assert warns == [], "Bei 1 Fehler darf keine HUGINN-166-WARNING erscheinen"
        assert bot_module._consecutive_poll_errors == 1

    def test_threshold_errors_emit_one_warning(self, monkeypatch, tmp_path, caplog):
        """5 aufeinanderfolgende Fehler → genau EINE WARNING mit Zaehler."""
        calls = {"n": 0}

        async def fake_get_updates(token, offset=0, timeout=30, allowed_updates=None):
            calls["n"] += 1
            if calls["n"] <= _POLL_ERROR_WARN_THRESHOLD + 2:
                bot_module._LAST_POLL_FAILED = True
                return []
            raise asyncio.CancelledError()

        run = _build_loop_runner(monkeypatch, tmp_path, fake_get_updates)
        caplog.set_level(logging.WARNING, logger="zerberus.huginn")
        run()

        warns = [
            r for r in caplog.records
            if "[HUGINN-166]" in r.getMessage() and r.levelno == logging.WARNING
        ]
        assert len(warns) == 1, f"Erwartet genau 1 WARNING, gefunden {len(warns)}"
        assert "Internetverbindung" in warns[0].getMessage()
        assert str(_POLL_ERROR_WARN_THRESHOLD) in warns[0].getMessage()

    def test_success_after_errors_resets_counter(self, monkeypatch, tmp_path):
        """Erfolg nach Fehlern → Counter auf 0, kein WARNING mehr beim naechsten Fehler."""
        calls = {"n": 0}

        async def fake_get_updates(token, offset=0, timeout=30, allowed_updates=None):
            calls["n"] += 1
            if calls["n"] in (1, 2):
                bot_module._LAST_POLL_FAILED = True
                return []
            if calls["n"] == 3:
                # Erfolgreicher Poll (kein Fehler-Flag).
                bot_module._LAST_POLL_FAILED = False
                return []
            raise asyncio.CancelledError()

        run = _build_loop_runner(monkeypatch, tmp_path, fake_get_updates)
        run()

        assert bot_module._consecutive_poll_errors == 0
        assert bot_module._poll_error_warning_emitted is False

    def test_recovery_message_after_warning_threshold(
        self, monkeypatch, tmp_path, caplog
    ):
        """Erfolg nach 5+ Fehlern → INFO „Verbindung wiederhergestellt"."""
        calls = {"n": 0}

        async def fake_get_updates(token, offset=0, timeout=30, allowed_updates=None):
            calls["n"] += 1
            if calls["n"] <= _POLL_ERROR_WARN_THRESHOLD:
                bot_module._LAST_POLL_FAILED = True
                return []
            if calls["n"] == _POLL_ERROR_WARN_THRESHOLD + 1:
                bot_module._LAST_POLL_FAILED = False
                return []
            raise asyncio.CancelledError()

        run = _build_loop_runner(monkeypatch, tmp_path, fake_get_updates)
        caplog.set_level(logging.INFO, logger="zerberus.huginn")
        run()

        recovery = [
            r for r in caplog.records
            if "Verbindung wiederhergestellt" in r.getMessage()
            and r.levelno == logging.INFO
        ]
        assert len(recovery) == 1, f"Erwartet 1 Recovery-INFO, gefunden {len(recovery)}"


class TestGetUpdatesLogLevel:
    def test_get_updates_exception_is_debug_not_warning(self, monkeypatch, caplog):
        """Einzelne ``getUpdates``-Exception → DEBUG (nicht mehr WARNING)."""
        import httpx

        class _BoomClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def post(self, *a, **kw):
                raise RuntimeError("DNS-Aussetzer simuliert")

        monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)

        caplog.set_level(logging.DEBUG, logger="zerberus.huginn")

        result = asyncio.run(bot_module.get_updates("TOKEN", offset=0, timeout=1))
        assert result == []

        # Die Exception soll als DEBUG geloggt werden, nicht als WARNING.
        relevant = [
            r for r in caplog.records
            if "getUpdates Exception" in r.getMessage()
        ]
        assert len(relevant) == 1
        assert relevant[0].levelno == logging.DEBUG
        # Und das Modul-Flag muss gesetzt sein, damit der Loop eskalieren kann.
        assert bot_module._LAST_POLL_FAILED is True
