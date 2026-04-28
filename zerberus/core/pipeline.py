"""Patch 174 — Transport-agnostische Message-Pipeline (Phase E, Block 2).

Extrahiert die lineare Text-Verarbeitung aus
``zerberus/modules/telegram/router.py::_process_text_message`` als
transport-unabhaengige Funktion. Konsumiert ``IncomingMessage`` (P173),
liefert ``OutgoingMessage`` (P173).

WICHTIG (P174-Scope):
    Diese Pipeline deckt NUR den linearen DM-Text-Pfad ab:
    Sanitize → LLM → Intent-Parse → Guard → Output-Routing.
    Ausserhalb des Scopes (bleibt im Telegram-Router bis P175):
        - HitL-Inline-Button-Flow (file_effort_5)
        - Gruppen-Kontext / autonome Einwuerfe
        - Callback-Queries (HitL-Approval)
        - Vision (Bilder via image_urls)
        - Admin-DM-Spiegelungen (HitL-Hinweis, Guard-WARNUNG)
        - Sandbox-Execution-Hook
    Diese Faelle bleiben im legacy ``_process_text_message`` und werden
    in P175 schrittweise migriert.

Dependency-Injection:
    Statt feste Imports nimmt ``process_message`` ein ``PipelineDeps``-
    Objekt entgegen. Der Telegram-Adapter injiziert die echten
    Implementierungen; Tests injizieren Mocks. Damit hat die Pipeline
    keine harten Telegram-/HTTP-/OpenRouter-Abhaengigkeiten.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from .input_sanitizer import InputSanitizer
from .intent import HuginnIntent
from .intent_parser import parse_llm_response
from .message_bus import IncomingMessage, OutgoingMessage

logger = logging.getLogger("zerberus.pipeline")


# ──────────────────────────────────────────────────────────────────────
# Dependency-Bag
# ──────────────────────────────────────────────────────────────────────


@dataclass
class PipelineDeps:
    """Sammelt alle externen Funktionen die ``process_message`` braucht.

    Felder:
        sanitizer:       Input-Sanitizer-Instanz (P162/P173).
        llm_caller:      ``async (user_message, system_prompt) -> {content, error?, latency_ms}``.
        guard_caller:    ``async (user_msg, assistant_msg, caller_context) -> {verdict, reason?, latency_ms}``.
                         Optional — wenn None, Guard-Schritt wird uebersprungen.
        system_prompt:   Effektiver System-Prompt (Persona + Intent-Instruction)
                         der dem LLM mitgegeben wird.
        guard_context:   Caller-Kontext fuer den Guard (Persona-Beschreibung,
                         damit Persona-Elemente nicht als Halluzination gelten).
        guard_fail_policy: ``"allow"`` (Default) oder ``"block"`` — was tun
                         wenn Guard ``ERROR`` liefert.
        llm_unavailable_text: Text fuer die ``OutgoingMessage`` wenn der LLM-Call
                         nach allen Retries leer/fehlerhaft zurueckkommt.
        sanitizer_blocked_text: Text wenn ``SanitizeResult.blocked=True``.
        guard_block_text: Text wenn Guard ERROR + Policy=block.
        should_send_as_file: ``(intent_str, length) -> bool`` Callable —
                         Output-Routing-Entscheidung (P168).
        determine_file_format: ``(intent_str, content) -> (filename, mime_type)``.
        format_text:     Optionale Text-Nachbearbeitung vor dem Versand
                         (z. B. Code-Block-Markdown via ``format_code_response``).
                         Default: identity.
    """
    sanitizer: InputSanitizer
    llm_caller: Callable[..., Awaitable[dict]]
    system_prompt: str
    should_send_as_file: Callable[[str, int], bool]
    determine_file_format: Callable[[str, str], tuple[str, str]]
    guard_caller: Optional[Callable[..., Awaitable[dict]]] = None
    guard_context: str = ""
    guard_fail_policy: str = "allow"
    llm_unavailable_text: str = "Meine Kristallkugel ist gerade trüb. Versucht's später nochmal. 🔮"
    sanitizer_blocked_text: str = "🚫 Nachricht wurde aus Sicherheitsgründen blockiert."
    guard_block_text: str = "⚠️ Sicherheitsprüfung nicht verfügbar. Antwort zurückgehalten."
    format_text: Callable[[str], str] = field(default=lambda s: s)


# ──────────────────────────────────────────────────────────────────────
# Pipeline-Result (Diagnostik fuer Adapter + Tests)
# ──────────────────────────────────────────────────────────────────────


@dataclass
class PipelineResult:
    """Ergebnis eines ``process_message``-Laufs.

    Der Adapter konsumiert primaer ``message`` (zum Senden). ``reason`` und
    die Roh-Felder erleichtern Logging, Tests und spaetere Telemetrie.
    """
    message: Optional[OutgoingMessage] = None
    reason: str = "ok"                     # 'ok' | 'sanitizer_blocked' | 'llm_unavailable' | 'guard_block' | 'empty_input' | 'empty_llm'
    intent: Optional[str] = None
    effort: int = 0
    needs_hitl: bool = False
    guard_verdict: Optional[str] = None
    sanitizer_findings: list[str] = field(default_factory=list)
    llm_latency_ms: int = 0


# ──────────────────────────────────────────────────────────────────────
# Pipeline-Hauptfunktion
# ──────────────────────────────────────────────────────────────────────


async def process_message(
    incoming: IncomingMessage,
    deps: PipelineDeps,
) -> PipelineResult:
    """Verarbeitet eine eingehende Nachricht zu einer ausgehenden.

    Linearer Text-Pfad:
        1. Input-Sanitize (Findings ins Log; ``blocked=True`` → Block-Antwort).
        2. LLM-Call (mit Retry beim Caller, NICHT in dieser Funktion).
        3. Intent-Header parsen.
        4. Guard-Check (optional).
        5. Output-Routing — Text vs. Datei (via ``should_send_as_file``).

    Returns:
        ``PipelineResult`` — ``message`` ist die fuer den Adapter bestimmte
        Antwort (oder None, wenn kein Output sinnvoll ist, z. B. bei
        leerem Input).
    """
    user_msg = incoming.text or ""

    # ── 1. Sanitize ──────────────────────────────────────────────────
    sanitize_result = deps.sanitizer.sanitize(
        user_msg,
        metadata={
            "user_id": incoming.user_id,
            "chat_type": incoming.metadata.get("chat_type", "private"),
            "is_forwarded": bool(incoming.metadata.get("is_forwarded")),
            "is_reply": incoming.metadata.get("reply_to_message_id") is not None,
        },
    )
    if sanitize_result.blocked:
        return PipelineResult(
            message=OutgoingMessage(text=deps.sanitizer_blocked_text),
            reason="sanitizer_blocked",
            sanitizer_findings=list(sanitize_result.findings),
        )
    user_msg = sanitize_result.cleaned_text

    if not user_msg.strip():
        return PipelineResult(
            message=None,
            reason="empty_input",
            sanitizer_findings=list(sanitize_result.findings),
        )

    # ── 2. LLM-Call ──────────────────────────────────────────────────
    llm_result = await deps.llm_caller(
        user_message=user_msg,
        system_prompt=deps.system_prompt,
    )
    answer = (llm_result.get("content") or "")
    llm_latency_ms = int(llm_result.get("latency_ms") or 0)

    if not answer.strip():
        if llm_result.get("error"):
            return PipelineResult(
                message=OutgoingMessage(text=deps.llm_unavailable_text),
                reason="llm_unavailable",
                sanitizer_findings=list(sanitize_result.findings),
                llm_latency_ms=llm_latency_ms,
            )
        return PipelineResult(
            message=None,
            reason="empty_llm",
            sanitizer_findings=list(sanitize_result.findings),
            llm_latency_ms=llm_latency_ms,
        )

    # ── 3. Intent-Header parsen ──────────────────────────────────────
    parsed = parse_llm_response(answer)
    if parsed.raw_header is not None and not parsed.body.strip():
        # LLM hat nur den Header geliefert — Roh-Antwort behalten ist
        # haesslich aber besser als leere Telegram-Nachricht.
        body = answer
    else:
        body = parsed.body if parsed.raw_header is not None else answer

    intent_str = parsed.intent.value if isinstance(parsed.intent, HuginnIntent) else str(parsed.intent)

    # ── 4. Guard-Check (optional) ────────────────────────────────────
    guard_verdict: Optional[str] = None
    if deps.guard_caller is not None:
        guard = await deps.guard_caller(
            user_msg=user_msg,
            assistant_msg=body,
            caller_context=deps.guard_context,
        )
        guard_verdict = guard.get("verdict") if isinstance(guard, dict) else None

        if guard_verdict == "ERROR" and deps.guard_fail_policy == "block":
            return PipelineResult(
                message=OutgoingMessage(text=deps.guard_block_text),
                reason="guard_block",
                intent=intent_str,
                effort=parsed.effort,
                needs_hitl=parsed.needs_hitl,
                guard_verdict=guard_verdict,
                sanitizer_findings=list(sanitize_result.findings),
                llm_latency_ms=llm_latency_ms,
            )

    # ── 5. Output-Routing ────────────────────────────────────────────
    if deps.should_send_as_file(intent_str, len(body)):
        filename, mime_type = deps.determine_file_format(intent_str, body)
        outgoing = OutgoingMessage(
            file=body.encode("utf-8"),
            file_name=filename,
            mime_type=mime_type,
            text=None,
        )
    else:
        outgoing = OutgoingMessage(text=deps.format_text(body))

    return PipelineResult(
        message=outgoing,
        reason="ok",
        intent=intent_str,
        effort=parsed.effort,
        needs_hitl=parsed.needs_hitl,
        guard_verdict=guard_verdict,
        sanitizer_findings=list(sanitize_result.findings),
        llm_latency_ms=llm_latency_ms,
    )
