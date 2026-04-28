"""Patch 175 — Tests fuer ``adapters/nala_adapter.py`` (Phase E, Block 1)."""
from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from zerberus.adapters.nala_adapter import NalaAdapter
from zerberus.core.message_bus import (
    Channel,
    IncomingMessage,
    OutgoingMessage,
    TrustLevel,
)


# ──────────────────────────────────────────────────────────────────────
# translate_incoming
# ──────────────────────────────────────────────────────────────────────


class TestTranslateIncoming:
    def test_jwt_user_authenticated(self):
        a = NalaAdapter()
        incoming = a.translate_incoming({
            "text": "Hallo Nala",
            "profile_name": "chris",
            "permission_level": "user",
            "session_id": "sess-1",
        })
        assert incoming.channel == Channel.NALA
        assert incoming.user_id == "chris"
        assert incoming.trust_level == TrustLevel.AUTHENTICATED
        assert incoming.text == "Hallo Nala"
        assert incoming.metadata["profile_name"] == "chris"
        assert incoming.metadata["session_id"] == "sess-1"

    def test_admin_jwt_admin_trust(self):
        a = NalaAdapter()
        incoming = a.translate_incoming({
            "text": "Hi",
            "profile_name": "chris",
            "permission_level": "admin",
        })
        assert incoming.trust_level == TrustLevel.ADMIN

    def test_guest_jwt_authenticated(self):
        # ``guest`` ist eingeloggt mit eingeschraenkter Permission, aber JWT.
        a = NalaAdapter()
        incoming = a.translate_incoming({
            "text": "Hi",
            "profile_name": "guest",
            "permission_level": "guest",
        })
        assert incoming.trust_level == TrustLevel.AUTHENTICATED

    def test_kein_profile_name_public(self):
        a = NalaAdapter()
        incoming = a.translate_incoming({"text": "Hi"})
        assert incoming.trust_level == TrustLevel.PUBLIC
        assert incoming.user_id == "anonymous"

    def test_audio_attachment(self):
        a = NalaAdapter()
        incoming = a.translate_incoming({
            "text": "",
            "profile_name": "chris",
            "permission_level": "user",
            "audio": {
                "data": b"\x00\x01\x02\x03",
                "filename": "voice.webm",
                "mime_type": "audio/webm",
            },
        })
        assert incoming is not None
        assert len(incoming.attachments) == 1
        att = incoming.attachments[0]
        assert att.data == b"\x00\x01\x02\x03"
        assert att.filename == "voice.webm"
        assert att.mime_type == "audio/webm"
        assert att.size == 4

    def test_session_id_in_metadata(self):
        a = NalaAdapter()
        incoming = a.translate_incoming({
            "text": "Frage",
            "profile_name": "chris",
            "permission_level": "user",
            "session_id": "abc-123",
        })
        assert incoming.metadata["session_id"] == "abc-123"

    def test_permission_level_in_metadata(self):
        a = NalaAdapter()
        incoming = a.translate_incoming({
            "text": "x",
            "profile_name": "chris",
            "permission_level": "admin",
        })
        assert incoming.metadata["permission_level"] == "admin"

    def test_extra_metadata_durchgereicht(self):
        a = NalaAdapter()
        incoming = a.translate_incoming({
            "text": "x",
            "profile_name": "chris",
            "permission_level": "user",
            "metadata": {"client": "ios", "version": "1.2.3"},
        })
        assert incoming.metadata["client"] == "ios"
        assert incoming.metadata["version"] == "1.2.3"
        # Standard-Felder bleiben
        assert incoming.metadata["profile_name"] == "chris"

    def test_leeres_text_und_kein_audio_liefert_none(self):
        a = NalaAdapter()
        assert a.translate_incoming({"text": "", "profile_name": "chris"}) is None
        assert a.translate_incoming({"profile_name": "chris"}) is None

    def test_unbekanntes_permission_level_authenticated(self):
        # Unbekannte permission_level (z. B. "moderator") → AUTHENTICATED.
        # Konservativ: nur explizite "admin" eskaliert auf ADMIN.
        a = NalaAdapter()
        incoming = a.translate_incoming({
            "text": "Hi",
            "profile_name": "chris",
            "permission_level": "moderator",
        })
        assert incoming.trust_level == TrustLevel.AUTHENTICATED


# ──────────────────────────────────────────────────────────────────────
# translate_outgoing
# ──────────────────────────────────────────────────────────────────────


class TestTranslateOutgoing:
    def test_text_response(self):
        a = NalaAdapter()
        out = a.translate_outgoing(OutgoingMessage(text="Antwort"))
        assert out["kind"] == "text"
        assert out["text"] == "Antwort"
        assert out["file"] is None
        assert out["file_name"] is None

    def test_file_response(self):
        a = NalaAdapter()
        msg = OutgoingMessage(
            file=b"# Dokument",
            file_name="rag.md",
            mime_type="text/markdown",
            text="Bericht",
        )
        out = a.translate_outgoing(msg)
        assert out["kind"] == "file"
        assert out["file"] == b"# Dokument"
        assert out["file_name"] == "rag.md"
        assert out["mime_type"] == "text/markdown"
        assert out["text"] == "Bericht"  # Caption bleibt erhalten

    def test_metadata_durchgereicht(self):
        a = NalaAdapter()
        msg = OutgoingMessage(
            text="ok", metadata={"session_id": "s-1", "model": "deepseek"}
        )
        out = a.translate_outgoing(msg)
        assert out["metadata"]["session_id"] == "s-1"
        assert out["metadata"]["model"] == "deepseek"


# ──────────────────────────────────────────────────────────────────────
# send (raised NotImplementedError)
# ──────────────────────────────────────────────────────────────────────


class TestSend:
    def test_send_raised_not_implemented(self):
        a = NalaAdapter()
        with pytest.raises(NotImplementedError) as exc:
            asyncio.run(a.send(OutgoingMessage(text="x")))
        # Hinweis auf SSE muss im Fehlertext stehen, damit Caller
        # versteht warum send hier nicht funktioniert.
        assert "SSE" in str(exc.value) or "sse" in str(exc.value).lower()
