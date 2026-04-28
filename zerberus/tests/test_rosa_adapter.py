"""Patch 175 — Tests fuer ``adapters/rosa_adapter.py`` (Phase E, Block 3 — Stub)."""
from __future__ import annotations

import asyncio

import pytest

from zerberus.adapters.rosa_adapter import RosaAdapter
from zerberus.core.message_bus import OutgoingMessage
from zerberus.core.transport import TransportAdapter


class TestRosaAdapter:
    def test_ist_transport_adapter_subclass(self):
        # Wichtig fuer den Phase-E-Vertrag: alle adapter implementieren
        # die gleiche Basis.
        assert issubclass(RosaAdapter, TransportAdapter)

    def test_kann_instanziiert_werden(self):
        # Alle abstrakten Methoden sind in der Stub-Klasse ueberschrieben,
        # sonst wuerde Python beim ``RosaAdapter()`` einen TypeError werfen.
        # Heisst: der TransportAdapter-Vertrag ist formal eingehalten.
        a = RosaAdapter()
        assert isinstance(a, TransportAdapter)

    def test_send_raised_not_implemented(self):
        a = RosaAdapter()
        with pytest.raises(NotImplementedError) as exc:
            asyncio.run(a.send(OutgoingMessage(text="x")))
        assert "Rosa" in str(exc.value)

    def test_translate_incoming_raised_not_implemented(self):
        a = RosaAdapter()
        with pytest.raises(NotImplementedError):
            a.translate_incoming({"foo": "bar"})

    def test_translate_outgoing_raised_not_implemented(self):
        a = RosaAdapter()
        with pytest.raises(NotImplementedError):
            a.translate_outgoing(OutgoingMessage(text="x"))

    def test_fehlertext_verweist_auf_diagramm(self):
        # Phase-E-Anker: wer den Stub trifft soll wissen wo er nachlesen kann.
        a = RosaAdapter()
        with pytest.raises(NotImplementedError) as exc:
            asyncio.run(a.send(OutgoingMessage(text="x")))
        assert "trust_boundary_diagram" in str(exc.value)
