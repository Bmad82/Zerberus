"""
Telegram Modul – Bot-Integration.
"""
import logging
import os
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel

from zerberus.core.config import get_settings, Settings
from zerberus.core.event_bus import get_event_bus, Event

try:
    from telegram import Update, Bot
    from telegram.ext import Application, CommandHandler, MessageHandler, filters
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Telegram"])

class WebhookUpdate(BaseModel):
    update_id: int
    message: dict = None

_telegram_app = None

def init_telegram(settings: Settings):
    global _telegram_app
    if not TELEGRAM_AVAILABLE:
        logger.warning("python-telegram-bot nicht installiert")
        return
    token = settings.modules.get("telegram", {}).get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.warning("Telegram Bot Token fehlt")
        return
    logger.info("Telegram Bot initialisiert")

@router.post("/webhook")
async def telegram_webhook(request: Request, settings: Settings = Depends(get_settings)):
    mod_cfg = settings.modules.get("telegram", {})
    if not mod_cfg.get("enabled", False):
        raise HTTPException(403, "Telegram Modul deaktiviert")
    
    data = await request.json()
    logger.info(f"📩 Telegram Webhook erhalten: {data}")
    
    bus = get_event_bus()
    await bus.publish(Event(type="telegram_message", data=data))
    
    return {"ok": True}

@router.get("/set_webhook")
async def set_webhook(settings: Settings = Depends(get_settings)):
    return {"message": "Webhook würde gesetzt werden (Mock)"}

@router.get("/health")
async def health_check():
    return {"status": "ok", "module": "telegram"}
