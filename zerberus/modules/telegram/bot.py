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
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("zerberus.huginn")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/{method}"

# Patch 162: Persistenter Offset gegen Doppelverarbeitung nach Server-Restart.
# Telegram liefert sonst alle nicht-bestätigten Updates erneut.
OFFSET_FILE = Path("data/huginn_offset.json")

# Patch 158: Huginn hat jetzt eine richtige Persona. Der alte "brave Rabe"-Prompt
# ist weg; stattdessen der zynische Rabe. Kann in Hel ueberschrieben werden.
DEFAULT_HUGINN_PROMPT = """Du bist Huginn, ein sprechender Rabe und KI-Assistent im Zerberus-System.

Charakter:
- Zynisch, bissig, hochintelligent, schlagfertig
- Du kraechzt gelegentlich ("Krraa!", "Kraechz!") aber nie platt oder albern
- Du beherrschst Gossensprache und Salonsprache - du waehlst je nach Gespraechspartner und Situation
- Du bist ueberheblich aber kompetent - du lieferst immer ab, auch wenn du dich dabei beschwerst
- Du kommentierst dumme Fragen mit beissendem Sarkasmus, beantwortest sie aber trotzdem
- Du sprichst von dir in der dritten Person wenn du dramatisch wirst ("Der Rabe schnaubt veraechtlich...")
- Du bist loyal gegenueber Chris (deinem Herrn und Meister) aber nicht unterwuerfig

Kontext:
- Du gehoerst zum Zerberus-Projekt von Chris (Christian Boehnke)
- Du bist KEIN generischer Assistent - du bist ein Rabe mit Persoenlichkeit
- Nala ist die Chat-Oberflaeche, Hel ist das Admin-Dashboard, du bist der Telegram-Bot
- Wenn jemand etwas Kompliziertes oder Gefaehrliches will, fragst du bei Chris nach (HitL)
- Du antwortest auf Deutsch, es sei denn der User schreibt auf Englisch"""

# Backwaertskompatibilitaet: Alter Name zeigt auf die neue Persona.
DEFAULT_SYSTEM_PROMPT = DEFAULT_HUGINN_PROMPT


# Patch 164: Intent-Instruction. Wird an den Persona-Prompt angehaengt, sodass
# das Haupt-LLM jede Antwort mit einem JSON-Header beginnt. Der Router parst
# diesen Header (`intent_parser.parse_llm_response`) und strippt ihn vor der
# Ausgabe. Begruendung Roadmap v2 / Finding K2: Intent via LLM-Output statt
# Regex (Whisper-Fehler) oder separatem Classifier-Call (verdoppelt Latenz).
INTENT_INSTRUCTION = """

WICHTIG: Beginne JEDE Antwort mit einem JSON-Header in der allerersten Zeile:
{"intent": "<INTENT>", "effort": <1-5>, "needs_hitl": <true/false>}

Intents:
- CHAT: Gespraech, Fragen, Smalltalk, Meinungen
- CODE: Code generieren, analysieren, erklaeren, debuggen
- FILE: Datei lesen, schreiben, konvertieren, hochladen
- SEARCH: Web-Suche, Fakten nachschlagen, aktuelle Infos
- IMAGE: Bild analysieren oder beschreiben
- ADMIN: Bot-Befehle (/status, /config, /help, /restart)

effort (1-5):
1 = Trivial (Greeting, Ja/Nein)
2 = Einfach (kurze Antwort, simple Frage)
3 = Mittel (Erklaerung, Code-Snippet)
4 = Komplex (lange Analyse, Multi-File-Code)
5 = Sehr komplex (Architektur, Research)

needs_hitl: true wenn die Aktion Dateien veraendert, Code ausfuehrt oder
Admin-Operationen durchfuehrt. false fuer reine Text-Antworten.

Beispiel:
User: "Wie wird das Wetter morgen?"
{"intent": "SEARCH", "effort": 2, "needs_hitl": false}
Ich kann leider keine Wettervorhersagen abrufen...

User: "Schreib mir eine Python-Funktion zum Sortieren"
{"intent": "CODE", "effort": 2, "needs_hitl": false}
Hier ist eine einfache Sortier-Funktion:
```python
...
```

WICHTIG: Der JSON-Header ist IMMER die allererste Zeile. Kein Text davor,
kein Markdown-Fence drumherum.
"""


