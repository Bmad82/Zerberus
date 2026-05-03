"""Patch 211 (Phase 5a #11) — GPU-Queue fuer VRAM-Konsumenten.

Kooperatives Scheduling zwischen den vier VRAM-Konsumenten auf der RTX
3060 (12 GB): Whisper (Docker :8002), Gemma E2B (Prosodie), Embedder
(MiniLM/Multilingual) und Reranker (CrossEncoder). Vorher konnten alle
vier parallel ein Modell auf die GPU laden — bei einer typischen Voice-
Eingabe (Whisper + sofort danach Embedder + Reranker fuer das Projekt-
RAG) reichten 12 GB nicht und der Server crashte mit
``CUDA out of memory``.

Architektur:

* **Statisches VRAM-Budget pro Consumer-Name.** Kein dynamisches
  ``nvidia-smi``-Polling — die Modelle haben bekannte Speicherbedarfe
  (Whisper FP16 ~4 GB, Gemma E2B ~2 GB, MiniLM/Multilingual ~1 GB,
  Reranker ~512 MB). Statisches Budget ist robust gegen
  Treiber-Anomalien und tut auch ohne CUDA-Header.

* **Globaler Token-Bucket** mit ``TOTAL_VRAM_MB``. Jeder Acquire
  reserviert ``compute_vram_budget(consumer_name)`` Megabyte; passt das
  in den Restbudget, ist der Slot sofort frei. Sonst wird der Caller
  in eine FIFO-Queue gelegt und bekommt den Slot beim Release des
  Vorgaengers, sobald wieder Platz ist.

* **FIFO-Reihenfolge** verhindert Starvation: ein 4-GB-Whisper-Job
  blockt nicht hinter einer Schlange von 512-MB-Rerank-Calls. Wer
  zuerst wartet, kommt zuerst dran. Head-of-Line-Blocking ist akzeptabel
  — der typische Workload hat selten mehr als 2-3 parallele Konsumenten.

* **Fail-fast bei Timeout.** Default 30s. Lieber ein 500 mit klarer
  Error-Message als ein hängender Long-Running-Request, der das Frontend
  blockiert. Beim Timeout wird der Waiter sauber aus der Queue entfernt
  (kein Leak).

* **Audit-Trail in ``gpu_queue_audits``** mit Wartezeit, Halte-Dauer,
  Queue-Position und Timeout-Flag. Best-Effort: Audit-Fehler blocken
  den Hauptpfad nicht.

Logging-Tag: ``[GPU-211]``.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


logger = logging.getLogger("zerberus.gpu_queue")


# ── Pure-Function-Schicht ────────────────────────────────────────────────

# Statisches VRAM-Budget pro Konsument in Megabyte. Werte sind
# konservativ-realistische Obergrenzen aus produktiven Messungen + Puffer
# fuer KV-Cache und Aktivierungen.
#
# Whisper: faster-whisper FP16 (Large-v3) ~3.5 GB → 4 GB Budget.
# Gemma:   Gemma-4-E2B Audio-Multimodal ~1.8 GB → 2 GB Budget.
# Embedder: MiniLM-L6-v2 (~80 MB) bis Multilingual-E5-large (~2.2 GB) —
#           wir reservieren 1 GB als Mittelwert. Bei E5-large kann es
#           knapp werden, aber das ist ein bewusster Trade-off (sonst
#           waere die Queue staendig blockiert).
# Reranker: BGE-Reranker-Base (~280 MB) → 512 MB Budget mit Puffer.
VRAM_BUDGET_MB: dict[str, int] = {
    "whisper": 4_000,
    "gemma": 2_000,
    "embedder": 1_000,
    "reranker": 512,
}

# Total verfuegbares VRAM-Budget. RTX 3060 hat 12 GB physisch, aber
# Treiber + Display + Reserve essen ~1 GB. 11 GB nutzbar ist konservativ.
TOTAL_VRAM_MB = 11_000

# Default-Consumer-Budget fuer unbekannte Namen — defensiv hoch, damit
# wir lieber blocken als die GPU ueberlaufen zu lassen.
DEFAULT_CONSUMER_BUDGET_MB = 1_500

# Zulaessige Consumer-Namen (Whitelist) — verhindert Tippfehler in der
# Verdrahtung, die unbemerkt das Default-Budget triggern wuerden.
KNOWN_CONSUMERS = frozenset(VRAM_BUDGET_MB.keys())


def compute_vram_budget(consumer_name: str) -> int:
    """Statisches VRAM-Budget pro Consumer-Name in Megabyte.

    Unbekannte Namen bekommen ``DEFAULT_CONSUMER_BUDGET_MB`` und werden
    geloggt. Pure Function — kein Side-Effect ausser Log.
    """
    name = (consumer_name or "").strip().lower()
    if name in VRAM_BUDGET_MB:
        return VRAM_BUDGET_MB[name]
    logger.warning(
        "[GPU-211] unbekannter consumer=%r — nutze default %d MB",
        consumer_name, DEFAULT_CONSUMER_BUDGET_MB,
    )
    return DEFAULT_CONSUMER_BUDGET_MB


def should_queue(active_total_mb: int, requested_mb: int,
                 *, total_mb: int = TOTAL_VRAM_MB) -> bool:
    """``True`` wenn der Request NICHT sofort durchgewunken werden kann.

    Entscheidung: ``active + requested > total``. Pure-Function fuer
    Tests + Klartext-Trennung von der Async-Schicht.
    """
    if requested_mb <= 0:
        return False
    return (active_total_mb + requested_mb) > total_mb


# ── Datenklassen ─────────────────────────────────────────────────────────

@dataclass
class GpuSlotInfo:
    """Beobachtungs-Snapshot fuer Status-Endpoints + Audit.

    Keine LLM-Korrelation hier — das Audit-Schreiben passiert am Slot-
    Release.
    """
    audit_id: str
    consumer_name: str
    requested_mb: int
    queue_position: int
    waited_at: datetime
    acquired_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    timed_out: bool = False

    @property
    def wait_ms(self) -> int:
        end = self.acquired_at or datetime.utcnow()
        return max(0, int((end - self.waited_at).total_seconds() * 1000))

    @property
    def held_ms(self) -> Optional[int]:
        if not self.acquired_at:
            return None
        end = self.released_at or datetime.utcnow()
        return max(0, int((end - self.acquired_at).total_seconds() * 1000))


# ── Async-Wrapper: GpuQueue ──────────────────────────────────────────────

class GpuQueue:
    """Globale GPU-Queue-Instanz.

    Verwendung als async Context-Manager:

    .. code-block:: python

        async with get_gpu_queue().slot("whisper"):
            await call_whisper(...)

    Der Manager blockt, falls das Budget voll ist, und gibt den Slot beim
    Verlassen frei. Bei Timeout wird ``asyncio.TimeoutError`` geworfen
    (Caller entscheidet ueber Error-Mapping).

    Singleton: Eine Instanz pro Prozess. ``reset_for_tests()`` wirft die
    State-Maschine auf Default zurueck — fuer Test-Isolation.
    """

    def __init__(self, total_mb: int = TOTAL_VRAM_MB):
        self.total_mb = total_mb
        self._active_mb: int = 0
        self._waiters: list[tuple[str, int, asyncio.Future]] = []
        self._lock = asyncio.Lock()
        self._active_slots: list[GpuSlotInfo] = []  # nur fuer Status-Endpoint

    # ---------------- Public API ---------------------------------------

    def slot(self, consumer_name: str, *, timeout: float = 30.0
             ) -> "_SlotContextManager":
        """Async Context-Manager fuer einen VRAM-Slot."""
        return _SlotContextManager(self, consumer_name, timeout)

    async def status(self) -> dict:
        """Snapshot fuer den Status-Endpoint."""
        async with self._lock:
            return {
                "total_mb": self.total_mb,
                "active_mb": self._active_mb,
                "free_mb": max(0, self.total_mb - self._active_mb),
                "active_slots": [
                    {
                        "consumer": s.consumer_name,
                        "requested_mb": s.requested_mb,
                        "held_ms": s.held_ms or 0,
                    }
                    for s in self._active_slots
                ],
                "waiters": [
                    {"consumer": cn, "requested_mb": req}
                    for (cn, req, _fut) in self._waiters
                ],
            }

    def reset_for_tests(self) -> None:
        """Hard-Reset fuer Test-Isolation. NICHT in Produktion aufrufen."""
        for (_cn, _req, fut) in list(self._waiters):
            if not fut.done():
                fut.cancel()
        self._waiters.clear()
        self._active_slots.clear()
        self._active_mb = 0

    # ---------------- Internals ----------------------------------------

    async def _acquire(self, consumer_name: str, timeout: float
                       ) -> GpuSlotInfo:
        requested = compute_vram_budget(consumer_name)
        info = GpuSlotInfo(
            audit_id=uuid.uuid4().hex,
            consumer_name=consumer_name,
            requested_mb=requested,
            queue_position=0,
            waited_at=datetime.utcnow(),
        )

        loop = asyncio.get_event_loop()
        future: Optional[asyncio.Future] = None

        async with self._lock:
            if not should_queue(self._active_mb, requested, total_mb=self.total_mb):
                self._active_mb += requested
                info.acquired_at = datetime.utcnow()
                self._active_slots.append(info)
                logger.info(
                    "[GPU-211] acquired immediate consumer=%s requested_mb=%d "
                    "active_mb=%d/%d",
                    consumer_name, requested, self._active_mb, self.total_mb,
                )
                return info
            future = loop.create_future()
            self._waiters.append((consumer_name, requested, future))
            info.queue_position = len(self._waiters)
            logger.info(
                "[GPU-211] queued consumer=%s requested_mb=%d position=%d "
                "active_mb=%d/%d",
                consumer_name, requested, info.queue_position,
                self._active_mb, self.total_mb,
            )

        # Auf den Slot warten — ausserhalb des Locks, sonst blockiert
        # alles. Bei Timeout sauber aus der Queue entfernen.
        try:
            await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            info.timed_out = True
            async with self._lock:
                self._waiters = [
                    (cn, req, fut)
                    for (cn, req, fut) in self._waiters
                    if fut is not future
                ]
            logger.warning(
                "[GPU-211] timeout consumer=%s requested_mb=%d wait_ms=%d",
                consumer_name, requested, info.wait_ms,
            )
            raise

        # Wurde von _release durchgewinkt — _active_mb ist bereits hochgezaehlt.
        info.acquired_at = datetime.utcnow()
        async with self._lock:
            self._active_slots.append(info)
        logger.info(
            "[GPU-211] acquired after_wait consumer=%s requested_mb=%d "
            "wait_ms=%d active_mb=%d/%d",
            consumer_name, requested, info.wait_ms,
            self._active_mb, self.total_mb,
        )
        return info

    async def _release(self, info: GpuSlotInfo) -> None:
        info.released_at = datetime.utcnow()
        async with self._lock:
            self._active_mb = max(0, self._active_mb - info.requested_mb)
            try:
                self._active_slots.remove(info)
            except ValueError:
                pass
            # Schaue, ob der naechste Waiter passt. FIFO — wir wecken nur
            # den vordersten. Wenn der nicht passt, bleibt er warten und
            # alle dahinter ebenfalls (Head-of-Line-Block by design).
            woken = 0
            while self._waiters:
                (cn, req, fut) = self._waiters[0]
                if should_queue(self._active_mb, req, total_mb=self.total_mb):
                    break
                # passt
                self._waiters.pop(0)
                self._active_mb += req
                if not fut.done():
                    fut.set_result(None)
                    woken += 1
        logger.info(
            "[GPU-211] released consumer=%s requested_mb=%d held_ms=%s "
            "active_mb=%d/%d woken=%d",
            info.consumer_name, info.requested_mb, info.held_ms,
            self._active_mb, self.total_mb, woken,
        )


class _SlotContextManager:
    """Context-Manager-Wrapper, damit ``async with queue.slot(...)`` geht."""

    def __init__(self, queue: GpuQueue, consumer_name: str, timeout: float):
        self._queue = queue
        self._consumer_name = consumer_name
        self._timeout = timeout
        self._info: Optional[GpuSlotInfo] = None

    async def __aenter__(self) -> GpuSlotInfo:
        self._info = await self._queue._acquire(self._consumer_name, self._timeout)
        return self._info

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._info is not None and not self._info.timed_out:
            await self._queue._release(self._info)
            try:
                await store_gpu_queue_audit(self._info)
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("[GPU-211] audit_failed (non-fatal): %s", e)
        elif self._info is not None and self._info.timed_out:
            try:
                await store_gpu_queue_audit(self._info)
            except Exception as e:  # pragma: no cover — defensive
                logger.warning("[GPU-211] audit_failed (non-fatal): %s", e)


# ── Singleton ────────────────────────────────────────────────────────────

_global_queue: Optional[GpuQueue] = None


def get_gpu_queue() -> GpuQueue:
    """Singleton-Accessor. Lazy initialisiert beim ersten Aufruf."""
    global _global_queue
    if _global_queue is None:
        _global_queue = GpuQueue()
    return _global_queue


def reset_global_queue_for_tests() -> None:
    """Test-Helper: globale Queue komplett verwerfen + neu anlegen."""
    global _global_queue
    if _global_queue is not None:
        _global_queue.reset_for_tests()
    _global_queue = None


def vram_slot(consumer_name: str, *, timeout: float = 30.0
              ) -> _SlotContextManager:
    """Convenience: ``async with vram_slot("whisper"): ...``.

    Identisch zu ``get_gpu_queue().slot(consumer_name, timeout=timeout)``
    — nur kuerzer fuer die Verdrahtung in den Konsumenten.
    """
    return get_gpu_queue().slot(consumer_name, timeout=timeout)


# ── Audit-Trail ──────────────────────────────────────────────────────────

async def store_gpu_queue_audit(info: GpuSlotInfo) -> None:
    """Schreibt eine ``gpu_queue_audits``-Zeile als Audit-Trail.

    Best-Effort: jeder Fehler wird geloggt + verschluckt. Hauptpfad
    blockiert nicht durch Audit-Probleme.
    """
    try:
        from zerberus.core.database import (
            GpuQueueAudit,
            _async_session_maker,
        )
    except Exception as e:
        logger.warning("[GPU-211] audit_import_failed: %s", e)
        return

    if _async_session_maker is None:
        return

    try:
        async with _async_session_maker() as session:
            row = GpuQueueAudit(
                audit_id=info.audit_id,
                consumer_name=info.consumer_name,
                requested_mb=info.requested_mb,
                queue_position=info.queue_position,
                wait_ms=info.wait_ms,
                held_ms=info.held_ms,
                timed_out=bool(info.timed_out),
            )
            session.add(row)
            await session.commit()
        logger.info(
            "[GPU-211] audit_written consumer=%s wait_ms=%d held_ms=%s "
            "position=%d timed_out=%s",
            info.consumer_name, info.wait_ms, info.held_ms,
            info.queue_position, info.timed_out,
        )
    except Exception as e:
        logger.warning("[GPU-211] audit_failed (non-fatal): %s", e)
