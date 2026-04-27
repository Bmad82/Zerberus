"""
Telegram Modul – Bot-Integration.

Patch 123: Huginn als vollwertiger Telegram-Chat-Partner.
- Webhook empfaengt Updates
- Guard (Mistral Small 3) prueft jede Antwort bevor sie rausgeht
- HitL fuer destruktive Aktionen (Code-Run, Gruppenbeitritt)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from zerberus.core.config import get_settings, Settings
from zerberus.core.event_bus import get_event_bus, Event
from zerberus.core.hitl_policy import get_hitl_policy
from zerberus.core.input_sanitizer import get_sanitizer
from zerberus.core.intent import HuginnIntent
from zerberus.core.intent_parser import parse_llm_response
from zerberus.core.rate_limiter import get_rate_limiter
from zerberus.modules.telegram.bot import (
    DEFAULT_HUGINN_PROMPT,
    DEFAULT_SYSTEM_PROMPT,
    HuginnConfig,
    answer_callback_query,
    build_huginn_system_prompt,
    call_llm,
    extract_message_info,
    format_code_response,
    get_file_url,
    get_me,
    is_bot_mentioned,
    long_polling_loop,
    register_webhook,
    send_document,
    send_telegram_message,
    send_telegram_message_throttled,
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
    build_timeout_message,
    hitl_sweep_loop,
    parse_callback_data,
)
from zerberus.utils.file_output import (
    build_file_caption,
    determine_file_format,
    is_extension_allowed,
    should_send_as_file,
    validate_file_size,
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
_hitl_sweep_task: Optional["asyncio.Task"] = None
_bot_user_id: Optional[int] = None


def _resolve_hitl_timeout(mod_cfg: Dict[str, Any]) -> int:
    """Liest ``timeout_seconds`` aus der Telegram-HitL-Section.

    Patch 167: neue Schluessel ``timeout_seconds`` (mit Backward-Compat
    auf den Patch-123-Schluessel ``confirmation_timeout_seconds``). Default
    kommt aus ``HitlConfig`` (siehe ``core/config.py``) — der Wert greift
    auch nach frischem ``git clone`` ohne config.yaml-Override.
    """
    from zerberus.core.config import HitlConfig
    defaults = HitlConfig()
    hitl_cfg = mod_cfg.get("hitl", {}) or {}
    return int(
        hitl_cfg.get("timeout_seconds")
        or hitl_cfg.get("confirmation_timeout_seconds")
        or defaults.timeout_seconds
    )


def _resolve_hitl_sweep_interval(mod_cfg: Dict[str, Any]) -> int:
    """Patch 167 — Sweep-Frequenz fuer den Auto-Reject-Loop."""
    from zerberus.core.config import HitlConfig
    defaults = HitlConfig()
    hitl_cfg = mod_cfg.get("hitl", {}) or {}
    return int(hitl_cfg.get("sweep_interval_seconds") or defaults.sweep_interval_seconds)


def _get_managers(settings: Settings) -> tuple[GroupManager, HitlManager]:
    """Singleton-Access auf GroupManager + HitlManager."""
    global _group_manager, _hitl_manager
    mod_cfg = settings.modules.get("telegram", {}) or {}
    behavior = mod_cfg.get("group_behavior", {}) or {}
    if _group_manager is None:
        _group_manager = GroupManager(
            cooldown_seconds=int(behavior.get("interjection_cooldown_seconds", 300))
        )
    if _hitl_manager is None:
        _hitl_manager = HitlManager(
            timeout_seconds=_resolve_hitl_timeout(mod_cfg),
        )
    return _group_manager, _hitl_manager


def _reset_telegram_singletons_for_tests() -> None:
    """Test-Helfer: setzt GroupManager und HitlManager zurueck."""
    global _group_manager, _hitl_manager, _hitl_sweep_task
    _group_manager = None
    _hitl_manager = None
    _hitl_sweep_task = None


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

    Patch 167: zusaetzlich der HitL-Sweep-Task (Auto-Reject-Timeout). Stoppen
    erfolgt in ``shutdown_huginn`` (siehe ``main.py``).

    Returns:
        Den Polling-Task bei mode="polling", sonst None.
    """
    import asyncio
    global _hitl_sweep_task
    mod_cfg = settings.modules.get("telegram", {}) or {}
    if not mod_cfg.get("enabled", False):
        return None
    cfg = HuginnConfig.from_dict(mod_cfg)
    if not cfg.bot_token:
        logger.warning("    ❌ bot_token fehlt — Bot nicht gestartet")
        return None

    _, hitl_mgr = _get_managers(settings)

    global _bot_user_id
    me = await get_me(cfg.bot_token)
    if me and me.get("id"):
        _bot_user_id = int(me["id"])
        logger.info(f"    ✅ Bot: @{me.get('username', '?')} (id={_bot_user_id})")
    else:
        logger.warning("    ⚠️ Bot-Identität nicht abrufbar (getMe fehlgeschlagen)")

    # ── Patch 167 (Block 3) — HitL-Sweep-Task starten ──────────────
    if _hitl_sweep_task is None or _hitl_sweep_task.done():
        sweep_interval = _resolve_hitl_sweep_interval(mod_cfg)

        async def _on_expired(task) -> None:
            try:
                await send_telegram_message(
                    cfg.bot_token,
                    task.chat_id,
                    build_timeout_message(task),
                )
            except Exception as e:
                logger.warning(
                    "[HITL-167] Timeout-Hinweis konnte nicht zugestellt werden "
                    "(task=%s): %s", task.id, e,
                )

        _hitl_sweep_task = asyncio.create_task(
            hitl_sweep_loop(hitl_mgr, sweep_interval, _on_expired),
            name="huginn-hitl-sweep",
        )
        logger.info("    ✅ HitL-Sweep aktiv (timeout=%ds, interval=%ds)",
                    hitl_mgr.timeout, sweep_interval)

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


