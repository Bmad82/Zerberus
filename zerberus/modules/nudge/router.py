"""
Nudge Modul – Predictive Nudge Engine (Proaktive Vorschläge).
"""
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from datetime import datetime, timedelta

from zerberus.core.config import get_settings, Settings
from zerberus.core.event_bus import get_event_bus, Event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Nudge"])

_nudge_history = []

class NudgeRequest(BaseModel):
    event_type: str
    score: float
    context: dict = {}

class NudgeResponse(BaseModel):
    should_nudge: bool
    reason: str
    cooldown_until: str = None

@router.post("/evaluate")
async def evaluate_nudge(
    req: NudgeRequest,
    settings: Settings = Depends(get_settings)
):
    mod_cfg = settings.modules.get("nudge", {})
    if not mod_cfg.get("enabled", False):
        return {"message": "Nudge Modul deaktiviert"}
    
    threshold = mod_cfg.get("threshold", 0.8)
    hysteresis = mod_cfg.get("hysteresis", 0.1)
    cooldown_minutes = mod_cfg.get("cooldown_minutes", 30)
    
    now = datetime.now()
    recent_nudges = [
        n for n in _nudge_history
        if n["event_type"] == req.event_type and
        datetime.fromisoformat(n["timestamp"]) > now - timedelta(minutes=cooldown_minutes)
    ]
    if recent_nudges:
        return NudgeResponse(
            should_nudge=False,
            reason="Cooldown aktiv",
            cooldown_until=recent_nudges[-1]["cooldown_until"]
        )
    
    if req.score >= threshold + hysteresis:
        cooldown_until = (now + timedelta(minutes=cooldown_minutes)).isoformat()
        _nudge_history.append({
            "event_type": req.event_type,
            "score": req.score,
            "timestamp": now.isoformat(),
            "cooldown_until": cooldown_until
        })
        bus = get_event_bus()
        await bus.publish(Event(type="nudge_sent", data={"event_type": req.event_type, "score": req.score}))
        return NudgeResponse(should_nudge=True, reason=f"Score {req.score} >= Threshold {threshold}", cooldown_until=cooldown_until)
    
    return NudgeResponse(should_nudge=False, reason=f"Score {req.score} < Threshold {threshold}")

@router.get("/health")
async def health_check():
    return {"status": "ok", "module": "nudge"}
