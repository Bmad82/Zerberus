"""Patch 173 — Tests für Message-Bus + Transport-Interfaces (Phase E)."""
from __future__ import annotations

import pytest

from zerberus.core.message_bus import (
    Attachment,
    Channel,
    IncomingMessage,
    OutgoingMessage,
    TrustLevel,
)
from zerberus.core.transport import TransportAdapter


# ──────────────────────────────────────────────────────────────────────
# Channel / TrustLevel Enums
# ──────────────────────────────────────────────────────────────────────


class TestChannelEnum:
    def test_alle_drei_werte_vorhanden(self):
        assert Channel.TELEGRAM.value == "telegram"
        assert Channel.NALA.value == "nala"
        assert Channel.ROSA_INTERNAL.value == "rosa_internal"

    def test_string_enum_kompatibilitaet(self):
        assert Channel.TELEGRAM == "telegram"
        assert "telegram" == Channel.TELEGRAM


class TestTrustLevelEnum:
    def test_alle_drei_werte_vorhanden(self):
        assert TrustLevel.PUBLIC.value == "public"
        assert TrustLevel.AUTHENTICATED.value == "authenticated"
        assert TrustLevel.ADMIN.value == "admin"


# ──────────────────────────────────────────────────────────────────────
# Attachment
# ──────────────────────────────────────────────────────────────────────


class TestAttachment:
    def test_dataclass_felder(self):
        a = Attachment(
            data=b"\x89PNG\r\n",
            filename="bild.png",
            mime_type="image/png",
            size=6,
        )
        assert a.data == b"\x89PNG\r\n"
        assert a.filename == "bild.png"
        assert a.mime_type == "image/png"
        assert a.size == 6


# ──────────────────────────────────────────────────────────────────────
# IncomingMessage
# ──────────────────────────────────────────────────────────────────────


class TestIncomingMessage:
    def test_default_werte(self):
        m = IncomingMessage(
            text="Hallo",
            user_id="123",
            channel=Channel.TELEGRAM,
        )
        assert m.trust_level == TrustLevel.PUBLIC
        assert m.attachments == []
        assert m.metadata == {}

    def test_alle_felder_gesetzt(self):
        att = Attachment(data=b"x", filename="a.txt", mime_type="text/plain", size=1)
        m = IncomingMessage(
            text="Hallo",
            user_id="user-42",
            channel=Channel.NALA,
            trust_level=TrustLevel.AUTHENTICATED,
            attachments=[att],
            metadata={"thread_id": "t1", "is_forwarded": False},
        )
        assert m.text == "Hallo"
        assert m.user_id == "user-42"
        assert m.channel == Channel.NALA
        assert m.trust_level == TrustLevel.AUTHENTICATED
        assert len(m.attachments) == 1
        assert m.attachments[0].filename == "a.txt"
        assert m.metadata["thread_id"] == "t1"

    def test_default_factories_unabhaengig(self):
        """Regression-Schutz: dataclass-Defaults dürfen kein shared mutable State sein."""
        m1 = IncomingMessage(text="a", user_id="1", channel=Channel.TELEGRAM)
        m2 = IncomingMessage(text="b", user_id="2", channel=Channel.TELEGRAM)
        m1.attachments.append(
            Attachment(data=b"x", filename="x", mime_type="text/plain", size=1)
        )
        m1.metadata["foo"] = "bar"
        assert m2.attachments == []
        assert m2.metadata == {}


# ──────────────────────────────────────────────────────────────────────
# OutgoingMessage
# ──────────────────────────────────────────────────────────────────────


class TestOutgoingMessage:
    def test_text_only_message(self):
        m = OutgoingMessage(text="Hallo Welt")
        assert m.text == "Hallo Welt"
        assert m.file is None
        assert m.keyboard is None
        assert m.metadata == {}

    def test_file_message(self):
        m = OutgoingMessage(
            file=b"\x89PNG\r\n",
            file_name="output.png",
            mime_type="image/png",
        )
        assert m.text is None
        assert m.file == b"\x89PNG\r\n"
        assert m.file_name == "output.png"
        assert m.mime_type == "image/png"

    def test_message_mit_keyboard(self):
        kb = [
            [{"text": "Ja", "callback_data": "yes"}, {"text": "Nein", "callback_data": "no"}],
        ]
        m = OutgoingMessage(text="Sicher?", keyboard=kb)
        assert m.text == "Sicher?"
        assert m.keyboard == kb

    def test_reply_to(self):
        m = OutgoingMessage(text="Antwort", reply_to="msg-42")
        assert m.reply_to == "msg-42"


# ──────────────────────────────────────────────────────────────────────
# TransportAdapter ABC
# ──────────────────────────────────────────────────────────────────────


class TestTransportAdapter:
    def test_abstract_kann_nicht_instanziiert_werden(self):
        with pytest.raises(TypeError):
            TransportAdapter()  # type: ignore[abstract]

    def test_subclass_ohne_methoden_failt(self):
        class Unvollstaendig(TransportAdapter):
            pass

        with pytest.raises(TypeError):
            Unvollstaendig()  # type: ignore[abstract]

    def test_subclass_mit_allen_methoden_ok(self):
        class Dummy(TransportAdapter):
            async def send(self, message: OutgoingMessage) -> bool:
                return True

            def translate_incoming(self, raw_data: dict) -> IncomingMessage:
                return IncomingMessage(
                    text=raw_data.get("text", ""),
                    user_id=raw_data.get("user_id", ""),
                    channel=Channel.TELEGRAM,
                )

            def translate_outgoing(self, message: OutgoingMessage) -> dict:
                return {"text": message.text}

        d = Dummy()
        # Roundtrip — beweist dass die Interfaces zusammenpassen.
        incoming = d.translate_incoming({"text": "ping", "user_id": "u1"})
        assert incoming.text == "ping"
        assert incoming.channel == Channel.TELEGRAM
        outgoing = d.translate_outgoing(OutgoingMessage(text="pong"))
        assert outgoing == {"text": "pong"}
