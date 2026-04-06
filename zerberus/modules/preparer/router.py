"""
Preparer Modul – Der Vordenker (Kalender-Integration).
"""
import logging
from fastapi import APIRouter, Depends
import httpx
from datetime import datetime, timedelta

from zerberus.core.config import get_settings, Settings
from zerberus.core.event_bus import get_event_bus, Event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Preparer"])

@router.get("/upcoming")
async def get_upcoming_events(settings: Settings = Depends(get_settings)):
    mod_cfg = settings.modules.get("preparer", {})
    if not mod_cfg.get("enabled", False):
        return {"message": "Preparer Modul deaktiviert"}
    
    calendar_url = mod_cfg.get("calendar_url")
    # Mock-Daten
    events = [
        {"title": "Meeting mit Team", "start": (datetime.now() + timedelta(hours=2)).isoformat()},
        {"title": "Zahnarzt", "start": (datetime.now() + timedelta(days=1)).isoformat()}
    ]
    
    bus = get_event_bus()
    await bus.publish(Event(type="calendar_fetched", data={"events": len(events)}))
    
    return {"upcoming_events": events}

@router.get("/health")
async def health_check():
    return {"status": "ok", "module": "preparer"}
