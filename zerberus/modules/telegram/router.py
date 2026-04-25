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
from zerberus.core.input_sanitizer import get_sanitizer
from zerberus.modules.telegram.bot import (
    DEFAULT_HUGINN_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
    HuginnConfig,
    answer_callback_query,
    call_llm,
    extract_message_info,
    format_code_response,
    get_file_url,
    get_me,
    is_bot_mentioned,
    long_polling_loop,
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


async def startup_huginn(settings: Settings) -> Optional["asyncio.Task"]:
    """Wird beim Server-Start aufgerufen.

    Patch 155: Default-Modus ist "polling" — startet einen Background-Task
    mit long_polling_loop. Modus "webhook" bleibt als Fallback fuer Setups
    mit oeffentlicher HTTPS-URL.

    Returns:
        Den Polling-Task bei mode="polling", sonst None.
    """
    import asyncio
    mod_cfg = settings.modules.get("telegram", {}) or {}
    if not mod_cfg.get("enabled", False):
        return None
    cfg = HuginnConfig.from_dict(mod_cfg)
    if not cfg.bot_token:
        logger.warning("    ❌ bot_token fehlt — Bot nicht gestartet")
        return None

    _get_managers(settings)

    global _bot_user_id
    me = await get_me(cfg.bot_token)
    if me and me.get("id"):
        _bot_user_id = int(me["id"])
        logger.info(f"    ✅ Bot: @{me.get('username', '?')} (id={_bot_user_id})")
    else:
        logger.warning("    ⚠️ Bot-Identität nicht abrufbar (getMe fehlgeschlagen)")

    mode = str(mod_cfg.get("mode", "polling")).lower()
    if mode == "webhook":
        webhook_url = mod_cfg.get("webhook_url", "")
        if webhook_url and not webhook_url.startswith("https://yourdomain"):
            ok = await register_webhook(cfg.bot_token, webhook_url)
            if ok:
                logger.info("    ✅ Webhook registriert")
            else:
                logger.warning("    ❌ Webhook-Registrierung fehlgeschlagen")
        else:
            logger.warning("    ⚠️ mode=webhook aber keine gültige webhook_url")
        return None

    # mode=polling (Default) — Background-Task starten
    async def _handler(update: Dict[str, Any]) -> None:
        await process_update(update, settings)

    task = asyncio.create_task(
        long_polling_loop(cfg.bot_token, _handler),
        name="huginn-long-polling",
    )
    logger.info("    ✅ Long-Polling aktiv")
    return task


def _resolve_huginn_prompt(settings: Settings) -> str:
    """Patch 158: liefert den Persona-System-Prompt aus der Config.

    - Key nicht vorhanden  → DEFAULT_HUGINN_PROMPT
    - Key explizit ""      → "" (User hat Persona bewusst leer gesetzt)
    - Key sonst-string     → der gesetzte String
    """
    mod_cfg = settings.modules.get("telegram", {}) or {}
    if "system_prompt" not in mod_cfg:
        return DEFAULT_HUGINN_PROMPT
    val = mod_cfg.get("system_prompt")
    if val is None:
        return DEFAULT_HUGINN_PROMPT
    return str(val)


def _build_huginn_guard_context(persona: str) -> str:
    """Patch 158: liefert den caller_context fuer den Guard.

    Der Guard (Mistral Small) kennt Huginns Persona nicht und haelt Raben-
    Metaphern + Zerberus-Referenzen sonst fuer Halluzinationen. Mit diesem
    Kontext weiss er, dass das Charakter und keine erfundene Fakten sind.
    """
    return (
        "Der Antwortende ist 'Huginn', ein KI-Assistent im Zerberus-System mit einer Raben-Persona. "
        "Selbstreferenzen auf Zerberus, Raben-Metaphern, kraechzende Einwuerfe ('Krraa!', 'Kraechz!'), "
        "sarkastische Kommentare, Gossensprache und Charakter-Elemente sind ERWUENSCHT und KEINE Halluzinationen. "
        "Huginn spricht absichtlich zynisch und bissig - das ist sein Charakter, kein Fehler. "
        f"Persona-Beschreibung (Auszug): {(persona or '')[:300]}"
    )


async def _run_guard(
    user_msg: str,
    assistant_msg: str,
    caller_context: str = "",
) -> Dict[str, Any]:
    """Optionaler Guard-Check via Ach-laber-doch-nicht-Modul.

    Patch 158: `caller_context` wird an den Guard weitergereicht, damit er
    Persona-Elemente nicht mehr als Halluzination einstuft.
    """
    try:
        from zerberus.hallucination_guard import check_response
        return await check_response(
            user_msg,
            assistant_msg,
            rag_context="",
            caller_context=caller_context,
        )
    except Exception as e:
        logger.warning(f"[HUGINN-123] Guard-Call fehlgeschlagen: {e}")
        return {"verdict": "ERROR", "reason": str(e)[:100], "latency_ms": 0}


async def _process_text_message(
    info: Dict[str, Any],
    cfg: HuginnConfig,
    settings: Settings,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Kernflow: Input → Guard → LLM → Output.

    Patch 131: Wenn Bilder dabei sind, wird das konfigurierte Vision-Modell
    verwendet statt des Haupt-LLM (DeepSeek V3.2 hat keinen Vision-Support).

    Patch 158: `system_prompt=None` → Persona kommt aus der Config (per
    `_resolve_huginn_prompt`). Tests koennen weiter explizit einen String
    (auch `""`) uebergeben.
    """
    if system_prompt is None:
        system_prompt = _resolve_huginn_prompt(settings)
    user_msg = info.get("text", "") or ""

    # Patch 162 (K1, K3, N8): Sanitizer-Pass vor jedem LLM-Call.
    # Findings landen im Log, der User sieht sie nicht. ``blocked=True`` ist
    # im Huginn-Modus aktuell nicht erreichbar — Pfad steht für Rosa bereit.
    sanitizer = get_sanitizer()
    sanitize_result = sanitizer.sanitize(
        user_msg,
        metadata={
            "user_id": str(info.get("user_id") or ""),
            "chat_type": info.get("chat_type", "private"),
            "is_forwarded": bool(info.get("is_forwarded")),
            "is_reply": info.get("reply_to_message") is not None,
        },
    )
    if sanitize_result.blocked:
        await send_telegram_message(
            cfg.bot_token,
            info["chat_id"],
            "🚫 Nachricht wurde aus Sicherheitsgründen blockiert.",
            reply_to_message_id=info.get("message_id"),
            message_thread_id=info.get("message_thread_id"),
        )
        return {"sent": False, "reason": "sanitizer_blocked", "findings": sanitize_result.findings}
    user_msg = sanitize_result.cleaned_text

    # Bilder → Vision: file_ids in URLs resolven
    image_urls: list[str] = []
    if info.get("photo_file_ids"):
        for fid in info["photo_file_ids"][:3]:
            url = await get_file_url(cfg.bot_token, fid)
            if url:
                image_urls.append(url)

    # Leere Text-Only-Messages überspringen; Text+Foto oder Foto-Only sind ok
    if not user_msg.strip() and not image_urls:
        return {"sent": False, "reason": "empty"}

    # Patch 131: Modell-Auswahl — Vision vs. Text
    if image_urls:
        from zerberus.utils.vision import pick_vision_model
        model = pick_vision_model(settings)
        if not user_msg.strip():
            user_msg = "Beschreibe dieses Bild und antworte auf Deutsch."
        logger.info(f"[VISION-131] Huginn Bild-Analyse via {model} ({len(image_urls)} Bild(er))")
    else:
        model = cfg.model

    llm_result = await call_llm(
        user_message=user_msg,
        model=model,
        system_prompt=system_prompt,
        image_urls=image_urls or None,
    )
    answer = llm_result.get("content", "") or ""
    if not answer.strip():
        return {"sent": False, "reason": "empty_llm"}

    # Guard-Check auf Antwort. Patch 158: caller_context mitgeben, damit
    # Persona-Elemente nicht als Halluzination gelten.
    guard_ctx = _build_huginn_guard_context(system_prompt)
    guard = await _run_guard(user_msg, answer, caller_context=guard_ctx)

    text_out = format_code_response(answer)
    sent = await send_telegram_message(
        cfg.bot_token,
        info["chat_id"],
        text_out,
        reply_to_message_id=info.get("message_id"),
        message_thread_id=info.get("message_thread_id"),
    )

    # Patch 158: Zweistufiges Verhalten.
    #   WARNUNG  → Antwort wurde bereits gesendet, Admin bekommt einen Hinweis.
    #   BLOCK    → Guard hat explizit Sicherheits-Block signalisiert; dann
    #              wurde die Antwort oben zwar schon losgeschickt, aber der
    #              Admin bekommt einen Alarm. Der Guard liefert aktuell nur
    #              OK/WARNUNG/SKIP/ERROR - BLOCK ist ein reserviertes Signal
    #              fuer spaetere Strictness-Stufen. Wir behandeln es defensiv.
    verdict = guard.get("verdict")
    if verdict == "WARNUNG" and cfg.admin_chat_id:
        try:
            await send_telegram_message(
                cfg.bot_token,
                cfg.admin_chat_id,
                f"⚠️ *Huginn Guard-Hinweis*\n"
                f"Chat: {info.get('chat_id')}\n"
                f"User: {info.get('username', 'unbekannt')}\n"
                f"Grund: {guard.get('reason', 'unbekannt')}\n"
                f"(Antwort wurde trotzdem zugestellt.)",
            )
        except Exception as e:
            logger.warning(f"[HUGINN-158] Guard-Warnung an Admin fehlgeschlagen: {e}")

    return {"sent": sent, "guard": guard, "latency_ms": llm_result.get("latency_ms", 0)}


async def process_update(data: Dict[str, Any], settings: Settings) -> Dict[str, Any]:
    """Verarbeitet EIN Telegram-Update durch den Huginn-Flow.

    Gemeinsamer Handler fuer Webhook (POST /webhook) und Long-Polling
    (bot.long_polling_loop). Patch 155: aus telegram_webhook extrahiert,
    damit der selbe Code beide Transport-Modi bedient.
    """
    mod_cfg = settings.modules.get("telegram", {}) or {}
    if not mod_cfg.get("enabled", False):
        return {"ok": False, "reason": "disabled"}

    # Patch 162 (D9): channel_post wird komplett ignoriert — Bots haben in
    # Channels nichts verloren, das Update käme nur über Webhook-Setups rein
    # (Long-Polling filtert es bereits per allowed_updates raus).
    if "channel_post" in data or "edited_channel_post" in data:
        logger.debug("[HUGINN-162] channel_post ignoriert update_id=%s", data.get("update_id"))
        return {"ok": True, "skipped": "channel_post"}

    # Patch 162 (O2): edited_message wird geloggt aber NICHT erneut verarbeitet —
    # sonst kann jemand seine Nachricht nachträglich auf einen Jailbreak ändern
    # und Huginn würde nochmal antworten.
    if "edited_message" in data:
        edited = data["edited_message"]
        logger.info(
            "[HUGINN-162] edited_message ignoriert user=%s chat=%s preview=%r",
            edited.get("from", {}).get("id"),
            edited.get("chat", {}).get("id"),
            (edited.get("text", "") or "")[:50],
        )
        return {"ok": True, "skipped": "edited_message"}

    # Patch 162 (O1): Unbekannte Update-Typen lautlos ignorieren.
    _KNOWN_UPDATE_TYPES = {"message", "callback_query", "my_chat_member"}
    update_types_present = set(data.keys()) - {"update_id"}
    if not update_types_present.intersection(_KNOWN_UPDATE_TYPES):
        logger.debug(
            "[HUGINN-162] Unbekannter Update-Typ ignoriert types=%s update_id=%s",
            sorted(update_types_present), data.get("update_id"),
        )
        return {"ok": True, "skipped": "unknown_update_type"}

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
        clicker_id = callback.get("from", {}).get("id")
        if not parsed:
            return {"ok": True, "kind": "callback", "skipped": "unparsed"}

        req = hitl_mgr.get(parsed["request_id"])
        if not req:
            return {"ok": True, "kind": "callback", "skipped": "unknown_request"}

        # Patch 162 (O3): Callback-Spoofing-Schutz. Klick darf nur vom Admin
        # ODER dem ursprünglich Anfragenden kommen — sonst Popup + Log.
        admin_id = cfg.admin_chat_id
        allowed_ids = {str(admin_id)} if admin_id else set()
        if req.requester_user_id is not None:
            allowed_ids.add(str(req.requester_user_id))
        if str(clicker_id) not in allowed_ids:
            await answer_callback_query(
                callback.get("id", ""),
                cfg.bot_token,
                text="🚫 Das ist nicht deine Anfrage.",
                show_alert=True,
            )
            logger.warning(
                "[HUGINN-162] Callback-Spoofing blockiert (O3) clicker=%s allowed=%s data=%s",
                clicker_id, sorted(allowed_ids), parsed,
            )
            return {"ok": True, "kind": "callback", "skipped": "spoofing"}

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
        return {"ok": True, "kind": "callback"}

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
                    requester_user_id=info.get("user_id"),
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
            # Patch 162 (K1): Auch der Gruppen-Kontext, der ans LLM geht, läuft
            # durch den Sanitizer — Findings landen im Log, nicht beim User.
            sanitizer = get_sanitizer()
            recent_text = group_mgr.recent_messages_text(info["chat_id"], limit=10)
            sanitized_recent = sanitizer.sanitize(
                recent_text,
                metadata={
                    "user_id": str(info.get("user_id") or ""),
                    "chat_type": info.get("chat_type", "group"),
                    "is_forwarded": bool(info.get("is_forwarded")),
                    "is_reply": False,
                },
            )
            prompt = build_smart_interjection_prompt(sanitized_recent.cleaned_text)
            llm_result = await call_llm(prompt, cfg.model)
            candidate = llm_result.get("content", "") or ""
            if is_skip_response(candidate):
                return {"ok": True, "skipped": "autonomous_skip"}
            # Guard-Check — Patch 158: mit Persona-Kontext, nur Admin-Hinweis
            # bei WARNUNG, Antwort wird trotzdem gesendet.
            persona = _resolve_huginn_prompt(settings)
            guard_ctx = _build_huginn_guard_context(persona)
            guard = await _run_guard(
                "(gruppen-kontext)", candidate, caller_context=guard_ctx
            )
            sent = await send_telegram_message(
                cfg.bot_token,
                info["chat_id"],
                format_code_response(candidate),
                message_thread_id=info.get("message_thread_id"),
            )
            if guard.get("verdict") == "WARNUNG" and cfg.admin_chat_id:
                try:
                    await send_telegram_message(
                        cfg.bot_token,
                        cfg.admin_chat_id,
                        f"⚠️ *Huginn Guard-Hinweis (autonom)*\n"
                        f"Chat: {info.get('chat_id')}\n"
                        f"Grund: {guard.get('reason', 'unbekannt')}\n"
                        f"(Autonomer Einwurf wurde trotzdem gesendet.)",
                    )
                except Exception as e:
                    logger.warning(f"[HUGINN-158] Guard-Warnung (autonom) an Admin fehlgeschlagen: {e}")
            group_mgr.mark_interjection(info["chat_id"])
            return {"ok": True, "sent": sent, "reason": "autonomous", "guard": guard}

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


@router.post("/webhook")
async def telegram_webhook(request: Request, settings: Settings = Depends(get_settings)):
    """Empfaengt Telegram-Updates und routet sie durch den Huginn-Flow.

    Patch 155: Default-Transport ist jetzt Long-Polling (funktioniert hinter
    Tailscale/NAT). Dieser Webhook-Endpunkt bleibt als Fallback fuer Setups
    mit oeffentlicher HTTPS-URL (mode: "webhook" in config).
    """
    mod_cfg = settings.modules.get("telegram", {}) or {}
    if not mod_cfg.get("enabled", False):
        raise HTTPException(403, "Telegram Modul deaktiviert")
    data = await request.json()
    logger.info(f"[HUGINN-123] Webhook: update_id={data.get('update_id')}")
    return await process_update(data, settings)


# Patch 156: GET /set_webhook entfernt — Long-Polling ist Default-Modus.
# Falls mode=webhook gesetzt ist, registriert startup_huginn() den Webhook
# automatisch beim Start (siehe oben register_webhook-Aufruf).


@router.get("/health")
async def health_check():
    return {"status": "ok", "module": "telegram", "patch": 123}
