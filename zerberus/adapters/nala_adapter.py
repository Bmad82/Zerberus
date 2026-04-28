"""Patch 175 — Nala-Adapter (Phase E, Block 1).

``TransportAdapter``-Implementierung fuer den Nala-Web-Frontend-Pfad.
Uebersetzt zwischen HTTP-Request-Daten (post-JWT-Middleware) und dem
Message-Bus (P173).

WICHTIG (P175-Scope):
    Der Adapter ist ein OVERLAY, kein Ersatz fuer die bestehenden
    Nala-Pipelines (``app/routers/legacy.py``, ``app/routers/nala.py``,
    ``app/routers/orchestrator.py``). Die SSE-Streaming-Logik, RAG, Memory,
    Audio-Pipeline und Sentiment bleiben unveraendert. Diese Klasse
    macht den Aufruf an ``core/pipeline.py::process_message`` aus dem
    Nala-Pfad heraus moeglich, **erzwingt ihn aber nicht**.

``send`` ist bewusst NICHT implementiert — Nala antwortet ueber SSE-Streams
(``app/routers/nala.py::sse_events``) bzw. JSON-Responses, nicht ueber
einen direkten Push-Kanal. Wer die Pipeline aus einem Nala-Endpoint
ruft, schreibt das ``OutgoingMessage`` selbst in den SSE-Stream / die
HTTP-Response. ``translate_outgoing`` liefert ein generisches dict, das
der Caller dafuer benutzen kann.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from zerberus.core.message_bus import (
    Attachment,
    Channel,
    IncomingMessage,
    OutgoingMessage,
    TrustLevel,
)
from zerberus.core.transport import TransportAdapter

logger = logging.getLogger("zerberus.adapter.nala")


# Permission-Level → TrustLevel-Mapping. ``permission_level`` kommt aus
# dem JWT-Payload via ``zerberus/core/middleware.py``. ``admin`` ist die
# einzige Stufe die ADMIN bekommt; alles andere mit gueltigem JWT ist
# AUTHENTICATED. Ohne JWT (``request_data`` ohne ``profile_name``) →
# PUBLIC, was hauptsaechlich fuer Tests / interne Aufrufer relevant ist.
_PERMISSION_TO_TRUST: Dict[str, TrustLevel] = {
    "admin": TrustLevel.ADMIN,
    "guest": TrustLevel.AUTHENTICATED,
    "user": TrustLevel.AUTHENTICATED,
}


class NalaAdapter(TransportAdapter):
    """TransportAdapter fuer den Nala-Web-Frontend-Pfad.

    Erwartetes ``request_data``-Format (das, was Caller im Nala-Router
    ohnehin schon parsen):

        {
            "text": "...",                  # User-Nachricht (oder Whisper-Transkript)
            "profile_name": "chris",        # aus ``request.state.profile_name`` (JWT)
            "permission_level": "admin",    # aus ``request.state.permission_level`` (JWT)
            "session_id": "abc-123",        # X-Session-ID-Header
            "audio": {                      # optional: Whisper-Pfad
                "data": b"...",
                "filename": "voice.webm",
                "mime_type": "audio/webm",
            },
            "metadata": { ... },            # zusaetzliche Felder (frei)
        }
    """

    def translate_incoming(
        self, raw_data: Dict[str, Any]
    ) -> Optional[IncomingMessage]:
        """Nala-Request-Dict → ``IncomingMessage``.

        Liefert ``None`` wenn weder Text noch Audio drin sind — sonst
        ist die Nachricht inhaltsleer und sollte vom Caller geskipt
        werden.
        """
        text = str(raw_data.get("text") or "")
        audio = raw_data.get("audio") or None
        if not text.strip() and not audio:
            return None

        profile_name = str(raw_data.get("profile_name") or "")
        permission_level = str(raw_data.get("permission_level") or "").lower()

        # Trust-Mapping:
        #   kein profile_name → PUBLIC (kein gueltiger JWT)
        #   permission_level=admin → ADMIN
        #   sonst (gueltiger JWT) → AUTHENTICATED
        if not profile_name:
            trust = TrustLevel.PUBLIC
        else:
            trust = _PERMISSION_TO_TRUST.get(permission_level, TrustLevel.AUTHENTICATED)

        attachments: list[Attachment] = []
        if isinstance(audio, dict) and audio.get("data") is not None:
            data = audio["data"]
            attachments.append(
                Attachment(
                    data=bytes(data) if isinstance(data, (bytes, bytearray)) else b"",
                    filename=str(audio.get("filename") or "voice.webm"),
                    mime_type=str(audio.get("mime_type") or "audio/webm"),
                    size=len(data) if hasattr(data, "__len__") else 0,
                )
            )

        # Caller-bereitgestelltes metadata + Standard-Felder.
        # Standard-Felder gewinnen NICHT — der Caller darf z. B. ein
        # eigenes ``session_id`` setzen ohne dass es ueberschrieben wird,
        # solange es in raw_data["metadata"] steht.
        metadata: Dict[str, Any] = {
            "profile_name": profile_name,
            "permission_level": permission_level,
            "session_id": str(raw_data.get("session_id") or ""),
        }
        extra = raw_data.get("metadata")
        if isinstance(extra, dict):
            metadata.update(extra)

        return IncomingMessage(
            text=text,
            user_id=profile_name or "anonymous",
            channel=Channel.NALA,
            trust_level=trust,
            attachments=attachments,
            metadata=metadata,
        )

    def translate_outgoing(self, message: OutgoingMessage) -> Dict[str, Any]:
        """``OutgoingMessage`` → Nala-spezifische Response-Daten.

        Liefert ein generisches dict, das der Caller in eine
        OpenAI-kompatible ``ChatCompletionResponse``, einen SSE-Event
        (``data: {...}``) oder eine Datei-Download-Response uebersetzen
        kann.

        Felder:
            ``kind``:       ``"text"`` | ``"file"``
            ``text``:       Antwort-Text (bei ``kind=text``) oder
                            optionale Caption (bei ``kind=file``).
            ``file``:       Bytes des Datei-Outputs (bei ``kind=file``).
            ``file_name``:  Dateiname.
            ``mime_type``:  MIME.
            ``reply_to``:   Optionale Korrelations-ID (Caller-spezifisch,
                            in Nala typischerweise unbenutzt).
            ``metadata``:   Direkt durchgereicht (z. B. ``session_id``).
        """
        if message.file is not None:
            return {
                "kind": "file",
                "text": message.text,
                "file": message.file,
                "file_name": message.file_name or "nala.txt",
                "mime_type": message.mime_type or "application/octet-stream",
                "reply_to": message.reply_to,
                "metadata": dict(message.metadata or {}),
            }

        return {
            "kind": "text",
            "text": message.text or "",
            "file": None,
            "file_name": None,
            "mime_type": None,
            "reply_to": message.reply_to,
            "metadata": dict(message.metadata or {}),
        }

    async def send(self, message: OutgoingMessage) -> bool:
        """Nala antwortet ueber SSE / HTTP-Response, nicht ueber Push.

        Die SSE-Streaming-Logik liegt in ``app/routers/nala.py::sse_events``
        bzw. die JSON-Response wird vom jeweiligen Endpoint zurueckgegeben.
        Wer ``send`` braucht, hat den falschen Adapter — aus einem
        Background-Task in den Nala-Frontend zurueck-pushen geht nur
        ueber den EventBus (``zerberus/core/event_bus.py``).
        """
        raise NotImplementedError(
            "NalaAdapter.send: Nala antwortet ueber SSE/HTTP-Response, "
            "nicht ueber Push. Nutze translate_outgoing und schreibe das "
            "Result selbst in den SSE-Stream bzw. die HTTP-Response. "
            "Fuer Background-Push: zerberus/core/event_bus.py."
        )
