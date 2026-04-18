"""
Middleware für Zerberus (Quiet Hours, Rate Limiting, JWT-Auth).
"""
import logging
from datetime import datetime, time
import pytz
import jwt
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional
from collections import defaultdict

from zerberus.core.config import get_settings

logger = logging.getLogger(__name__)

# Rate Limiting Speicher (in Produktion durch Redis ersetzen)
_rate_limit_cache: Dict[str, List[datetime]] = defaultdict(list)

async def quiet_hours_middleware(request: Request, call_next):
    settings = get_settings()
    qh = settings.quiet_hours
    
    if not qh.enabled:
        return await call_next(request)
    
    if any(request.url.path.startswith(path) for path in qh.exclude_paths):
        return await call_next(request)
    
    tz = pytz.timezone(qh.timezone)
    now = datetime.now(tz).time()
    start = datetime.strptime(qh.start, "%H:%M").time()
    end = datetime.strptime(qh.end, "%H:%M").time()
    
    if start > end:
        in_quiet_hours = now >= start or now < end
    else:
        in_quiet_hours = start <= now < end
    
    if in_quiet_hours:
        logger.warning(f"🚫 Quiet Hours aktiv: {request.url.path} blockiert")
        raise HTTPException(
            status_code=503,
            detail=f"🌙 Quiet Hours aktiv ({qh.start}-{qh.end}). Bitte später wiederkommen!"
        )
    
    return await call_next(request)


async def rate_limiting_middleware(request: Request, call_next):
    settings = get_settings()
    rl = settings.rate_limiting
    
    if not rl.enabled:
        return await call_next(request)
    
    client_ip = request.client.host
    path = request.url.path
    
    limit_str = rl.limits.get(path, rl.default)
    try:
        max_requests, per_unit = limit_str.split("/")
        max_requests = int(max_requests)
        window = 60 if per_unit == "minute" else 1
    except:
        max_requests, window = 100, 60
    
    key = f"{client_ip}:{path}"
    now = datetime.now()
    
    _rate_limit_cache[key] = [
        ts for ts in _rate_limit_cache[key]
        if (now - ts).total_seconds() < window
    ]
    
    if len(_rate_limit_cache[key]) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail=f"⏱️ Rate Limit erreicht: {limit_str}"
        )
    
    _rate_limit_cache[key].append(now)

    return await call_next(request)


# ---------------------------------------------------------------------------
# JWT-Auth – Patch 54
# ---------------------------------------------------------------------------

# Pfade, die kein JWT benötigen
_JWT_EXCLUDED_PREFIXES = [
    "/nala/profile/login",
    "/v1/audio/transcriptions",
    "/nala/events",
    "/static/",
    "/favicon.ico",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
]


def verify_token(request: Request) -> Optional[dict]:
    """
    Liest den Authorization: Bearer <token> Header und verifiziert ihn.
    Gibt das Payload-Dict zurück bei Erfolg, None bei Fehler.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    try:
        settings = get_settings()
        payload = jwt.decode(token, settings.auth.token_secret, algorithms=["HS256"])
        return payload
    except Exception:
        return None


async def token_auth_middleware(request: Request, call_next):
    """
    JWT-Auth-Middleware (Patch 54).
    Setzt request.state.profile_name, .permission_level, .allowed_model
    aus dem verifizierten Token-Payload.
    Patch 61: Setzt zusätzlich request.state.temperature (Per-User Temperatur-Override).
    Schützt alle Pfade außer den explizit ausgenommenen.
    Hel-Dashboard (/hel) ist ausgenommen – hat eigene Basic-Auth.
    """
    path = request.url.path

    # Patch 82: Debug-Logging für Auth-Entscheidungen
    api_key_header = request.headers.get("X-API-Key", "")
    settings = get_settings()
    static_key = getattr(settings.auth, 'static_api_key', '')

    # Patch 59/82: Statischer API-Key als Alternative zu Bearer (für externe Clients wie Dictate)
    # Prüfung VOR JWT und VOR Pfad-Exclusions, damit Fast Lane immer greift
    if static_key and api_key_header == static_key:
        logger.warning(f"[DEBUG-82] Auth: path={path} | X-API-Key=MATCH | method={request.method} → Fast Lane")
        return await call_next(request)

    # Pfade ohne JWT-Anforderung
    for excl in _JWT_EXCLUDED_PREFIXES:
        if path.startswith(excl):
            logger.debug(f"[DEBUG-82] Auth: path={path} | excluded prefix → pass")
            return await call_next(request)

    # Hel-Dashboard: eigene Basic-Auth
    if path.startswith("/hel"):
        return await call_next(request)

    # Nala-Root und Nala-Profilliste: öffentlich (Login-Seite + Profil-Auswahl)
    if path in ("/nala", "/nala/", "/nala/health", "/nala/profile/prompts"):
        return await call_next(request)

    # API-Key vorhanden aber falsch
    if api_key_header:
        logger.warning(f"[DEBUG-82] Auth: path={path} | X-API-Key=MISMATCH | method={request.method}")

    # Token validieren
    payload = verify_token(request)
    if payload is None:
        logger.warning(f"[DEBUG-82] Auth: path={path} | X-API-Key={'present' if api_key_header else 'missing'} | JWT=invalid | method={request.method} → 401")
        return JSONResponse(
            status_code=401,
            content={"detail": "Nicht authentifiziert – bitte einloggen"}
        )

    # Payload in request.state schreiben
    request.state.profile_name = payload.get("sub")
    request.state.permission_level = payload.get("permission_level", "guest")
    request.state.allowed_model = payload.get("allowed_model")
    request.state.temperature = payload.get("temperature")  # Patch 61: Per-User Temperatur-Override

    return await call_next(request)
