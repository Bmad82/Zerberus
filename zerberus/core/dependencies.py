"""
Zentrale Dependencies für FastAPI.
"""
from zerberus.core.config import get_settings
from zerberus.core.event_bus import get_event_bus
from zerberus.core.database import get_db

__all__ = ["get_settings", "get_event_bus", "get_db"]
