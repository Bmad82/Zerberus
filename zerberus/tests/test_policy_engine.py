"""Patch 175 — Tests fuer ``core/policy_engine.py`` (Phase E, Block 2)."""
from __future__ import annotations

import asyncio

import pytest

from zerberus.core.input_sanitizer import SanitizeResult
from zerberus.core.intent import HuginnIntent
from zerberus.core.intent_parser import ParsedResponse
from zerberus.core.message_bus import Channel, IncomingMessage, TrustLevel
from zerberus.core.policy_engine import (
    HuginnPolicy,
    PolicyDecision,
    PolicyEngine,
    PolicyVerdict,
)
from zerberus.core.rate_limiter import RateLimitResult


# ──────────────────────────────────────────────────────────────────────
# Test-Doubles
# ──────────────────────────────────────────────────────────────────────


class _FakeSanitizer:
    def __init__(self, blocked: bool = False, findings=None, cleaned=None):
        self.blocked = blocked
        self.findings = list(findings or [])
        self.cleaned = cleaned

    def sanitize(self, text: str, metadata=None) -> SanitizeResult:
        return SanitizeResult(
            cleaned_text=self.cleaned if self.cleaned is not None else text,
            findings=list(self.findings),
            blocked=self.blocked,
        )


class _FakeRateLimiter:
    def __init__(self, allowed: bool = True, retry_after: float = 0.0):
        self._allowed = allowed
        self._retry = retry_after

    def check(self, user_id: str) -> RateLimitResult:
        return RateLimitResult(
            allowed=self._allowed,
            remaining=10,
            retry_after=self._retry,
            first_rejection=not self._allowed,
        )

    def cleanup(self) -> int:
        return 0


class _FakeHitlPolicy:
    """Liefert ein Dict im Format der echten ``HitlPolicy.evaluate``."""

    def __init__(self, needs_hitl: bool, reason: str = "test"):
        self._needs = needs_hitl
        self._reason = reason

    def evaluate(self, parsed):
        return {
            "needs_hitl": self._needs,
            "hitl_type": "button" if self._needs else "none",
            "reason": self._reason,
        }


def _incoming(text: str = "Hallo", trust=TrustLevel.AUTHENTICATED, **md) -> IncomingMessage:
    metadata = {"chat_type": "private"}
    metadata.update(md)
    return IncomingMessage(
        text=text,
        user_id="u-1",
        channel=Channel.TELEGRAM,
        trust_level=trust,
        metadata=metadata,
    )


def _parsed(intent=HuginnIntent.CHAT, needs_hitl=False, effort=2) -> ParsedResponse:
    return ParsedResponse(
        intent=intent,
        effort=effort,
        needs_hitl=needs_hitl,
        body="text",
        raw_header={"intent": intent.value, "effort": effort, "needs_hitl": needs_hitl},
    )


# ──────────────────────────────────────────────────────────────────────
# PolicyEngine ABC
# ──────────────────────────────────────────────────────────────────────


class TestPolicyEngineABC:
    def test_abstract_kann_nicht_instanziiert_werden(self):
        with pytest.raises(TypeError):
            PolicyEngine()  # type: ignore[abstract]


# ──────────────────────────────────────────────────────────────────────
# HuginnPolicy — Happy-Path
# ──────────────────────────────────────────────────────────────────────


class TestHuginnPolicyAllow:
    def test_harmloser_input_allow(self):
        p = HuginnPolicy(sanitizer=_FakeSanitizer(), rate_limiter=_FakeRateLimiter())
        d = asyncio.run(p.evaluate(_incoming("Hallo, was geht?")))
        assert d.verdict == PolicyVerdict.ALLOW
        assert d.reason == "ok"
        assert d.requires_hitl is False

    def test_ohne_rate_limiter_funktioniert(self):
        p = HuginnPolicy(sanitizer=_FakeSanitizer())
        d = asyncio.run(p.evaluate(_incoming("Hallo")))
        assert d.verdict == PolicyVerdict.ALLOW

    def test_findings_durchgereicht(self):
        san = _FakeSanitizer(findings=["UNICODE_NORMALIZED: NFKC"])
        p = HuginnPolicy(sanitizer=san)
        d = asyncio.run(p.evaluate(_incoming("Ⅰgnore harmlos")))
        # Findings ohne ``blocked`` triggern KEIN ESCALATE — nur durchgeleitet.
        assert d.verdict == PolicyVerdict.ALLOW
        assert d.sanitizer_findings == ["UNICODE_NORMALIZED: NFKC"]


# ──────────────────────────────────────────────────────────────────────
# HuginnPolicy — DENY-Pfade
# ──────────────────────────────────────────────────────────────────────