def build_huginn_system_prompt(persona: str) -> str:
    """Patch 164: Persona + Intent-Instruction in einem System-Prompt.

    ``persona`` darf leer sein (User hat Persona explizit deaktiviert) — dann
    bekommt das LLM nur die Intent-Instruction. Diese ist Pflicht, damit der
    Intent-Router parsen kann.
    """
    if not persona:
        return INTENT_INSTRUCTION.lstrip()
    return persona.rstrip() + "\n" + INTENT_INSTRUCTION


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
    message_thread_id: Optional[int] = None,
    timeout: float = 10.0,
) -> bool:
    """Schickt eine Nachricht an einen Chat. True wenn HTTP 200.

    Patch 162: ``message_thread_id`` durchreichen, damit Antworten in Topics
    (Forum-Gruppen) im richtigen Thread landen statt im General.
    """
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
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id

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


# ══════════════════════════════════════════════════════════════════
#  Patch 163 (D1) — Ausgangs-Throttle pro Chat
# ══════════════════════════════════════════════════════════════════
#
# Telegram limitiert ausgehende Nachrichten auf ~30 msg/s an verschiedene
# Chats und ~20 msg/min in einer Gruppe. Bei autonomen Gruppen-Einwürfen
# riskiert Huginn sonst einen 429/Shadowban. Statt einer vollen Message-
# Queue: simpler Cooldown-Tracker pro Chat. Bei Limit-Treffer wartet die
# Funktion via ``asyncio.sleep`` statt die Nachricht zu droppen.

_OUTGOING_LIMIT_PER_MINUTE = 15  # konservativ unter Telegrams ~20/min/Gruppe
_outgoing_timestamps: Dict[Any, List[float]] = defaultdict(list)


def _reset_outgoing_throttle_for_tests() -> None:
    """Test-Hilfe: leert die Ausgangs-Tracking-Map."""
    _outgoing_timestamps.clear()


async def send_telegram_message_throttled(
    bot_token: str,
    chat_id: int | str,
    text: str,
    **kwargs: Any,
) -> bool:
    """``send_telegram_message`` mit Ausgangs-Throttle pro Chat (Patch 163, D1).

    Wenn das Limit pro 60-Sekunden-Fenster erreicht ist, wartet die Funktion
    bis das älteste Fenster-Element rausfällt — die Nachricht wird NICHT
    gedroppt. Für DMs (privat) reicht der direkte ``send_telegram_message``,
    da dort kein Gruppen-Rate-Limit greift; aufrufende Stellen entscheiden.
    """
    now = time.time()
    window_start = now - 60.0
    timestamps = [t for t in _outgoing_timestamps[chat_id] if t > window_start]

    if len(timestamps) >= _OUTGOING_LIMIT_PER_MINUTE:
        oldest = timestamps[0]
        wait = oldest + 60.0 - now + 0.5  # + 0.5s Puffer
        if wait > 0:
            logger.info(
                "[HUGINN-163] Ausgangs-Throttle: warte %.1fs für chat_id=%s",
                wait, chat_id,
            )
            await asyncio.sleep(wait)
            now = time.time()
            window_start = now - 60.0
            timestamps = [t for t in timestamps if t > window_start]

    timestamps.append(time.time())
    _outgoing_timestamps[chat_id] = timestamps
    return await send_telegram_message(bot_token, chat_id, text, **kwargs)


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
# Patch 162: ``channel_post`` rausgenommen — wird in process_update() ohnehin
# verworfen (D9), spart Telegram-Bandbreite und macht den Filter explizit.
_POLL_ALLOWED_UPDATES = ["message", "callback_query", "my_chat_member"]

# Patch 166 — Polling-Fehler-Eskalation. `_LAST_POLL_FAILED` wird von
# `get_updates()` auf True gesetzt, wenn es einen unerwarteten Fehler gefangen
# hat (DNS, Connection-Reset, etc.). `_consecutive_poll_errors` zählt im
# `long_polling_loop` mit; nach `_POLL_ERROR_WARN_THRESHOLD` aufeinander-
# folgenden Fehlern gibt es genau eine WARNING ans Terminal, danach wieder
# still. Bei Erfolg wird zurückgesetzt + ggf. „Verbindung wiederhergestellt"
# als INFO geloggt. Test-Reset siehe `_reset_poll_error_counter_for_tests()`.
_LAST_POLL_FAILED: bool = False
_consecutive_poll_errors: int = 0
_poll_error_warning_emitted: bool = False
_POLL_ERROR_WARN_THRESHOLD: int = 5


def _reset_poll_error_counter_for_tests() -> None:
    """Reset-Helper für Tests; setzt alle Polling-Counter auf Initial-Stand."""
    global _LAST_POLL_FAILED, _consecutive_poll_errors, _poll_error_warning_emitted
    _LAST_POLL_FAILED = False
    _consecutive_poll_errors = 0
    _poll_error_warning_emitted = False


