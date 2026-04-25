"""
Patch 123 – Huginn Human-in-the-Loop.

HitL fuer destruktive Aktionen:
- Code-Ausfuehrung in Sandbox
- Gruppenbeitritt in nicht-erlaubte Gruppen

Admin bekommt DM mit Inline-Keyboard-Buttons (✅ Freigeben / ❌ Ablehnen).
In der anfragenden Gruppe wird der Prozess als "wartet auf Admin" sichtbar gemacht.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger("zerberus.huginn.hitl")


@dataclass
class HitlRequest:
    request_id: str
    request_type: str  # "code_execution" | "group_join" | ...
    requester_chat_id: int
    requester_username: str
    details: str
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending | approved | rejected | timeout
    resolved_at: Optional[float] = None
    admin_comment: str = ""
    # Patch 162 (O3): User-ID des Anfragenden — gegen Callback-Spoofing in
    # Gruppen. Default None: alte Aufrufer ohne user_id-Wissen brechen nicht.
    requester_user_id: Optional[int] = None


class HitlManager:
    """Zentrale Stelle fuer alle laufenden HitL-Anfragen."""

    def __init__(self, timeout_seconds: int = 300):
        self.timeout = timeout_seconds
        self._requests: Dict[str, HitlRequest] = {}
        self._events: Dict[str, asyncio.Event] = {}

    def create_request(
        self,
        request_type: str,
        requester_chat_id: int,
        requester_username: str,
        details: str,
        payload: Optional[Dict[str, Any]] = None,
        requester_user_id: Optional[int] = None,
    ) -> HitlRequest:
        req = HitlRequest(
            request_id=uuid.uuid4().hex[:12],
            request_type=request_type,
            requester_chat_id=requester_chat_id,
            requester_username=requester_username,
            details=details,
            payload=payload or {},
            requester_user_id=requester_user_id,
        )
        self._requests[req.request_id] = req
        self._events[req.request_id] = asyncio.Event()
        logger.info(
            f"[HITL-123] Neu: {req.request_id} ({req.request_type}) "
            f"von {requester_username}"
        )
        return req

    def get(self, request_id: str) -> Optional[HitlRequest]:
        return self._requests.get(request_id)

    def approve(self, request_id: str, admin_comment: str = "") -> bool:
        req = self._requests.get(request_id)
        if not req or req.status != "pending":
            return False
        req.status = "approved"
        req.admin_comment = admin_comment
        req.resolved_at = time.time()
        self._events[request_id].set()
        logger.info(f"[HITL-123] {request_id} freigegeben")
        return True

    def reject(self, request_id: str, admin_comment: str = "") -> bool:
        req = self._requests.get(request_id)
        if not req or req.status != "pending":
            return False
        req.status = "rejected"
        req.admin_comment = admin_comment
        req.resolved_at = time.time()
        self._events[request_id].set()
        logger.info(f"[HITL-123] {request_id} abgelehnt")
        return True

    async def wait_for_decision(
        self, request_id: str, timeout: Optional[float] = None
    ) -> str:
        """Blockiert bis Admin entscheidet oder Timeout. Gibt finalen Status zurueck."""
        event = self._events.get(request_id)
        if event is None:
            return "unknown"
        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            req = self._requests.get(request_id)
            if req and req.status == "pending":
                req.status = "timeout"
                req.resolved_at = time.time()
                logger.warning(f"[HITL-123] {request_id} Timeout")
        req = self._requests.get(request_id)
        return req.status if req else "unknown"


def build_admin_keyboard(request_id: str) -> Dict[str, Any]:
    """Inline-Keyboard fuer die Admin-DM: ✅ Freigeben | ❌ Ablehnen."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Freigeben", "callback_data": f"hitl_approve:{request_id}"},
                {"text": "❌ Ablehnen", "callback_data": f"hitl_reject:{request_id}"},
            ]
        ]
    }


def build_admin_message(req: HitlRequest) -> str:
    """Nachricht fuer den Admin mit Kontext."""
    lines = [
        f"🛎 *HitL-Anfrage* `{req.request_id}`",
        f"*Typ:* {req.request_type}",
        f"*Von:* @{req.requester_username} (chat {req.requester_chat_id})",
        "",
        req.details[:1500],
    ]
    return "\n".join(lines)


def build_group_waiting_message(req: HitlRequest) -> str:
    """Nachricht die in der anfragenden Gruppe waehrend des Wartens sichtbar ist."""
    return (
        f"⏳ Huginn wartet auf Freigabe vom Admin "
        f"(Typ: {req.request_type}, ID `{req.request_id}`)..."
    )


def build_group_decision_message(req: HitlRequest) -> str:
    """Nachricht in der Gruppe nachdem Admin entschieden hat."""
    if req.status == "approved":
        return f"✅ Admin hat freigegeben ({req.request_id})"
    if req.status == "rejected":
        reason = req.admin_comment or "keine Begruendung"
        return f"❌ Admin hat abgelehnt ({req.request_id}): {reason}"
    if req.status == "timeout":
        return f"⏱ Keine Admin-Reaktion - Aktion abgebrochen ({req.request_id})"
    return f"❓ Unbekannter Status ({req.request_id})"


def parse_callback_data(data: str) -> Optional[Dict[str, str]]:
    """Parsed callback_data aus den Inline-Buttons. None wenn nicht HitL."""
    if not data or ":" not in data:
        return None
    action, _, rid = data.partition(":")
    if action not in ("hitl_approve", "hitl_reject"):
        return None
    return {"action": action, "request_id": rid}
