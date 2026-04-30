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


class TestAdminPlausibilityCheckP182:
    """Patch 182: Wenn das LLM ADMIN sagt, der User-Text aber keine
    Admin-Marker enthaelt (Slash-Prefix oder Keyword), wird der Verdict
    auf CHAT downgegradet. Schutz vor Smalltalk-False-Positives.
    """

    def test_admin_ohne_user_message_unveraendert(self):
        """Backward-Compat: Aufrufer ohne user_message-Argument bekommen
        das alte P164-Verhalten (ADMIN erzwingt HitL).
        """
        policy = HitlPolicy()
        decision = policy.evaluate(_parsed(HuginnIntent.ADMIN, needs_hitl=False))
        assert decision["needs_hitl"] is True

    def test_admin_smalltalk_wird_chat(self):
        """'Wie geht's dir?' → ADMIN-Verdict downgegradet auf CHAT."""
        policy = HitlPolicy()
        decision = policy.evaluate(
            _parsed(HuginnIntent.ADMIN, needs_hitl=True),
            user_message="Wie geht's dir, Rabe?",
        )
        assert decision["needs_hitl"] is False
        assert decision["hitl_type"] == "none"
        assert "ADMIN" in decision["reason"]

    def test_admin_mit_slash_befehl_bleibt(self):
        """'/status' → echter Admin-Befehl, ADMIN bleibt."""
        policy = HitlPolicy()
        decision = policy.evaluate(
            _parsed(HuginnIntent.ADMIN, needs_hitl=True),
            user_message="/status",
        )
        assert decision["needs_hitl"] is True
        assert decision["hitl_type"] == "button"

    def test_admin_mit_keyword_bleibt(self):
        """Wort 'restart' im Text → Admin-Marker, ADMIN bleibt."""
        policy = HitlPolicy()
        decision = policy.evaluate(
            _parsed(HuginnIntent.ADMIN, needs_hitl=True),
            user_message="Mach mal nen Restart bitte",
        )
        assert decision["needs_hitl"] is True

    def test_admin_mit_config_keyword_bleibt(self):
        policy = HitlPolicy()
        decision = policy.evaluate(
            _parsed(HuginnIntent.ADMIN, needs_hitl=True),
            user_message="Zeig mir die Config",
        )
        assert decision["needs_hitl"] is True

    def test_admin_keyword_substring_matched_nicht_falsch(self):
        """'voraussetzung' enthaelt 'auss' aber kein Admin-Token —
        regex-basiertes Token-Matching darf das nicht als Admin werten.
        """
        policy = HitlPolicy()
        decision = policy.evaluate(
            _parsed(HuginnIntent.ADMIN, needs_hitl=True),
            user_message="Eine Voraussetzung dafuer ist Geduld",
        )
        # Hier ist KEIN Admin-Token drin → downgrade auf CHAT.
        assert decision["needs_hitl"] is False

    def test_chat_intent_unbeeinflusst_von_user_message(self):
        """Plausibilitaets-Check greift NUR fuer ADMIN — CHAT bleibt CHAT."""
        policy = HitlPolicy()
        decision = policy.evaluate(
            _parsed(HuginnIntent.CHAT, needs_hitl=True),
            user_message="/status",  # Slash, aber Intent ist CHAT
        )
        assert decision["needs_hitl"] is False  # NEVER_HITL fuer CHAT

    def test_code_intent_unbeeinflusst(self):
        """CODE+needs_hitl bleibt button — der ADMIN-Plausi-Check greift nicht."""
        policy = HitlPolicy()
        decision = policy.evaluate(
            _parsed(HuginnIntent.CODE, needs_hitl=True),
            user_message="Wie geht's?",
        )
        assert decision["needs_hitl"] is True
        assert decision["hitl_type"] == "button"

    def test_reset_helper_clears_singleton(self):
        a = get_hitl_policy()
        _reset_hitl_policy_for_tests()
        b = get_hitl_policy()
        assert a is not b
