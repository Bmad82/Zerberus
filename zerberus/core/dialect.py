"""
Dialekt-Hilfsfunktionen: Marker-Erkennung, Ersetzung.
"""
import json
import logging
import re
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
        "🐻🐻🐻🐻🐻": "berlin",
        "🥨🥨🥨🥨🥨": "schwaebisch",
        "✨✨✨✨✨": "emojis"
    }
    stripped = text.lstrip()
    for marker, dialect in markers.items():
        if stripped.startswith(marker):
            rest = stripped[len(marker):].lstrip()
            logger.warning(f"[DIALECT-103] Dialekt erkannt: {dialect} (Marker {marker})")
            return dialect, rest
    return None, text

def apply_dialect(text: str, dialect_name: str) -> str:
    """Wendet die Dialekt-Ersetzungen an (falls vorhanden).
    Patch 103: Wortgrenzen-Matching — 'ich' matcht nicht mehr in 'nich'.
    Längere Keys zuerst, damit Multi-Wort-Regeln wie 'haben wir' → 'hamm wa' greifen.
    """
    dialects = load_dialects()
    dialect_data = dialects.get(dialect_name, {})
    if not dialect_data:
        return text
    # Flache Key→Value-Struktur
    if "patterns" not in dialect_data:
        result = text
        for key in sorted(dialect_data.keys(), key=len, reverse=True):
            if not key:
                continue
            # Wortgrenzen nur auf Wort-Start/Ende anwenden (nicht bei Emojis/Satzzeichen)
            left = r'(?<!\w)' if key[0].isalnum() or key[0] in "äöüÄÖÜß" else ''
            right = r'(?!\w)' if key[-1].isalnum() or key[-1] in "äöüÄÖÜß" else ''
            pattern = left + re.escape(key) + right
            replacement = dialect_data[key]
            result = re.sub(pattern, lambda m, v=replacement: v, result)
        return result
    # Legacy-Struktur mit "patterns"-Liste (Trigger/Response)
    result = text
    for pat in dialect_data.get("patterns", []):
        trigger = pat.get("trigger", "")
        response = pat.get("response", "")
        if trigger and response:
            result = result.replace(trigger, response)
    return result
