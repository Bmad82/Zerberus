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
_RESET = "\033[0m"


def _log_ok(name: str, detail: str = "") -> None:
    """Grüne Statuszeile für erfolgreich gestartete Dienste."""
    msg = f"{_GREEN}  ✅ {name}{_RESET}"
    if detail:
        msg += f"  ({detail})"
    logger.info(msg)


def _log_fail(name: str, reason: str = "") -> None:
    """Rote Statuszeile für fehlgeschlagene Dienste."""
    msg = f"{_RED}  ❌ {name}{_RESET}"
    if reason:
        msg += f"  — {reason}"
    logger.info(msg)


def _log_skip(name: str, reason: str = "") -> None:
    """Grau/neutral für deaktivierte/nicht konfigurierte Dienste."""
    msg = f"  ⏭️  {name}"
    if reason:
        msg += f"  ({reason})"
    logger.info(msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)

    logger.info("=" * 60)
    logger.info("🚀 ZERBERUS PRO 4.0 STARTING")
    logger.info("=" * 60)

    # --- Datenbank ---
    try:
        await init_db()
        await run_all()
        _log_ok("Datenbank")
    except Exception as _db_err:
        _log_fail("Datenbank", str(_db_err)[:120])

    # --- Docker ---
    global _DOCKER_OK
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=3,
        )
        _DOCKER_OK = result.returncode == 0
    except Exception:
        _DOCKER_OK = False
    if _DOCKER_OK:
        _log_ok("Docker")
    else:
        _log_fail("Docker", "nicht erreichbar – Sandbox deaktiviert")

    # --- Whisper ---
    _whisper_url = settings.legacy.urls.whisper_url if settings.legacy else ""
    if _whisper_url:
        try:
            # Nur Basis-URL prüfen (ohne Pfad), kurzes Timeout
            from urllib.parse import urlparse as _urlparse
            _parsed = _urlparse(_whisper_url)
            _base = f"{_parsed.scheme}://{_parsed.netloc}/"
            async with httpx.AsyncClient(timeout=3.0) as _client:
                await _client.get(_base)
            _log_ok("Whisper")
        except Exception as _w_err:
            _log_fail("Whisper", str(_w_err)[:100])
    else:
        _log_skip("Whisper", "URL nicht konfiguriert")

    # --- Ollama / Lokales LLM ---
    _local_url = settings.legacy.urls.local_url if settings.legacy else ""
    if _local_url:
        try:
            async with httpx.AsyncClient(timeout=3.0) as _client:
                await _client.get(_local_url)
            _log_ok("Ollama")
        except Exception as _o_err:
            _log_fail("Ollama", str(_o_err)[:100])
    else:
        _log_skip("Ollama", "local_url nicht konfiguriert")

    # --- RAG / FAISS ---
    _rag_cfg = settings.modules.get("rag", {})
    if _rag_cfg.get("enabled", False):
        try:
            import faiss as _faiss  # noqa: F401
            from sentence_transformers import SentenceTransformer as _ST  # noqa: F401
            _index_path = pathlib.Path(_rag_cfg.get("vector_db_path", "./data/vectors")) / "faiss.index"
            if _index_path.exists():
                _idx = _faiss.read_index(str(_index_path))
                _log_ok("RAG/FAISS", f"{_idx.ntotal} Vektoren im Index")
            else:
                _log_ok("RAG/FAISS", "Index leer – noch keine Dokumente hochgeladen")
        except ImportError:
            _log_fail("RAG/FAISS", "faiss oder sentence-transformers nicht installiert")
        except Exception as _rag_err:
            _log_fail("RAG/FAISS", str(_rag_err)[:100])

        # Patch 111: VRAM-Status einmalig beim Start loggen
        try:
            from zerberus.modules.rag.device import log_gpu_status
            log_gpu_status()
        except Exception as _gpu_err:
            logger.warning(f"[GPU-111] Status-Check fehlgeschlagen: {_gpu_err}")
    else:
        _log_skip("RAG/FAISS", "deaktiviert")

    # --- EventBus ---
    bus = get_event_bus()
    bus.start()
    _log_ok("EventBus")

    logger.info("💓 Pacemaker im Standby – wird bei erster Interaktion aktiv")

    # --- Overnight-Scheduler (Patch 57 – BERT-Sentiment täglich 04:30) ---
    _scheduler = None
    try:
        from zerberus.modules.sentiment.overnight import create_scheduler
        _scheduler = create_scheduler()
        if _scheduler:
            _scheduler.start()
            _log_ok("Overnight-Scheduler", "BERT-Sentiment täglich 04:30 Europe/Berlin")
    except Exception as _sched_err:
        _log_fail("Overnight-Scheduler", str(_sched_err)[:100])

    # --- Whisper-Watchdog (Patch 119 – stuendlicher Auto-Restart) ---
    _whisper_watchdog_task = None
    if _DOCKER_OK and settings.features.get("whisper_watchdog", True):
        try:
            from zerberus.whisper_watchdog import whisper_watchdog_loop
            _whisper_watchdog_task = asyncio.create_task(whisper_watchdog_loop())
            _log_ok("Whisper-Watchdog", "stuendlicher Docker-Restart aktiv")
        except Exception as _wd_err:
            _log_fail("Whisper-Watchdog", str(_wd_err)[:100])
    else:
        _log_skip("Whisper-Watchdog", "Docker nicht erreichbar oder Feature deaktiviert")

    # --- Module dynamisch laden ---
    logger.info("📦 Lade Module...")
    modules_path = pathlib.Path(__file__).parent / "modules"
    for module_info in pkgutil.iter_modules([str(modules_path)]):
        if module_info.ispkg:
            module_name = module_info.name
            mod_cfg = settings.modules.get(module_name, {})
            if mod_cfg.get("enabled", True):
                try:
                    module = importlib.import_module(f"zerberus.modules.{module_name}.router")
                    if hasattr(module, "router"):
                        app.include_router(module.router, prefix=f"/{module_name}")
                        logger.info(f"  ✅ {module_name}")
                except Exception as e:
                    logger.error(f"  ❌ {module_name}: {e}")
            else:
                logger.info(f"  ⏭️  {module_name} (deaktiviert)")

    logger.info("=" * 60)
    logger.info("✨ ZERBERUS PRO 4.0 READY")
    logger.info("=" * 60)
    
    yield
    
    logger.info("🛑 Shutting down...")
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
