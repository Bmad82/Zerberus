"""Patch 165 — Tests fuer ``zerberus.modules.telegram.hitl``.

Deckt den HitL-Lifecycle ab (P123/167): Request anlegen, approve/reject,
Timeout-Pfad, Callback-Daten-Parser, Inline-Keyboard-Builder.

Bisher gab es nur ``test_hitl_policy.py`` (Policy-Decisions); die
Manager-Klasse selbst war ungetestet.

Patch 167 — Sync-API ist Backward-Compat-Shim. UUID4-IDs sind 32 Hex-Zeichen.
"""
from __future__ import annotations

import asyncio

import pytest

from zerberus.modules.telegram.hitl import (
    HitlManager,
    HitlRequest,
    build_admin_keyboard,
    build_admin_message,
    build_group_decision_message,
    build_group_waiting_message,
    parse_callback_data,
)


# ----- HitlManager: Lifecycle -----------------------------------------------


class TestHitlManagerCreate:
    def test_create_request_returns_unique_id(self):
        m = HitlManager()
        a = m.create_request("code_execution", 1, "u1", "details A")
        b = m.create_request("code_execution", 2, "u2", "details B")
        assert a.request_id != b.request_id
        # Patch 167: UUID4-Hex (32 Zeichen) statt der alten 12-Zeichen-IDs.
        assert len(a.request_id) == 32

    def test_create_request_default_status_pending(self):
        m = HitlManager()
        req = m.create_request("group_join", 1, "u1", "details")
        assert req.status == "pending"
        assert req.resolved_at is None

    def test_create_request_payload_default_empty_dict(self):
        m = HitlManager()
        req = m.create_request("code_execution", 1, "u1", "x")
        assert req.payload == {}

    def test_create_request_propagates_user_id(self):
        # P162/O3 — requester_user_id muss durchgereicht werden.
        m = HitlManager()
        req = m.create_request(
            "code_execution", 42, "u1", "x", requester_user_id=999
        )
        assert req.requester_user_id == 999

    def test_get_returns_none_for_unknown_id(self):
        m = HitlManager()
        assert m.get("does-not-exist") is None


class TestHitlManagerDecisions:
    def test_approve_sets_status_and_event(self):
        m = HitlManager()
        req = m.create_request("code_execution", 1, "u1", "x")
        assert m.approve(req.request_id, admin_comment="ok") is True
        assert req.status == "approved"
        assert req.admin_comment == "ok"
        assert req.resolved_at is not None

    def test_reject_sets_status_and_comment(self):
        m = HitlManager()
        req = m.create_request("code_execution", 1, "u1", "x")
        assert m.reject(req.request_id, admin_comment="nope") is True
        assert req.status == "rejected"
        assert req.admin_comment == "nope"

    def test_approve_unknown_id_returns_false(self):
        m = HitlManager()
        assert m.approve("does-not-exist") is False

    def test_approve_already_resolved_request_returns_false(self):
        m = HitlManager()
        req = m.create_request("code_execution", 1, "u1", "x")
        m.approve(req.request_id)
        # Zweiter Approve auf bereits-approved Request darf nicht greifen.
        assert m.approve(req.request_id) is False

    def test_reject_already_approved_request_returns_false(self):
        m = HitlManager()
        req = m.create_request("code_execution", 1, "u1", "x")
        m.approve(req.request_id)
        assert m.reject(req.request_id) is False


class TestHitlManagerWait:
    def test_wait_returns_approved_after_approve(self):
        async def run():
            m = HitlManager(timeout_seconds=10, persistent=False)
            req = m.create_request("code_execution", 1, "u1", "x")

            async def approver():
                await asyncio.sleep(0.01)
                m.approve(req.request_id)

            results = await asyncio.gather(
                m.wait_for_decision(req.request_id, timeout=2.0),
                approver(),
            )
            return results[0]

        assert asyncio.run(run()) == "approved"

    def test_wait_returns_timeout_after_no_decision(self):
        async def run():
            # persistent=False — reiner In-Memory-Modus (kein DB-Stub noetig).
            m = HitlManager(timeout_seconds=10, persistent=False)
            req = m.create_request("code_execution", 1, "u1", "x")
            return await m.wait_for_decision(req.request_id, timeout=0.05)

        # Patch 167: Status heisst jetzt 'expired' statt 'timeout'.
        assert asyncio.run(run()) == "expired"

    def test_wait_unknown_id_returns_unknown(self):
        async def run():
            m = HitlManager()
            return await m.wait_for_decision("does-not-exist", timeout=0.05)

        assert asyncio.run(run()) == "unknown"


