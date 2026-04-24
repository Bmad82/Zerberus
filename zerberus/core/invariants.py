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
    """Seit Patch 105/112: config.yaml ist Single Source of Truth. config.json ist obsolet."""
    config_json_path = Path("config.json")
    if config_json_path.exists():
        logger.warning(
            "⚠️ config.json existiert noch, wird aber seit Patch 112 nicht mehr gelesen. "
            "Kann gefahrlos gelöscht werden — config.yaml ist Single Source of Truth."
        )
    try:
        _ = get_settings().legacy.models.cloud_model
    except Exception as e:  # pragma: no cover
        logger.error("❌ config.yaml nicht lesbar: %s", e)


def check_faiss_available():
    """Stellt sicher, dass FAISS importierbar ist (sonst keine RAG‑Funktion)."""
    try:
        import faiss  # noqa: F401
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


def check_api_keys():
    """Prüft, ob wichtige API‑Keys gesetzt sind (nur Warnung)."""
    keys = ["OPENROUTER_API_KEY"]
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        logger.warning(f"⚠️ Fehlende API‑Keys: {', '.join(missing)} – bestimmte Funktionen werden nicht verfügbar sein.")


async def run_all():
    """Führt alle Invarianten‑Checks aus. Wirft bei hartem Fehler."""
    check_config_consistency()
    check_faiss_available()
    await check_database()
    check_api_keys()
