"""Patch 175 — Rosa-Adapter Placeholder (Phase E, Block 3).

Platzhalter fuer den Rosa-internen Messenger-Adapter. Wird in einer
zukuenftigen Phase implementiert wenn der interne Messenger
(WebSocket / Matrix / XMPP — Entscheidung offen) steht.

Die Klasse erfuellt die ``TransportAdapter``-Vertragsform aus P173, ihre
Methoden raisen aber alle ``NotImplementedError``. Der Sinn:

    - Anker-Punkt fuer den Phase-E-Plan (siehe
      ``docs/trust_boundary_diagram.md``).
    - Das Verzeichnis ``zerberus/adapters/`` ist damit komplett (Telegram,
      Nala, Rosa) — Nachfolge-Patches fuegen nur Code hinzu, keine neuen
      Dateien.
    - ``RosaAdapter()`` selbst ist instanziierbar (alle abstrakten
      Methoden sind ueberschrieben) — das macht ``__init__``-Tests trivial
      und stellt sicher dass der TransportAdapter-Vertrag eingehalten ist.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from zerberus.core.message_bus import IncomingMessage, OutgoingMessage
from zerberus.core.transport import TransportAdapter


class RosaAdapter(TransportAdapter):
    """Placeholder — Implementierung folgt mit dem internen Messenger."""

    _NOT_IMPL_MSG = (
        "RosaAdapter ist noch nicht implementiert. "
        "Wird mit dem internen Rosa-Messenger (Phase F) live geschaltet. "
        "Siehe docs/trust_boundary_diagram.md."
    )

    async def send(self, message: OutgoingMessage) -> bool:
        raise NotImplementedError(self._NOT_IMPL_MSG)

    def translate_incoming(
        self, raw_data: Dict[str, Any]
    ) -> Optional[IncomingMessage]:
        raise NotImplementedError(self._NOT_IMPL_MSG)

    def translate_outgoing(self, message: OutgoingMessage) -> Dict[str, Any]:
        raise NotImplementedError(self._NOT_IMPL_MSG)
