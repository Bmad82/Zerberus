"""
Patch 167 — HitL-Hardening (Phase C, Block 1-4).

Loest die RAM-basierte Variante aus Patch 123 ab. Tasks ueberleben jetzt
Server-Restarts (SQLite-Persistenz), bekommen UUID4-IDs, einen Auto-Reject-
Timeout-Sweep, und der Callback-Handler prueft Ownership ueber die Task-ID.

Findings (aus 7-LLM-Review):
- N2  Persistenz von HitL-Tasks ueber Restarts hinweg.
- N4  Ownership: Requester selbst + Admin duerfen bestaetigen, andere User nicht.
- D2  Multi-Task-Disambiguierung: Task-ID liegt im Callback-Daten-String.
- P4  Auto-Reject-Timeout via periodischem Sweep.
- P8  CODE/FILE/ADMIN-Bestaetigung NUR via Inline-Buttons, NIE via NL-Text.
- O3  Callback-Spoofing-Schutz (war schon Patch 162, jetzt mit Task-ID-Bezug).

Backward-Compat: ``HitlRequest`` bleibt als Alias fuer ``HitlTask`` erhalten.
Alte Felder (``request_id``, ``request_type``, ``requester_chat_id``,
``requester_user_id``) sind als Properties weiterhin lesbar, damit Tests und
Builder-Helfer nicht schlagartig brechen.

Persistenz-Modus:
- ``persistent=True`` (Default) → DB ist Source-of-Truth. Tasks werden bei
  jedem Status-Wechsel committed; nach Restart kann ``load_pending()`` den
  In-Memory-Cache rehydrieren.
- ``persistent=False`` → reiner In-Memory-Modus (fuer Unit-Tests ohne DB).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

logger = logging.getLogger("zerberus.huginn.hitl")


# ──────────────────────────────────────────────────────────────────────
#  HitlTask – In-Memory-Repraesentation (mirror der DB-Zeile)
# ──────────────────────────────────────────────────────────────────────


@dataclass
class HitlTask:
    """In-Memory-Spiegel der DB-Zeile ``hitl_tasks``.

    Wird vom Manager hin- und herkonvertiert. Backward-Compat-Properties
    (``request_id``, ``request_type``, ...) sorgen dafuer, dass Bestands-
    Code aus Patch 123 weiter laeuft.
    """

    id: str
    requester_id: int
    chat_id: int
    intent: str
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[int] = None
    admin_comment: str = ""
    requester_username: str = ""
    details: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    # ----- Backward-Compat (Patch 123 Feld-Namen) -----
    @property
    def request_id(self) -> str:
        return self.id

    @property
    def request_type(self) -> str:
        return self.intent

    @property
    def requester_chat_id(self) -> int:
        return self.chat_id

    @property
    def requester_user_id(self) -> Optional[int]:
        return self.requester_id


# Alias damit alter Code (`from ... import HitlRequest`) weiter laeuft.
HitlRequest = HitlTask


# ──────────────────────────────────────────────────────────────────────
#  HitlManager
# ──────────────────────────────────────────────────────────────────────


class HitlManager:
    """Zentrale Stelle fuer alle laufenden HitL-Tasks.

    Patch 167: SQLite ist Source-of-Truth. Der In-Memory-Map dient als
    Cache + traegt die ``asyncio.Event``-Notifizierung fuer Coroutines,
    die per ``wait_for_decision`` auf eine Entscheidung warten.
    """

    def __init__(self, timeout_seconds: int = 300, *, persistent: bool = True):
        self.timeout = timeout_seconds
        self.persistent = persistent
        self._cache: Dict[str, HitlTask] = {}
        self._events: Dict[str, asyncio.Event] = {}

    # ---------- DB-Helfer ----------------------------------------------------

    def _db_session_maker(self):
        """Gibt den ``async_sessionmaker`` zurueck, oder None wenn DB nicht
        initialisiert ist (typisch in Unit-Tests ohne ``init_db``)."""
        try:
            from zerberus.core import database as db_mod
            return db_mod._async_session_maker
        except Exception:
            return None

    @staticmethod
    def _row_to_task(row: Any) -> HitlTask:
        """Konvertiert eine DB-Row in einen ``HitlTask``-Dataclass."""
        payload: Dict[str, Any] = {}
        if row.payload_json:
            try:
                payload = json.loads(row.payload_json) or {}
            except (ValueError, TypeError):
                payload = {}
        return HitlTask(
            id=row.id,
            requester_id=int(row.requester_id),
            chat_id=int(row.chat_id),
            intent=row.intent,
            status=row.status or "pending",
            created_at=row.created_at or datetime.utcnow(),
            resolved_at=row.resolved_at,
            resolved_by=row.resolved_by,
            admin_comment=row.admin_comment or "",
            requester_username=row.requester_username or "",
            details=row.details or "",
            payload=payload,
        )

    # ---------- Neue API (Patch 167) -----------------------------------------

    async def create_task(
        self,
        requester_id: int,
        chat_id: int,
        intent: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        requester_username: str = "",
        details: str = "",
    ) -> HitlTask:
        """Legt einen neuen HitL-Task an. UUID4 als ID, Status ``pending``."""
        from zerberus.core.database import HitlTask as HitlTaskRow

        task = HitlTask(
            id=uuid.uuid4().hex,
            requester_id=int(requester_id),
            chat_id=int(chat_id),
            intent=intent,
            payload=payload or {},
            requester_username=requester_username,
            details=details,
        )
        self._cache[task.id] = task
        self._events[task.id] = asyncio.Event()

        if self.persistent:
            sm = self._db_session_maker()
            if sm is not None:
                try:
                    async with sm() as session:
                        row = HitlTaskRow(
                            id=task.id,
                            requester_id=task.requester_id,
                            chat_id=task.chat_id,
                            intent=task.intent,
                            payload_json=json.dumps(task.payload) if task.payload else None,
                            status=task.status,
                            created_at=task.created_at,
                            requester_username=task.requester_username or None,
                            details=task.details or None,
                        )
                        session.add(row)
                        await session.commit()
                except Exception as e:
                    logger.warning("[HITL-167] DB-Persist fehlgeschlagen: %s", e)

        logger.info(
            "[HITL-167] Task erstellt: %s von User %s, Intent=%s",
            task.id, task.requester_id, task.intent,
        )
        return task

    async def get_task(self, task_id: str) -> Optional[HitlTask]:
        """Lookup per ID. Cache zuerst, dann DB."""
        cached = self._cache.get(task_id)
        if cached is not None:
            return cached

        if self.persistent:
            sm = self._db_session_maker()
            if sm is not None:
                try:
                    from zerberus.core.database import HitlTask as HitlTaskRow
                    async with sm() as session:
                        row = (await session.execute(
                            select(HitlTaskRow).where(HitlTaskRow.id == task_id)
                        )).scalar_one_or_none()
                        if row is not None:
                            task = self._row_to_task(row)
                            self._cache[task.id] = task
                            self._events.setdefault(task.id, asyncio.Event())
                            if task.status != "pending":
                                self._events[task.id].set()
                            return task
                except Exception as e:
                    logger.warning("[HITL-167] DB-Lookup fehlgeschlagen: %s", e)
        return None

    async def resolve_task(
        self,
        task_id: str,
        resolver_id: int,
        decision: str,
        admin_comment: str = "",
        *,
        is_admin_override: bool = False,
    ) -> bool:
        """Setzt den Status auf ``approved`` oder ``rejected``.

        - Liefert ``False`` wenn der Task unbekannt ist oder bereits
          aufgeloest wurde (Doppel-Bestaetigung wird verworfen).
        - ``is_admin_override`` ist nur fuer Logging — die Ownership-Pruefung
          gehoert in den aufrufenden Layer (Router), weil dort die
          Admin-Chat-ID liegt.
        """
        if decision not in ("approved", "rejected"):
            return False

        task = await self.get_task(task_id)
        if task is None or task.status != "pending":
            return False

        task.status = decision
        task.resolved_at = datetime.utcnow()
        task.resolved_by = int(resolver_id)
        task.admin_comment = admin_comment
        self._cache[task.id] = task
        ev = self._events.get(task.id)
        if ev is not None:
            ev.set()

        if self.persistent:
            sm = self._db_session_maker()
            if sm is not None:
                try:
                    from zerberus.core.database import HitlTask as HitlTaskRow
                    async with sm() as session:
                        await session.execute(
                            update(HitlTaskRow)
                            .where(HitlTaskRow.id == task_id)
                            .values(
                                status=task.status,
                                resolved_at=task.resolved_at,
                                resolved_by=task.resolved_by,
                                admin_comment=admin_comment or None,
                            )
                        )
                        await session.commit()
                except Exception as e:
                    logger.warning("[HITL-167] DB-Resolve fehlgeschlagen: %s", e)

        if is_admin_override and task.requester_id != resolver_id:
            logger.info(
                "[HITL-167] Admin-Override: %s bestaetigt Task %s von %s "
                "(decision=%s)",
                resolver_id, task_id, task.requester_id, decision,
            )
        if decision == "approved":
            logger.info(
                "[HITL-167] Task %s bestaetigt von %s", task_id, resolver_id,
            )
        else:
            logger.info(
                "[HITL-167] Task %s abgelehnt von %s", task_id, resolver_id,
            )
        return True

    async def get_pending_tasks(
        self, chat_id: Optional[int] = None
    ) -> List[HitlTask]:
        """Liefert alle Tasks im Status ``pending``. Optional nach chat_id."""
        if self.persistent:
            sm = self._db_session_maker()
            if sm is not None:
                try:
                    from zerberus.core.database import HitlTask as HitlTaskRow
                    async with sm() as session:
                        stmt = select(HitlTaskRow).where(
                            HitlTaskRow.status == "pending"
                        )
                        if chat_id is not None:
                            stmt = stmt.where(HitlTaskRow.chat_id == int(chat_id))
                        rows = (await session.execute(stmt)).scalars().all()
                        tasks = [self._row_to_task(r) for r in rows]
                        for t in tasks:
                            self._cache[t.id] = t
                            self._events.setdefault(t.id, asyncio.Event())
                        return tasks
                except Exception as e:
                    logger.warning("[HITL-167] DB-Pending-Query fehlgeschlagen: %s", e)

        # Fallback: nur Cache.
        result = [
            t for t in self._cache.values()
            if t.status == "pending"
            and (chat_id is None or t.chat_id == int(chat_id))
        ]
        return result

    async def expire_stale_tasks(self) -> List[HitlTask]:
        """Setzt alle Pending-Tasks aelter als ``self.timeout`` auf
        ``expired``. Liefert die Liste der gerade abgelaufenen Tasks
        (damit der Aufrufer Telegram-Hinweise schicken kann)."""
        cutoff = datetime.utcnow() - timedelta(seconds=self.timeout)
        expired: List[HitlTask] = []

        # 1) DB-Pfad — alle ``pending`` Rows aelter als cutoff abrufen.
        candidate_ids: List[str] = []
        if self.persistent:
            sm = self._db_session_maker()
            if sm is not None:
                try:
                    from zerberus.core.database import HitlTask as HitlTaskRow
                    async with sm() as session:
                        rows = (await session.execute(
                            select(HitlTaskRow).where(
                                HitlTaskRow.status == "pending",
                                HitlTaskRow.created_at < cutoff,
                            )
                        )).scalars().all()
                        candidate_ids = [r.id for r in rows]
                        for r in rows:
                            r.status = "expired"
                            r.resolved_at = datetime.utcnow()
                        await session.commit()
                        # Cache aktualisieren
                        for r in rows:
                            task = self._row_to_task(r)
                            self._cache[task.id] = task
                            expired.append(task)
                except Exception as e:
                    logger.warning("[HITL-167] DB-Sweep fehlgeschlagen: %s", e)

        # 2) Cache-Pfad — fuer In-Memory-Mode oder DB-Faelle, in denen die
        # Aufgabe nur in der Cache-Map existiert (z. B. wenn DB-Persist beim
        # Anlegen scheiterte).
        for task in list(self._cache.values()):
            if task.status != "pending":
                continue
            if task.created_at >= cutoff:
                continue
            if task.id in candidate_ids:
                continue  # bereits per DB-Pfad behandelt
            task.status = "expired"
            task.resolved_at = datetime.utcnow()
            expired.append(task)

        for task in expired:
            ev = self._events.get(task.id)
            if ev is not None:
                ev.set()
            logger.warning(
                "[HITL-167] Task %s abgelaufen (Timeout %ds)",
                task.id, self.timeout,
            )
        return expired

    async def wait_for_decision(
        self, task_id: str, timeout: Optional[float] = None
    ) -> str:
        """Blockiert bis Admin entscheidet oder Timeout. Gibt finalen
        Status zurueck (``approved``/``rejected``/``expired``/``unknown``).

        Source-of-Truth ist die DB; das ``asyncio.Event`` ist nur ein
        Notification-Shortcut, damit der Caller nicht pollt.
        """
        task = await self.get_task(task_id)
        if task is None:
            return "unknown"
        if task.status != "pending":
            return task.status

        event = self._events.setdefault(task_id, asyncio.Event())
        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            await asyncio.wait_for(event.wait(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            # Eigenhaendig auf expired schalten — der Sweep-Task wuerde es
            # spaeter eh tun, aber der Caller braucht ein Ergebnis JETZT.
            task = await self.get_task(task_id)
            if task and task.status == "pending":
                task.status = "expired"
                task.resolved_at = datetime.utcnow()
                self._cache[task.id] = task
                if self.persistent:
                    sm = self._db_session_maker()
                    if sm is not None:
                        try:
                            from zerberus.core.database import HitlTask as HitlTaskRow
                            async with sm() as session:
                                await session.execute(
                                    update(HitlTaskRow)
                                    .where(HitlTaskRow.id == task_id)
                                    .values(
                                        status="expired",
                                        resolved_at=task.resolved_at,
                                    )
                                )
                                await session.commit()
                        except Exception as e:
                            logger.warning("[HITL-167] Wait-Expire DB-Update fehlgeschlagen: %s", e)
                logger.warning("[HITL-167] Task %s abgelaufen (Wait-Timeout)", task_id)

        task = await self.get_task(task_id)
        return task.status if task else "unknown"

    # ---------- Backward-Compat-Wrapper (Patch 123 sync API) ----------------
    #
    # Bestands-Tests (test_hitl_manager.py, test_telegram_bot.py) rufen den
    # Manager noch synchron auf. Diese Wrapper bedienen sie, ohne die DB
    # anzufassen — sie lassen den Manager wie in Patch 123 als reines
    # In-Memory-Objekt laufen. Neue Pfade (Router, Hardening-Tests) muessen
    # die async API benutzen.

    def create_request(
        self,
        request_type: str,
        requester_chat_id: int,
        requester_username: str,
        details: str,
        payload: Optional[Dict[str, Any]] = None,
        requester_user_id: Optional[int] = None,
    ) -> HitlTask:
        """Sync-Wrapper, In-Memory-only — Backward-Compat zu Patch 123."""
        task = HitlTask(
            id=uuid.uuid4().hex,
            requester_id=int(requester_user_id) if requester_user_id is not None else 0,
            chat_id=int(requester_chat_id),
            intent=request_type,
            payload=payload or {},
            requester_username=requester_username,
            details=details,
        )
        self._cache[task.id] = task
        self._events[task.id] = asyncio.Event()
        logger.info(
            "[HITL-167] (sync) Task erstellt: %s von %s, intent=%s",
            task.id, requester_username or task.requester_id, task.intent,
        )
        return task

    def get(self, task_id: str) -> Optional[HitlTask]:
        return self._cache.get(task_id)

    def approve(self, task_id: str, admin_comment: str = "") -> bool:
        task = self._cache.get(task_id)
        if task is None or task.status != "pending":
            return False
        task.status = "approved"
        task.admin_comment = admin_comment
        task.resolved_at = datetime.utcnow()
        ev = self._events.get(task_id)
        if ev is not None:
            ev.set()
        logger.info("[HITL-167] (sync) %s bestaetigt", task_id)
        return True

    def reject(self, task_id: str, admin_comment: str = "") -> bool:
        task = self._cache.get(task_id)
        if task is None or task.status != "pending":
            return False
        task.status = "rejected"
        task.admin_comment = admin_comment
        task.resolved_at = datetime.utcnow()
        ev = self._events.get(task_id)
        if ev is not None:
            ev.set()
        logger.info("[HITL-167] (sync) %s abgelehnt", task_id)
        return True


# ──────────────────────────────────────────────────────────────────────
#  Builder-Helfer (Inline-Keyboard, Admin-/Group-Messages)
# ──────────────────────────────────────────────────────────────────────


def build_admin_keyboard(task_id: str) -> Dict[str, Any]:
    """Inline-Keyboard fuer die Admin-DM: ✅ Freigeben | ❌ Ablehnen."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Freigeben", "callback_data": f"hitl_approve:{task_id}"},
                {"text": "❌ Ablehnen", "callback_data": f"hitl_reject:{task_id}"},
            ]
        ]
    }


