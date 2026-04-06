"""
MQTT Modul – IoT Integration (Smarthome).
"""
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zerberus.core.config import get_settings, Settings
from zerberus.core.event_bus import get_event_bus, Event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["MQTT"])

class MQTTPublishRequest(BaseModel):
    topic: str
    payload: str

@router.post("/publish")
async def publish_mqtt(
    req: MQTTPublishRequest,
    settings: Settings = Depends(get_settings)
):
    mod_cfg = settings.modules.get("mqtt", {})
    if not mod_cfg.get("enabled", False):
        return {"message": "MQTT Modul deaktiviert"}
    
    logger.info(f"📡 MQTT Publish: {req.topic} -> {req.payload}")
    bus = get_event_bus()
    await bus.publish(Event(type="mqtt_publish", data={"topic": req.topic, "payload": req.payload}))
    
    return {"status": "published", "topic": req.topic}

@router.get("/status")
async def mqtt_status(settings: Settings = Depends(get_settings)):
    mod_cfg = settings.modules.get("mqtt", {})
    return {
        "enabled": mod_cfg.get("enabled", False),
        "broker": mod_cfg.get("broker", "localhost"),
        "port": mod_cfg.get("port", 1883),
        "connected": False
    }

@router.get("/health")
async def health_check():
    return {"status": "ok", "module": "mqtt"}
