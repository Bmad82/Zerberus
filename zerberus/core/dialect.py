"""
Dialekt-Hilfsfunktionen: Marker-Erkennung, Ersetzung.
"""
import json
import logging
from pathlib import Path
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

DIALECT_PATH = Path("dialect.json")

def load_dialects():
    if DIALECT_PATH.exists():
        with open(DIALECT_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def detect_dialect_marker(text: str) -> Tuple[Optional[str], str]:
    """Erkennt Dialekt-Marker und gibt (dialekt_name, rest_text) zurück."""
    markers = {
        "🐻🐻": "berlin",
        "🥨🥨": "schwaebisch",
        "✨✨": "emojis"
    }
    stripped = text.lstrip()
    for marker, dialect in markers.items():
        if stripped.startswith(marker):
            rest = stripped[len(marker):].lstrip()
            logger.info(f"✅ Dialekt erkannt: {dialect} (Marker {marker})")
            return dialect, rest
    return None, text

def apply_dialect(text: str, dialect_name: str) -> str:
    """Wendet die Dialekt-Ersetzungen an (falls vorhanden)."""
    dialects = load_dialects()
    dialect_data = dialects.get(dialect_name, {})
    if not dialect_data:
        return text
    # Flache Key→Value-Struktur: längere Keys zuerst, um Überlappungen zu vermeiden
    if "patterns" not in dialect_data:
        result = text
        for key in sorted(dialect_data.keys(), key=len, reverse=True):
            result = result.replace(key, dialect_data[key])
        return result
    # Legacy-Struktur mit "patterns"-Liste (Trigger/Response)
    result = text
    for pat in dialect_data.get("patterns", []):
        trigger = pat.get("trigger", "")
        response = pat.get("response", "")
        if trigger and response:
            result = result.replace(trigger, response)
    return result
