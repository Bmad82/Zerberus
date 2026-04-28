"""Patch 173 — Transport-agnostischer Message-Bus für Zerberus (Phase E).

Definiert die abstrakten Interfaces für eingehende und ausgehende Nachrichten.
Jeder Transport (Telegram, Nala-Web, Rosa-Internal) implementiert einen Adapter
der zwischen seinem nativen Format und diesen Interfaces übersetzt.

WICHTIG (P173):
    Dieser Patch definiert nur die Interfaces. Es gibt noch keine Adapter-
    Implementierungen — die kommen in P174 (Telegram-Adapter) und P175
    (legacy.py / orchestrator.py-Refactor). Bewusst so geschnitten: erst
    das Interface stabilisieren, dann schrittweise migrieren.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Channel(str, Enum):
    """Bekannte Transport-Kanäle."""
    TELEGRAM = "telegram"
    NALA = "nala"
    ROSA_INTERNAL = "rosa_internal"


class TrustLevel(str, Enum):
    """Vertrauensstufe der Quelle einer eingehenden Nachricht.

    PUBLIC          — Telegram-Gruppen, unbekannte User
    AUTHENTICATED   — Eingeloggte Nala-User
    ADMIN           — Admin (admin_chat_id / Admin-JWT)
    """
    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    ADMIN = "admin"


@dataclass
class Attachment:
    """Anhang einer Nachricht (Bild, Audio, Datei, …)."""
    data: bytes
    filename: str
    mime_type: str
    size: int


@dataclass
class IncomingMessage:
    """Transport-agnostische Darstellung einer eingehenden Nachricht.

    metadata kann je nach Transport enthalten:
        thread_id, reply_to_message_id, is_forwarded,
        chat_id, message_id, …
    """
    text: str
    user_id: str
    channel: Channel
    trust_level: TrustLevel = TrustLevel.PUBLIC
    attachments: list[Attachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    """Transport-agnostische Darstellung einer ausgehenden Nachricht."""
    text: str | None = None
    file: bytes | None = None
    file_name: str | None = None
    mime_type: str | None = None
    reply_to: str | None = None
    keyboard: list[dict] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