# ----- Helper: parse_callback_data ------------------------------------------


class TestParseCallbackData:
    def test_approve_parsed(self):
        assert parse_callback_data("hitl_approve:abc123") == {
            "action": "hitl_approve",
            "request_id": "abc123",
        }

    def test_reject_parsed(self):
        assert parse_callback_data("hitl_reject:abc123") == {
            "action": "hitl_reject",
            "request_id": "abc123",
        }

    def test_unknown_action_returns_none(self):
        assert parse_callback_data("foo:bar") is None

    def test_missing_colon_returns_none(self):
        assert parse_callback_data("hitl_approve_no_colon") is None

    def test_empty_input_returns_none(self):
        assert parse_callback_data("") is None
        assert parse_callback_data(None) is None  # type: ignore[arg-type]


# ----- Helper: Keyboard + Message Builder -----------------------------------


class TestBuildAdminKeyboard:
    def test_keyboard_has_two_buttons(self):
        kb = build_admin_keyboard("rid42")
        row = kb["inline_keyboard"][0]
        assert len(row) == 2
        assert row[0]["callback_data"] == "hitl_approve:rid42"
        assert row[1]["callback_data"] == "hitl_reject:rid42"

    def test_keyboard_button_labels_have_emojis(self):
        kb = build_admin_keyboard("rid42")
        row = kb["inline_keyboard"][0]
        assert "Freigeben" in row[0]["text"]
        assert "Ablehnen" in row[1]["text"]


class TestBuildAdminMessage:
    def test_admin_message_contains_metadata(self):
        # Patch 167: HitlRequest ist Alias fuer HitlTask mit neuen Feld-Namen.
        req = HitlRequest(
            id="rid42",
            requester_id=42,
            chat_id=1234,
            intent="code_execution",
            requester_username="chris",
            details="run rm -rf /",
        )
        msg = build_admin_message(req)
        assert "rid42" in msg
        assert "code_execution" in msg
        assert "@chris" in msg
        assert "1234" in msg
        assert "rm -rf" in msg

    def test_admin_message_truncates_long_details(self):
        long_details = "Z" * 5000
        req = HitlRequest(
            id="rid42",
            requester_id=42,
            chat_id=1,
            intent="group_join",  # kein 'Z' im Vorwort
            requester_username="u",
            details=long_details,
        )
        msg = build_admin_message(req)
        assert msg.count("Z") == 1500


class TestBuildGroupMessages:
    def test_waiting_message_contains_request_id_and_type(self):
        req = HitlRequest(
            id="rid42",
            requester_id=42,
            chat_id=1,
            intent="group_join",
            requester_username="u",
            details="x",
        )
        msg = build_group_waiting_message(req)
        assert "rid42" in msg
        assert "group_join" in msg

    def test_decision_message_approved(self):
        req = HitlRequest(
            id="rid42",
            requester_id=42,
            chat_id=1,
            intent="x",
            requester_username="u",
            details="d",
            status="approved",
        )
        assert "freigegeben" in build_group_decision_message(req).lower()

    def test_decision_message_rejected_with_reason(self):
        req = HitlRequest(
            id="rid42",
            requester_id=42,
            chat_id=1,
            intent="x",
            requester_username="u",
            details="d",
            status="rejected",
            admin_comment="zu riskant",
        )
        msg = build_group_decision_message(req)
        assert "abgelehnt" in msg.lower()
        assert "zu riskant" in msg

    def test_decision_message_timeout(self):
        # Patch 167: 'expired' ist der neue Status; 'timeout' bleibt als
        # Backward-Compat erkannt (Patch 123).
        req = HitlRequest(
            id="rid42",
            requester_id=42,
            chat_id=1,
            intent="x",
            requester_username="u",
            details="d",
            status="expired",
        )
        assert "Keine Admin-Reaktion" in build_group_decision_message(req)
