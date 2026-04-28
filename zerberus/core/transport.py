"""Patch 173 — Abstract Base Class für Transport-Adapter (Phase E).

Jeder Transport-Adapter (Telegram, Nala, Rosa) übersetzt zwischen seinem
nativen Format und dem Message-Bus (``message_bus.py``).

WICHTIG (P173):
    Hier ist nur das Interface definiert. Konkrete Adapter werden in
    späteren Patches implementiert:
        P174 — Telegram-Adapter (zerberus/telegram/router.py-Refactor)
        P175 — Pipeline-Refactor (legacy.py / orchestrator.py)
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .message_bus import IncomingMessage, OutgoingMessage


class TransportAdapter(ABC):
    """Interface für Transport-Adapter (Telegram, Nala, Rosa)."""

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> bool:
        """Sende eine Nachricht über diesen Transport.

        Returns:
            True bei erfolgreichem Versand, False sonst.
        """
        ...

    @abstractmethod
    def translate_incoming(self, raw_data: dict) -> IncomingMessage:
        """Übersetze transport-spezifische Rohdaten in ein ``IncomingMessage``."""
        ...

    @abstractmethod
    def translate_outgoing(self, message: OutgoingMessage) -> dict:
        """Übersetze ein ``OutgoingMessage`` in transport-spezifische Daten."""
        ...
