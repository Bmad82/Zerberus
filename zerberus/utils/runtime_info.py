"""Patch 185 — Runtime-Info-Block fuer System-Prompts.

Statt Modellname, Modul-Status und Version statisch im RAG-Dokument zu
pflegen (huginn_kennt_zerberus.md, das schnell veraltet), liefert dieses
Modul einen kurzen Block aus der LIVE-Konfiguration. Wird sowohl von
Huginn (telegram/router.py) als auch von Nala (legacy.py) als Suffix an
die Persona gehaengt — Position: NACH Persona, VOR RAG-Kontext.

Robust gegen kaputte oder partielle Settings — bei Lesefehler liefert
build_runtime_info einen Stub-Block, der das System nicht crasht.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("zerberus.runtime_info")

ZERBERUS_VERSION = "4.0"


def _short_model_name(full: str | None) -> str:
    """openrouter-Pfade in Kurzname konvertieren — `deepseek/deepseek-v3.2`
    → `deepseek-v3.2`. Macht den Block lesbarer und vermeidet falsche
    Anbieter-Anhaftung im LLM-Self-Talk."""
    if not full or full == "unbekannt":
        return "unbekannt"
    return full.split("/")[-1] if "/" in full else full


def _read_cloud_model(settings: Any) -> str:
    """Liest legacy.models.cloud_model defensiv. Settings sind Pydantic-
    Objekte in production, koennen aber in Tests Dicts/SimpleNamespaces sein."""
    try:
        legacy = getattr(settings, "legacy", None)
        if legacy is None and isinstance(settings, dict):
            legacy = settings.get("legacy")
        if legacy is None:
            return "unbekannt"
        models = getattr(legacy, "models", None)
        if models is None and isinstance(legacy, dict):
            models = legacy.get("models")
        if models is None:
            return "unbekannt"
        cloud = getattr(models, "cloud_model", None)
        if cloud is None and isinstance(models, dict):
            cloud = models.get("cloud_model")
        return cloud or "unbekannt"
    except Exception as exc:
        logger.debug("[RUNTIME-185] cloud_model lookup failed: %s", exc)
        return "unbekannt"


def _read_module_enabled(settings: Any, module: str) -> bool:
    """Liest modules.{module}.enabled defensiv."""
    try:
        mods = getattr(settings, "modules", None)
        if mods is None and isinstance(settings, dict):
            mods = settings.get("modules")
        if not isinstance(mods, dict):
            return False
        cfg = mods.get(module, {})
        if not isinstance(cfg, dict):
            return False
        return bool(cfg.get("enabled", False))
    except Exception as exc:
        logger.debug("[RUNTIME-185] module %s lookup failed: %s", module, exc)
        return False


def _read_guard_model() -> str:
    """Guard-Modell ist hardcoded in hallucination_guard.py — Import-Fehler
    sind moeglich wenn das Modul Probleme hat."""
    try:
        from zerberus.hallucination_guard import GUARD_MODEL
        return GUARD_MODEL
    except Exception as exc:
        logger.debug("[RUNTIME-185] guard_model lookup failed: %s", exc)
        return "unbekannt"


def build_runtime_info(settings: Any) -> str:
    """Baut einen Runtime-Info-Block aus den aktuellen Settings.

    Enthält:
    - Zerberus-Version
    - Aktives Cloud-LLM (Kurzname)
    - Guard-Modell (Kurzname)
    - RAG-Aktivierungsstatus
    - Sandbox-Aktivierungsstatus

    Beispiel-Output:
        [Aktuelle System-Informationen — automatisch generiert]
        Zerberus Version: 4.0
        Dein LLM: deepseek-v3.2 (via OpenRouter)
        Guard-Modell: mistral-small-24b-instruct-2501
        RAG: aktiv
        Sandbox: aktiv
    """
    cloud_model = _read_cloud_model(settings)
    guard_model = _read_guard_model()
    rag_active = _read_module_enabled(settings, "rag")
    sandbox_active = _read_module_enabled(settings, "sandbox")

    lines = [
        "[Aktuelle System-Informationen — automatisch generiert]",
        f"Zerberus Version: {ZERBERUS_VERSION}",
        f"Dein LLM: {_short_model_name(cloud_model)} (via OpenRouter)",
        f"Guard-Modell: {_short_model_name(guard_model)}",
        f"RAG: {'aktiv' if rag_active else 'deaktiviert'}",
        f"Sandbox: {'aktiv' if sandbox_active else 'deaktiviert'}",
    ]
    return "\n".join(lines)


def append_runtime_info(system_prompt: str, settings: Any) -> str:
    """Hängt den Runtime-Info-Block an einen System-Prompt.

    Position: NACH der Persona, VOR optionalem RAG-Kontext. Bei leerem
    System-Prompt wird der Block trotzdem zurückgegeben — das LLM bekommt
    dann zumindest die System-Infos, auch wenn keine Persona gesetzt ist.
    """
    block = build_runtime_info(settings)
    if not system_prompt:
        return block
    return f"{system_prompt}\n\n{block}"
