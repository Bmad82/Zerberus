"""Tests für Patch 131 — Vision-Registry + analyze_image() + Hel-Endpoints."""
from __future__ import annotations

import asyncio
import base64
from unittest.mock import MagicMock, patch

import pytest

from zerberus.core.vision_models import (
    VISION_MODELS,
    get_vision_model_by_id,
    get_vision_models,
    is_vision_model,
)
from zerberus.utils.vision import (
    analyze_image,
    build_data_url,
    pick_vision_model,
    DEFAULT_MODEL,
)


class TestVisionRegistry:
    def test_get_vision_models_sorted_by_input_price(self):
        models = get_vision_models()
        assert len(models) == len(VISION_MODELS)
        prices = [m["input_price"] for m in models]
        assert prices == sorted(prices), "Modelle sollten nach Input-Preis sortiert sein"

    def test_get_vision_model_by_id_known(self):
        m = get_vision_model_by_id("qwen/qwen2.5-vl-7b-instruct")
        assert m is not None
        assert m["tier"] == "budget"

    def test_get_vision_model_by_id_unknown_returns_none(self):
        assert get_vision_model_by_id("not/a-real-model") is None
        assert get_vision_model_by_id("") is None

    def test_is_vision_model(self):
        assert is_vision_model("openai/gpt-4o-mini") is True
        assert is_vision_model("deepseek/deepseek-chat") is False

    def test_all_models_have_required_fields(self):
        required = {"id", "name", "input_price", "output_price", "context", "tier"}
        for m in VISION_MODELS:
            missing = required - set(m.keys())
            assert not missing, f"Modell {m.get('id')} fehlt Felder: {missing}"


class TestBuildDataUrl:
    def test_png_header_detected(self):
        png_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        url = build_data_url(png_data)
        assert url.startswith("data:image/png;base64,")

    def test_jpeg_header_detected(self):
        jpeg_data = b"\xff\xd8\xff" + b"\x00" * 10
        url = build_data_url(jpeg_data)
        assert url.startswith("data:image/jpeg;base64,")

    def test_webp_header_detected(self):
        webp_data = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 10
        url = build_data_url(webp_data)
        assert url.startswith("data:image/webp;base64,")

    def test_unknown_defaults_to_jpeg(self):
        url = build_data_url(b"random bytes here")
        assert url.startswith("data:image/jpeg;base64,")

    def test_payload_is_base64_encoded(self):
        data = b"\x89PNG\r\n\x1a\n" + b"testdata"
        url = build_data_url(data)
        _, b64 = url.split(",", 1)
        decoded = base64.b64decode(b64)
        assert decoded == data


class TestAnalyzeImage:
    def test_no_image_returns_error(self):
        result = asyncio.run(analyze_image())
        assert result["error"] == "no_image"

    def test_too_large_image_rejected(self):
        big = b"x" * (11 * 1024 * 1024)
        result = asyncio.run(analyze_image(image_data=big, max_bytes=10 * 1024 * 1024))
        assert result["error"].startswith("image_too_large")

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        result = asyncio.run(analyze_image(image_data=b"\xff\xd8\xfftest"))
        assert result["error"] == "missing_api_key"

    def test_successful_call_format(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": "Ein blauer Himmel."}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

        posted: dict = {}

        class FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, headers=None, json=None):
                posted["url"] = url
                posted["payload"] = json
                posted["headers"] = headers
                return fake_resp

        with patch("zerberus.utils.vision.httpx.AsyncClient", FakeClient):
            result = asyncio.run(analyze_image(
                image_data=b"\x89PNG\r\n\x1a\ntest",
                prompt="Was ist das?",
                model="qwen/qwen2.5-vl-7b-instruct",
            ))

        assert result["content"] == "Ein blauer Himmel."
        assert result["error"] is None
        messages = posted["payload"]["messages"]
        assert messages[0]["role"] == "user"
        content_parts = messages[0]["content"]
        assert any(p["type"] == "image_url" for p in content_parts)
        assert any(p["type"] == "text" and p["text"] == "Was ist das?" for p in content_parts)

    def test_http_error_captured(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

        fake_resp = MagicMock()
        fake_resp.status_code = 500
        fake_resp.text = "server down"

        class FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, *a, **kw): return fake_resp

        with patch("zerberus.utils.vision.httpx.AsyncClient", FakeClient):
            result = asyncio.run(analyze_image(image_data=b"\xff\xd8\xfftest"))
        assert result["error"] == "http_500"


class TestPickVisionModel:
    def test_override_wins_if_valid(self):
        settings = MagicMock()
        settings.model_dump = MagicMock(return_value={"vision": {"model": "google/gemini-2.5-pro"}})
        model = pick_vision_model(settings, override="openai/gpt-4o")
        assert model == "openai/gpt-4o"

    def test_override_ignored_if_invalid(self):
        settings = MagicMock()
        settings.model_dump = MagicMock(return_value={"vision": {"model": "google/gemini-2.5-pro"}})
        model = pick_vision_model(settings, override="not/exists")
        assert model == "google/gemini-2.5-pro"

    def test_falls_back_to_default(self):
        settings = MagicMock()
        settings.model_dump = MagicMock(return_value={})
        model = pick_vision_model(settings)
        assert model == DEFAULT_MODEL


class TestHelVisionEndpoints:
    def test_vision_models_function_returns_sorted(self):
        """Ruft die Endpoint-Funktion direkt auf (umgeht Admin-Auth)."""
        from zerberus.app.routers.hel import get_vision_models_list
        result = asyncio.run(get_vision_models_list())
        assert "models" in result
        assert len(result["models"]) == len(VISION_MODELS)
        prices = [m["input_price"] for m in result["models"]]
        assert prices == sorted(prices)
