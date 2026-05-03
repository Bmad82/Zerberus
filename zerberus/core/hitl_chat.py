"""Patch 206 (Phase 5a #6) — HitL-Gate fuer Chat-Code-Execution.

Eigene, schlanke Mechanik fuer den ``/v1/chat/completions``-Pfad. NICHT
zu verwechseln mit ``modules/telegram/hitl.py`` (P167) — dessen
``HitlManager`` ist fuer Telegram-Callbacks gebaut (Integer-IDs,
Persistenz ueber Server-Restart, Sweep-Loop fuer Stunden-spaete
Approvals). Der Chat-HitL ist transient: er existiert nur waehrend
ein Chat-Request long-pollt. Restart heisst Request-Tod heisst
Pending-Tod — kein DB-Persist noetig.

Lifecycle pro Pending:

1. Chat-Endpunkt erkennt Code-Block (P203d-1) und Feature-Flag
   ``projects.hitl_enabled`` ist True.
2. ``create_pending(...)`` legt einen ``ChatHitlPending`` an,
   ``asyncio.Event`` als Notification-Shortcut. Pending steht im
   In-Memory-Registry, indiziert nach UUID4 + nach session_id.
3. Endpunkt blockt via ``wait_for_decision(pending_id, timeout)``.
4. Nala-Frontend pollt parallel ``GET /v1/hitl/poll?session_id=X``
   und rendert die HitL-Karte (Code-Vorschau + ✅/❌-Buttons).
5. User klickt → ``POST /v1/hitl/resolve`` ruft ``resolve(...)`` →
   ``Event.set()`` → Endpunkt wacht auf, liest decision, faehrt fort.
6. Bei Timeout: ``wait_for_decision`` setzt status=``timeout`` und
   gibt das frei, Endpunkt behandelt's wie reject.

Ownership-Hinweis: ``/v1/`` ist auth-frei (Dictate-Lane-Invariante).
session_id ist der einzige Diskriminator zwischen parallelen Chats —
``poll(session_id=X)`` liefert NUR Pendings dieser Session, niemals
fremde. Resolve via UUID4 (raten ist kombinatorisch ausgeschlossen).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


logger = logging.getLogger("zerberus.hitl_chat")


@dataclass
class ChatHitlPending:
    """In-Memory-Repraesentation einer wartenden Code-Execution.

    ``status`` Lebenszyklus:
        pending → approved | rejected | timeout

    Einmal ausserhalb von ``pending`` ist die Entscheidung final —
    Doppel-Resolve wird verworfen.
    """
    id: str
    session_id: str
    project_id: int
    project_slug: str
    code: str
    language: str
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None

    def to_public_dict(self) -> dict:
        """JSON-Repraesentation fuer ``GET /v1/hitl/poll``.

        Enthaelt KEINE internen Felder wie ``status`` — der Endpunkt
        liefert nur ``pending``-Tasks aus, der Status ist also implizit
        bekannt. Code wird als-ist durchgereicht (Frontend escaped).
        """
        return {
            "id": self.id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "project_slug": self.project_slug,
            "code": self.code,
            "language": self.language,
            "created_at": self.created_at.isoformat() + "Z",
        }


class ChatHitlGate:
    """Singleton-Registry fuer alle wartenden Chat-HitL-Pendings.

    In-Memory-only: Restart killt alle Pendings, was bei Long-Poll-
    Requests sowieso passiert (Client-Connection bricht ab).

    Threadsafe nicht noetig — FastAPI/Uvicorn bedient einen Endpunkt
    pro Coroutine im selben Event-Loop, asyncio.Event reicht.
    """

    def __init__(self) -> None:
        self._pendings: Dict[str, ChatHitlPending] = {}
        self._events: Dict[str, asyncio.Event] = {}

    async def create_pending(
        self,
        *,
        session_id: str,
        project_id: int,
        project_slug: str,
        code: str,
        language: str,
    ) -> ChatHitlPending:
        """Legt ein neues Pending an. UUID4-hex als ID."""
        pending = ChatHitlPending(
            id=uuid.uuid4().hex,
            session_id=session_id,
            project_id=int(project_id),
            project_slug=project_slug,
            code=code,
            language=language,
        )
        self._pendings[pending.id] = pending
        self._events[pending.id] = asyncio.Event()
        logger.info(
            "[HITL-206] pending_create id=%s session=%s project_id=%s "
            "language=%s code_len=%d",
            pending.id, session_id, project_id, language, len(code or ""),
        )
        return pending

    def get(self, pending_id: str) -> Optional[ChatHitlPending]:
        return self._pendings.get(pending_id)

    def list_for_session(self, session_id: str) -> List[ChatHitlPending]:
        """Pending-Tasks dieser Session, status=pending only.

        Pendings anderer Sessions werden nicht ausgeliefert — Ownership
        per session_id (siehe Module-Docstring).
        """
        if not session_id:
            return []
        return [
            p for p in self._pendings.values()
            if p.session_id == session_id and p.status == "pending"
        ]

    async def resolve(
        self,
        pending_id: str,
        decision: str,
        *,
        session_id: Optional[str] = None,
    ) -> bool:
        """Setzt einen Pending auf ``approved`` oder ``rejected``.

        - ``False`` bei unbekanntem Pending, falscher decision oder
          bereits resolvtem Task (idempotent — zweiter Klick ignoriert).
        - ``session_id``-Param ist optional; wenn gesetzt, MUSS er mit
          dem Pending uebereinstimmen (Defense-in-Depth: kein
          Cross-Session-Resolve via geleakter UUID).
        """
        if decision not in ("approved", "rejected"):
            logger.warning(
                "[HITL-206] resolve_invalid_decision id=%s decision=%r",
                pending_id, decision,
            )
            return False
        pending = self._pendings.get(pending_id)
        if pending is None:
            logger.info("[HITL-206] resolve_unknown id=%s", pending_id)
            return False
        if pending.status != "pending":
            logger.info(
                "[HITL-206] resolve_already_done id=%s status=%s",
                pending_id, pending.status,
            )
            return False
        if session_id is not None and session_id != pending.session_id:
            logger.warning(
                "[HITL-206] resolve_session_mismatch id=%s "
                "expected=%s got=%s",
                pending_id, pending.session_id, session_id,
            )
            return False

        pending.status = decision
        pending.resolved_at = datetime.utcnow()
        ev = self._events.get(pending_id)
        if ev is not None:
            ev.set()
        logger.info(
            "[HITL-206] resolve id=%s decision=%s session=%s",
            pending_id, decision, pending.session_id,
        )
        return True

    async def wait_for_decision(
        self,
        pending_id: str,
        timeout: float,
    ) -> str:
        """Blockt bis Entscheidung oder Timeout.

        Returns: ``approved`` | ``rejected`` | ``timeout`` | ``unknown``.
        Bei Timeout wird der Pending intern auf ``timeout`` gesetzt —
        ein spaeteres ``resolve`` wuerde dann False liefern (idempotent).
        """
        pending = self._pendings.get(pending_id)
        if pending is None:
            return "unknown"
        if pending.status != "pending":
            return pending.status

        event = self._events.setdefault(pending_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            if pending.status == "pending":
                pending.status = "timeout"
                pending.resolved_at = datetime.utcnow()
                logger.info(
                    "[HITL-206] timeout id=%s after=%.1fs",
                    pending_id, timeout,
                )
        return pending.status

    def cleanup(self, pending_id: str) -> None:
        """Entfernt ein Pending samt Event aus der Registry.

        Aufrufer ist der Endpunkt nach ``wait_for_decision`` — ohne
        Cleanup wuerde der In-Memory-Store waechst monoton bei jedem
        Chat-Request mit Code-Block.
        """
        self._pendings.pop(pending_id, None)
        self._events.pop(pending_id, None)


# ── Singleton ────────────────────────────────────────────────────────────

_GATE: Optional[ChatHitlGate] = None


def get_chat_hitl_gate() -> ChatHitlGate:
    """Module-level Singleton. Tests koennen via Reset des globalen
    State neu starten (siehe ``reset_chat_hitl_gate``)."""
    global _GATE
    if _GATE is None:
        _GATE = ChatHitlGate()
    return _GATE


def reset_chat_hitl_gate() -> None:
    """Test-Helper: setzt den Singleton auf None zurueck. Niemals im
    Produktiv-Pfad aufrufen — vorhandene ``wait_for_decision``-Coroutinen
    wuerden ins Leere zeigen."""
    global _GATE
    _GATE = None


# ── Audit-Trail ──────────────────────────────────────────────────────────

# Audit-Truncate-Limits (Bytes). Lange Outputs sollen die DB nicht
# fluten — der User-sichtbare Output liegt sowieso in der Synthese-
# ``answer`` aus P203d-2 und in der Frontend-Output-Card aus P203d-3.
AUDIT_MAX_TEXT_BYTES = 8_000


def _truncate_for_audit(text: Optional[str]) -> Optional[str]:
    """Bytes-genau truncaten fuer Audit-Spalten."""
    if text is None:
        return None
    s = str(text)
    if len(s.encode("utf-8")) <= AUDIT_MAX_TEXT_BYTES:
        return s
    head = s.encode("utf-8")[:AUDIT_MAX_TEXT_BYTES].decode(
        "utf-8", errors="ignore"
    )
    return head + "\n…[gekuerzt]"


async def store_code_execution_audit(
    *,
    session_id: Optional[str],
    project_id: Optional[int],
    project_slug: Optional[str],
    payload: dict,
    pending_id: Optional[str],
    hitl_status: str,
) -> None:
    """Schreibt eine ``code_executions``-Zeile als Audit-Trail.

    Best-Effort: jeder Fehler wird geloggt + verschluckt. Der Chat-
    Endpunkt darf NICHT durch Audit-Probleme blockiert werden.

    ``payload`` ist das ``code_execution``-Dict aus dem Endpunkt
    (Schema P203d-1 + P206-Erweiterungen ``skipped``/``hitl_status``).
    """
    try:
        from zerberus.core.database import (
            CodeExecution,
            _async_session_maker,
        )
    except Exception as e:
        logger.warning("[HITL-206] audit_import_failed: %s", e)
        return

    if _async_session_maker is None:
        # DB nicht initialisiert (Unit-Tests ohne init_db) — silent skip
        return

    try:
        async with _async_session_maker() as session:
            row = CodeExecution(
                pending_id=pending_id,
                session_id=session_id,
                project_id=project_id,
                project_slug=project_slug,
                language=payload.get("language"),
                exit_code=payload.get("exit_code"),
                execution_time_ms=payload.get("execution_time_ms"),
                truncated=1 if payload.get("truncated") else 0,
                skipped=1 if payload.get("skipped") else 0,
                hitl_status=hitl_status,
                code_text=_truncate_for_audit(payload.get("code")),
                stdout_text=_truncate_for_audit(payload.get("stdout")),
                stderr_text=_truncate_for_audit(payload.get("stderr")),
                error_text=_truncate_for_audit(payload.get("error")),
                resolved_at=datetime.utcnow()
                if hitl_status in ("approved", "rejected", "timeout", "bypassed")
                else None,
            )
            session.add(row)
            await session.commit()
        logger.info(
            "[HITL-206] audit_written session=%s project_id=%s "
            "hitl_status=%s skipped=%s exit_code=%s",
            session_id, project_id, hitl_status,
            payload.get("skipped"), payload.get("exit_code"),
        )
    except Exception as e:
        logger.warning("[HITL-206] audit_failed (non-fatal): %s", e)
