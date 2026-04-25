"""Patch 164 — HitL-Policy: Entscheidet ob eine Aktion Bestätigung braucht.

Findings (aus 7-LLM-Review): K5 (Effort als Jailbreak-Verstärker), K6
(natürlich-sprachliche HitL-Bestätigung gefährlich), G3/G5 (Policy-Layer
muss VOR Persona-Layer stehen).

Architektur:

- Huginn-jetzt: Statische Regeln basierend auf Intent.
- Rosa-Erweiterung (Phase C+): Dynamische Policy mit Kontext-Awareness
  (User-Vertrauen, History, Tageszeit, Kosten-Budget, ...).

Policy-Tabelle::

    Intent   | LLM needs_hitl | Effective | hitl_type
    ---------|----------------|-----------|----------
    CHAT     | true / false   | false     | none      (NEVER_HITL überstimmt LLM)
    SEARCH   | true / false   | false     | none      (NEVER_HITL)
    IMAGE    | true / false   | false     | none      (NEVER_HITL)
    CODE     | true           | true      | button
    CODE     | false          | false     | none      (LLM-Vertrauen)
    FILE     | true           | true      | button
    FILE     | false          | false     | none      (LLM-Vertrauen)
    ADMIN    | true / false   | true      | button    (ADMIN erzwingt HitL)

K6: ``hitl_type="button"`` heißt **Inline-Keyboard mit ✅/❌**, NICHT
„antworte 'ja' im Chat". „Ja, mach den Server kaputt" ist kein gültiger
GO-Befehl. Aufrufer dürfen den ``button``-Pfad nicht durch natürliche
Sprache ersetzen.
"""
from __future__ import annotations

import logging
from typing import Optional, TypedDict

from zerberus.core.intent import HuginnIntent
from zerberus.core.intent_parser import ParsedResponse

logger = logging.getLogger("zerberus.hitl_policy")


class HitlDecision(TypedDict):
    needs_hitl: bool
    hitl_type: str          # "button" | "none"
    reason: str


class HitlPolicy:
    """Entscheidet ob HitL nötig ist und welchen Typ."""

    # Intents die IMMER eine Inline-Keyboard-Bestätigung brauchen sobald
    # das LLM ``needs_hitl=true`` setzt (oder ADMIN ungeachtet des Flags).
    BUTTON_REQUIRED = {HuginnIntent.CODE, HuginnIntent.FILE, HuginnIntent.ADMIN}

    # Intents die NIE HitL brauchen — selbst wenn das LLM ``needs_hitl=true``
    # setzt. Schutz gegen K5 (Effort-Inflation als Jailbreak-Verstärker).
    NEVER_HITL = {HuginnIntent.CHAT, HuginnIntent.SEARCH, HuginnIntent.IMAGE}

    def evaluate(self, parsed: ParsedResponse) -> HitlDecision:
        """Evaluiert ob HitL nötig ist und liefert Decision-Dict."""
        # 1) NEVER_HITL überstimmt das LLM-Flag.
        if parsed.intent in self.NEVER_HITL:
            if parsed.needs_hitl:
                logger.info(
                    "[HITL-POLICY-164] LLM wollte HitL für %s, überstimmt (NEVER_HITL)",
                    parsed.intent.value,
                )
            return {
                "needs_hitl": False,
                "hitl_type": "none",
                "reason": f"Intent {parsed.intent.value} braucht kein HitL",
            }

        # 2) ADMIN erzwingt IMMER HitL — auch wenn das LLM ``needs_hitl=false``
        # setzt. Schutz gegen jailbroken-LLM, das HitL-Flag manipuliert.
        if parsed.intent == HuginnIntent.ADMIN:
            if not parsed.needs_hitl:
                logger.warning(
                    "[HITL-POLICY-164] ADMIN-Intent ohne HitL-Flag → erzwungen",
                )
            return {
                "needs_hitl": True,
                "hitl_type": "button",
                "reason": "ADMIN-Intent erzwingt immer Button-Bestätigung",
            }

        # 3) CODE/FILE: LLM-Flag entscheidet, aber Bestätigung MUSS Button sein.
        if parsed.intent in self.BUTTON_REQUIRED and parsed.needs_hitl:
            return {
                "needs_hitl": True,
                "hitl_type": "button",
                "reason": f"Intent {parsed.intent.value} erfordert Button-Bestätigung",
            }

        # 4) Default: kein HitL.
        return {
            "needs_hitl": False,
            "hitl_type": "none",
            "reason": "Kein HitL nötig",
        }


# Modul-Singleton
_policy: Optional[HitlPolicy] = None


def get_hitl_policy() -> HitlPolicy:
    """Liefert die Policy-Instanz (Singleton)."""
    global _policy
    if _policy is None:
        _policy = HitlPolicy()
    return _policy


def _reset_hitl_policy_for_tests() -> None:
    """Test-Hilfe: setzt den Singleton zurück."""
    global _policy
    _policy = None
