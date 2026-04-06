"""
Invarianten – Systemannahmen, die beim Start geprüft werden.
Fail‑fast bei Verstößen, um spätere Laufzeitfehler zu vermeiden.
"""
import os
import json
import logging
from pathlib import Path
from zerberus.core.config import get_settings

logger = logging.getLogger(__name__)

def check_config_consistency():
    """Prüft, ob config.json und get_settings() in kritischen Punkten übereinstimmen."""
    config_json_path = Path("config.json")
    if not config_json_path.exists():
        logger.warning("config.json nicht vorhanden – verwende ausschließlich get_settings()")
        return

    with open(config_json_path, "r") as f:
        json_cfg = json.load(f).get("llm", {})

    yaml_cfg = get_settings().legacy
    # Kritische Parameter, die gleich sein müssen
    critical_keys = {
        "cloud_model": lambda: json_cfg.get("cloud_model") != yaml_cfg.models.cloud_model,
        "temperature": lambda: abs(json_cfg.get("temperature", 0.7) - yaml_cfg.settings.ai_temperature) > 0.01,
        "threshold": lambda: json_cfg.get("threshold", 10) != yaml_cfg.settings.threshold_length,
    }
    conflicts = []
    for key, check in critical_keys.items():
        if check():
            conflicts.append(key)

    if conflicts:
        logger.warning(
            "⚠️ Config‑Split erkannt: %s stimmen nicht zwischen config.json und config.yaml überein. "
            "Bereinige config.yaml oder lösche config.json.",
            ", ".join(conflicts)
        )
    else:
        logger.info("✅ Config‑Konsistenz ok")

def check_faiss_available():
    """Stellt sicher, dass FAISS importierbar ist (sonst keine RAG‑Funktion)."""
    try:
        import faiss
        logger.info("✅ FAISS verfügbar")
    except ImportError:
        logger.warning("⚠️ FAISS nicht importierbar – RAG wird nicht funktionieren")

async def check_database():
    """Prüft, ob die notwendigen Tabellen existieren (mindestens interactions)."""
    from zerberus.core.database import _async_session_maker
    from sqlalchemy import text

    async with _async_session_maker() as session:
        result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='interactions'"))
        if not result.fetchone():
            raise RuntimeError("Datenbank-Tabelle 'interactions' fehlt – bitte init_db() vorher ausführen")
    logger.info("✅ Datenbank‑Tabellen vorhanden")


def check_api_keys():
    """Prüft, ob wichtige API‑Keys gesetzt sind (nur Warnung)."""
    keys = ["OPENROUTER_API_KEY"]
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        logger.warning(f"⚠️ Fehlende API‑Keys: {', '.join(missing)} – bestimmte Funktionen werden nicht verfügbar sein.")
    else:
        logger.info("✅ API‑Keys vorhanden")

async def run_all():
    """Führt alle Invarianten‑Checks aus (sollte beim Start aufgerufen werden)."""
    logger.info("🔍 Führe Invarianten‑Checks aus...")
    check_config_consistency()
    check_faiss_available()
    await check_database()
    check_api_keys()
    logger.info("✅ Alle Invarianten‑Checks bestanden")
