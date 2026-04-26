"""
Whisper Docker Watchdog — Patch 119

Periodischer Health-Check + Auto-Restart des Whisper-Docker-Containers.
Laeuft als Background-Task im FastAPI-Lifespan.

Motivation: Der Container `der_latsch` (fedirz/faster-whisper-server) neigt nach
~1 Tag Betrieb zu langsamer Loop-Degradation. Ein stuendlicher Soft-Restart
(docker restart, kein Rebuild) haelt die Latenz stabil und kostet ~5 s Downtime.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime

import httpx

from zerberus.core.config import get_settings

logger = logging.getLogger("zerberus.whisper_watchdog")

# Container-Name aus `docker ps` (Block 1). Kein Config-Override noetig —
# wenn der Name jemals wechselt, ist das ein gezielter Eingriff.
WHISPER_CONTAINER_NAME = "der_latsch"

# Health-Endpoint des OpenAI-kompatiblen Servers. /v1/models ist billig
# (kein Model-Load), antwortet 200 wenn der Server steht.
WHISPER_HEALTH_URL = "http://127.0.0.1:8002/v1/models"

RESTART_INTERVAL_SECONDS = 3600  # 1 Stunde
HEALTH_CHECK_TIMEOUT = 10
POST_RESTART_WAIT_SECONDS = 15
DOCKER_RESTART_TIMEOUT = 60


async def check_whisper_health() -> bool:
    """True wenn /v1/models mit HTTP 200 antwortet."""
    try:
        async with httpx.AsyncClient(timeout=HEALTH_CHECK_TIMEOUT) as client:
            resp = await client.get(WHISPER_HEALTH_URL)
            return resp.status_code == 200
    except Exception as e:
        # P166: transienter Fehler — der Caller (Loop) entscheidet, ob das
        # ein echtes Problem ist (siehe restart_whisper_container-Pfad).
        logger.debug(f"[WATCHDOG-119] Health-Check fehlgeschlagen: {e}")
        return False


def restart_whisper_container() -> bool:
    """`docker restart <name>` — synchron, blockierend. True bei Erfolg."""
    try:
        # P166: Routine-Restart auf INFO statt WARNING — kein Problem, nur Aktion.
        logger.info(
            f"[WATCHDOG-119] Starte Whisper-Container neu: {WHISPER_CONTAINER_NAME}"
        )
        result = subprocess.run(
            ["docker", "restart", WHISPER_CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=DOCKER_RESTART_TIMEOUT,
        )
        if result.returncode == 0:
            # P166: erfolgreicher Restart = INFO; nur Failures bleiben WARNING/ERROR.
            logger.info("[WATCHDOG-119] Container-Restart erfolgreich")
            return True
        logger.error(
            f"[WATCHDOG-119] Restart fehlgeschlagen (rc={result.returncode}): "
            f"{result.stderr.strip()[:200]}"
        )
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"[WATCHDOG-119] docker restart > {DOCKER_RESTART_TIMEOUT}s — Timeout")
        return False
    except FileNotFoundError:
        logger.error("[WATCHDOG-119] `docker` CLI nicht im PATH")
        return False
    except Exception as e:
        logger.error(f"[WATCHDOG-119] Restart-Exception: {e}")
        return False


async def whisper_watchdog_loop() -> None:
    """
    Haupt-Loop: schlaeft RESTART_INTERVAL_SECONDS, prueft Health, restartet,
    prueft erneut. Bricht nicht ab; Fehler werden geloggt und der Loop laeuft weiter.
    """
    settings = get_settings()
    enabled = settings.features.get("whisper_watchdog", True) if hasattr(settings, "features") else True
    if not enabled:
        logger.info("[WATCHDOG-119] Deaktiviert via settings.features.whisper_watchdog=False")
        return

    # P166: Startup-Banner = INFO (es passiert ja was Sichtbares).
    logger.info(
        f"[WATCHDOG-119] Whisper-Watchdog aktiv. Intervall={RESTART_INTERVAL_SECONDS}s, "
        f"Container={WHISPER_CONTAINER_NAME}"
    )

    while True:
        try:
            await asyncio.sleep(RESTART_INTERVAL_SECONDS)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            healthy = await check_whisper_health()
            if healthy:
                # P166: Routine-Restart bei gesundem Container → DEBUG.
                logger.debug(f"[WATCHDOG-119] {timestamp} — Geplanter stuendlicher Restart")
            else:
                # P166: unresponsive Container = echter Befund → WARNING bleibt.
                logger.warning(f"[WATCHDOG-119] {timestamp} — Whisper unresponsive, sofortiger Restart")

            restart_whisper_container()

            await asyncio.sleep(POST_RESTART_WAIT_SECONDS)
            post_health = await check_whisper_health()
            if post_health:
                # P166: Container ist nach Restart wieder OK — Routine, kein Alarm.
                logger.debug("[WATCHDOG-119] Whisper nach Restart gesund")
            else:
                logger.error("[WATCHDOG-119] Whisper NACH Restart nicht erreichbar")
        except asyncio.CancelledError:
            logger.info("[WATCHDOG-119] Loop abgebrochen (shutdown)")
            raise
        except Exception as e:
            logger.error(f"[WATCHDOG-119] Unerwarteter Loop-Fehler: {e}")
            await asyncio.sleep(60)