def build_admin_message(task: HitlTask) -> str:
    """Nachricht fuer den Admin mit Kontext."""
    lines = [
        f"🛎 *HitL-Anfrage* `{task.id}`",
        f"*Typ:* {task.intent}",
        f"*Von:* @{task.requester_username or 'unbekannt'} (chat {task.chat_id})",
        "",
        (task.details or "")[:1500],
    ]
    return "\n".join(lines)


def build_group_waiting_message(task: HitlTask) -> str:
    """Nachricht die in der anfragenden Gruppe waehrend des Wartens sichtbar ist."""
    return (
        f"⏳ Huginn wartet auf Freigabe vom Admin "
        f"(Typ: {task.intent}, ID `{task.id}`)..."
    )


def build_group_decision_message(task: HitlTask) -> str:
    """Nachricht in der Gruppe nachdem Admin entschieden hat."""
    if task.status == "approved":
        return f"✅ Admin hat freigegeben ({task.id})"
    if task.status == "rejected":
        reason = task.admin_comment or "keine Begruendung"
        return f"❌ Admin hat abgelehnt ({task.id}): {reason}"
    if task.status in ("timeout", "expired"):
        return f"⏱ Keine Admin-Reaktion - Aktion abgebrochen ({task.id})"
    return f"❓ Unbekannter Status ({task.id})"


