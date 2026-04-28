"""Patch 174 — Tests fuer ``adapters/telegram_adapter.py`` (Phase E, Block 1).

Pruefen ``translate_incoming``, ``translate_outgoing`` und ``send`` (mit
gemockten ``send_telegram_message`` / ``send_document``).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from zerberus.adapters import telegram_adapter as adapter_mod
from zerberus.adapters.telegram_adapter import TelegramAdapter
from zerberus.core.message_bus import (
    Channel,
    IncomingMessage,
    OutgoingMessage,
    TrustLevel,
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _adapter(admin_chat_id: str = "999") -> TelegramAdapter:
    return TelegramAdapter(bot_token="TOKEN-X", admin_chat_id=admin_chat_id)


def _update(
    chat_id: int = 42,
    chat_type: str = "private",
    user_id: int = 7,
    text: str = "Hallo",
    **extra,
) -> Dict[str, Any]:
    msg: Dict[str, Any] = {
        "message_id": 100,
        "chat": {"id": chat_id, "type": chat_type, "title": "MyChat", "username": "myuser"},
        "from": {"id": user_id, "username": "alice"},
        "text": text,
    }
    msg.update(extra)
    return {"update_id": 1, "message": msg}


# ──────────────────────────────────────────────────────────────────────
# translate_incoming
# ──────────────────────────────────────────────────────────────────────


class TestTranslateIncoming:
    def test_private_chat_authenticated(self):
        a = _adapter(admin_chat_id="999")
        incoming = a.translate_incoming(_update(chat_type="private", user_id=7))
        assert incoming.channel == Channel.TELEGRAM
        assert incoming.user_id == "7"
        assert incoming.trust_level == TrustLevel.AUTHENTICATED
        assert incoming.text == "Hallo"
        assert incoming.metadata["chat_id"] == 42
        assert incoming.metadata["chat_type"] == "private"

    def test_private_chat_admin(self):
        a = _adapter(admin_chat_id="999")
        incoming = a.translate_incoming(_update(chat_type="private", user_id=999))
        assert incoming.trust_level == TrustLevel.ADMIN

    def test_group_chat_public(self):
        a = _adapter(admin_chat_id="999")
        incoming = a.translate_incoming(_update(chat_type="group", chat_id=-1001))
        assert incoming.trust_level == TrustLevel.PUBLIC
        assert incoming.metadata["chat_id"] == -1001

    def test_supergroup_chat_public(self):
        a = _adapter()
        incoming = a.translate_incoming(_update(chat_type="supergroup", chat_id=-1002))
        assert incoming.trust_level == TrustLevel.PUBLIC

    def test_admin_in_gruppe_bleibt_public(self):
        # Auch wenn admin_chat_id mit der user_id matcht, gilt im
        # Gruppen-Kontext PUBLIC — das Trust-Mapping ist absichtlich
        # konservativ.
        a = _adapter(admin_chat_id="999")
        incoming = a.translate_incoming(
            _update(chat_type="supergroup", chat_id=-1001, user_id=999)
        )
        assert incoming.trust_level == TrustLevel.PUBLIC

    def test_mit_thread_id_in_metadata(self):
        a = _adapter()
        incoming = a.translate_incoming(_update(message_thread_id=55))
        assert incoming.metadata["thread_id"] == 55

    def test_forwarded_flag(self):
        a = _adapter()
        incoming = a.translate_incoming(_update(forward_origin={"type": "user"}))
        assert incoming.metadata["is_forwarded"] is True

    def test_nicht_forwarded_flag(self):
        a = _adapter()
        incoming = a.translate_incoming(_update())
        assert incoming.metadata["is_forwarded"] is False

    def test_reply_to_message_id_in_metadata(self):
        a = _adapter()
        incoming = a.translate_incoming(_update(reply_to_message={"message_id": 88}))
        assert incoming.metadata["reply_to_message_id"] == 88

    def test_photo_file_ids_in_metadata(self):
        a = _adapter()
        incoming = a.translate_incoming(
            _update(photo=[{"file_id": "abc"}, {"file_id": "def"}])
        )
        assert incoming.metadata["photo_file_ids"] == ["abc", "def"]
        # Photo-Bytes werden bewusst nicht vorgeladen
        assert incoming.attachments == []

    def test_caption_als_text(self):
        # Telegram liefert bei Foto+Text die Beschreibung als ``caption``.
        a = _adapter()
        incoming = a.translate_incoming(
            _update(text=None, caption="Bildunterschrift", photo=[{"file_id": "x"}])
        )
        assert incoming.text == "Bildunterschrift"

    def test_kein_message_liefert_none(self):
        a = _adapter()
        assert a.translate_incoming({"update_id": 1}) is None

    def test_username_in_metadata(self):
        a = _adapter()
        incoming = a.translate_incoming(_update())
        assert incoming.metadata["username"] == "alice"


# ──────────────────────────────────────────────────────────────────────
# translate_outgoing
# ──────────────────────────────────────────────────────────────────────


class TestTranslateOutgoing:
    def test_text_message(self):
        a = _adapter()
        msg = OutgoingMessage(text="Antwort", metadata={"chat_id": 42, "thread_id": 5})
        kwargs = a.translate_outgoing(msg)
        assert kwargs["method"] == "sendMessage"
        assert kwargs["chat_id"] == 42
        assert kwargs["text"] == "Antwort"
        assert kwargs["message_thread_id"] == 5
        assert kwargs["reply_to_message_id"] is None

    def test_file_message(self):
        a = _adapter()
        msg = OutgoingMessage(
            file=b"\x89PNG\r\n",
            file_name="bild.png",
            mime_type="image/png",
            text="Bild dabei",
            metadata={"chat_id": 42},
        )
        kwargs = a.translate_outgoing(msg)
        assert kwargs["method"] == "sendDocument"
        assert kwargs["chat_id"] == 42
        assert kwargs["content"] == b"\x89PNG\r\n"
        assert kwargs["filename"] == "bild.png"
        assert kwargs["mime_type"] == "image/png"
        assert kwargs["caption"] == "Bild dabei"

    def test_keyboard_durchgereicht(self):
        a = _adapter()
        kb = {"inline_keyboard": [[{"text": "Ja", "callback_data": "y"}]]}
        msg = OutgoingMessage(
            text="Sicher?", metadata={"chat_id": 42, "reply_markup": kb}
        )
        kwargs = a.translate_outgoing(msg)
        assert kwargs["reply_markup"] == kb

    def test_reply_to_konvertiert_zu_int(self):
        a = _adapter()
        msg = OutgoingMessage(text="Antwort", reply_to="100", metadata={"chat_id": 42})
        kwargs = a.translate_outgoing(msg)
        assert kwargs["reply_to_message_id"] == 100

    def test_reply_to_unparsbar_wird_none(self):
        a = _adapter()
        msg = OutgoingMessage(text="x", reply_to="abc", metadata={"chat_id": 42})
        kwargs = a.translate_outgoing(msg)
        assert kwargs["reply_to_message_id"] is None


# ──────────────────────────────────────────────────────────────────────
# send (mit gemockten Bot-API-Funktionen)
# ──────────────────────────────────────────────────────────────────────


class TestSend:
    def test_send_text_ruft_send_telegram_message(self, monkeypatch):
        captured: List[Dict[str, Any]] = []

        async def fake_send(token, chat_id, text, **kwargs):
            captured.append({"token": token, "chat_id": chat_id, "text": text, **kwargs})
            return True

        monkeypatch.setattr(adapter_mod, "send_telegram_message", fake_send)

        a = _adapter()
        msg = OutgoingMessage(
            text="Hallo Welt",
            reply_to="100",
            metadata={"chat_id": 42, "thread_id": 5},
        )
        ok = asyncio.run(a.send(msg))
        assert ok is True
        assert len(captured) == 1
        c = captured[0]
        assert c["token"] == "TOKEN-X"
        assert c["chat_id"] == 42
        assert c["text"] == "Hallo Welt"
        assert c["reply_to_message_id"] == 100
        assert c["message_thread_id"] == 5

    def test_send_file_ruft_send_document(self, monkeypatch):
        captured: List[Dict[str, Any]] = []

        async def fake_send_document(token, chat_id, content, filename, **kwargs):
            captured.append({
                "token": token,
                "chat_id": chat_id,
                "content": content,
                "filename": filename,
                **kwargs,
            })
            return True

        monkeypatch.setattr(adapter_mod, "send_document", fake_send_document)

        a = _adapter()
        msg = OutgoingMessage(
            file=b"hello",
            file_name="x.txt",
            mime_type="text/plain",
            text="caption",
            metadata={"chat_id": 42},
        )
        ok = asyncio.run(a.send(msg))
        assert ok is True
        assert len(captured) == 1
        c = captured[0]
        assert c["chat_id"] == 42
        assert c["content"] == b"hello"
        assert c["filename"] == "x.txt"
        assert c["caption"] == "caption"
        assert c["mime_type"] == "text/plain"

    def test_send_ohne_chat_id_liefert_false(self, monkeypatch):
        # send_telegram_message darf gar nicht erst gerufen werden.
        async def fake_send(*a, **kw):
            raise AssertionError("nicht aufrufen ohne chat_id")

        monkeypatch.setattr(adapter_mod, "send_telegram_message", fake_send)
        a = _adapter()
        ok = asyncio.run(a.send(OutgoingMessage(text="Hallo")))  # metadata leer
        assert ok is False

    def test_send_text_leer_liefert_false(self, monkeypatch):
        async def fake_send(*a, **kw):
            raise AssertionError("nicht aufrufen ohne text")

        monkeypatch.setattr(adapter_mod, "send_telegram_message", fake_send)
        a = _adapter()
        ok = asyncio.run(a.send(OutgoingMessage(text="", metadata={"chat_id": 42})))
        assert ok is False


# ──────────────────────────────────────────────────────────────────────
# from_settings (Convenience-Factory)
# ──────────────────────────────────────────────────────────────────────


class _StubSettings:
    def __init__(self, modules: Dict[str, Any]):
        self.modules = modules


class TestFromSettings:
    def test_baut_adapter_aus_settings(self):
        s = _StubSettings({"telegram": {"bot_token": "T", "admin_chat_id": "5"}})
        a = TelegramAdapter.from_settings(s)
        # interne Felder bewusst ueber translate-Pfade testen statt direkt
        incoming = a.translate_incoming(_update(chat_type="private", user_id=5))
        assert incoming.trust_level == TrustLevel.ADMIN

    def test_fehlende_keys_okay(self):
        s = _StubSettings({})
        a = TelegramAdapter.from_settings(s)
        incoming = a.translate_incoming(_update(chat_type="private", user_id=5))
        # Ohne admin_chat_id → kein User wird ADMIN
        assert incoming.trust_level == TrustLevel.AUTHENTICATED
