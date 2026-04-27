"""
Haupteinstiegspunkt der FastAPI-Anwendung.
"""
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager
import logging
import importlib
import pkgutil
import pathlib
import asyncio
import subprocess

import httpx

from zerberus.core.config import get_settings
from zerberus.core.logging import setup_logging
from zerberus.core.event_bus import get_event_bus
from zerberus.core.middleware import quiet_hours_middleware, rate_limiting_middleware, token_auth_middleware
from zerberus.core.database import init_db
from zerberus.core.invariants import run_all
from zerberus.app.pacemaker import update_interaction

logger = logging.getLogger(__name__)

# Docker-Verfügbarkeit (gesetzt im Lifespan-Manager)
_DOCKER_OK: bool = False

# ANSI-Farben für Startup-Status
_GREEN = "\033[32m"
_RED   = "\033[31m"
_CYAN  = "\033[36m"
_RESET = "\033[0m"

_ICONS = {"ok": "✅", "skip": "⏭️ ", "fail": "❌", "info": "⚡", "wait": "💓"}


def _log_item(label: str, status: str = "ok", detail: str = "") -> None:
    icon = _ICONS.get(status, "•")
    line = f"    {icon} {label:<20s}"
    if detail:
        line += f" {detail}"
    if status == "fail":
        line = f"{_RED}{line}{_RESET}"
    elif status == "ok":
        line = f"{_GREEN}{line}{_RESET}"
    logger.info(line)