def build_timeout_message(task: HitlTask) -> str:
    """Patch 167 Block 3 — Nachricht fuer abgelaufene Tasks."""
    return f"⏰ Anfrage verworfen — zu langsam, Bro. (`{task.id}`)"


def parse_callback_data(data: str) -> Optional[Dict[str, str]]:
    """Parsed callback_data aus den Inline-Buttons. None wenn nicht HitL.

    Backward-Compat-Hinweis: das Ergebnis-Dict liefert ``request_id`` (statt
    ``task_id``), damit Bestandscode aus Patch 123/162 nicht angefasst werden
    muss. Inhalt ist die UUID4-Hex-Task-ID.
    """
    if not data or ":" not in data:
        return None
    action, _, rid = data.partition(":")
    if action not in ("hitl_approve", "hitl_reject"):
        return None
    if not rid:
        return None
    return {"action": action, "request_id": rid}


# ──────────────────────────────────────────────────────────────────────
#  Sweep-Loop (Patch 167, Block 3)
# ──────────────────────────────────────────────────────────────────────


async def hitl_sweep_loop(
    manager: "HitlManager",
    interval_seconds: float,
    on_expired,
) -> None:
    """Periodischer Sweep-Task: markiert abgelaufene Tasks als ``expired``
    und uebergibt sie an ``on_expired(task)`` (z. B. um eine Telegram-
    Nachricht zu schicken).

    ``on_expired`` ist ein async Callable. CancelledError wird durchgereicht,
    sodass der Caller den Task im Shutdown sauber stoppen kann.
    """
    logger.info(
        "[HITL-167] Sweep-Task gestartet (interval=%.0fs, timeout=%ds)",
        interval_seconds, manager.timeout,
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            expired = await manager.expire_stale_tasks()
            for task in expired:
                try:
                    await on_expired(task)
                except Exception as e:
                    logger.warning(
                        "[HITL-167] on_expired-Callback fehlgeschlagen "
                        "(task=%s): %s", task.id, e,
                    )
        except asyncio.CancelledError:
            logger.info("[HITL-167] Sweep-Task gestoppt")
            raise
        except Exception as e:
            logger.warning("[HITL-167] Sweep-Loop-Fehler: %s", e)