async def shutdown_huginn() -> None:
    """Patch 167 — beim Shutdown den HitL-Sweep-Task sauber stoppen."""
    global _hitl_sweep_task
    if _hitl_sweep_task is not None and not _hitl_sweep_task.done():
        _hitl_sweep_task.cancel()
        try:
            await _hitl_sweep_task
        except (asyncio.CancelledError, Exception):
            pass
    _hitl_sweep_task = None


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


# ══════════════════════════════════════════════════════════════════
#  Patch 163 — Graceful Degradation Helpers (K4, O10)
# ══════════════════════════════════════════════════════════════════

# Retry-Parameter für OpenRouter — exponentielles Backoff 2s/4s/8s.
LLM_MAX_RETRIES = 3
LLM_BACKOFF_BASE = 2.0


def _resolve_guard_fail_policy(settings: Settings) -> str:
    """Patch 163 (K4): Liest ``security.guard_fail_policy`` aus der Config.

    Werte: ``"allow"`` (Default, Huginn-Modus — Antwort durchlassen),
    ``"block"`` (Rosa-Modus — Antwort zurückhalten),
    ``"degrade"`` (Future — Fallback auf lokales Modell, aktuell wie ``allow``).
    """
    sec = getattr(settings, "security", None)
    if not isinstance(sec, dict):
        return "allow"
    return str(sec.get("guard_fail_policy", "allow")).lower()


def _is_retryable_llm_error(error_str: str) -> bool:
    """True wenn der OpenRouter-Fehler nach Backoff retryt werden sollte.

    Retryable: 429 (Rate-Limit), 503 (Service Unavailable), generelles
    "rate"-Schlagwort. NICHT retryable: 400 (Bad Request), 401 (Auth),
    404, 500, sonstige.
    """
    if not error_str:
        return False
    lower = error_str.lower()
    return "429" in lower or "503" in lower or "rate" in lower


