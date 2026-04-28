"""Patch 174 — Telegram-Adapter (Phase E, Block 1).

Konkrete ``TransportAdapter``-Implementierung fuer Telegram. Uebersetzt
zwischen Telegram Bot API und dem Message-Bus (P173).

Verantwortlichkeiten:
    - ``translate_incoming``: Telegram Update → IncomingMessage
    - ``translate_outgoing``: OutgoingMessage → sendMessage/sendDocument-kwargs
    - ``send``: OutgoingMessage tatsaechlich rausschicken (delegiert an
      die bestehenden Funktionen aus ``modules/telegram/bot.py``)

NICHT in Scope (P174):
    - Vision (Photo-Bytes resolven) — Photo-File-IDs werden in metadata
      gestellt, das Resolven (``get_file_url``) bleibt im legacy
      ``_process_text_message`` bis P175.
    - Callback-Queries / HitL-Buttons — bleiben im Router.
    - Inline-Keyboards in OutgoingMessage werden via ``reply_markup`` in
      ``metadata`` durchgereicht; sie zu bauen ist Sache des Callers
      (z. B. HitL-Manager).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from zerberus.core.message_bus import (
    Channel,
    IncomingMessage,
    OutgoingMessage,
    TrustLevel,
)
from zerberus.core.transport import TransportAdapter
from zerberus.modules.telegram.bot import (
    extract_message_info,
    send_document,
    send_telegram_message,
)

logger = logging.getLogger("zerberus.adapter.telegram")


class TelegramAdapter(TransportAdapter):
    """TransportAdapter fuer Telegram Bot API.

    Holt ``bot_token`` und ``admin_chat_id`` aus den Settings; beide werden
    fuer ``send`` (Token) bzw. ``translate_incoming`` (Trust-Mapping)
    gebraucht.
    """

    def __init__(self, bot_token: str, admin_chat_id: str = "") -> None:
        self._bot_token = bot_token or ""
        self._admin_chat_id = str(admin_chat_id or "")

    @classmethod
    def from_settings(cls, settings: Any) -> "TelegramAdapter":
        """Convenience-Factory: liest ``modules.telegram`` aus Settings."""
        mod_cfg = (getattr(settings, "modules", {}) or {}).get("telegram", {}) or {}
        return cls(
            bot_token=str(mod_cfg.get("bot_token") or ""),
            admin_chat_id=str(mod_cfg.get("admin_chat_id") or ""),
        )

    # ──────────────────────────────────────────────────────────────────
    # translate_incoming
    # ──────────────────────────────────────────────────────────────────

    def translate_incoming(self, raw_data: Dict[str, Any]) -> Optional[IncomingMessage]:
        """Telegram-Update → ``IncomingMessage``.

        Liefert ``None`` wenn das Update keine verarbeitbare Message ist
        (Service-Events, leere Updates, …) — passt zu ``extract_message_info``.
        """
        info = extract_message_info(raw_data)
        if info is None:
            return None

        chat_type = str(info.get("chat_type", "private"))
        user_id = str(info.get("user_id") or "")

        # Trust-Mapping (Phase-E-Konvention):
        #   private + admin_chat_id → ADMIN
        #   private                 → AUTHENTICATED (User hat 1:1-Zugang)
        #   group/supergroup/...    → PUBLIC
        if (
            chat_type == "private"
            and self._admin_chat_id
            and user_id == self._admin_chat_id
        ):
            trust = TrustLevel.ADMIN
        elif chat_type == "private":
            trust = TrustLevel.AUTHENTICATED
        else:
            trust = TrustLevel.PUBLIC

        # Photo-File-IDs in metadata, NICHT als Attachment-Bytes.
        # Das Resolven (get_file_url + Download) gehoert in den
        # Vision-Pfad und bleibt bis P175 im legacy router.
        reply_to = info.get("reply_to_message") or {}
        metadata: Dict[str, Any] = {
            "chat_id": info.get("chat_id"),
            "chat_type": chat_type,
            "chat_title": info.get("chat_title", ""),
            "message_id": info.get("message_id"),
            "thread_id": info.get("message_thread_id"),
            "is_forwarded": bool(info.get("is_forwarded")),
            "reply_to_message_id": reply_to.get("message_id"),
            "username": info.get("username", ""),
            "photo_file_ids": list(info.get("photo_file_ids") or []),
            "new_chat_members": list(info.get("new_chat_members") or []),
        }

        return IncomingMessage(
            text=str(info.get("text") or ""),
            user_id=user_id,
            channel=Channel.TELEGRAM,
            trust_level=trust,
            attachments=[],  # Photo-Bytes werden bewusst NICHT vorgeladen
            metadata=metadata,
        )

    # ──────────────────────────────────────────────────────────────────
    # translate_outgoing
    # ──────────────────────────────────────────────────────────────────

    def translate_outgoing(self, message: OutgoingMessage) -> Dict[str, Any]:
        """Baut die kwargs fuer ``send_telegram_message`` / ``send_document``.

        Liefert immer ein Dict mit:
            ``method``: ``"sendMessage"`` oder ``"sendDocument"``
            + die zugehoerigen Felder (``chat_id``, ``text``, ``content``,
            ``filename``, ``caption``, ``mime_type``, ``reply_to_message_id``,
            ``message_thread_id``, ``reply_markup``).

        ``chat_id`` wird aus ``message.metadata['chat_id']`` gezogen — der
        Caller (Pipeline / Adapter-User) MUSS das Feld setzen, da
        ``OutgoingMessage`` selbst transport-agnostisch ist.
        """
        chat_id = message.metadata.get("chat_id")
        thread_id = message.metadata.get("thread_id")
        reply_markup = message.metadata.get("reply_markup")
        reply_to = None
        if message.reply_to is not None:
            try:
                reply_to = int(message.reply_to)
            except (TypeError, ValueError):
                reply_to = None

        if message.file is not None:
            return {
                "method": "sendDocument",
                "chat_id": chat_id,
                "content": message.file,
                "filename": message.file_name or "document",
                "caption": message.text,
                "mime_type": message.mime_type,
                "reply_to_message_id": reply_to,
                "message_thread_id": thread_id,
            }

        return {
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": message.text or "",
            "reply_to_message_id": reply_to,
            "message_thread_id": thread_id,
            "reply_markup": reply_markup,
        }

    # ──────────────────────────────────────────────────────────────────
    # send
    # ──────────────────────────────────────────────────────────────────

    async def send(self, message: OutgoingMessage) -> bool:
        """Sendet eine ``OutgoingMessage`` ueber die Telegram Bot API.

        Delegiert an die bestehenden Funktionen aus ``modules/telegram/bot.py``
        (``send_telegram_message`` bzw. ``send_document``). Liefert False
        wenn weder Text noch Datei gesetzt sind oder ``chat_id`` fehlt.
        """
        kwargs = self.translate_outgoing(message)
        chat_id = kwargs.get("chat_id")
        if chat_id is None:
            logger.warning("[ADAPTER-174] send: kein chat_id in metadata, drop")
            return False

        if kwargs["method"] == "sendDocument":
            return await send_document(
                self._bot_token,
                chat_id,
                kwargs["content"],
                kwargs["filename"],
                caption=kwargs.get("caption"),
                reply_to_message_id=kwargs.get("reply_to_message_id"),
                message_thread_id=kwargs.get("message_thread_id"),
                mime_type=kwargs.get("mime_type"),
            )

        text = kwargs.get("text") or ""
        if not text:
            logger.warning("[ADAPTER-174] send: leerer Text, drop")
            return False
        return await send_telegram_message(
            self._bot_token,
            chat_id,
            text,
            reply_to_message_id=kwargs.get("reply_to_message_id"),
            message_thread_id=kwargs.get("message_thread_id"),
            reply_markup=kwargs.get("reply_markup"),
        )
