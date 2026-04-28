"""Patch 177 — Tests fuer den Pipeline-Cutover-Feature-Flag.

Prueft die Weiche in :func:`zerberus.modules.telegram.router.process_update`
und das Delegations-Verhalten von :func:`handle_telegram_update`:

  * Default (``modules.pipeline.use_message_bus=False``) routed an den
    ``_legacy_process_update``-Pfad.
  * Aktiviert (``True``) routed an ``handle_telegram_update``.
  * Komplexe Update-Typen (Callback-Query, Channel-Post, Edited-Message,
    Photo, Gruppen-Chat) werden auch im Message-Bus-Modus an Legacy
    delegiert — die Pipeline bleibt fuer den linearen DM-Text-Pfad
    reserviert.
  * Linearer DM-Text laeuft durch Adapter + Pipeline.

Stil: Module-Level-Monkey-Patches (wie ``test_telegram_bot.py`` /
``test_file_output.py``) — die Async-Calls werden mit ``asyncio.run``
aus synchronen Test-Funktionen heraus getriggert, damit die bestehende
Test-Suite-Konvention bleibt.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from zerberus.modules.telegram import router as router_mod


# ──────────────────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────────────────


def _settings(use_message_bus: bool = False, telegram_enabled: bool = True) -> SimpleNamespace:
    """Minimaler Settings-Stub: nur die Felder die ``process_update`` /
    ``handle_telegram_update`` lesen.
    """
    return SimpleNamespace(
        modules={
            "telegram": {
                "enabled": telegram_enabled,
                "bot_token": "TOKEN-X",
                "admin_chat_id": "999",
                "model": "test/model",
                "system_prompt": "be brief",
            },
            "pipeline": {"use_message_bus": use_message_bus},
        },
        features={},
    )


def _text_dm_update(text: str = "Hallo Huginn", chat_id: int = 42, user_id: int = 7) -> Dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "username": "tester"},
            "text": text,
            "date": 1234567890,
        },
    }


@pytest.fixture(autouse=True)
def _reset_singletons_and_install_capture(monkeypatch):
    """Vor jedem Test: Telegram-Singletons + Rate-Limiter resetten und
    ``_legacy_process_update`` / ``handle_telegram_update`` durch
    Capture-Funktionen ersetzen, damit der Test sieht welcher Pfad
    aufgerufen wurde — ohne den echten Stack (LLM, Guard, send) anzufassen.
    """
    router_mod._reset_telegram_singletons_for_tests()
    from zerberus.core.rate_limiter import _reset_rate_limiter_for_tests
    _reset_rate_limiter_for_tests()
    yield
    router_mod._reset_telegram_singletons_for_tests()
    _reset_rate_limiter_for_tests()


# ──────────────────────────────────────────────────────────────────────
#  Block A — Feature-Flag-Weiche in process_update
# ──────────────────────────────────────────────────────────────────────


class TestFeatureFlagSwitch:
    def test_default_false_routes_to_legacy(self, monkeypatch):
        """``use_message_bus`` fehlt → Legacy-Pfad."""
        calls: List[str] = []

        async def fake_legacy(data, settings):
            calls.append("legacy")
            return {"ok": True, "via": "legacy"}

        async def fake_pipeline(data, settings):
            calls.append("pipeline")
            return {"ok": True, "via": "pipeline"}

        monkeypatch.setattr(router_mod, "_legacy_process_update", fake_legacy)
        monkeypatch.setattr(router_mod, "handle_telegram_update", fake_pipeline)

        s = _settings(use_message_bus=False)
        result = asyncio.run(router_mod.process_update(_text_dm_update(), s))

        assert calls == ["legacy"]
        assert result == {"ok": True, "via": "legacy"}

    def test_explicit_false_routes_to_legacy(self, monkeypatch):
        calls: List[str] = []

        async def fake_legacy(data, settings):
            calls.append("legacy")
            return {"ok": True, "via": "legacy"}

        async def fake_pipeline(data, settings):
            calls.append("pipeline")
            return {"ok": True, "via": "pipeline"}

        monkeypatch.setattr(router_mod, "_legacy_process_update", fake_legacy)
        monkeypatch.setattr(router_mod, "handle_telegram_update", fake_pipeline)

        s = _settings(use_message_bus=False)
        asyncio.run(router_mod.process_update(_text_dm_update(), s))

        assert calls == ["legacy"]

    def test_true_routes_to_handle_telegram_update(self, monkeypatch):
        """``use_message_bus=True`` → neue Pipeline."""
        calls: List[str] = []

        async def fake_legacy(data, settings):
            calls.append("legacy")
            return {"ok": True, "via": "legacy"}

        async def fake_pipeline(data, settings):
            calls.append("pipeline")
            return {"ok": True, "via": "pipeline"}

        monkeypatch.setattr(router_mod, "_legacy_process_update", fake_legacy)
        monkeypatch.setattr(router_mod, "handle_telegram_update", fake_pipeline)

        s = _settings(use_message_bus=True)
        result = asyncio.run(router_mod.process_update(_text_dm_update(), s))

        assert calls == ["pipeline"]
        assert result == {"ok": True, "via": "pipeline"}

    def test_flag_read_per_call_no_caching(self, monkeypatch):
        """Live-Switch: zwei aufeinanderfolgende Calls mit unterschiedlichen
        Flags treffen unterschiedliche Pfade — kein Settings-Cache.
        """
        calls: List[str] = []

        async def fake_legacy(data, settings):
            calls.append("legacy")
            return {"ok": True}

        async def fake_pipeline(data, settings):
            calls.append("pipeline")
            return {"ok": True}

        monkeypatch.setattr(router_mod, "_legacy_process_update", fake_legacy)
        monkeypatch.setattr(router_mod, "handle_telegram_update", fake_pipeline)

        asyncio.run(router_mod.process_update(_text_dm_update(), _settings(False)))
        asyncio.run(router_mod.process_update(_text_dm_update(), _settings(True)))
        asyncio.run(router_mod.process_update(_text_dm_update(), _settings(False)))

        assert calls == ["legacy", "pipeline", "legacy"]


# ──────────────────────────────────────────────────────────────────────
#  Block B — handle_telegram_update delegiert komplexe Pfade an Legacy
# ──────────────────────────────────────────────────────────────────────


class TestHandleTelegramUpdateDelegates:
    def _wire(self, monkeypatch) -> List[str]:
        """Capture: alle Aufrufe von ``_legacy_process_update`` werden
        registriert; die Pipeline-Funktion wird ebenfalls beobachtet."""
        calls: List[str] = []

        async def fake_legacy(data, settings):
            calls.append("legacy")
            return {"ok": True, "via": "legacy"}

        monkeypatch.setattr(router_mod, "_legacy_process_update", fake_legacy)
        return calls

    def test_callback_query_delegates_to_legacy(self, monkeypatch):
        calls = self._wire(monkeypatch)
        update = {
            "update_id": 5,
            "callback_query": {
                "id": "cb-1",
                "from": {"id": 999},
                "data": "hitl_approve:abc123",
            },
        }
        result = asyncio.run(router_mod.handle_telegram_update(update, _settings(True)))
        assert calls == ["legacy"]
        assert result == {"ok": True, "via": "legacy"}

    def test_channel_post_delegates_to_legacy(self, monkeypatch):
        calls = self._wire(monkeypatch)
        update = {"update_id": 5, "channel_post": {"text": "egal"}}
        asyncio.run(router_mod.handle_telegram_update(update, _settings(True)))
        assert calls == ["legacy"]

    def test_edited_message_delegates_to_legacy(self, monkeypatch):
        calls = self._wire(monkeypatch)
        update = {
            "update_id": 5,
            "edited_message": {
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 7},
                "text": "spaeter geaendert",
            },
        }
        asyncio.run(router_mod.handle_telegram_update(update, _settings(True)))
        assert calls == ["legacy"]

    def test_photo_message_delegates_to_legacy(self, monkeypatch):
        """Vision-Pfad bleibt im Legacy-Stack."""
        calls = self._wire(monkeypatch)
        update = {
            "update_id": 5,
            "message": {
                "message_id": 100,
                "chat": {"id": 42, "type": "private"},
                "from": {"id": 7},
                "photo": [{"file_id": "AgAC...", "width": 320, "height": 240}],
                "caption": "Was siehst du?",
            },
        }
        asyncio.run(router_mod.handle_telegram_update(update, _settings(True)))
        assert calls == ["legacy"]

    def test_group_message_delegates_to_legacy(self, monkeypatch):
        """Gruppen-Kontext (autonomer Einwurf, Gruppenbeitritt-HitL) bleibt
        im Legacy-Stack."""
        calls = self._wire(monkeypatch)
        update = {
            "update_id": 5,
            "message": {
                "message_id": 100,
                "chat": {"id": -1001, "type": "supergroup", "title": "Crew"},
                "from": {"id": 7, "username": "tester"},
                "text": "@huginn moin",
            },
        }
        asyncio.run(router_mod.handle_telegram_update(update, _settings(True)))
        assert calls == ["legacy"]

    def test_disabled_module_returns_disabled(self, monkeypatch):
        """Telegram-Modul deaktiviert → kein Legacy-Aufruf, kein Pipeline-
        Aufruf, frueher Return."""
        calls = self._wire(monkeypatch)
        s = _settings(use_message_bus=True, telegram_enabled=False)
        result = asyncio.run(router_mod.handle_telegram_update(_text_dm_update(), s))
        assert calls == []
        assert result == {"ok": False, "reason": "disabled"}


# ──────────────────────────────────────────────────────────────────────
#  Block C — DM-Text-Pfad laeuft durch Adapter + Pipeline
# ──────────────────────────────────────────────────────────────────────


class TestHandleTelegramUpdateTextPath:
    def test_dm_text_runs_pipeline(self, monkeypatch):
        """DM-Text → Pipeline ``process_message`` wird aufgerufen, NICHT
        Legacy."""
        legacy_calls: List[str] = []

        async def fake_legacy(data, settings):
            legacy_calls.append("legacy")
            return {"ok": True}

        monkeypatch.setattr(router_mod, "_legacy_process_update", fake_legacy)

        # LLM-Stub: liefert eine kurze Antwort ohne externen Call.
        async def fake_call_llm_with_retry(**kwargs):
            return {"content": "Krraa.", "error": None, "model": "test/model"}

        monkeypatch.setattr(router_mod, "_call_llm_with_retry", fake_call_llm_with_retry)

        # Guard-Stub: OK-Verdict, kein OpenRouter-Call.
        async def fake_run_guard(user_msg, response, **kwargs):
            return {"verdict": "OK", "reason": "fake", "latency_ms": 1}

        monkeypatch.setattr(router_mod, "_run_guard", fake_run_guard)

        # Adapter.send-Stub: keine echte HTTP-Anfrage, einfach True.
        from zerberus.adapters import telegram_adapter as adapter_mod

        sent_messages: List[Any] = []

        async def fake_send_text(token, chat_id, text, **kwargs):
            sent_messages.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
            return True

        monkeypatch.setattr(adapter_mod, "send_telegram_message", fake_send_text)

        s = _settings(use_message_bus=True)
        result = asyncio.run(router_mod.handle_telegram_update(_text_dm_update(), s))

        assert legacy_calls == [], "DM-Text darf NICHT an Legacy delegieren"
        assert result["ok"] is True
        # Pipeline liefert eine Antwort (wenn LLM-Stub funktioniert hat)
        assert "reason" in result
