"""
Patch 123 – Huginn, Zerberus Telegram-Bot.

Huginn (altnordisch: "der Denkende") ist Odins rechter Rabe — ein Bullauge
aus Zerberus in die Welt. Vollwertiger Chat-Partner, NICHT nur Alert-Pipe.

Fastlane-Prinzip:
    Input → Guard-Check (Mistral Small 3) → LLM → Output
    kein RAG, kein Memory, kein Sentiment.

HitL für destruktive Aktionen (Code-Ausführung, Gruppenbeitritt).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("zerberus.huginn")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"

DEFAULT_SYSTEM_PROMPT = (
    "Du bist Huginn, einer von Odins Raben und das Bullauge des Zerberus-Systems. "
    "Du plauderst natuerlich, bist neugierig, klug und ehrlich. Keine Corporate-Phrasen. "
    "Wenn du Code schreibst, formatiere ihn als Telegram-Code-Block (```sprache ... ```). "
    "Du hast KEIN Gedaechtnis zwischen Nachrichten - jede Konversation ist frisch."
)


@dataclass
class HuginnConfig:
    """Laufzeit-Konfiguration aus config.yaml.modules.telegram."""
    enabled: bool = False
    bot_token: str = ""
    admin_chat_id: str = ""
    allowed_group_ids: list[int] = None
    model: str = "deepseek/deepseek-chat"
    max_response_length: int = 4000

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "HuginnConfig":
        return cls(
            enabled=bool(raw.get("enabled", False)),
            bot_token=str(raw.get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN") or ""),
            admin_chat_id=str(raw.get("admin_chat_id") or ""),
            allowed_group_ids=list(raw.get("allowed_group_ids") or []),
            model=str(raw.get("model") or "deepseek/deepseek-chat"),
            max_response_length=int(raw.get("max_response_length") or 4000),
        )


async def call_llm(
    user_message: str,
    model: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    image_urls: Optional[list[str]] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """OpenRouter-Chat-Completion. Gibt {content, usage, latency_ms} zurück."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"content": "", "error": "Kein OPENROUTER_API_KEY gesetzt", "latency_ms": 0}

    user_content: Any
    if image_urls:
        user_content = [{"type": "text", "text": user_message}]
        for url in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": url}})
    else:
        user_content = user_message

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 1000,
    }

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        latency_ms = int((time.time() - start) * 1000)
        if resp.status_code != 200:
            logger.warning(f"[HUGINN-123] LLM HTTP {resp.status_code}: {resp.text[:200]}")
            return {"content": "", "error": f"HTTP {resp.status_code}", "latency_ms": latency_ms}
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return {"content": content, "usage": usage, "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        logger.warning(f"[HUGINN-123] LLM exception: {e}")
        return {"content": "", "error": str(e)[:200], "latency_ms": latency_ms}


async def send_telegram_message(
    bot_token: str,
    chat_id: int | str,
    text: str,
    reply_to_message_id: Optional[int] = None,
    parse_mode: str = "Markdown",
    reply_markup: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> bool:
    """Schickt eine Nachricht an einen Chat. True wenn HTTP 200."""
    if not bot_token:
        logger.warning("[HUGINN-123] Kein bot_token - send_telegram_message uebersprungen")
        return False

    url = TELEGRAM_API_URL.format(token=bot_token, method="sendMessage")
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": parse_mode,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            # Fallback ohne parse_mode (Markdown-Fehler sind haeufig)
            if parse_mode:
                payload.pop("parse_mode", None)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning(f"[HUGINN-123] Telegram {resp.status_code}: {resp.text[:200]}")
                return False
        return True
    except Exception as e:
        logger.warning(f"[HUGINN-123] send_telegram_message Exception: {e}")
        return False


async def register_webhook(bot_token: str, webhook_url: str, timeout: float = 10.0) -> bool:
    """Registriert den Webhook bei Telegram. True wenn Erfolg."""
    if not bot_token or not webhook_url:
        return False
    url = TELEGRAM_API_URL.format(token=bot_token, method="setWebhook")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json={"url": webhook_url})
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"[HUGINN-123] register_webhook Exception: {e}")
        return False


async def deregister_webhook(bot_token: str, timeout: float = 10.0) -> bool:
    """Entfernt den Webhook."""
    if not bot_token:
        return False
    url = TELEGRAM_API_URL.format(token=bot_token, method="deleteWebhook")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url)
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"[HUGINN-123] deregister_webhook Exception: {e}")
        return False


# ══════════════════════════════════════════════════════════════════
#  Patch 155: Long-Polling (funktioniert hinter Tailscale/NAT)
# ══════════════════════════════════════════════════════════════════

# Erlaubte Update-Typen — deckt alles ab was process_update() verarbeitet.
_POLL_ALLOWED_UPDATES = ["message", "channel_post", "callback_query", "my_chat_member"]


