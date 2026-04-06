"""
WhatsApp Modul – Integration über Twilio.
"""
import logging
import os
from fastapi import APIRouter, Depends, Request, HTTPException, Form
from fastapi.responses import Response
from pydantic import BaseModel

from zerberus.core.config import get_settings, Settings
from zerberus.core.event_bus import get_event_bus, Event

try:
    from twilio.twiml.messaging_response import MessagingResponse
    from twilio.request_validator import RequestValidator
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False

logger = logging.getLogger(__name__)
router = APIRouter(tags=["WhatsApp"])

@router.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    settings: Settings = Depends(get_settings)
):
    mod_cfg = settings.modules.get("whatsapp", {})
    if not mod_cfg.get("enabled", False):
        raise HTTPException(403, "WhatsApp Modul deaktiviert")
    
    form = await request.form()
    logger.info(f"📩 WhatsApp Webhook erhalten: {dict(form)}")
    
    bus = get_event_bus()
    await bus.publish(Event(type="whatsapp_message", data=dict(form)))
    
    resp = MessagingResponse()
    resp.message("👋 Nachricht empfangen! (Zerberus)")
    return Response(content=str(resp), media_type="application/xml")

@router.get("/health")
async def health_check():
    return {"status": "ok", "module": "whatsapp"}
