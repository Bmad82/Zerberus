"""
Archiv-Router – Zugriff auf gespeicherte Chats (Session-basiert).
"""
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

import yaml

from zerberus.core.database import get_all_sessions, get_session_messages, delete_session
from zerberus.core.config import get_settings, Settings
from zerberus.core.cleaner import clean_transcript
from zerberus.core.dialect import detect_dialect_marker, apply_dialect
from zerberus.core.llm import LLMService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/archive", tags=["Archive"])


def _get_test_profile_keys() -> list[str]:
    """
    Patch 138 (B-004): Liest aus config.yaml die Liste aller Profile mit
    is_test=true — deren Sessions werden aus der Standard-Session-Liste
    ausgeblendet (sonst überschwemmen Playwright-Testläufe die UI).
    """
    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        return []
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        profiles = data.get("profiles", {}) or {}
        return [k for k, v in profiles.items() if v and v.get("is_test", False)]
    except Exception as e:
        logger.warning(f"[PATCH-138] Konnte Test-Profile nicht aus config.yaml lesen: {e}")
        return []

class SessionInfo(BaseModel):
    session_id: str
    first_message: str
    created_at: str | None
    last_message_at: str | None

class MessageInfo(BaseModel):
    role: str
    content: str
    timestamp: str
    sentiment: float | None

@router.get("/sessions", response_model=List[SessionInfo])
async def list_sessions(limit: int = 50, include_test: bool = False):
    """
    Listet alle verfügbaren Chat-Sessions auf.

    Patch 138 (B-004): `include_test=False` (Default) blendet Sessions von
    Test-Profilen (loki, fenrir, …) aus. Für Debugging/Admin-Zwecke kann
    `include_test=true` übergeben werden.
    """
    exclude = [] if include_test else _get_test_profile_keys()
    sessions = await get_all_sessions(limit, exclude_profiles=exclude)
    return sessions

@router.get("/session/{session_id}", response_model=List[MessageInfo])
async def get_session(session_id: str):
    """Liefert alle Nachrichten einer bestimmten Session."""
    messages = await get_session_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Session nicht gefunden")
    return messages

@router.delete("/session/{session_id}")
async def delete_session_endpoint(session_id: str):
    """Löscht eine Session und alle zugehörigen Nachrichten."""
    await delete_session(session_id)
    return {"status": "deleted"}
