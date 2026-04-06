"""
Sandbox-Modul – Patch 52: Docker-Executor aktiv.
Patch 54: Permission-Check liest aus request.state (JWT-Middleware) statt X-Permission-Level Header.
Docker-basierte Code-Ausführung mit Permission-Check und EventBus-Integration.
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from zerberus.core.event_bus import get_event_bus, Event
from zerberus.modules.sandbox.executor import execute_in_sandbox

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Sandbox"])


async def health_check() -> dict:
    from zerberus.main import _DOCKER_OK
    return {"status": "ok" if _DOCKER_OK else "disabled", "docker": _DOCKER_OK}


@router.get("/health")
async def sandbox_health():
    return await health_check()


class SandboxRequest(BaseModel):
    code: str
    language: str = "python"
    session_id: str = ""


@router.post("/execute")
async def sandbox_execute(
    req: SandboxRequest,
    request: Request,
):
    """
    Führt Code in einer Docker-Sandbox aus.
    Erfordert permission_level=admin (aus JWT-Middleware via request.state).
    """
    permission_level = getattr(request.state, "permission_level", "guest")
    if permission_level != "admin":
        raise HTTPException(status_code=403, detail="Nur Admins dürfen die Sandbox nutzen")

    try:
        from zerberus.main import _DOCKER_OK
    except ImportError:
        _DOCKER_OK = False

    if not _DOCKER_OK:
        raise HTTPException(status_code=503, detail="Docker nicht verfügbar – Sandbox deaktiviert")

    result = await execute_in_sandbox(req.code, req.language)

    bus = get_event_bus()
    await bus.publish(Event(
        type="sandbox_executed",
        data={
            "session_id": req.session_id,
            "exit_code": result["exit_code"],
            "timed_out": result["timed_out"],
        },
        session_id=req.session_id,
    ))

    return result
