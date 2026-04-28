"""Patch 175 — Policy-Engine-Interface (Phase E, Block 2).

Entscheidet BEVOR der LLM-Guard aufgerufen wird, ob eine eingehende
Nachricht ueberhaupt verarbeitet werden darf. Deterministische Schicht
(Sanitizer, Rate-Limit, HitL-Vorab-Check) — schnell, billig, vorhersehbar.

Zwei Implementierungen:
    - ``HuginnPolicy``: Pragmatisch, Fassade ueber die bestehenden
      Module ``input_sanitizer.py`` (P162/P173), ``hitl_policy.py`` (P164),
      ``rate_limiter.py`` (P163). Wird in P175 angeboten — die Pipeline
      kann optional eine ``PolicyEngine`` im ``PipelineDeps`` mitfuehren;
      ohne ist das alte Verhalten aktiv (direkte Modul-Aufrufe).
    - ``RosaPolicy``: streng, multi-layer, audit-trail. Spaeterer Patch.

WICHTIG (P175-Scope):
    - HuginnPolicy WRAPPET die bestehenden Module — kein Ersatz.
    - Keine Audit-Trail-Implementierung (nur in der Doku erwaehnt).
    - Kein Admin-Rollen-System — weiterhin single ``admin_chat_id`` /
      Admin-JWT.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from .input_sanitizer import InputSanitizer
from .intent import HuginnIntent
from .intent_parser import ParsedResponse
from .message_bus import IncomingMessage, TrustLevel
from .rate_limiter import RateLimiter

logger = logging.getLogger("zerberus.policy")


class PolicyVerdict(str, Enum):
    """Drei moegliche Verdikte einer Policy-Entscheidung."""
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"   # An Admin/HitL weiterleiten


@dataclass
class PolicyDecision:
    """Ergebnis einer ``PolicyEngine.evaluate``-Auswertung.

    Felder:
        verdict:        ALLOW / DENY / ESCALATE
        reason:         Maschinenlesbarer Grund-Slug (``ok``, ``rate_limited``,
                        ``sanitizer_blocked``, ``hitl_required``, ...).
        requires_hitl:  True wenn ESCALATE einen HitL-Button-Flow ausloesen
                        soll (Caller verantwortlich).
        severity:       ``low`` / ``medium`` / ``high`` / ``critical`` —
                        primaer fuer Logging/Telemetrie.
        sanitizer_findings: Optionale Findings vom Sanitizer-Pass (Liste
                        von Strings im P162-Format).
        retry_after:    Bei ``rate_limited`` — Sekunden bis naechster
                        Versuch erlaubt ist (sonst 0.0).
    """
    verdict: PolicyVerdict
    reason: str
    requires_hitl: bool = False
    severity: str = "low"
    sanitizer_findings: list[str] = None  # type: ignore[assignment]
    retry_after: float = 0.0

    def __post_init__(self) -> None:
        if self.sanitizer_findings is None:
            self.sanitizer_findings = []


class PolicyEngine(ABC):
    """Interface fuer Policy-Entscheidungen vor dem LLM-Call."""

    @abstractmethod
    async def evaluate(
        self,
        message: IncomingMessage,
        parsed_intent: Optional[ParsedResponse] = None,
    ) -> PolicyDecision:
        """Evaluiere eine eingehende Nachricht.

        Args:
            message:        Die eingehende Nachricht (post-Adapter).
            parsed_intent:  Optional — falls der Caller bereits eine
                            geparste LLM-Antwort hat, kann der HitL-Check
                            den Intent direkt einbeziehen. Bei ``None``
                            wird der HitL-Check uebersprungen.
        """
        ...


# ──────────────────────────────────────────────────────────────────────
# HuginnPolicy — Fassade ueber bestehende Module
# ──────────────────────────────────────────────────────────────────────


class HuginnPolicy(PolicyEngine):
    """Pragmatische Policy fuer Huginn (P175).

    Aggregiert die existierenden Schichten zu einer einzigen
    ``PolicyDecision``. Die Reihenfolge ist absichtlich:

        1. Rate-Limit (billigster Check, ein Dict-Lookup)
        2. Sanitizer (Regex-Match, ms-Bereich)
        3. HitL-Check (nur wenn ``parsed_intent`` mitgegeben — sonst skip)

    Trust-Level beeinflusst nur das ``severity``-Feld der Decision; die
    deterministischen Checks selbst sind trust-blind (defense-in-depth:
    auch ein Admin soll einen kaputten Loop nicht in Sekunden 1000x
    durchjagen koennen).
    """

    def __init__(
        self,
        sanitizer: InputSanitizer,
        rate_limiter: Optional[RateLimiter] = None,
        hitl_policy: Optional[Any] = None,  # zerberus.core.hitl_policy.HitlPolicy
    ) -> None:
        self._sanitizer = sanitizer
        self._rate_limiter = rate_limiter
        self._hitl_policy = hitl_policy

    async def evaluate(
        self,
        message: IncomingMessage,
        parsed_intent: Optional[ParsedResponse] = None,
    ) -> PolicyDecision:
        # ── 1. Rate-Limit ──────────────────────────────────────────────
        if self._rate_limiter is not None and message.user_id:
            rl_result = self._rate_limiter.check(message.user_id)
            if not rl_result.allowed:
                logger.info(
                    "[POLICY-175] Rate-Limit Deny user=%s retry_after=%.1fs first=%s",
                    message.user_id, rl_result.retry_after, rl_result.first_rejection,
                )
                return PolicyDecision(
                    verdict=PolicyVerdict.DENY,
                    reason="rate_limited",
                    severity=self._severity_for(message.trust_level, base="medium"),
                    retry_after=rl_result.retry_after,
                )

        # ── 2. Sanitizer ───────────────────────────────────────────────
        san_result = self._sanitizer.sanitize(
            message.text or "",
            metadata={
                "user_id": message.user_id,
                "chat_type": message.metadata.get("chat_type", "private"),
                "is_forwarded": bool(message.metadata.get("is_forwarded")),
                "is_reply": message.metadata.get("reply_to_message_id") is not None,
            },
        )
        if san_result.blocked:
            return PolicyDecision(
                verdict=PolicyVerdict.DENY,
                reason="sanitizer_blocked",
                severity=self._severity_for(message.trust_level, base="high"),
                sanitizer_findings=list(san_result.findings),
            )
        # Findings ohne ``blocked`` sind Hinweise — wir loggen sie via
        # Sanitizer selbst; hier KEIN Eskalations-Trigger, sonst rotten
        # WARNUNG-Patterns in einem zu strengen Pre-Check (das war die
        # Lehre aus der "Determinismus dominiert Semantik"-Diskussion in
        # docs/guard_policy_limits.md).

        # ── 3. HitL-Check (nur mit parsed_intent) ──────────────────────
        if parsed_intent is not None and self._hitl_policy is not None:
            hitl = self._hitl_policy.evaluate(parsed_intent)
            if hitl.get("needs_hitl"):
                return PolicyDecision(
                    verdict=PolicyVerdict.ESCALATE,
                    reason=hitl.get("reason", "hitl_required"),
                    requires_hitl=True,
                    severity=self._severity_for(message.trust_level, base="medium"),
                    sanitizer_findings=list(san_result.findings),
                )

        # ── ALLOW ──────────────────────────────────────────────────────
        return PolicyDecision(
            verdict=PolicyVerdict.ALLOW,
            reason="ok",
            severity=self._severity_for(message.trust_level, base="low"),
            sanitizer_findings=list(san_result.findings),
        )

    @staticmethod
    def _severity_for(trust: TrustLevel, base: str) -> str:
        """Hebt severity bei PUBLIC eine Stufe an, senkt sie bei ADMIN.

        ADMIN-DENY bleibt mindestens ``medium`` — ein Admin-Block ist
        immer auffaellig genug. PUBLIC-DENY heben wir nie auf
        ``critical`` weil ``critical`` der Audit-Trail-Trigger fuer Rosa
        sein soll, der hier noch nicht existiert.
        """
        order = ["low", "medium", "high", "critical"]
        idx = order.index(base) if base in order else 0
        if trust == TrustLevel.PUBLIC and idx < 2:
            idx += 1
        elif trust == TrustLevel.ADMIN and idx > 0:
            idx -= 1
        return order[idx]
