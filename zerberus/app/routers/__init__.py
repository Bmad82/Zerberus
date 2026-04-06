from zerberus.core.config import get_settings, Settings
from zerberus.core.cleaner import clean_transcript
from zerberus.core.dialect import detect_dialect_marker, apply_dialect
from zerberus.core.llm import LLMService
"""Zerberus Routers."""
# v1_root wurde entfernt (deaktiviert)
from . import legacy, nala, orchestrator, hel, archive
