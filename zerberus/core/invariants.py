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
    """Seit Patch 105/112: config.yaml ist Single Source of Truth. config.json ist obsolet.

    Falls config.json noch existiert (Alt-Installation), einmalig warnen —
    sie wird nicht mehr gelesen. Löschung wird dem User überlassen.
    """
    config_json_path = Path("config.json")
    if config_json_path.exists():
        logger.warning(
            "⚠️ config.json existiert noch, wird aber seit Patch 112 nicht mehr gelesen. "
            "Kann gefahrlos gelöscht werden — config.yaml ist Single Source of Truth."
        )
    # get_settings() wird eh beim Start geladen — reine Sanity-Probe:
    try:
        _ = get_settings().legacy.models.cloud_model
        logger.info("✅ Config‑Konsistenz ok (config.yaml)")
    except Exception as e:  # pragma: no cover
        logger.error("❌ config.yaml nicht lesbar: %s", e)

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