async def _call_llm_with_retry(**call_llm_kwargs: Any) -> Dict[str, Any]:
    """Wrappt ``call_llm`` mit exponentiellem Backoff bei OpenRouter 429/503.

    Patch 163 (O10): ``call_llm`` selbst raised nicht — Fehler kommen als
    ``{"content": "", "error": "HTTP 429"}`` zurück. Diese Funktion erkennt
    retryable Fehler und versucht es bis zu ``LLM_MAX_RETRIES`` mal mit
    Backoff (2s, 4s, 8s). Andere Fehler (400, 401, …) werden sofort
    zurückgegeben — kein Retry-Sinn.
    """
    last_result: Dict[str, Any] = {}
    for attempt in range(LLM_MAX_RETRIES):
        result = await call_llm(**call_llm_kwargs)
        last_result = result
        error_str = result.get("error") or ""
        if not error_str:
            return result
        if not _is_retryable_llm_error(error_str):
            return result
        if attempt < LLM_MAX_RETRIES - 1:
            wait = LLM_BACKOFF_BASE * (2 ** attempt)
            logger.warning(
                "[HUGINN-163] OpenRouter Retry %d/%d in %.1fs (err=%s)",
                attempt + 1, LLM_MAX_RETRIES, wait, error_str[:80],
            )
            await asyncio.sleep(wait)
    logger.error(
        "[HUGINN-163] OpenRouter nach %d Retries nicht erreichbar (err=%s)",
        LLM_MAX_RETRIES, (last_result.get("error") or "")[:120],
    )
    return last_result


_FALLBACK_LLM_UNAVAILABLE = (
    "Meine Kristallkugel ist gerade trüb. Versucht's später nochmal. 🔮"
)


# ══════════════════════════════════════════════════════════════════
#  Patch 168 — Datei-Output-Pfad (Block 1, 3, 4)
# ══════════════════════════════════════════════════════════════════


_HITL_FILE_QUESTION = (
    "🪶 *Achtung, Riesenakt.*\n"
    "Du fragst nach einer kompletten Datei und ich schaetze den Aufwand "
    "auf 5 (sehr komplex). Bist du sicher dass du die volle Datei willst?\n\n"
    "Tipp: Bei kleineren Anfragen liefer ich schneller. ✅ = ja, mach. ❌ = lass."
)


async def _wait_for_file_hitl_decision(
    answer: str,
    info: Dict[str, Any],
    cfg: HuginnConfig,
    hitl_mgr: "HitlManager",
) -> str:
    """Patch 168 (Block 3): FILE + effort=5 -> User-Rueckfrage via HitL-Button.

    Erstellt einen ``HitlTask`` (intent ``FILE_EFFORT5``), schickt eine
    Rueckfrage mit dem ✅/❌-Inline-Keyboard aus P167 und blockt bis der
    Requester (oder Admin) klickt — oder der Sweep den Task expired.

    Returns:
        ``"approved"`` | ``"rejected"`` | ``"expired"`` | ``"unknown"``
    """
    task = await hitl_mgr.create_task(
        requester_id=info.get("user_id") or 0,
        chat_id=info["chat_id"],
        intent="FILE_EFFORT5",
        requester_username=info.get("username", "") or "",
        details=f"Vorschau: {(answer or '')[:300]}",
        payload={"answer_length": len(answer or "")},
    )
    await send_telegram_message(
        cfg.bot_token,
        info["chat_id"],
        _HITL_FILE_QUESTION,
        reply_to_message_id=info.get("message_id"),
        message_thread_id=info.get("message_thread_id"),
        reply_markup=build_admin_keyboard(task.id),
    )
    return await hitl_mgr.wait_for_decision(task.id)