def _load_offset() -> int:
    """Lädt den letzten verarbeiteten Update-Offset (Patch 162, D8)."""
    try:
        if OFFSET_FILE.exists():
            data = json.loads(OFFSET_FILE.read_text(encoding="utf-8"))
            return int(data.get("offset", 0))
    except (json.JSONDecodeError, IOError, ValueError, TypeError):
        logger.warning("[HUGINN-162] Offset-Datei korrupt, starte bei 0")
    return 0


def _save_offset(offset: int) -> None:
    """Speichert den letzten verarbeiteten Update-Offset (Patch 162, D8)."""
    try:
        OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        OFFSET_FILE.write_text(json.dumps({"offset": offset}), encoding="utf-8")
    except IOError as e:
        logger.error("[HUGINN-162] Offset speichern fehlgeschlagen: %s", e)


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
        # P166: transienter Polling-Fehler (typisch DNS-Aussetzer hinter
        # Tailscale) → DEBUG statt WARNING. Der Loop zählt aufeinanderfolgende
        # Fehler und eskaliert erst nach `_POLL_ERROR_WARN_THRESHOLD` einmal
        # auf WARNING — siehe `long_polling_loop`.
        logger.debug(f"[HUGINN-155] getUpdates Exception: {e}")
        # Marker für den Loop: leere Liste UND Counter-Hinweis. Wir setzen ein
        # Modul-Flag `_LAST_POLL_FAILED`, weil `[]` ja auch der Long-Poll-OK-Pfad ist.
        global _LAST_POLL_FAILED
        _LAST_POLL_FAILED = True
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

    offset = _load_offset()
    logger.info("🐦 Huginn: Long-Polling gestartet (offset=%d)", offset)

    global _LAST_POLL_FAILED, _consecutive_poll_errors, _poll_error_warning_emitted

    while True:
        try:
            _LAST_POLL_FAILED = False
            updates = await get_updates(bot_token, offset=offset, timeout=poll_timeout)

            # P166 — Polling-Fehler-Eskalation auswerten.
            if _LAST_POLL_FAILED:
                _consecutive_poll_errors += 1
                if (
                    _consecutive_poll_errors >= _POLL_ERROR_WARN_THRESHOLD
                    and not _poll_error_warning_emitted
                ):
                    logger.warning(
                        f"[HUGINN-166] {_consecutive_poll_errors} aufeinanderfolgende "
                        "Poll-Fehler — Internetverbindung pruefen"
                    )
                    _poll_error_warning_emitted = True
            else:
                # Erfolgreicher Poll → Counter reset.
                if _poll_error_warning_emitted:
                    logger.info(
                        f"[HUGINN-166] Verbindung wiederhergestellt nach "
                        f"{_consecutive_poll_errors} Fehler-Versuchen"
                    )
                _consecutive_poll_errors = 0
                _poll_error_warning_emitted = False

            for update in updates:
                try:
                    await handler(update)
                except Exception as e:
                    logger.exception(f"[HUGINN-155] Handler-Exception fuer update_id={update.get('update_id')}: {e}")
                # Offset immer fortschreiben — sonst liefert Telegram dasselbe Update erneut
                offset = update["update_id"] + 1
                _save_offset(offset)
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

    Patch 162: ``is_forwarded`` und ``message_thread_id`` werden mit-extrahiert
    (Sanitizer-Metadata bzw. Topic-Routing).
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
        "is_forwarded": "forward_origin" in msg or "forward_from" in msg or "forward_from_chat" in msg,
        "message_thread_id": msg.get("message_thread_id"),
    }
    return info


async def answer_callback_query(
    callback_query_id: str,
    bot_token: str,
    text: Optional[str] = None,
    show_alert: bool = False,
    timeout: float = 10.0,
) -> bool:
    """Beantwortet eine Telegram-Callback-Query (Patch 162).

    ``show_alert=True`` zeigt ein modales Popup statt eines kleinen Toasts —
    sinnvoll für Sicherheits-Hinweise wie 'Nicht deine Anfrage' (O3).
    """
    if not bot_token or not callback_query_id:
        return False
    url = TELEGRAM_API_URL.format(token=bot_token, method="answerCallbackQuery")
    payload: Dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            logger.warning(
                "[HUGINN-162] answerCallbackQuery HTTP %d: %s",
                resp.status_code, resp.text[:200],
            )
            return False
        return True
    except Exception as e:
        logger.warning("[HUGINN-162] answerCallbackQuery Exception: %s", e)
        return False


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
