"""
Patch 131 — Vision-Modell Utility für Bild-Analyse.

`analyze_image()` sendet ein Bild (bytes oder URL) an das konfigurierte
Vision-Modell via OpenRouter und gibt die Analyse zurück. Wird von:
  - Huginn (Telegram) für Bild-Messages
  - Nala (zukünftig) für Bild-Upload im Chat

Fail-Safe: API-Key-Fehler, Timeout, HTTP ≠ 200 führen zu einer
Fehler-Markierung im Rückgabewert statt zu Exceptions — Aufrufer
entscheidet ob sie an den User gehen.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from typing import Optional

import httpx

from zerberus.core.vision_models import get_vision_model_by_id, is_vision_model

logger = logging.getLogger("zerberus.vision")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_PROMPT = "Beschreibe dieses Bild detailliert. Was ist zu sehen?"
DEFAULT_MODEL = "qwen/qwen2.5-vl-7b-instruct"
MAX_BYTES_DEFAULT = 10 * 1024 * 1024  # 10 MB


def _infer_mime_type(image_data: bytes) -> str:
    """Erkennt MIME-Type aus den ersten Bytes. Fallback: image/jpeg."""
    if not image_data or len(image_data) < 8:
        return "image/jpeg"
    if image_data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def build_data_url(image_data: bytes) -> str:
    """Baut eine `data:image/...;base64,...` URL für OpenRouter-Image-Inputs."""
    mime = _infer_mime_type(image_data)
    b64 = base64.b64encode(image_data).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def analyze_image(
    image_data: Optional[bytes] = None,
    image_url: Optional[str] = None,
    prompt: str = DEFAULT_PROMPT,
    model: Optional[str] = None,
    max_tokens: int = 1000,
    timeout: float = 60.0,
    max_bytes: int = MAX_BYTES_DEFAULT,
) -> dict:
    """Sendet ein Bild an das Vision-Modell via OpenRouter.

    Args:
        image_data: Bild als bytes (alternativ image_url).
        image_url: Bild als https-URL (alternativ image_data).
        prompt: Begleitender Text-Prompt.
        model: OpenRouter-Modell-ID (None → DEFAULT_MODEL).
        max_tokens: Max Output-Tokens.
        timeout: HTTP-Timeout in Sekunden.
        max_bytes: Obergrenze der Bild-Größe (nur image_data).

    Returns:
        {"content": str, "usage": dict, "latency_ms": int, "error": str | None}
    """
    if not image_data and not image_url:
        return {"content": "", "error": "no_image", "latency_ms": 0}

    if image_data and len(image_data) > max_bytes:
        return {
            "content": "",
            "error": f"image_too_large: {len(image_data)} > {max_bytes} bytes",
            "latency_ms": 0,
        }

    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"content": "", "error": "missing_api_key", "latency_ms": 0}

    model = model or DEFAULT_MODEL
    if not is_vision_model(model):
        logger.warning(
            f"[VISION-131] Modell '{model}' nicht in Vision-Registry — Request gestartet, kann fehlschlagen"
        )

    if image_data:
        url = build_data_url(image_data)
    else:
        url = image_url

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        latency_ms = int((time.time() - start) * 1000)
        if resp.status_code != 200:
            logger.warning(f"[VISION-131] HTTP {resp.status_code}: {resp.text[:200]}")
            return {
                "content": "",
                "error": f"http_{resp.status_code}",
                "latency_ms": latency_ms,
            }
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        return {
            "content": content,
            "usage": data.get("usage", {}),
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        logger.warning(f"[VISION-131] Exception: {type(e).__name__}: {e}")
        return {"content": "", "error": f"exception_{type(e).__name__}", "latency_ms": latency_ms}


def get_vision_config(settings) -> dict:
    """Liest `vision:`-Block aus config.yaml. Default-Dict bei Fehler."""
    try:
        raw = getattr(settings, "vision", None)
        if isinstance(raw, dict):
            return raw
        # Settings-Objekt hat `modules` — aber `vision` als Top-Level
        if hasattr(settings, "model_dump"):
            dumped = settings.model_dump()
            return dumped.get("vision", {}) or {}
    except Exception:
        pass
    return {}


def pick_vision_model(settings, override: Optional[str] = None) -> str:
    """Wählt ein Vision-Modell: override → config → DEFAULT_MODEL."""
    if override and is_vision_model(override):
        return override
    cfg = get_vision_config(settings)
    model = cfg.get("model") if isinstance(cfg, dict) else None
    if model and is_vision_model(model):
        return model
    return DEFAULT_MODEL