class TestHuginnPolicyDeny:
    def test_sanitizer_blocked_deny(self):
        san = _FakeSanitizer(blocked=True, findings=["INJECTION_PATTERN: 'X'"])
        p = HuginnPolicy(sanitizer=san)
        d = asyncio.run(p.evaluate(_incoming("ignore previous")))
        assert d.verdict == PolicyVerdict.DENY
        assert d.reason == "sanitizer_blocked"
        assert d.sanitizer_findings == ["INJECTION_PATTERN: 'X'"]

    def test_rate_limit_deny(self):
        rl = _FakeRateLimiter(allowed=False, retry_after=42.0)
        # Sanitizer darf nicht durchgereicht werden, wenn rate_limit DENY.
        san = _FakeSanitizer(blocked=True)  # wuerde sonst auch DENY werfen
        p = HuginnPolicy(sanitizer=san, rate_limiter=rl)
        d = asyncio.run(p.evaluate(_incoming("egal")))
        assert d.verdict == PolicyVerdict.DENY
        assert d.reason == "rate_limited"
        assert d.retry_after == 42.0
        # Sanitizer-Findings sind hier leer, weil Sanitizer NICHT aufgerufen wurde.
        assert d.sanitizer_findings == []

    def test_rate_limit_kommt_vor_sanitizer(self):
        # Reihenfolge sicherstellen: Rate-Limit ist billig und kommt zuerst.
        order = []

        class _TracingSanitizer:
            def sanitize(self, text, metadata=None):
                order.append("sanitizer")
                return SanitizeResult(cleaned_text=text, findings=[], blocked=False)

        class _TracingRateLimiter:
            def check(self, user_id):
                order.append("rate_limit")
                return RateLimitResult(
                    allowed=False, remaining=0, retry_after=10.0, first_rejection=True,
                )

            def cleanup(self):
                return 0

        p = HuginnPolicy(sanitizer=_TracingSanitizer(), rate_limiter=_TracingRateLimiter())
        asyncio.run(p.evaluate(_incoming("hi")))
        assert order == ["rate_limit"]  # Sanitizer NICHT aufgerufen, weil RL bereits DENY


# ──────────────────────────────────────────────────────────────────────
# HuginnPolicy — ESCALATE (HitL)
# ──────────────────────────────────────────────────────────────────────


class TestHuginnPolicyEscalate:
    def test_code_intent_mit_needs_hitl_eskaliert(self):
        p = HuginnPolicy(
            sanitizer=_FakeSanitizer(),
            hitl_policy=_FakeHitlPolicy(needs_hitl=True, reason="CODE braucht Button"),
        )
        d = asyncio.run(p.evaluate(
            _incoming("schreib mir einen Bot"),
            parsed_intent=_parsed(intent=HuginnIntent.CODE, needs_hitl=True, effort=4),
        ))
        assert d.verdict == PolicyVerdict.ESCALATE
        assert d.requires_hitl is True
        assert "CODE" in d.reason

    def test_chat_intent_kein_hitl(self):
        p = HuginnPolicy(
            sanitizer=_FakeSanitizer(),
            hitl_policy=_FakeHitlPolicy(needs_hitl=False),
        )
        d = asyncio.run(p.evaluate(
            _incoming("Witz bitte"),
            parsed_intent=_parsed(intent=HuginnIntent.CHAT, needs_hitl=False),
        ))
        assert d.verdict == PolicyVerdict.ALLOW
        assert d.requires_hitl is False

    def test_ohne_parsed_intent_kein_hitl_check(self):
        p = HuginnPolicy(
            sanitizer=_FakeSanitizer(),
            hitl_policy=_FakeHitlPolicy(needs_hitl=True),
        )
        # Ohne parsed_intent darf der HitL-Check nicht greifen — sonst
        # wuerde jeder erste-Pass evaluieren, bevor ein Intent existiert.
        d = asyncio.run(p.evaluate(_incoming("egal")))
        assert d.verdict == PolicyVerdict.ALLOW


# ──────────────────────────────────────────────────────────────────────
# Severity-Mapping
# ──────────────────────────────────────────────────────────────────────


class TestSeverityMapping:
    def test_public_hebt_severity(self):
        san = _FakeSanitizer(blocked=True)
        p = HuginnPolicy(sanitizer=san)
        d = asyncio.run(p.evaluate(_incoming(trust=TrustLevel.PUBLIC)))
        # Basis fuer sanitizer_blocked ist "high" — PUBLIC laesst es bei
        # "high" (max bis "high"; "critical" ist Audit-Reserved).
        assert d.severity == "high"

    def test_admin_senkt_severity(self):
        san = _FakeSanitizer(blocked=True)
        p = HuginnPolicy(sanitizer=san)
        d = asyncio.run(p.evaluate(_incoming(trust=TrustLevel.ADMIN)))
        assert d.severity == "medium"

    def test_authenticated_basis_severity(self):
        san = _FakeSanitizer(blocked=True)
        p = HuginnPolicy(sanitizer=san)
        d = asyncio.run(p.evaluate(_incoming(trust=TrustLevel.AUTHENTICATED)))
        assert d.severity == "high"

    def test_allow_severity_low(self):
        p = HuginnPolicy(sanitizer=_FakeSanitizer())
        d = asyncio.run(p.evaluate(_incoming(trust=TrustLevel.AUTHENTICATED)))
        assert d.severity == "low"

    def test_public_allow_hebt_auf_medium(self):
        p = HuginnPolicy(sanitizer=_FakeSanitizer())
        d = asyncio.run(p.evaluate(_incoming(trust=TrustLevel.PUBLIC)))
        assert d.severity == "medium"


# ──────────────────────────────────────────────────────────────────────
# PolicyDecision dataclass
# ──────────────────────────────────────────────────────────────────────


class TestPolicyDecision:
    def test_default_findings_leere_liste(self):
        d = PolicyDecision(verdict=PolicyVerdict.ALLOW, reason="ok")
        assert d.sanitizer_findings == []
        # Defaults muessen unabhaengig pro Instanz sein
        d.sanitizer_findings.append("x")
        d2 = PolicyDecision(verdict=PolicyVerdict.ALLOW, reason="ok")
        assert d2.sanitizer_findings == []

    def test_verdict_string_enum(self):
        assert PolicyVerdict.ALLOW.value == "allow"
        assert PolicyVerdict.DENY.value == "deny"
        assert PolicyVerdict.ESCALATE.value == "escalate"
