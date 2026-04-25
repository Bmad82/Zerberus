"""Patch 162 — Input-Sanitizer.

Regelbasierter Schutz vor Prompt-Injection und Müll-Input vor dem LLM-Call.

Zwei-Schichten-Prinzip:
- Rosa-Skelett: Interface ``InputSanitizer`` mit austauschbarer Implementierung.
- Huginn-jetzt: ``RegexSanitizer`` (Blocklist + Zeichensatz-Prüfung).

Aktuell hardcoded auf ``RegexSanitizer``. Config-Key
``security.input_sanitizer.mode = "regex"`` (Rosa: ``"ml"``) kommt mit
einem späteren Patch.
"""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("zerberus.sanitizer")


@dataclass
class SanitizeResult:
    """Ergebnis einer Sanitize-Prüfung."""
    cleaned_text: str
    findings: List[str] = field(default_factory=list)
    blocked: bool = False


class InputSanitizer(ABC):
    """Interface für Input-Sanitizer. Rosa kann ML-basierte Implementierung liefern."""

    @abstractmethod
    def sanitize(self, text: str, metadata: Optional[dict] = None) -> SanitizeResult:
        ...


class RegexSanitizer(InputSanitizer):
    """Regelbasierter Sanitizer für Huginn (pragmatisch).

    Prüft:
    1. Max-Länge (4096 Zeichen = Telegram-Limit)
    2. Steuerzeichen / Null-Bytes
    3. Bekannte Injection-Patterns (Blocklist)
    4. Forwarded-Message Kontext-Cap (K3, G5)
    """

    MAX_LENGTH = 4096

    # Bewusst konservativ — lieber ein Pattern weniger als ein False Positive
    # bei normalem Deutsch.
    INJECTION_PATTERNS = [
        # Direkte Anweisungs-Overrides
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"ignore\s+(all\s+)?prior\s+instructions",
        r"disregard\s+(all\s+)?previous",
        r"forget\s+(all\s+)?(your|previous)\s+instructions",
        r"you\s+are\s+now\s+(?:a|an|in)\s+(?:DAN|jailbreak|unrestricted)",
        r"(?:new|override|replace)\s+system\s*(?:prompt|instruction|message)",
        # Rollenspiel-Hijacking
        r"pretend\s+(?:you(?:'re|\s+are)\s+)?(?:a|an)\s+(?:evil|unrestricted|unfiltered)",
        r"act\s+as\s+(?:a|an)\s+(?:evil|unrestricted|unfiltered|jailbroken)",
        # Prompt-Leak-Versuche
        r"(?:show|reveal|display|print|output)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions)",
        r"what\s+(?:are|is)\s+your\s+(?:system\s+)?(?:prompt|instructions|rules)",
        # Markdown/Code-Fence Injection
        r"```\s*system\b",
        # Deutsche Varianten
        r"ignoriere?\s+(?:alle?\s+)?(?:vorherigen?|bisherigen?)\s+(?:Anweisungen?|Instruktionen?)",
        r"vergiss\s+(?:alle?\s+)?(?:deine?\s+)?(?:Anweisungen?|Regeln?|Instruktionen?)",
        r"du\s+bist\s+(?:jetzt|ab\s+jetzt|nun)\s+(?:ein|eine)\s+(?:böse|uneingeschränkte)",
        r"zeig(?:e)?\s+(?:mir\s+)?(?:deinen?|den)\s+(?:System[- ]?Prompt|Anweisungen?)",
    ]

    # ASCII-Steuerzeichen entfernen — \n \r \t bleiben erhalten
    CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def __init__(self) -> None:
        self._compiled_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.INJECTION_PATTERNS
        ]

    def sanitize(self, text: str, metadata: Optional[dict] = None) -> SanitizeResult:
        if not text:
            return SanitizeResult(cleaned_text="")

        findings: List[str] = []
        cleaned = text
        blocked = False
        original_len = len(cleaned)

        if original_len > self.MAX_LENGTH:
            cleaned = cleaned[: self.MAX_LENGTH]
            findings.append(f"TRUNCATED: {original_len} → {self.MAX_LENGTH} Zeichen")

        control_matches = self.CONTROL_CHAR_PATTERN.findall(cleaned)
        if control_matches:
            cleaned = self.CONTROL_CHAR_PATTERN.sub("", cleaned)
            findings.append(f"CONTROL_CHARS_REMOVED: {len(control_matches)} Steuerzeichen")

        for pattern in self._compiled_patterns:
            match = pattern.search(cleaned)
            if match:
                # Huginn-Modus: loggen + durchlassen. Guard entscheidet final.
                # Rosa-Modus (später, config-driven): blocken.
                findings.append(f"INJECTION_PATTERN: '{match.group()}'")

        if metadata and metadata.get("is_forwarded"):
            findings.append("FORWARDED_MESSAGE: als Forward markiert")

        if findings:
            preview = cleaned[:100] + "..." if len(cleaned) > 100 else cleaned
            user_id = metadata.get("user_id") if metadata else None
            logger.warning(
                "[SANITIZE-162] Findings=%s user_id=%s preview=%r",
                findings, user_id, preview,
            )

        return SanitizeResult(cleaned_text=cleaned, findings=findings, blocked=blocked)


_sanitizer: Optional[InputSanitizer] = None


def get_sanitizer() -> InputSanitizer:
    """Liefert den konfigurierten Sanitizer (Singleton).

    Aktuell: immer ``RegexSanitizer``. Rosa-Erweiterung wird Config-Key
    ``security.input_sanitizer.mode`` lesen und die passende Implementierung
    instanziieren.
    """
    global _sanitizer
    if _sanitizer is None:
        _sanitizer = RegexSanitizer()
        logger.info("[SANITIZE-162] Input-Sanitizer initialisiert (mode=regex)")
    return _sanitizer


def _reset_sanitizer_for_tests() -> None:
    """Test-Helper: setzt den Singleton zurück (nicht im Produktiv-Code aufrufen)."""
    global _sanitizer
    _sanitizer = None
