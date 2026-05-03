"""Patch 213 (Phase 5a #13) — Reasoning-Schritte sichtbar im Chat.

Wenn die Pipeline laeuft (Spec-Probe → RAG → Veto → HitL-Wait → Sandbox-
Run → Synthese), sieht der User aktuell nur die finale Antwort. Auf
Mobile dauert das oft mehrere Sekunden — wer mit unzuverlaessigem Netz
spricht, weiss nicht ob das System haengt oder arbeitet.

Dieses Modul sammelt die Zwischenschritte einer Chat-Turn als kleine
Karten, die das Frontend unter der Bot-Bubble (oder waehrend der
Long-Poll-Phase) ausspielen kann — analog `gpu-toast` aus P211, aber
korreliert mit der laufenden Session statt globalem GPU-State.

Architektur (analog `hitl_chat.ChatHitlGate` aus P206 +
`gpu_queue.GpuQueue` aus P211):

* **Pure-Function-Schicht** — `compute_step_duration_ms`, `should_emit`,
  Whitelist-Konstanten + `ReasoningStep`-Dataclass.
* **Async-Wrapper-Schicht** — `ReasoningStreamGate`-Singleton mit
  Per-Session-FIFO-Buffer (Default 32 Steps pro Session). Steps liegen
  In-Memory; ein TTL-Sweep aeltere Eintraege koennen via
  `cleanup_stale_sessions(...)` weg.
* **Audit-Tabelle** `reasoning_audits` mit `step_id`/`session_id`/
  `kind`/`status`/`duration_ms` — Best-Effort-Insert. Auswertung:
  ``SELECT kind, AVG(duration_ms) FROM reasoning_audits GROUP BY kind``.
* **Convenience** — `emit_step(...)` ist sync (kein await im Hot-Path),
  legt Step direkt im Buffer ab. `mark_done(step_id, status)` schliesst
  ihn ab und triggert den Audit-Insert.

Logging-Tag: ``[REASON-213]``.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional


logger = logging.getLogger("zerberus.reasoning_steps")


# ── Whitelist + Konstanten ────────────────────────────────────────────────

# Erlaubte Step-Typen. Frontend ordnet jedem ein Status-Icon zu — neue
# Kinds sollten hier gepflegt UND im Renderer ergaenzt werden, sonst zeigt
# das Frontend nur den Default-`?`.
KNOWN_STEP_KINDS: frozenset[str] = frozenset({
    "spec_check",
    "rag_query",
    "veto_probe",
    "hitl_wait",
    "sandbox_run",
    "synthesis",
    "embedder",
    "reranker",
    "guard",
    "llm_call",
})

# Status-Lifecycle pro Step. `running` ist der Default beim Anlegen,
# `done`/`error`/`skipped` sind Endzustaende. Ein Step bleibt `running`
# bis `mark_done` aufgerufen wird — ohne Mark bleibt er ewig haengen,
# wird aber durch `cleanup_stale_sessions` weg-gesweept.
KNOWN_STATUSES: frozenset[str] = frozenset({
    "running",
    "done",
    "error",
    "skipped",
})

# Default-Buffer-Limit pro Session. Eine typische Chat-Turn produziert
# 4-7 Steps. 32 Slots reichen fuer 4-5 Turns mit Overlap; danach werden
# die aeltesten Eintraege beim Insert weggeschoben.
DEFAULT_BUFFER_PER_SESSION = 32

# TTL fuer Stale-Sweep. Sessions, in denen seit dieser Zeitspanne kein
# Step mehr emittiert wurde, werden aus dem Buffer geworfen. Verhindert
# unbegrenztes Wachstum bei unsauberem Frontend (Tab geschlossen ohne
# Long-Poll-Finish).
DEFAULT_SESSION_TTL_SECONDS = 600  # 10 Minuten

# Long-Poll-Timeout fuer den HTTP-Endpoint. Frontend pollt im 4s-Takt;
# der Endpoint kann bis zu 10s warten, falls kein Step bereitliegt, um
# Round-Trip-Bursts zu reduzieren. Frontend kann den Wert ueber Query
# kuerzen (`?wait=1`) — Default-Wert ist konservativ.
DEFAULT_POLL_TIMEOUT_SECONDS = 10.0

# Maximale Bytes fuer `summary` und `detail`-Felder im Audit. Lange Texte
# werden trunciert — der Audit dient der Statistik, nicht der Forensik.
SUMMARY_MAX_BYTES = 200
DETAIL_MAX_BYTES = 1_000


# ── Pure-Function-Schicht ─────────────────────────────────────────────────

def compute_step_duration_ms(
    started_at: datetime,
    finished_at: Optional[datetime],
) -> Optional[int]:
    """Dauer in Millisekunden (gerundet). None falls noch laufend.

    Pure Function — kein Side-Effect. Immer >= 0.
    """
    if finished_at is None:
        return None
    delta_ms = (finished_at - started_at).total_seconds() * 1000.0
    return max(0, int(delta_ms))


def should_emit(
    kind: str,
    *,
    enabled: bool = True,
    disabled_kinds: frozenset[str] | set[str] | None = None,
) -> bool:
    """Trigger-Gate als Pure-Function.

    - ``enabled=False`` → kein Emit (globaler Kill-Switch).
    - ``kind`` nicht in `KNOWN_STEP_KINDS` → kein Emit (Tippfehler-Schutz).
    - ``kind`` in `disabled_kinds` → kein Emit (per-kind Opt-out).

    Pure: testbar ohne Lock + Singleton.
    """
    if not enabled:
        return False
    if kind not in KNOWN_STEP_KINDS:
        logger.warning("[REASON-213] unbekannter step-kind=%r — skip", kind)
        return False
    if disabled_kinds and kind in disabled_kinds:
        return False
    return True


def truncate_text(text: Optional[str], *, max_bytes: int) -> Optional[str]:
    """Bytes-genau truncaten — Text-only, kein Markdown-Awareness.

    Pure Function. Liefert None bei None-Input. Liefert den Original-
    String falls er bereits unter dem Limit ist.
    """
    if text is None:
        return None
    s = str(text)
    if len(s.encode("utf-8")) <= max_bytes:
        return s
    return s.encode("utf-8")[:max_bytes].decode(
        "utf-8", errors="ignore"
    ) + "…"


# ── Datenklassen ──────────────────────────────────────────────────────────

@dataclass
class ReasoningStep:
    """Ein einzelner Pipeline-Schritt einer Chat-Turn.

    ``step_id`` ist eine UUID4-hex (32 chars), damit der Frontend-DOM-
    Key stabil ist und Resort/Update einen einzelnen Eintrag finden
    koennen. ``summary`` ist der menschenlesbare Kurz-Text fuer die
    Karte (z.B. "Wartet auf Bestaetigung", "Modell prueft Code").
    ``detail`` ist optional und wird im Frontend nicht gezeigt — nur
    fuer den Audit-Insert.
    """
    step_id: str
    session_id: str
    kind: str  # spec_check|rag_query|veto_probe|hitl_wait|sandbox_run|synthesis|embedder|reranker|guard|llm_call
    summary: str
    started_at: datetime
    status: str = "running"  # running|done|error|skipped
    finished_at: Optional[datetime] = None
    detail: Optional[str] = None

    @property
    def duration_ms(self) -> Optional[int]:
        return compute_step_duration_ms(self.started_at, self.finished_at)

    def to_public_dict(self) -> dict:
        """JSON-Repraesentation fuer den Poll-Endpoint.

        Bewusst schmal: kein ``detail``-Feld nach aussen (Audit-only),
        kein ``session_id`` (Endpoint filtert bereits, leakt also nicht).
        """
        return {
            "step_id": self.step_id,
            "kind": self.kind,
            "summary": self.summary,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "started_at": self.started_at.isoformat() + "Z",
            "finished_at": (
                self.finished_at.isoformat() + "Z"
                if self.finished_at else None
            ),
        }


# ── ReasoningStreamGate ───────────────────────────────────────────────────

class ReasoningStreamGate:
    """Singleton-Buffer fuer alle aktiven Reasoning-Steps.

    Per-Session-FIFO: pro ``session_id`` eine Liste mit max
    ``buffer_per_session`` Eintraegen. Wer ueberlaeuft, schiebt den
    aeltesten Eintrag raus.

    Threading: einziger Schreib/Lese-Accessor ist asyncio-Coroutinen
    auf dem gleichen Loop (FastAPI/Uvicorn). Ein simpler dict reicht;
    der einzige zustimmungspflichtige Punkt sind ``consume`` und
    ``cleanup_stale``, die ueber `asyncio.Event`-Wait laufen.
    """

    def __init__(
        self,
        *,
        buffer_per_session: int = DEFAULT_BUFFER_PER_SESSION,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    ) -> None:
        self.buffer_per_session = max(1, int(buffer_per_session))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._steps: Dict[str, List[ReasoningStep]] = {}
        self._signals: Dict[str, asyncio.Event] = {}
        self._last_seen: Dict[str, datetime] = {}

    # ---------------- Public Sync-API ----------------------------------

    def emit(
        self,
        *,
        session_id: str,
        kind: str,
        summary: str,
        detail: Optional[str] = None,
    ) -> Optional[ReasoningStep]:
        """Legt einen neuen Step an. Kein await — sync, damit Hot-Path
        nicht blockt.

        Liefert den Step (oder None falls Trigger-Gate ablehnt). Der
        Caller MUSS spaeter ``mark_done(step.step_id, ...)`` aufrufen,
        sonst bleibt der Step bis zum TTL-Sweep auf ``running``.
        """
        if not session_id:
            logger.warning("[REASON-213] emit ohne session_id — skip")
            return None
        if not should_emit(kind):
            return None
        step = ReasoningStep(
            step_id=uuid.uuid4().hex,
            session_id=session_id,
            kind=kind,
            summary=truncate_text(summary, max_bytes=SUMMARY_MAX_BYTES) or "",
            started_at=datetime.utcnow(),
            detail=truncate_text(detail, max_bytes=DETAIL_MAX_BYTES),
        )
        bucket = self._steps.setdefault(session_id, [])
        bucket.append(step)
        # FIFO-Cap: aeltesten rauswerfen, wenn das Limit ueberschritten ist.
        while len(bucket) > self.buffer_per_session:
            removed = bucket.pop(0)
            logger.debug(
                "[REASON-213] buffer_overflow session=%s removed_kind=%s",
                session_id, removed.kind,
            )
        self._last_seen[session_id] = datetime.utcnow()
        ev = self._signals.get(session_id)
        if ev is not None:
            ev.set()
        logger.info(
            "[REASON-213] emit step=%s session=%s kind=%s status=%s",
            step.step_id, session_id, kind, step.status,
        )
        return step

    def mark_done(
        self,
        step_id: str,
        *,
        status: str = "done",
        detail: Optional[str] = None,
    ) -> Optional[ReasoningStep]:
        """Schliesst einen Step ab — ``status`` muss in `KNOWN_STATUSES`
        sein, sonst No-Op.

        Best-Effort: unbekannte step_ids loggen, returnen None.
        """
        if status not in KNOWN_STATUSES:
            logger.warning(
                "[REASON-213] mark_done invalid_status=%r step=%s",
                status, step_id,
            )
            return None
        step = self._find(step_id)
        if step is None:
            logger.info("[REASON-213] mark_done unknown step=%s", step_id)
            return None
        if step.status != "running":
            # Doppel-Mark — idempotent ignorieren, der erste gewinnt.
            return step
        step.status = status
        step.finished_at = datetime.utcnow()
        if detail is not None:
            step.detail = truncate_text(detail, max_bytes=DETAIL_MAX_BYTES)
        ev = self._signals.get(step.session_id)
        if ev is not None:
            ev.set()
        logger.info(
            "[REASON-213] done step=%s session=%s kind=%s status=%s "
            "duration_ms=%s",
            step_id, step.session_id, step.kind, status, step.duration_ms,
        )
        return step

    def list_for_session(self, session_id: str) -> List[ReasoningStep]:
        """Snapshot der Steps dieser Session (chronologisch).

        Kopie — Caller darf die Liste mutieren ohne den Gate zu
        verschmutzen.
        """
        if not session_id:
            return []
        return list(self._steps.get(session_id, []))

    def cleanup_session(self, session_id: str) -> int:
        """Wirft die komplette Session weg (alle Steps + Signal).

        Aufrufer ist normalerweise das Chat-Turn-Ende oder ein
        explizites POST/cleanup. Liefert die Anzahl entfernter Steps.
        """
        if not session_id:
            return 0
        removed = self._steps.pop(session_id, [])
        self._signals.pop(session_id, None)
        self._last_seen.pop(session_id, None)
        return len(removed)

    def cleanup_stale_sessions(
        self,
        *,
        now: Optional[datetime] = None,
    ) -> int:
        """Sweept alle Sessions, bei denen ``last_seen`` aelter als TTL
        ist. Liefert die Anzahl entfernter Sessions.
        """
        threshold = (now or datetime.utcnow()) - timedelta(
            seconds=self.ttl_seconds,
        )
        stale = [
            sid for sid, ts in list(self._last_seen.items())
            if ts < threshold
        ]
        for sid in stale:
            self.cleanup_session(sid)
        if stale:
            logger.info(
                "[REASON-213] sweep removed_sessions=%d", len(stale),
            )
        return len(stale)

    # ---------------- Public Async-API ---------------------------------

    async def consume_steps(
        self,
        session_id: str,
        *,
        wait_seconds: float = 0.0,
    ) -> List[ReasoningStep]:
        """Liefert die aktuellen Steps der Session.

        ``wait_seconds=0`` ist sofort. ``wait_seconds>0`` blockt long-
        poll-style bis ein Step emittiert wird ODER der Timeout greift —
        Frontend kann den Round-Trip-Burst damit reduzieren.
        """
        if not session_id:
            return []
        # Snapshot-vor-wait: wenn schon Steps drin sind, sofort raus.
        existing = self._steps.get(session_id, [])
        if existing or wait_seconds <= 0:
            return list(existing)
        ev = self._signals.setdefault(session_id, asyncio.Event())
        try:
            await asyncio.wait_for(ev.wait(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            return list(self._steps.get(session_id, []))
        finally:
            # Event zuruecksetzen, damit der naechste Poll wieder
            # blockiert bis ein neuer Step kommt.
            ev.clear()
        return list(self._steps.get(session_id, []))

    # ---------------- Internals ----------------------------------------

    def _find(self, step_id: str) -> Optional[ReasoningStep]:
        for steps in self._steps.values():
            for s in steps:
                if s.step_id == step_id:
                    return s
        return None


# ── Singleton ─────────────────────────────────────────────────────────────

_GATE: Optional[ReasoningStreamGate] = None


def get_reasoning_gate() -> ReasoningStreamGate:
    """Module-level Singleton. Lazy-init beim ersten Aufruf."""
    global _GATE
    if _GATE is None:
        _GATE = ReasoningStreamGate()
    return _GATE


def reset_reasoning_gate_for_tests() -> None:
    """Test-Helper: Singleton verwerfen + neu anlegen.

    NICHT in Produktion aufrufen — vorhandene `consume_steps`-Coroutinen
    wuerden ins Leere zeigen.
    """
    global _GATE
    _GATE = None


# ── Convenience-Wrapper ───────────────────────────────────────────────────

def emit_step(
    session_id: str,
    kind: str,
    summary: str,
    *,
    detail: Optional[str] = None,
) -> Optional[ReasoningStep]:
    """Sync-Convenience: ``emit_step(session, "veto_probe", "Modell prueft Code")``.

    Identisch zu ``get_reasoning_gate().emit(...)``. Pure-Style fuer die
    Verdrahtungs-Stellen — kein await noetig.
    """
    return get_reasoning_gate().emit(
        session_id=session_id,
        kind=kind,
        summary=summary,
        detail=detail,
    )


def mark_step_done(
    step: Optional[ReasoningStep] | str | None,
    *,
    status: str = "done",
    detail: Optional[str] = None,
) -> Optional[ReasoningStep]:
    """Sync-Convenience: ``mark_step_done(step)``.

    Akzeptiert sowohl ein `ReasoningStep`-Objekt (aus emit_step) als auch
    eine ``step_id``-String. Liefert None bei None-Input — das ist der
    erwartete Fall, wenn ``emit_step`` durch das Trigger-Gate keinen
    Step erzeugt hat (z.B. wegen unbekanntem kind). Der Aufrufer kann
    so unbedingt ``mark_step_done(emit_step(...))`` schreiben.
    """
    if step is None:
        return None
    step_id = step.step_id if isinstance(step, ReasoningStep) else str(step)
    result = get_reasoning_gate().mark_done(
        step_id, status=status, detail=detail,
    )
    if result is not None and result.finished_at is not None:
        # Audit-Insert nach Abschluss — fail-open. Nur wenn ein laufender
        # Event-Loop verfuegbar ist; sonst gar nicht erst die Coroutine
        # erzeugen (sonst RuntimeWarning "coroutine was never awaited").
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            try:
                loop.create_task(_audit_step(result))
            except Exception as e:  # pragma: no cover — defensive
                logger.debug(
                    "[REASON-213] audit-task-spawn failed step=%s err=%s",
                    step_id, e,
                )
        else:
            logger.debug(
                "[REASON-213] mark_done außerhalb asyncio-Loop step=%s",
                step_id,
            )
    return result


async def _audit_step(step: ReasoningStep) -> None:
    """Best-Effort-Audit-Insert. Fail-open: Hauptpfad blockiert nie."""
    try:
        from zerberus.core.database import (
            ReasoningAudit,
            _async_session_maker,
        )
    except Exception as e:
        logger.warning("[REASON-213] audit_import_failed: %s", e)
        return
    if _async_session_maker is None:
        return
    try:
        async with _async_session_maker() as session:
            row = ReasoningAudit(
                step_id=step.step_id,
                session_id=step.session_id,
                kind=step.kind,
                status=step.status,
                duration_ms=step.duration_ms,
                summary=truncate_text(
                    step.summary, max_bytes=SUMMARY_MAX_BYTES,
                ),
                detail=truncate_text(
                    step.detail, max_bytes=DETAIL_MAX_BYTES,
                ),
            )
            session.add(row)
            await session.commit()
        logger.info(
            "[REASON-213] audit_written step=%s session=%s kind=%s "
            "status=%s duration_ms=%s",
            step.step_id, step.session_id, step.kind, step.status,
            step.duration_ms,
        )
    except Exception as e:
        logger.warning("[REASON-213] audit_failed (non-fatal): %s", e)
