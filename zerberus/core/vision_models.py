"""
Patch 131 — Registry der Vision-fähigen Modelle auf OpenRouter.

Diese Liste wird für das Hel-Dropdown verwendet, damit NUR
Modelle mit Vision-Support auswählbar sind (DeepSeek V3.2 hat keinen).

Modell-IDs entsprechen OpenRouter-Konventionen (Stand 2026-04). Bei
ungültigen IDs gibt das Hel-Frontend eine Warnung aus, die statische
Liste bleibt aber funktionsfähig — der User kann den kompletten
Request selbst überprüfen.

Preise in USD pro 1M Tokens (Input / Output).
"""
from __future__ import annotations

from typing import Optional


VISION_MODELS: list[dict] = [
    {
        "id": "qwen/qwen2.5-vl-7b-instruct",
        "name": "Qwen 2.5-VL 7B (Budget)",
        "input_price": 0.10,
        "output_price": 0.15,
        "context": 32_768,
        "tier": "budget",
    },
    {
        "id": "qwen/qwen2.5-vl-72b-instruct",
        "name": "Qwen 2.5-VL 72B",
        "input_price": 0.35,
        "output_price": 0.90,
        "context": 32_768,
        "tier": "mid",
    },
    {
        "id": "google/gemini-2.5-flash",
        "name": "Gemini 2.5 Flash",
        "input_price": 0.075,
        "output_price": 0.30,
        "context": 1_000_000,
        "tier": "budget",
    },
    {
        "id": "google/gemini-2.5-flash-lite",
        "name": "Gemini 2.5 Flash Lite",
        "input_price": 0.0375,
        "output_price": 0.15,
        "context": 1_000_000,
        "tier": "budget",
    },
    {
        "id": "google/gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "input_price": 1.25,
        "output_price": 5.00,
        "context": 2_000_000,
        "tier": "premium",
    },
    {
        "id": "anthropic/claude-sonnet-4.5",
        "name": "Claude Sonnet 4.5",
        "input_price": 3.00,
        "output_price": 15.00,
        "context": 200_000,
        "tier": "premium",
    },
    {
        "id": "openai/gpt-4o-mini",
        "name": "GPT-4o Mini",
        "input_price": 0.15,
        "output_price": 0.60,
        "context": 128_000,
        "tier": "budget",
    },
    {
        "id": "openai/gpt-4o",
        "name": "GPT-4o",
        "input_price": 2.50,
        "output_price": 10.00,
        "context": 128_000,
        "tier": "premium",
    },
]


def get_vision_models() -> list[dict]:
    """Gibt die Liste der Vision-fähigen Modelle zurück, sortiert nach Input-Preis."""
    return sorted(VISION_MODELS, key=lambda m: m["input_price"])


def get_vision_model_by_id(model_id: str) -> Optional[dict]:
    """Sucht ein Modell nach ID. None wenn nicht gefunden."""
    if not model_id:
        return None
    return next((m for m in VISION_MODELS if m["id"] == model_id), None)


def is_vision_model(model_id: str) -> bool:
    """True wenn model_id in der Vision-Registry steht."""
    return get_vision_model_by_id(model_id) is not None