async def get_me(bot_token: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """Liefert den Bot-User (id, username, ...). None bei Fehler.

    Wird beim Polling-Start einmal aufgerufen, um `_bot_user_id` zu cachen
    (fuer was_bot_added_to_group()).
    """
    if not bot_token:
        return None
    url = TELEGRAM_API_URL.format(token=bot_token, method="getMe")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("ok"):
            return None
        return data.get("result")
    except Exception as e:
        logger.warning(f"[HUGINN-155] get_me Exception: {e}")
        return None


async def get_updates(
    bot_token: str,
    offset: int = 0,
    timeout: int = 30,
    allowed_updates: Optional[list[str]] = None,
) -> list[Dict[str, Any]]:
    """Telegram getUpdates mit Long-Poll.

    `timeout` ist der Telegram-Long-Poll-Timeout (Server haelt die Verbindung
    so lange offen, bis entweder Updates da sind oder der Timeout greift).
    HTTP-Client-Timeout wird auf `timeout + 5` gesetzt damit wir nicht vor
    Telegram abbrechen.
    """
    if not bot_token:
        return []
    url = TELEGRAM_API_URL.format(token=bot_token, method="getUpdates")
    params: Dict[str, Any] = {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": allowed_updates or _POLL_ALLOWED_UPDATES,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout + 5) as client:
            resp = await client.post(url, json=params)
        if resp.status_code != 200:
            logger.warning(f"[HUGINN-155] getUpdates HTTP {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        if not data.get("ok"):
            logger.warning(f"[HUGINN-155] getUpdates nicht-ok: {data}")
            return []
        return data.get("result", []) or []
    except httpx.TimeoutException:
        # Long-Poll ohne neue Updates → normal, nicht loggen
        return []
    except Exception as e:
        logger.warning(f"[HUGINN-155] getUpdates Exception: {e}")
        return []


async def long_polling_loop(
    bot_token: str,
    handler,
    poll_timeout: int = 30,
    error_backoff: float = 5.0,
) -> None:
    """Endlos-Loop: holt Updates von Telegram, ruft `handler(update)` pro Update.

    Patch 155: Telegram-Bot funktioniert hinter Tailscale/NAT ohne Webhook.

    - `handler` ist ein async callable `(update: dict) -> Any`.
    - Beim Start wird ein ggf. alter Webhook entfernt (getUpdates darf nicht
      parallel zu einem registrierten Webhook laufen).
    - Fehler werden geloggt, der Loop wartet `error_backoff` Sekunden und
      macht weiter — stoppt nur bei CancelledError (shutdown).
    """
    if not bot_token:
        logger.warning("[HUGINN-155] long_polling_loop: kein bot_token, Loop nicht gestartet")
        return

    # Alten Webhook entfernen — sonst liefert getUpdates HTTP 409 (Conflict)
    await deregister_webhook(bot_token)
    logger.info("🐦 Huginn: Long-Polling gestartet")

    offset = 0
    while True:
        try:
            updates = await get_updates(bot_token, offset=offset, timeout=poll_timeout)
            for update in updates:
                try:
                    await handler(update)
                except Exception as e:
                    logger.exception(f"[HUGINN-155] Handler-Exception fuer update_id={update.get('update_id')}: {e}")
                # Offset immer fortschreiben — sonst liefert Telegram dasselbe Update erneut
                offset = update["update_id"] + 1
        except asyncio.CancelledError:
            logger.info("🐦 Huginn: Long-Polling gestoppt (cancelled)")
            raise
        except Exception as e:
            logger.warning(f"[HUGINN-155] Polling-Loop-Fehler: {e}")
            await asyncio.sleep(error_backoff)


async def get_file_url(bot_token: str, file_id: str, timeout: float = 10.0) -> Optional[str]:
    """Resolved eine Telegram-file_id zu einer herunterladbaren URL (Vision-Inputs)."""
    if not bot_token or not file_id:
        return None
    url = TELEGRAM_API_URL.format(token=bot_token, method="getFile")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json={"file_id": file_id})
        if resp.status_code != 200:
            return None
        data = resp.json()
        file_path = data.get("result", {}).get("file_path")
        if not file_path:
            return None
        return f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    except Exception as e:
        logger.warning(f"[HUGINN-123] get_file_url Exception: {e}")
        return None


def extract_message_info(update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalisiert ein Telegram-Update auf die Felder die wir brauchen.

    Liefert None bei Updates die nicht verarbeitet werden sollen
    (edited messages ohne Text, reine Service-Events, etc.).
    """
    msg = update.get("message") or update.get("channel_post")
    if not msg:
        return None

    chat = msg.get("chat", {})
    info = {
        "message_id": msg.get("message_id"),
        "chat_id": chat.get("id"),
        "chat_type": chat.get("type", "private"),
        "chat_title": chat.get("title") or chat.get("username", ""),
        "user_id": msg.get("from", {}).get("id"),
        "username": msg.get("from", {}).get("username", ""),
        "text": msg.get("text") or msg.get("caption") or "",
        "photo_file_ids": [p["file_id"] for p in (msg.get("photo") or []) if p.get("file_id")],
        "reply_to_message": msg.get("reply_to_message"),
        "new_chat_members": msg.get("new_chat_members") or [],
    }
    return info


def is_bot_mentioned(text: str, bot_username: str = "HuginnBot", bot_name: str = "Huginn") -> bool:
    """True wenn Huginn im Text direkt angesprochen wird."""
    if not text:
        return False
    lower = text.lower()
    if f"@{bot_username.lower()}" in lower:
        return True
    if bot_name.lower() in lower:
        return True
    return False


def was_bot_added_to_group(info: Dict[str, Any], bot_user_id: int) -> bool:
    """True wenn dieses Update 'bot wurde zur Gruppe hinzugefuegt' signalisiert."""
    for member in info.get("new_chat_members") or []:
        if member.get("id") == bot_user_id:
            return True
    return False


def format_code_response(content: str) -> str:
    """Stellt sicher dass Code-Block-Markdown konsistent ist. Kürzt wenn zu lang."""
    if len(content) > 4000:
        content = content[:3900] + "\n\n…[gekuerzt]"
    return content
