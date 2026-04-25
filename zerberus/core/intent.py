"""Patch 164 — Intent-Definitionen für Huginn.

Basiert auf den 15 fehlenden Intents aus dem 7-LLM-Review
(``docs/huginn_review_final.md``).

- Huginn-jetzt: 6 Kern-Intents (CHAT, CODE, FILE, SEARCH, IMAGE, ADMIN).
- Rosa-Erweiterung (Phase D/E): EXECUTE, MEMORY, RAG, SCHEDULE, TRANSLATE,
  SUMMARIZE, CREATIVE, SYSTEM, MULTI — als Kommentar reserviert, damit der
  Wechsel zur vollen 15er-Liste rückwärtskompatibel bleibt.

Architektur-Entscheidung (Roadmap v2): Intent kommt vom Haupt-LLM via
JSON-Header in der eigenen Antwort, NICHT via Regex oder separatem
Classifier-Call. Whisper-Transkriptionsfehler machen Regex unbrauchbar,
und ein Extra-Call würde die Latenz verdoppeln.
"""
from __future__ import annotations

from enum import Enum


class HuginnIntent(str, Enum):
    """Intents die das LLM im JSON-Header zurückgibt."""

    # Phase B (jetzt aktiv)
    CHAT = "CHAT"           # Normales Gespräch, Fragen, Smalltalk, Meinungen
    CODE = "CODE"           # Code-Generierung, -Analyse, -Erklärung, -Debug
    FILE = "FILE"           # Datei-Operation (lesen, schreiben, konvertieren)
    SEARCH = "SEARCH"       # Web-Suche, Fakten-Lookup, aktuelle Infos
    IMAGE = "IMAGE"         # Bild-Analyse (Vision-Pfad)
    ADMIN = "ADMIN"         # Admin-Befehle (/status, /config, /restart, /help)

    # Phase D/E (Rosa-Skelett, noch nicht implementiert) — bewusst als
    # Kommentar, damit ``HuginnIntent.from_str("EXECUTE")`` heute auf CHAT
    # fällt statt auf einen halb-fertigen Pfad.
    # EXECUTE = "EXECUTE"     # Code-Ausführung in Sandbox
    # MEMORY = "MEMORY"       # Expliziter Memory-Zugriff
    # RAG = "RAG"             # Expliziter RAG-Zugriff
    # SCHEDULE = "SCHEDULE"   # Timer/Cron/Reminder
    # TRANSLATE = "TRANSLATE" # Übersetzung
    # SUMMARIZE = "SUMMARIZE" # Zusammenfassung
    # CREATIVE = "CREATIVE"   # Kreatives Schreiben
    # SYSTEM = "SYSTEM"       # System-Status-Queries
    # MULTI = "MULTI"         # Multi-Step Pipeline

    @classmethod
    def from_str(cls, value: str | None) -> "HuginnIntent":
        """Parst Intent-String, Fallback auf CHAT.

        Robustheit-Garantien:
        - ``None`` → CHAT
        - Leerstring → CHAT
        - Unbekannter Wert (z. B. ``"BANANA"``) → CHAT
        - Case-insensitive: ``"chat"`` → ``CHAT``
        """
        if not value:
            return cls.CHAT
        try:
            return cls(value.upper())
        except (ValueError, AttributeError):
            return cls.CHAT
