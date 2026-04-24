"""Patch 156 — Tests fuer den Huginn-Config-Save-Bug + Webhook-Button-Removal.

Hintergrund:
  Vor Patch 156 schrieb POST /hel/admin/huginn/config zwar config.yaml,
  invalidierte aber nie den globalen Settings-Singleton. Folge: der direkt
  darauf folgende GET las den alten gecachten Wert. Test 1 + 2 sichern den
  Fix (@invalidates_settings) ab. Test 3 stellt sicher, dass der obsolete
  Webhook-Button + die JS-Funktion aus dem UI entfernt wurden.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIN_CONFIG = {
    "environment": "test",
    "whisper_cleaner": {"corrections": [], "strip_trailing": []},
    "modules": {
        "telegram": {
            "enabled": True,
            "bot_token": "TESTTOKEN1234",
            "admin_chat_id": "123",
            "model": "deepseek/deepseek-chat",
            "max_response_length": 4000,
        }
    },
}


class _FakeRequest:
    """Minimal Stand-in fuer fastapi.Request — nur .json() wird gebraucht."""

    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture
def temp_config_cwd(tmp_path, monkeypatch):
    """Setzt cwd auf ein tmp-Verzeichnis und legt eine minimale config.yaml an.

    Die config.py / hel.py oeffnen `config.yaml` relativ zum cwd, also reicht
    chdir + reload_settings() aus, um die Tests gegen die echte Codebasis
    laufen zu lassen — ohne globalen Zustand zu beschaedigen.
    """
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(_MIN_CONFIG, sort_keys=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # Singleton vorher leeren, damit der erste get_settings() im Test
    # die frische tmp-config liest.
    from zerberus.core import config as cfg_module
    cfg_module._settings = None
    yield tmp_path
    # Cleanup: Singleton zuruecksetzen, damit Folgetests die echte config sehen
    cfg_module._settings = None


# ---------------------------------------------------------------------------
# Test 1 + 2 — Cache-Invalidate
# ---------------------------------------------------------------------------

class TestHuginnConfigCacheInvalidate:
    def test_post_then_get_returns_new_model(self, temp_config_cwd):
        """Patch 156: GET nach POST muss den neu gespeicherten Wert liefern.

        Vor dem Fix sprang das Modell-Dropdown im Hel-UI direkt nach Save
        wieder auf den alten Wert zurueck, weil der Settings-Singleton den
        YAML-Write nicht bemerkt hat.
        """
        from zerberus.app.routers.hel import (
            post_huginn_config,
            get_huginn_config,
        )

        new_model = "anthropic/claude-3-haiku"
        request = _FakeRequest({"model": new_model})

        asyncio.run(post_huginn_config(request))
        result = asyncio.run(get_huginn_config())

        assert result["model"] == new_model, (
            f"GET sollte den neu gespeicherten Wert liefern, "
            f"bekam aber {result['model']!r}"
        )

    def test_post_persists_to_yaml(self, temp_config_cwd):
        """Sichert den eigentlichen YAML-Write ab (unabhaengig vom Cache)."""
        from zerberus.app.routers.hel import post_huginn_config

        new_model = "openai/gpt-4o-mini"
        request = _FakeRequest({"model": new_model, "max_response_length": 2048})

        asyncio.run(post_huginn_config(request))

        on_disk = yaml.safe_load((temp_config_cwd / "config.yaml").read_text(encoding="utf-8"))
        tg = on_disk["modules"]["telegram"]
        assert tg["model"] == new_model
        assert tg["max_response_length"] == 2048


# ---------------------------------------------------------------------------
# Test 3 — Webhook-Button entfernt
# ---------------------------------------------------------------------------

class TestHuginnWebhookButtonRemoved:
    """Patch 156: Long-Polling ist Default — der Webhook-Button + JS-Funktion
    wurden ersatzlos entfernt."""

    @pytest.fixture(scope="class")
    def hel_src(self) -> str:
        path = Path(__file__).resolve().parents[1] / "app" / "routers" / "hel.py"
        return path.read_text(encoding="utf-8")

    def test_no_webhook_button_in_html(self, hel_src):
        assert "Webhook registrieren" not in hel_src, (
            "Webhook-Button sollte aus dem Huginn-Tab entfernt sein (Patch 156)"
        )

    def test_no_huginn_set_webhook_function(self, hel_src):
        # Die Funktion darf weder als Definition noch als onclick-Aufruf existieren.
        assert "huginnSetWebhook(" not in hel_src, (
            "huginnSetWebhook() sollte aus dem JS entfernt sein (Patch 156)"
        )

    def test_no_set_webhook_route_in_telegram_router(self):
        router_src = (
            Path(__file__).resolve().parents[1]
            / "modules" / "telegram" / "router.py"
        ).read_text(encoding="utf-8")
        assert '@router.get("/set_webhook")' not in router_src, (
            "GET /set_webhook sollte in telegram/router.py entfernt sein (Patch 156)"
        )
