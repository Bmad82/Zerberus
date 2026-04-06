"""
Event Bus für lose Kopplung zwischen Modulen.
Patch 46: session_id als optionales Feld im Event für SSE-Filterung.
"""
import asyncio
import logging
from typing import Dict, List, Callable, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class Event:
    """Event-Objekt"""
    type: str
    data: Dict[str, Any]
    timestamp: datetime = None
    session_id: str = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

class EventBus:
    """In-Memory Event Bus mit asyncio.Queue und SSE-Support."""

    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.running = False
        self._worker_task = None
        self._sse_queues: Dict[str, List[asyncio.Queue]] = {}
        logger.info("🔌 EventBus initialisiert")

    def subscribe(self, event_type: str, handler: Callable):
        """Abonniere einen Event-Typ"""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(handler)
        logger.info(f"📡 Handler für '{event_type}' registriert")

    def subscribe_sse(self, session_id: str) -> asyncio.Queue:
        """Erstelle eine SSE-Queue für eine Session. Gibt die Queue zurück."""
        q: asyncio.Queue = asyncio.Queue()
        if session_id not in self._sse_queues:
            self._sse_queues[session_id] = []
        self._sse_queues[session_id].append(q)
        logger.debug(f"📡 SSE-Subscriber für Session '{session_id}' registriert")
        return q

    def unsubscribe_sse(self, session_id: str, q: asyncio.Queue):
        """Entferne eine SSE-Queue für eine Session."""
        if session_id in self._sse_queues:
            try:
                self._sse_queues[session_id].remove(q)
            except ValueError:
                pass
            if not self._sse_queues[session_id]:
                del self._sse_queues[session_id]
        logger.debug(f"📡 SSE-Subscriber für Session '{session_id}' entfernt")

    async def publish(self, event: Event):
        """Veröffentliche ein Event"""
        await self.queue.put(event)
        # SSE-Queues befüllen: Events mit passender session_id oder ohne session_id (global)
        if event.session_id:
            for q in self._sse_queues.get(event.session_id, []):
                await q.put(event)
        else:
            # Globale Events an alle SSE-Queues
            for queues in self._sse_queues.values():
                for q in queues:
                    await q.put(event)
        logger.debug(f"📤 Event published: {event.type}")

    async def _worker(self):
        """Worker der Events aus der Queue verarbeitet"""
        logger.info("🔄 EventBus Worker gestartet")
        while self.running:
            try:
                event = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                await self._dispatch(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"❌ Fehler im EventBus Worker: {e}")

    async def _dispatch(self, event: Event):
        """Verteilt Event an alle Subscriber"""
        if event.type in self.subscribers:
            for handler in self.subscribers[event.type]:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(event)
                    else:
                        handler(event)
                except Exception as e:
                    logger.error(f"❌ Fehler in Handler: {e}")

    def start(self):
        """Startet den EventBus"""
        if not self.running:
            self.running = True
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("✅ EventBus gestartet")

    async def stop(self):
        """Stoppt den EventBus"""
        self.running = False
        if self._worker_task:
            await self._worker_task
        logger.info("🛑 EventBus gestoppt")

# Singleton
_event_bus: Optional[EventBus] = None

def get_event_bus() -> EventBus:
    """Singleton für EventBus"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