def _log_section(title: str) -> None:
    logger.info("")
    logger.info(f"  {title}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)

    _SEP = "═" * 46
    logger.info(_SEP)
    logger.info("  ZERBERUS PRO 4.0 — Starting")
    logger.info(_SEP)

    # ── Core ─────────────────────────────────────────────────────────
    _log_section("Core")
    try:
        await init_db()
        await run_all()
        _db_url = getattr(getattr(settings, "database", None), "url", "sqlite+aiosqlite:///./bunker_memory.db")
        _log_item("Datenbank", "ok", _db_url)
        _log_item("Invarianten", "ok", "4/4 Checks bestanden")
    except Exception as _db_err:
        _log_item("Datenbank", "fail", str(_db_err)[:120])

    bus = get_event_bus()
    bus.start()
    _log_item("EventBus", "ok")

    # ── Services ─────────────────────────────────────────────────────
    _log_section("Services")

    global _DOCKER_OK
    try:
        _result = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
        _DOCKER_OK = _result.returncode == 0
    except Exception:
        _DOCKER_OK = False
    if _DOCKER_OK:
        _log_item("Docker", "ok")
    else:
        _log_item("Docker", "fail", "nicht erreichbar – Sandbox deaktiviert")

    # Patch 171 (Block 4): Sandbox-Healthcheck. Der "Docker"-Check oben
    # prueft nur den Daemon — hier zusaetzlich Image-Inspect und Config.
    # Sandbox bleibt OPTIONAL: jeder Fehler ist WARNING, kein Error.
    try:
        from zerberus.modules.sandbox.manager import get_sandbox_manager, reset_sandbox_manager
        reset_sandbox_manager()
        _sandbox_status = get_sandbox_manager().healthcheck()
        if not _sandbox_status["ok"]:
            _reason = _sandbox_status["reason"]
            if _reason == "disabled":
                _log_item("Sandbox", "skip", "deaktiviert (modules.sandbox.enabled=false)")
            elif _reason == "docker_unavailable":
                _log_item("Sandbox", "skip", "Docker nicht erreichbar")
            elif _reason == "image_missing":
                _missing = [img for img, ok in _sandbox_status["images"].items() if not ok]
                _log_item(
                    "Sandbox", "fail",
                    f"Image fehlt: {', '.join(_missing)} – bitte 'docker pull' ausführen",
                )
            else:
                _log_item("Sandbox", "fail", _reason)
        else:
            _imgs = ", ".join(_sandbox_status["images"].keys())
            _log_item("Sandbox", "ok", f"bereit ({_imgs})")
    except Exception as _sb_err:
        _log_item("Sandbox", "fail", str(_sb_err)[:100])

    _whisper_url = settings.legacy.urls.whisper_url if settings.legacy else ""
    if _whisper_url:
        try:
            from urllib.parse import urlparse as _urlparse
            _parsed = _urlparse(_whisper_url)
            _base = f"{_parsed.scheme}://{_parsed.netloc}/"
            async with httpx.AsyncClient(timeout=3.0) as _client:
                await _client.get(_base)
            _w_port = _parsed.port or (443 if _parsed.scheme == "https" else 80)
            _log_item("Whisper", "ok", f"port {_w_port}")
        except Exception as _w_err:
            _log_item("Whisper", "fail", str(_w_err)[:100])
    else:
        _log_item("Whisper", "skip", "nicht konfiguriert")

    _local_url = settings.legacy.urls.local_url if settings.legacy else ""
    if _local_url:
        try:
            async with httpx.AsyncClient(timeout=3.0) as _client:
                await _client.get(_local_url)
            _log_item("Ollama", "ok")
        except Exception as _o_err:
            _log_item("Ollama", "fail", str(_o_err)[:100])
    else:
        _log_item("Ollama", "skip", "nicht konfiguriert")

    _rag_cfg = settings.modules.get("rag", {})
    if _rag_cfg.get("enabled", False):
        try:
            import faiss as _faiss  # noqa: F401
            _index_path = pathlib.Path(_rag_cfg.get("vector_db_path", "./data/vectors")) / "faiss.index"
            if _index_path.exists():
                _idx = _faiss.read_index(str(_index_path))
                _log_item("RAG/FAISS", "ok", f"{_idx.ntotal} Vektoren")
            else:
                _log_item("RAG/FAISS", "ok", "Index leer")
        except ImportError:
            _log_item("RAG/FAISS", "fail", "faiss nicht installiert")
        except Exception as _rag_err:
            _log_item("RAG/FAISS", "fail", str(_rag_err)[:100])

        try:
            from zerberus.modules.rag.device import _cuda_state
            _cuda_avail, _cuda_free, _cuda_total, _cuda_name = _cuda_state()
            if _cuda_avail:
                _log_item("GPU", "info", f"{_cuda_name}, {_cuda_free:.1f}/{_cuda_total:.1f} GB frei")
            else:
                _log_item("GPU", "skip", "kein CUDA verfügbar")
        except Exception as _gpu_err:
            _log_item("GPU", "fail", str(_gpu_err)[:80])
    else:
        _log_item("RAG/FAISS", "skip", "deaktiviert")

    # ── Scheduler ────────────────────────────────────────────────────
    _log_section("Scheduler")

    _scheduler = None
    try:
        from zerberus.modules.sentiment.overnight import create_scheduler
        _scheduler = create_scheduler()
        if _scheduler:
            _scheduler.start()
            _log_item("Overnight", "ok", "04:30 Europe/Berlin")
        else:
            _log_item("Overnight", "skip", "nicht konfiguriert")
    except Exception as _sched_err:
        _log_item("Overnight", "fail", str(_sched_err)[:100])

    _whisper_watchdog_task = None
    if _DOCKER_OK and settings.features.get("whisper_watchdog", True):
        try:
            from zerberus.whisper_watchdog import whisper_watchdog_loop
            _whisper_watchdog_task = asyncio.create_task(whisper_watchdog_loop())
            _log_item("Whisper-Watchdog", "ok", "stündlich")
        except Exception as _wd_err:
            _log_item("Whisper-Watchdog", "fail", str(_wd_err)[:100])
    else:
        _log_item("Whisper-Watchdog", "skip", "Docker nicht erreichbar oder deaktiviert")

    _log_item("Pacemaker", "wait", "Standby")

    # ── Module ───────────────────────────────────────────────────────
    _log_section("Module")

    modules_path = pathlib.Path(__file__).parent / "modules"
    for _mod_info in pkgutil.iter_modules([str(modules_path)]):
        if _mod_info.ispkg:
            _mod_name = _mod_info.name
            _mod_cfg = settings.modules.get(_mod_name, {})
            if _mod_cfg.get("enabled", True):
                _router_file = modules_path / _mod_name / "router.py"
                if not _router_file.exists():
                    _log_item(_mod_name, "skip", "Helper-Modul")
                    continue
                try:
                    _module = importlib.import_module(f"zerberus.modules.{_mod_name}.router")
                    if hasattr(_module, "router"):
                        app.include_router(_module.router, prefix=f"/{_mod_name}")
                    _log_item(_mod_name, "ok")
                except Exception as _mod_err:
                    _log_item(_mod_name, "fail", str(_mod_err)[:80])
            else:
                _log_item(_mod_name, "skip", "deaktiviert")

    # ── Huginn ───────────────────────────────────────────────────────
    _log_section("Huginn")

    _huginn_polling_task = None
    _tg_cfg = settings.modules.get("telegram", {}) or {}
    if not _tg_cfg.get("enabled", False):
        _log_item("Telegram", "skip", "deaktiviert")
    else:
        try:
            from zerberus.modules.telegram.router import startup_huginn
            _huginn_polling_task = await startup_huginn(settings)
        except Exception as _hg_err:
            _log_item("Huginn", "fail", str(_hg_err)[:80])

    # Kurze Pause, damit der Polling-Task seine erste Runde starten kann
    await asyncio.sleep(0.5)

    logger.info("")
    logger.info(_SEP)
    logger.info("  ✨ ZERBERUS PRO 4.0 READY")
    logger.info(_SEP)
    logger.info("")

    yield

    logger.info("🛑 Shutting down...")
    # --- Huginn: Polling-Task stoppen bzw. Webhook deregistrieren (Patch 155) ---
    try:
        _tg_cfg = settings.modules.get("telegram", {}) or {}
        if _tg_cfg.get("enabled", False):
            # Patch 167 — HitL-Sweep-Task sauber stoppen (vor allem anderen).
            try:
                from zerberus.modules.telegram.router import shutdown_huginn
                await shutdown_huginn()
                logger.info("[HUGINN-167] HitL-Sweep gestoppt")
            except Exception as _hg_err:
                logger.warning("[HUGINN-167] shutdown_huginn fehlgeschlagen: %s", _hg_err)
            # Polling-Task cancellen (falls mode=polling lief)
            if _huginn_polling_task is not None:
                _huginn_polling_task.cancel()
                try:
                    await _huginn_polling_task
                except (asyncio.CancelledError, Exception):
                    pass
                logger.info("[HUGINN-155] Long-Polling gestoppt")
            else:
                # Bei mode=webhook: Webhook bei Telegram entfernen
                from zerberus.modules.telegram.bot import deregister_webhook, HuginnConfig
                _cfg = HuginnConfig.from_dict(_tg_cfg)
                if _cfg.bot_token:
                    await deregister_webhook(_cfg.bot_token)
                    logger.info("[HUGINN-123] Webhook deregistriert")
    except Exception:
        pass

    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("⏰ Overnight-Scheduler gestoppt.")
        except Exception:
            pass
    if _whisper_watchdog_task is not None:
        _whisper_watchdog_task.cancel()
        try:
            await _whisper_watchdog_task
        except (asyncio.CancelledError, Exception):
            pass
    await bus.stop()

def create_app() -> FastAPI:
    app = FastAPI(
        title="Zerberus Pro 4.0",
        version="4.0.0",
        description="Ultimatives modulares KI-System",
        lifespan=lifespan
    )
    
    settings = get_settings()
    
    # Middleware (Reihenfolge: letzte Registrierung läuft zuerst)
    if settings.quiet_hours.enabled:
        app.middleware("http")(quiet_hours_middleware)
    if settings.rate_limiting.enabled:
        app.middleware("http")(rate_limiting_middleware)
    # JWT-Auth läuft zuerst (zuletzt registriert = zuerst ausgeführt in Starlette)
    app.middleware("http")(token_auth_middleware)
    
    # Router einbinden (v1_root deaktiviert)
    from zerberus.app.routers import legacy, nala, orchestrator, hel, archive
    # app.include_router(v1_root.router)  # auskommentiert
    app.include_router(legacy.router)
    app.include_router(nala.router)
    app.include_router(orchestrator.router)
    app.include_router(hel.router)
    app.include_router(archive.router)
    
    # Statische Dateien
    app.mount("/static", StaticFiles(directory="zerberus/static"), name="static")
    
    @app.get("/")
    async def root():
        return RedirectResponse(url="/static/index.html")
    
    return app

app = create_app()


@app.get("/health")
async def aggregate_health():
    """
    Aggregierter Health-Check – ruft alle Module direkt auf (kein HTTP-Roundtrip).
    Status "degraded" wenn mindestens ein Modul nicht "ok" zurückgibt.
    """
    settings = get_settings()
    modules: dict = {}

    async def _check(name: str, coro):
        try:
            modules[name] = await coro
        except Exception as exc:
            modules[name] = {"status": "error", "detail": str(exc)}

    # Core-Router Health
    from zerberus.app.routers.nala import health_check as _nala_hc
    from zerberus.app.routers.orchestrator import health_check as _orch_hc
    await _check("nala", _nala_hc())
    await _check("orchestrator", _orch_hc())

    # Modul Health (graceful – deaktivierte oder fehlende Module erzeugen keinen Crash)
    for _mod_name, _kwargs in [
        ("rag", {"settings": settings}),
        ("emotional", {}),
        ("nudge", {}),
        ("preparer", {}),
        ("sandbox", {}),
    ]:
        try:
            _mod = importlib.import_module(f"zerberus.modules.{_mod_name}.router")
            await _check(_mod_name, _mod.health_check(**_kwargs))
        except Exception as exc:
            modules[_mod_name] = {"status": "error", "detail": str(exc)}

    overall = (
        "ok"
        if all(m.get("status") == "ok" for m in modules.values())
        else "degraded"
    )
    return {"status": overall, "modules": modules}