async def _deferred_file_send_after_hitl(
    answer: str,
    intent_str: str,
    info: Dict[str, Any],
    cfg: HuginnConfig,
    hitl_mgr: "HitlManager",
    content_bytes: bytes,
    filename: str,
    mime_type: str,
) -> None:
    """Patch 168 (Block 3) — Background-Task fuer FILE+effort=5 HitL-Gate.

    Wartet auf den ✅/❌-Button-Klick und sendet die Datei erst nach
    Approval. Muss als ``asyncio.create_task`` gestartet werden — sonst
    blockt der long_polling_loop sequenziell das Verarbeiten genau des
    Click-Updates, das die Entscheidung liefern soll (Deadlock bis
    HitL-Sweep nach 5min die Task expired).
    """
    decision = await _wait_for_file_hitl_decision(answer, info, cfg, hitl_mgr)
    if decision == "approved":
        caption = build_file_caption(intent_str, answer, filename)
        await send_document(
            cfg.bot_token,
            info["chat_id"],
            content_bytes,
            filename,
            caption=caption,
            reply_to_message_id=info.get("message_id"),
            message_thread_id=info.get("message_thread_id"),
            mime_type=mime_type,
        )
        logger.info(
            "[FILE-168] HitL approved → Datei gesendet (chat=%s file=%s)",
            info.get("chat_id"), filename,
        )
        return
    if decision == "rejected":
        await send_telegram_message(
            cfg.bot_token,
            info["chat_id"],
            "Krraa! Auch gut. Spart mir Tinte.",
            reply_to_message_id=info.get("message_id"),
            message_thread_id=info.get("message_thread_id"),
        )
        logger.info("[FILE-168] HitL rejected → Datei verworfen")
        return
    # expired/unknown: hitl_sweep_loop verschickt bereits den Timeout-Hinweis.
    logger.info("[FILE-168] HitL %s → Datei verworfen", decision)


