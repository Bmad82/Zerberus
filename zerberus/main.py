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

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    
    logger.info("=" * 60)
    logger.info("🚀 ZERBERUS PRO 4.0 STARTING")
    logger.info("=" * 60)
    
    logger.info("📊 Initialisiere Datenbank...")
    await init_db()
    await run_all()

    # Docker-Verfügbarkeit prüfen (graceful – kein Hard-Fail bei fehlendem Docker)
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
        logger.info("[SANDBOX] Docker erreichbar")
    else:
        logger.warning("[SANDBOX] Docker nicht erreichbar – Sandbox deaktiviert")

    logger.info("🔌 Starte Event-Bus...")
    bus = get_event_bus()
    bus.start()
    
    logger.info("💓 Pacemaker im Standby – wird bei erster Interaktion aktiv")

    # Overnight-Scheduler starten (Patch 57 – BERT-Sentiment täglich 04:30)
    _scheduler = None
    try:
        from zerberus.modules.sentiment.overnight import create_scheduler
        _scheduler = create_scheduler()
        if _scheduler:
            _scheduler.start()
            logger.info("⏰ Overnight-Scheduler gestartet (BERT-Sentiment täglich 04:30 Europe/Berlin)")
    except Exception as _sched_err:
        logger.warning(f"⚠️ Overnight-Scheduler konnte nicht gestartet werden: {_sched_err}")

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
