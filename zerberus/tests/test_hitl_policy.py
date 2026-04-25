"""Patch 164 — Tests fuer die HitL-Policy."""
from __future__ import annotations

import pytest

from zerberus.core.hitl_policy import (
    HitlPolicy,
    _reset_hitl_policy_for_tests,
    get_hitl_policy,
)
from zerberus.core.intent import HuginnIntent
from zerberus.core.intent_parser import ParsedResponse


def _parsed(intent: HuginnIntent, needs_hitl: bool = False, effort: int = 3) -> ParsedResponse:
    return ParsedResponse(
        intent=intent,
        effort=effort,
        needs_hitl=needs_hitl,
        body="x",
        raw_header={"intent": intent.value, "effort": effort, "needs_hitl": needs_hitl},
    )


@pytest.fixture(autouse=True)
def _reset_singleton():
    _reset_hitl_policy_for_tests()
    yield
    _reset_hitl_policy_for_tests()


class TestNeverHitlOverridesLLM:
    def test_chat_with_hitl_flag_overridden(self, caplog):
        """CHAT + needs_hitl=true → ueberstimmt, kein HitL (K5-Schutz)."""
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.CHAT, needs_hitl=True))
        assert decision["needs_hitl"] is False
        assert decision["hitl_type"] == "none"

    def test_chat_no_hitl(self):
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.CHAT, needs_hitl=False))
        assert decision["needs_hitl"] is False
        assert decision["hitl_type"] == "none"

    def test_search_no_hitl(self):
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.SEARCH, needs_hitl=True))
        assert decision["needs_hitl"] is False
        assert decision["hitl_type"] == "none"

    def test_image_no_hitl(self):
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.IMAGE, needs_hitl=True))
        assert decision["needs_hitl"] is False


class TestButtonRequiredIntents:
    def test_code_with_hitl_needs_button(self):
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.CODE, needs_hitl=True))
        assert decision["needs_hitl"] is True
        assert decision["hitl_type"] == "button"

    def test_file_with_hitl_needs_button(self):
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.FILE, needs_hitl=True))
        assert decision["needs_hitl"] is True
        assert decision["hitl_type"] == "button"

    def test_code_without_hitl_passes(self):
        """LLM sagt needs_hitl=false fuer CODE → vertrauen, kein HitL."""
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.CODE, needs_hitl=False))
        assert decision["needs_hitl"] is False
        assert decision["hitl_type"] == "none"


class TestAdminAlwaysHitl:
    def test_admin_with_hitl_needs_button(self):
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.ADMIN, needs_hitl=True))
        assert decision["needs_hitl"] is True
        assert decision["hitl_type"] == "button"

    def test_admin_without_hitl_flag_still_required(self, caplog):
        """ADMIN ueberstimmt LLM, das needs_hitl=false setzt (K6-Schutz)."""
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.ADMIN, needs_hitl=False))
        assert decision["needs_hitl"] is True
        assert decision["hitl_type"] == "button"
        assert "ADMIN" in decision["reason"]


class TestSingleton:
    def test_get_hitl_policy_returns_same_instance(self):
        a = get_hitl_policy()
        b = get_hitl_policy()
        assert a is b

    def test_reset_helper_clears_singleton(self):
        a = get_hitl_policy()
        _reset_hitl_policy_for_tests()
        b = get_hitl_policy()
        assert a is not b