async def _send_as_file(
    answer: str,
    intent_str: str,
    effort: int,
    info: Dict[str, Any],
    cfg: HuginnConfig,
    settings: Settings,
) -> tuple[str, bool]:
    """Datei-Versand-Pfad. Liefert ``(kind, sent_ok)``.

    - Validiert MIME-Whitelist und 10-MB-Size-Limit (Block 2).
    - Bei ``intent=FILE`` und ``effort>=5``: HitL-Rueckfrage als
      Background-Task (Block 3); Rueckgabe ``hitl_pending``.
    - Sonst: encodet UTF-8 und ruft ``send_document`` direkt auf.

    ``kind`` ist eine kompakte String-Kategorie fuer Test/Logging
    (``file``, ``file_blocked``, ``file_too_large``, ``hitl_pending``).
    """
    filename, mime_type = determine_file_format(intent_str, answer)
    if not is_extension_allowed(filename):
        # Sollte mit determine_file_format nicht eintreten — Belt-and-suspenders.
        logger.error(
            "[FILE-168] Extension blockiert (Whitelist/Blocklist): %s", filename,
        )
        await send_telegram_message(
            cfg.bot_token,
            info["chat_id"],
            "⚠️ Datei-Generierung fehlgeschlagen (interner Fehler).",
            reply_to_message_id=info.get("message_id"),
            message_thread_id=info.get("message_thread_id"),
        )
        return "file_blocked", False

    content_bytes = (answer or "").encode("utf-8")
    if not validate_file_size(content_bytes):
        size_mb = len(content_bytes) / (1024 * 1024)
        logger.warning(
            "[FILE-168] Datei zu gross (%d Bytes / %.1f MB)",
            len(content_bytes), size_mb,
        )
        await send_telegram_message(
            cfg.bot_token,
            info["chat_id"],
            f"⚠️ Antwort waere zu gross ({size_mb:.1f} MB, Limit 10 MB).",
            reply_to_message_id=info.get("message_id"),
            message_thread_id=info.get("message_thread_id"),
        )
        return "file_too_large", False

    # Patch 168 (Block 3): effort=5 + FILE → HitL-Gate als Background-Task.
    # Direkter await wuerde den long_polling_loop blockieren — die Click-
    # Antwort, die das Gate aufloest, wuerde nie verarbeitet (Deadlock bis
    # Sweep-Timeout). create_task entkoppelt den Wartepfad sauber.
    if intent_str.upper() == "FILE" and int(effort or 0) >= 5:
        _, hitl_mgr = _get_managers(settings)
        asyncio.create_task(
            _deferred_file_send_after_hitl(
                answer=answer,
                intent_str=intent_str,
                info=info,
                cfg=cfg,
                hitl_mgr=hitl_mgr,
                content_bytes=content_bytes,
                filename=filename,
                mime_type=mime_type,
            ),
            name=f"huginn-hitl-file-{info.get('chat_id')}",
        )
        return "hitl_pending", True

    caption = build_file_caption(intent_str, answer, filename)
    sent = await send_document(
        cfg.bot_token,
        info["chat_id"],
        content_bytes,
        filename,
        caption=caption,
        reply_to_message_id=info.get("message_id"),
        message_thread_id=info.get("message_thread_id"),
        mime_type=mime_type,
    )
    logger.info(
        "[FILE-168] Datei gesendet: chat=%s intent=%s file=%s bytes=%d ok=%s",
        info.get("chat_id"), intent_str, filename, len(content_bytes), sent,
    )
    return "file", sent


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
    # Patch 164: Intent-Instruction an den Persona-Prompt anhaengen, damit das
    # LLM jeden Output mit einem JSON-Header versieht (CHAT/CODE/FILE/...).
    effective_system_prompt = build_huginn_system_prompt(system_prompt)
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

    # Patch 163 (O10): LLM-Call mit Backoff-Retry bei 429/503.
    llm_result = await _call_llm_with_retry(
        user_message=user_msg,
        model=model,
        system_prompt=effective_system_prompt,
        image_urls=image_urls or None,
    )
    answer = llm_result.get("content", "") or ""
    if not answer.strip():
        # Echte Erschöpfung (Retries durch + immer noch error) → Kristallkugel.
        if llm_result.get("error"):
            await send_telegram_message(
                cfg.bot_token,
                info["chat_id"],
                _FALLBACK_LLM_UNAVAILABLE,
                reply_to_message_id=info.get("message_id"),
                message_thread_id=info.get("message_thread_id"),
            )
            return {"sent": False, "reason": "llm_unavailable", "error": llm_result.get("error")}
        return {"sent": False, "reason": "empty_llm"}

    # Patch 164: Intent-Header parsen + von der eigentlichen Antwort trennen.
    # Wenn das LLM keinen Header geliefert hat, faellt der Parser auf
    # ``CHAT/effort=3/needs_hitl=False`` zurueck und ``parsed.body`` enthaelt
    # den gesamten Text — das alte Pre-164-Verhalten bleibt damit erhalten.
    parsed = parse_llm_response(answer)
    # Schutz gegen LLM-Output, das nur den JSON-Header enthaelt: dann
    # liefern wir die rohe Antwort zurueck (Header inklusive) — das ist
    # haesslich, aber besser als eine leere Telegram-Nachricht. In der
    # Praxis tritt das nur bei kaputten/zu kurzen LLM-Antworten auf.
    if parsed.raw_header is not None and not parsed.body.strip():
        logger.warning(
            "[INTENT-164] LLM lieferte nur Header, kein Body — sende Roh-Antwort",
        )
        answer = answer  # urspruengliches LLM-Ergebnis behalten
    else:
        answer = parsed.body  # ab hier sehen Guard + User die Antwort OHNE Header
    user_id_str = str(info.get("user_id") or "")
    logger.info(
        "[INTENT-164] Route: user=%s intent=%s effort=%d hitl=%s",
        user_id_str, parsed.intent.value, parsed.effort, parsed.needs_hitl,
    )
    # Patch 164 (1e): Effort-Score Logging — Datengrundlage fuer die
    # Aufwands-Kalibrierung in Phase C. Heute wird der Score nicht aktiv
    # genutzt (nur geloggt).
    effort_bucket = "low" if parsed.effort <= 2 else "mid" if parsed.effort <= 3 else "high"
    logger.info(
        "[EFFORT-164] user=%s intent=%s effort=%d bucket=%s",
        user_id_str, parsed.intent.value, parsed.effort, effort_bucket,
    )

    # Patch 164 (Block 2, K5/K6/G3/G5): HitL-Policy auswerten. Statische
    # Regeln pro Intent — NEVER_HITL ueberstimmt LLM-Flag, ADMIN erzwingt
    # immer Button-HitL. K6: Bestaetigung waere ein Inline-Keyboard,
    # NICHT natuerliche Sprache. Aktuell wird die Decision nur geloggt
    # bzw. als Admin-DM gespiegelt — der eigentliche Button-Flow fuer
    # CODE/FILE/ADMIN-Aktionen folgt mit Phase D (Sandbox/Code-Exec).
    policy = get_hitl_policy()
    hitl_decision = policy.evaluate(parsed)
    if hitl_decision["needs_hitl"]:
        logger.warning(
            "[HITL-POLICY-164] Empfehlung: %s (intent=%s, reason=%s)",
            hitl_decision["hitl_type"], parsed.intent.value, hitl_decision["reason"],
        )
        if cfg.admin_chat_id:
            try:
                await send_telegram_message(
                    cfg.bot_token,
                    cfg.admin_chat_id,
                    f"🛎 *HitL-Hinweis (P164)*\n"
                    f"Chat: {info.get('chat_id')}\n"
                    f"User: {info.get('username', 'unbekannt')}\n"
                    f"Intent: `{parsed.intent.value}` (effort {parsed.effort})\n"
                    f"Grund: {hitl_decision['reason']}\n"
                    f"_Inline-Button-Flow folgt mit Phase D (Sandbox)._",
                )
            except Exception as e:
                logger.warning("[HITL-POLICY-164] Admin-Hinweis fehlgeschlagen: %s", e)

    # Guard-Check auf den Body (ohne JSON-Header). Patch 158: caller_context
    # mitgeben, damit Persona-Elemente nicht als Halluzination gelten.
    guard_ctx = _build_huginn_guard_context(system_prompt)
    guard = await _run_guard(user_msg, answer, caller_context=guard_ctx)

    # Patch 163 (K4): Guard-Fail-Policy. ``ERROR`` = Guard nicht
    # erreichbar/kaputt. Default ist ``allow`` (Antwort durchlassen + loggen),
    # konfigurierbar über ``security.guard_fail_policy`` in config.yaml.
    if guard.get("verdict") == "ERROR":
        fail_policy = _resolve_guard_fail_policy(settings)
        if fail_policy == "block":
            logger.warning("[HUGINN-163] Guard-Fail Policy='block' → Antwort zurückgehalten")
            await send_telegram_message(
                cfg.bot_token,
                info["chat_id"],
                "⚠️ Sicherheitsprüfung nicht verfügbar. Antwort zurückgehalten.",
                reply_to_message_id=info.get("message_id"),
                message_thread_id=info.get("message_thread_id"),
            )
            return {"sent": False, "reason": "guard_fail_block", "guard": guard}
        # "allow" (Default) und "degrade" (Future) → durchlassen
        logger.warning(
            "[HUGINN-163] Guard-Fail Policy='%s' → Antwort wird durchgelassen", fail_policy,
        )

    # Patch 168 (Block 1+4): Output-Router — Text vs. Datei.
    intent_str = parsed.intent.value
    if should_send_as_file(intent_str, len(answer)):
        sent_kind, sent = await _send_as_file(
            answer=answer,
            intent_str=intent_str,
            effort=parsed.effort,
            info=info,
            cfg=cfg,
            settings=settings,
        )
        return {
            "sent": sent,
            "kind": sent_kind,
            "guard": guard,
            "latency_ms": llm_result.get("latency_ms", 0),
            "intent": intent_str,
        }

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

    # Patch 163 (N3, D1): Per-User Rate-Limit. Nur für User-Messages, nicht
    # für Callback-Queries (Admin-HitL-Klicks dürfen jederzeit). User-ID kommt
    # aus ``message.from.id``. Bei Überschreitung: genau EIN „Sachte, Keule"-
    # Reply (``first_rejection=True``), danach werden Folge-Nachrichten still
    # ignoriert. Der Bot-Token wird hier direkt aus ``mod_cfg`` gezogen, weil
    # ``HuginnConfig`` erst weiter unten gebaut wird.
    if "message" in data and isinstance(data["message"], dict):
        message = data["message"]
        user_id = str(message.get("from", {}).get("id") or "")
        if user_id:
            rate_limiter = get_rate_limiter()
            rate_result = rate_limiter.check(user_id)
            if not rate_result.allowed:
                if rate_result.first_rejection:
                    bot_token = str(
                        mod_cfg.get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
                    )
                    chat_id = message.get("chat", {}).get("id")
                    thread_id = message.get("message_thread_id")
                    if bot_token and chat_id is not None:
                        await send_telegram_message(
                            bot_token,
                            chat_id,
                            "Sachte, Keule. Du feuerst schneller als Huginn denken kann. "
                            f"Warte {int(rate_result.retry_after)} Sekunden.",
                            message_thread_id=thread_id,
                        )
                return {"ok": True, "skipped": "rate_limited", "user_id": user_id}

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

        # Patch 167 (Block 1) — Task aus DB/Cache holen via Task-ID.
        task = await hitl_mgr.get_task(parsed["request_id"])
        if not task:
            await answer_callback_query(
                callback.get("id", ""),
                cfg.bot_token,
                text="❓ Anfrage unbekannt oder bereits abgelaufen.",
                show_alert=True,
            )
            logger.info(
                "[HITL-167] Callback fuer unbekannte Task-ID: %s clicker=%s",
                parsed["request_id"], clicker_id,
            )
            return {"ok": True, "kind": "callback", "skipped": "unknown_request"}

        # Patch 167 (Block 2) — Ownership: Requester selbst ODER Admin.
        # Patch 162 (O3) bleibt erhalten als Spoofing-Schutz; jetzt mit
        # Task-ID-Bezug + Admin-Override-Logging.
        admin_id = cfg.admin_chat_id
        admin_id_str = str(admin_id) if admin_id else ""
        clicker_id_str = str(clicker_id) if clicker_id is not None else ""
        is_admin = bool(admin_id_str) and clicker_id_str == admin_id_str
        is_requester = (
            task.requester_id is not None
            and clicker_id_str == str(task.requester_id)
        )
        if not (is_admin or is_requester):
            await answer_callback_query(
                callback.get("id", ""),
                cfg.bot_token,
                text="🚫 Das ist nicht deine Anfrage.",
                show_alert=True,
            )
            logger.warning(
                "[HITL-167] Callback-Spoofing blockiert (O3) "
                "clicker=%s task=%s requester=%s admin=%s",
                clicker_id, task.id, task.requester_id, admin_id_str or "-",
            )
            return {"ok": True, "kind": "callback", "skipped": "spoofing"}

        decision = "approved" if parsed["action"] == "hitl_approve" else "rejected"
        is_override = is_admin and not is_requester
        ok = await hitl_mgr.resolve_task(
            task.id,
            resolver_id=int(clicker_id) if clicker_id is not None else 0,
            decision=decision,
            is_admin_override=is_override,
        )
        if not ok:
            await answer_callback_query(
                callback.get("id", ""),
                cfg.bot_token,
                text="ℹ️ Schon entschieden.",
            )
            return {"ok": True, "kind": "callback", "skipped": "already_resolved"}

        # Frisches Task-Objekt (mit aktuellem Status) holen fuer Echo-Message.
        task = await hitl_mgr.get_task(task.id) or task
        await answer_callback_query(callback.get("id", ""), cfg.bot_token)
        await send_telegram_message(
            cfg.bot_token,
            task.chat_id,
            build_group_decision_message(task),
        )
        return {"ok": True, "kind": "callback", "decision": decision, "task_id": task.id}

    info = extract_message_info(data)
    if not info:
        return {"ok": True, "skipped": "no_message"}

    # Gruppenbeitritt? HitL anstossen
    if _bot_user_id and was_bot_added_to_group(info, _bot_user_id):
        allowed = set(int(x) for x in (cfg.allowed_group_ids or []))
        if info["chat_id"] not in allowed and cfg.admin_chat_id:
            hitl_cfg = mod_cfg.get("hitl", {}) or {}
            if hitl_cfg.get("group_join", True):
                # Patch 167: persistente Tasks via async create_task.
                req = await hitl_mgr.create_task(
                    requester_id=info.get("user_id") or 0,
                    chat_id=info["chat_id"],
                    intent="group_join",
                    requester_username=info.get("chat_title", "?"),
                    details=(
                        f"Huginn wurde eingeladen zu: "
                        f"{info.get('chat_title','?')} (ID: {info['chat_id']})"
                    ),
                )
                await send_telegram_message(
                    cfg.bot_token,
                    cfg.admin_chat_id,
                    build_admin_message(req),
                    reply_markup=build_admin_keyboard(req.id),
                )
                return {"ok": True, "hitl": req.id}

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
            # Patch 163 (O10): Auch hier Retry bei 429/503.
            # Patch 164: System-Prompt mit Intent-Instruction, damit das LLM
            # auch bei autonomen Einwuerfen einen Header liefert (CHAT-only
            # Filter folgt unten).
            persona_for_prompt = _resolve_huginn_prompt(settings)
            llm_result = await _call_llm_with_retry(
                user_message=prompt,
                model=cfg.model,
                system_prompt=build_huginn_system_prompt(persona_for_prompt),
            )
            candidate = llm_result.get("content", "") or ""
            if not candidate.strip():
                # Bei autonomem Einwurf NIE den Kristallkugel-Fallback senden —
                # niemand hat gefragt. Einfach still überspringen.
                if llm_result.get("error"):
                    logger.warning(
                        "[HUGINN-163] Autonom skip: LLM unerreichbar (err=%s)",
                        (llm_result.get("error") or "")[:80],
                    )
                return {"ok": True, "skipped": "autonomous_llm_unavailable"}
            if is_skip_response(candidate):
                return {"ok": True, "skipped": "autonomous_skip"}
            # Patch 164 (Block 3, D3/D4/O6): Intent-Header parsen und
            # autonome Einwuerfe auf CHAT/SEARCH/IMAGE beschraenken. Ein Bot
            # darf in einer Gruppe nicht autonom Code ausfuehren oder
            # Admin-Befehle absetzen.
            parsed_autonom = parse_llm_response(candidate)
            allowed_autonomous = {
                HuginnIntent.CHAT, HuginnIntent.SEARCH, HuginnIntent.IMAGE,
            }
            if parsed_autonom.intent not in allowed_autonomous:
                logger.info(
                    "[INTENT-164] Gruppen-Einwurf unterdrueckt: Intent %s nicht erlaubt",
                    parsed_autonom.intent.value,
                )
                return {
                    "ok": True,
                    "skipped": "autonomous_intent_blocked",
                    "intent": parsed_autonom.intent.value,
                }
            # Body (ohne Header) ist die eigentliche Antwort — falls Header
            # fehlt, nutzt der Parser den gesamten Text als Body.
            candidate = parsed_autonom.body or candidate
            if is_skip_response(candidate):
                # Falls der Body selbst ein SKIP ist (z. B. weil das LLM nur
                # Header + "SKIP" geliefert hat).
                return {"ok": True, "skipped": "autonomous_skip"}
            # Guard-Check — Patch 158: mit Persona-Kontext, nur Admin-Hinweis
            # bei WARNUNG, Antwort wird trotzdem gesendet.
            persona = _resolve_huginn_prompt(settings)
            guard_ctx = _build_huginn_guard_context(persona)
            guard = await _run_guard(
                "(gruppen-kontext)", candidate, caller_context=guard_ctx
            )
            # Patch 163 (K4): Guard-Fail-Policy auch im autonomen Pfad.
            if guard.get("verdict") == "ERROR":
                fail_policy = _resolve_guard_fail_policy(settings)
                if fail_policy == "block":
                    logger.warning(
                        "[HUGINN-163] Autonom skip: Guard-Fail Policy='block'",
                    )
                    return {"ok": True, "skipped": "autonomous_guard_fail_block"}
                logger.warning(
                    "[HUGINN-163] Autonom: Guard-Fail Policy='%s' → durchlassen", fail_policy,
                )
            # Patch 163 (D1): Ausgangs-Throttle für autonome Gruppen-Einwürfe —
            # konservativ unter dem Telegram-Limit von 20 msg/min/Gruppe.
            sent = await send_telegram_message_throttled(
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
