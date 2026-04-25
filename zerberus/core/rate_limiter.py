"""Patch 163 — Per-User Rate-Limiter für Huginn.

Rosa-Skelett: Interface ``RateLimiter`` mit austauschbarer Implementierung.
Huginn-jetzt: ``InMemoryRateLimiter`` (RAM-basiert, kein Redis nötig).

Config-Keys (in P163 vorbereitet, aktives Reading kommt mit Config-Refactor
Phase B): ``limits.per_user_rpm`` (Default 10), ``limits.cooldown_seconds``
(Default 60). Solange greift der Code-Default in :func:`get_rate_limiter`.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("zerberus.rate_limiter")


@dataclass
class RateLimitResult:
    """Ergebnis einer Rate-Limit-Prüfung."""

    allowed: bool
    remaining: int          # Verbleibende Requests im aktuellen Fenster
    retry_after: float      # Sekunden bis zum nächsten erlaubten Request (0 wenn allowed)
    first_rejection: bool   # True wenn dies die ERSTE Ablehnung in dieser Cooldown-Periode ist


class RateLimiter(ABC):
    """Interface für Rate-Limiter. Rosa kann Redis-basierte Implementierung liefern."""

    @abstractmethod
    def check(self, user_id: str) -> RateLimitResult:
        """Prüft ob ein User noch Requests senden darf."""

    @abstractmethod
    def cleanup(self) -> int:
        """Räumt abgelaufene Einträge auf. Gibt Anzahl entfernter Einträge zurück."""


@dataclass
class _UserBucket:
    """Interner Tracking-Bucket pro User."""

    timestamps: List[float] = field(default_factory=list)
    cooldown_until: float = 0.0
    notified: bool = False  # Ob "Sachte, Keule" schon gesendet wurde


class InMemoryRateLimiter(RateLimiter):
    """RAM-basierter Rate-Limiter (Huginn-jetzt).

    Sliding-Window pro User: maximal ``max_rpm`` Nachrichten pro 60-Sekunden-
    Fenster. Bei Überschreitung: ``cooldown_seconds`` Pause. Während des
    Cooldowns liefert :meth:`check` weiterhin ``allowed=False`` — aber nur die
    ERSTE Ablehnung trägt ``first_rejection=True``, damit der Konsument genau
    eine „Sachte, Keule"-Antwort senden kann statt jede Folge-Nachricht zu
    quittieren.
    """

    def __init__(self, max_rpm: int = 10, cooldown_seconds: int = 60):
        self.max_rpm = max_rpm
        self.cooldown_seconds = cooldown_seconds
        self._buckets: Dict[str, _UserBucket] = defaultdict(_UserBucket)

    def check(self, user_id: str) -> RateLimitResult:
        now = time.time()
        bucket = self._buckets[user_id]

        # Cooldown noch aktiv?
        if bucket.cooldown_until > now:
            retry_after = bucket.cooldown_until - now
            first = not bucket.notified
            bucket.notified = True
            return RateLimitResult(
                allowed=False,
                remaining=0,
                retry_after=retry_after,
                first_rejection=first,
            )

        # Cooldown gerade abgelaufen → Reset
        if bucket.cooldown_until > 0 and bucket.cooldown_until <= now:
            bucket.cooldown_until = 0.0
            bucket.notified = False
            bucket.timestamps.clear()

        # Sliding Window: nur Timestamps der letzten 60s behalten
        window_start = now - 60.0
        bucket.timestamps = [t for t in bucket.timestamps if t > window_start]

        # Limit prüfen
        if len(bucket.timestamps) >= self.max_rpm:
            bucket.cooldown_until = now + self.cooldown_seconds
            bucket.notified = True  # Diese Antwort ist die "first_rejection"
            logger.warning(
                "[RATELIMIT-163] User rate-limited user_id=%s rpm=%d cooldown=%ds",
                user_id, self.max_rpm, self.cooldown_seconds,
            )
            return RateLimitResult(
                allowed=False,
                remaining=0,
                retry_after=float(self.cooldown_seconds),
                first_rejection=True,
            )

        # Erlaubt
        bucket.timestamps.append(now)
        return RateLimitResult(
            allowed=True,
            remaining=self.max_rpm - len(bucket.timestamps),
            retry_after=0.0,
            first_rejection=False,
        )

    def cleanup(self) -> int:
        """Entfernt Buckets die seit 5 Minuten inaktiv sind."""
        now = time.time()
        stale_threshold = now - 300  # 5 Minuten
        stale_users = [
            uid for uid, bucket in self._buckets.items()
            if not bucket.timestamps or max(bucket.timestamps) < stale_threshold
        ]
        for uid in stale_users:
            del self._buckets[uid]
        if stale_users:
            logger.debug("[RATELIMIT-163] Cleanup: %d Buckets entfernt", len(stale_users))
        return len(stale_users)


# Modul-Singleton — wie ``get_settings()`` und ``get_sanitizer()``
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter(max_rpm: int = 10, cooldown_seconds: int = 60) -> RateLimiter:
    """Liefert den konfigurierten Rate-Limiter (Singleton).

    ``max_rpm`` und ``cooldown_seconds`` werden nur beim ersten Aufruf
    verwendet. Spätere Aufrufe liefern dieselbe Instanz unabhängig von den
    Argumenten.

    Rosa-Erweiterung: Config-Keys ``limits.per_user_rpm`` und
    ``limits.cooldown_seconds`` lesen sobald der Config-Refactor (Phase B)
    durch ist. Bis dahin steuern die Defaults aus dem Code.
    """
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = InMemoryRateLimiter(max_rpm=max_rpm, cooldown_seconds=cooldown_seconds)
        logger.info(
            "[RATELIMIT-163] Rate-Limiter initialisiert max_rpm=%d cooldown=%ds",
            max_rpm, cooldown_seconds,
        )
    return _rate_limiter


def _reset_rate_limiter_for_tests() -> None:
    """Test-Hilfe: setzt den Singleton zurück."""
    global _rate_limiter
    _rate_limiter = None
