"""
Telegram Modul – Bot-Integration.

Patch 123: Huginn als vollwertiger Telegram-Chat-Partner.
- Webhook empfaengt Updates
- Guard (Mistral Small 3) prueft jede Antwort bevor sie rausgeht
- HitL fuer destruktive Aktionen (Code-Run, Gruppenbeitritt)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from zerberus.core.config import get_settings, Settings
from zerberus.core.event_bus import get_event_bus, Event
from zerberus.modules.telegram.bot import (
    DEFAULT_SYSTEM_PROMPT,
    HuginnConfig,
    call_llm,
    extract_message_info,
    format_code_response,
    get_file_url,
    is_bot_mentioned,
    register_webhook,
    send_telegram_message,
    was_bot_added_to_group,
)
from zerberus.modules.telegram.group_handler import (
    GroupManager,
    build_smart_interjection_prompt,
    is_skip_response,
    should_respond_in_group,
)
from zerberus.modules.telegram.hitl import (
    HitlManager,
    build_admin_keyboard,
    build_admin_message,
    build_group_decision_message,
    build_group_waiting_message,
    parse_callback_data,
)

try:
    from telegram import Update, Bot
    from telegram.ext import Application, CommandHandler, MessageHandler, filters
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

logger = logging.getLogger("zerberus.telegram")
router = APIRouter(tags=["Telegram"])


class WebhookUpdate(BaseModel):
    update_id: int
    message: dict = None


_telegram_app = None
_group_manager: Optional[GroupManager] = None
_hitl_manager: Optional[HitlManager] = None
_bot_user_id: Optional[int] = None


def _get_managers(settings: Settings) -> tuple[GroupManager, HitlManager]:
    """Singleton-Access auf GroupManager + HitlManager."""
    global _group_manager, _hitl_manager
    mod_cfg = settings.modules.get("telegram", {}) or {}
    behavior = mod_cfg.get("group_behavior", {}) or {}
    hitl_cfg = mod_cfg.get("hitl", {}) or {}
    if _group_manager is None:
        _group_manager = GroupManager(
            cooldown_seconds=int(behavior.get("interjection_cooldown_seconds", 300))
        )
    if _hitl_manager is None:
        _hitl_manager = HitlManager(
            timeout_seconds=int(hitl_cfg.get("confirmation_timeout_seconds", 300))
        )
    return _group_manager, _hitl_manager


def init_telegram(settings: Settings):
    """Legacy-Init (vor Patch 123). Bleibt fuer Kompatibilitaet erhalten."""
    global _telegram_app
    if not TELEGRAM_AVAILABLE:
        logger.warning("python-telegram-bot nicht installiert (optional)")
        return
    token = (
        settings.modules.get("telegram", {}).get("bot_token")
        or os.getenv("TELEGRAM_BOT_TOKEN")
    )
    if not token:
        logger.warning("Telegram Bot Token fehlt")
        return
    logger.info("Telegram Bot initialisiert")


async def startup_huginn(settings: Settings) -> None:
    """Wird beim Server-Start aufgerufen, registriert den Webhook wenn enabled."""
    mod_cfg = settings.modules.get("telegram", {}) or {}
    if not mod_cfg.get("enabled", False):
        logger.info("[HUGINN-123] Telegram-Modul deaktiviert - kein Webhook-Register")
        return
    cfg = HuginnConfig.from_dict(mod_cfg)
    webhook_url = mod_cfg.get("webhook_url", "")
    if cfg.bot_token and webhook_url and not webhook_url.startswith("https://yourdomain"):
        ok = await register_webhook(cfg.bot_token, webhook_url)
        logger.info(f"[HUGINN-123] Webhook-Register: {ok}")
    _get_managers(settings)


async def _run_guard(user_msg: str, assistant_msg: str) -> Dict[str, Any]:
    """Optionaler Guard-Check via Ach-laber-doch-nicht-Modul."""
    try:
        from zerberus.hallucination_guard import check_response
        return await check_response(user_msg, assistant_msg, rag_context="")
    except Exception as e:
        logger.warning(f"[HUGINN-123] Guard-Call fehlgeschlagen: {e}")
        return {"verdict": "ERROR", "reason": str(e)[:100], "latency_ms": 0}


async def _process_text_message(
    info: Dict[str, Any],
    cfg: HuginnConfig,
    settings: Settings,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> Dict[str, Any]:
    """Kernflow: Input → Guard → LLM → Output."""
    user_msg = info.get("text", "") or ""
    if not user_msg.strip():
        return {"sent": False, "reason": "empty"}

    # Bilder → Vision: file_ids in URLs resolven
    image_urls: list[str] = []
    if info.get("photo_file_ids"):
        for fid in info["photo_file_ids"][:3]:
            url = await get_file_url(cfg.bot_token, fid)
            if url:
                image_urls.append(url)

    llm_result = await call_llm(
        user_message=user_msg,
        model=cfg.model,
        system_prompt=system_prompt,
        image_urls=image_urls or None,
    )
    answer = llm_result.get("content", "") or ""
    if not answer.strip():
        return {"sent": False, "reason": "empty_llm"}

    # Guard-Check auf Antwort
    guard = await _run_guard(user_msg, answer)
    if guard.get("verdict") == "WARNUNG":
        # Im Admin-DM benachrichtigen, aber Antwort blockieren
        if cfg.admin_chat_id:
            await send_telegram_message(
                cfg.bot_token,
                cfg.admin_chat_id,
                f"⚠️ *Huginn Guard-WARNUNG*\n"
                f"User: {info.get('username', 'unbekannt')}\n"
                f"Grund: {guard.get('reason', 'unbekannt')}\n"
                f"Antwort unterdrueckt.",
            )
        return {"sent": False, "reason": "guard_blocked", "guard": guard}

    text_out = format_code_response(answer)
    sent = await send_telegram_message(
        cfg.bot_token,
        info["chat_id"],
        text_out,
        reply_to_message_id=info.get("message_id"),
    )
    return {"sent": sent, "guard": guard, "latency_ms": llm_result.get("latency_ms", 0)}


@router.post("/webhook")
async def telegram_webhook(request: Request, settings: Settings = Depends(get_settings)):
    """Empfaengt Telegram-Updates und routet sie durch den Huginn-Flow."""
    mod_cfg = settings.modules.get("telegram", {}) or {}
    if not mod_cfg.get("enabled", False):
        raise HTTPException(403, "Telegram Modul deaktiviert")

    data = await request.json()
    logger.info(f"[HUGINN-123] Webhook: update_id={data.get('update_id')}")

    # Event-Bus fuer legacy Listener
    bus = get_event_bus()
    await bus.publish(Event(type="telegram_message", data=data))

    group_mgr, hitl_mgr = _get_managers(settings)
    cfg = HuginnConfig.from_dict(mod_cfg)

    # Callback (Button-Klick) zuerst
    callback = data.get("callback_query")
    if callback:
        cb_data = callback.get("data", "")
        parsed = parse_callback_data(cb_data)
        if parsed and str(callback.get("from", {}).get("id")) == str(cfg.admin_chat_id):
            req = hitl_mgr.get(parsed["request_id"])
            if req:
                if parsed["action"] == "hitl_approve":
                    hitl_mgr.approve(parsed["request_id"])
                else:
                    hitl_mgr.reject(parsed["request_id"])
                # Bestaetigungs-Message in der anfragenden Gruppe
                await send_telegram_message(
                    cfg.bot_token,
                    req.requester_chat_id,
                    build_group_decision_message(req),
                )
        return {"ok": True}

    info = extract_message_info(data)
    if not info:
        return {"ok": True, "skipped": "no_message"}

    # Gruppenbeitritt? HitL anstossen
    if _bot_user_id and was_bot_added_to_group(info, _bot_user_id):
        allowed = set(int(x) for x in (cfg.allowed_group_ids or []))
        if info["chat_id"] not in allowed and cfg.admin_chat_id:
            hitl_cfg = mod_cfg.get("hitl", {}) or {}
            if hitl_cfg.get("group_join", True):
                req = hitl_mgr.create_request(
                    "group_join",
                    requester_chat_id=info["chat_id"],
                    requester_username=info.get("chat_title", "?"),
                    details=f"Huginn wurde eingeladen zu: {info.get('chat_title','?')} (ID: {info['chat_id']})",
                )
                await send_telegram_message(
                    cfg.bot_token,
                    cfg.admin_chat_id,
                    build_admin_message(req),
                    reply_markup=build_admin_keyboard(req.request_id),
                )
                return {"ok": True, "hitl": req.request_id}

    # In Gruppen Kontext sammeln
    if info["chat_type"] in ("group", "supergroup"):
        group_mgr.record_message(
            info["chat_id"], info.get("username", "?"), info.get("text", "")
        )
        decision = should_respond_in_group(
            info,
            behavior=mod_cfg.get("group_behavior", {}) or {},
            group_manager=group_mgr,
            bot_user_id=_bot_user_id,
        )
        if not decision["respond"]:
            return {"ok": True, "skipped": decision["reason"]}

        # Autonomer Einwurf muss vom LLM validiert werden
        if decision["needs_llm_decision"]:
            prompt = build_smart_interjection_prompt(
                group_mgr.recent_messages_text(info["chat_id"], limit=10)
            )
            llm_result = await call_llm(prompt, cfg.model)
            candidate = llm_result.get("content", "") or ""
            if is_skip_response(candidate):
                return {"ok": True, "skipped": "autonomous_skip"}
            # Direkt schicken (Guard-Check folgt)
            guard = await _run_guard("(gruppen-kontext)", candidate)
            if guard.get("verdict") == "WARNUNG":
                return {"ok": True, "skipped": "guard_blocked", "guard": guard}
            sent = await send_telegram_message(
                cfg.bot_token,
                info["chat_id"],
                format_code_response(candidate),
            )
            group_mgr.mark_interjection(info["chat_id"])
            return {"ok": True, "sent": sent, "reason": "autonomous"}

        # Direkte Ansprache → normaler Flow
        result = await _process_text_message(info, cfg, settings)
        if result.get("sent"):
            group_mgr.mark_interjection(info["chat_id"])
        return {"ok": True, "result": result}

    # DM → immer beantworten
    if info["chat_type"] == "private":
        result = await _process_text_message(info, cfg, settings)
        return {"ok": True, "result": result}

    return {"ok": True, "skipped": "unknown_chat_type"}


@router.get("/set_webhook")
async def set_webhook(settings: Settings = Depends(get_settings)):
    """Registriert den konfigurierten Webhook bei Telegram (manueller Trigger)."""
    mod_cfg = settings.modules.get("telegram", {}) or {}
    cfg = HuginnConfig.from_dict(mod_cfg)
    webhook_url = mod_cfg.get("webhook_url", "")
    if not cfg.bot_token or not webhook_url:
        return {"ok": False, "reason": "bot_token oder webhook_url fehlt"}
    ok = await register_webhook(cfg.bot_token, webhook_url)
    return {"ok": ok, "webhook_url": webhook_url}


@router.get("/health")
async def health_check():
    return {"status": "ok", "module": "telegram", "patch": 123}
