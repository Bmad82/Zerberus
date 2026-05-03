"""Patch 160 — Whisper-Transport-Helper.

Kapselt den httpx-Call an den Whisper-Docker (Port 8002) mit:
- Explizitem Timeout (Config-gesteuert, Default 120s read / 10s connect).
- Short-Audio-Guard: Audio < `min_audio_bytes` geht gar nicht erst raus.
- Einmal-Retry bei `httpx.ReadTimeout` mit Backoff.

Wird von `legacy.py::audio_transcriptions` und `nala.py::voice_endpoint`
aufgerufen — beide Pfade teilen damit die exakt gleiche Transport-Logik.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from zerberus.core.config import WhisperConfig
from zerberus.core.gpu_queue import vram_slot

logger = logging.getLogger("zerberus.whisper")

WHISPER_VRAM_TIMEOUT_SECONDS = 60.0


class WhisperSilenceGuard(Exception):
    """Wird geworfen wenn der Short-Audio-Guard greift (<min_audio_bytes).

    Aufrufer fangen das ab und liefern ihr jeweils korrektes Silence-Response-
    Format zurueck (legacy.py → {"text": ""}, nala.py → {"transcript": "", ...}).
    """


async def transcribe(
    whisper_url: str,
    audio_data: bytes,
    filename: Optional[str],
    content_type: Optional[str],
    whisper_cfg: WhisperConfig,
    *,
    model: str = "whisper-1",
) -> Dict[str, Any]:
    """Fuehrt einen Whisper-Call aus. Fail-loud bei echten Fehlern.

    Args:
        whisper_url: URL des Whisper-Docker-Endpunkts.
        audio_data: Rohdaten der Audio-Datei (bereits in den Speicher gelesen).
        filename: Dateiname fuer die Multipart-Form (kann None sein).
        content_type: MIME-Type fuer die Multipart-Form (kann None sein).
        whisper_cfg: Transport-Config (Timeout, Retry, Min-Audio-Bytes).
        model: Whisper-Model-Key (default "whisper-1").

    Returns:
        Das JSON-Dict das der Whisper-Docker zurueckliefert (mindestens `text`).

    Raises:
        WhisperSilenceGuard: Audio ist kuerzer als `whisper_cfg.min_audio_bytes`.
        httpx.ReadTimeout: Nach Retries immer noch Timeout → hoch zum Aufrufer,
            der das in 500 ummuenzt.
        httpx.HTTPStatusError: Whisper hat Non-2xx zurueckgegeben.
    """
    if len(audio_data) < whisper_cfg.min_audio_bytes:
        logger.warning(
            "[WHISPER-160] Audio zu kurz (%d Bytes < %d), uebersprungen",
            len(audio_data),
            whisper_cfg.min_audio_bytes,
        )
        raise WhisperSilenceGuard(
            f"audio_too_short ({len(audio_data)} bytes)"
        )

    timeout = httpx.Timeout(
        whisper_cfg.request_timeout_seconds,
        connect=whisper_cfg.connect_timeout_seconds,
    )
    files = {"file": (filename or "audio", audio_data, content_type or "audio/wav")}
    data = {"model": model}

    max_attempts = max(1, whisper_cfg.timeout_retries + 1)
    last_exc: Optional[Exception] = None

    # `verify=False` bleibt: der Whisper-Docker laeuft in der Regel auf http://,
    # aber wenn der whisper_url https:// mit Self-Signed-Cert ist (Tailscale),
    # schlaegt der Standard-Verify fehl. Wir reden nur ueber Localhost/Tailscale.
    # Patch 211: VRAM-Slot um den HTTP-Call. Whisper-Docker laeuft auf
    # derselben GPU wie Gemma/Embedder/Reranker — der Slot serialisiert.
    async with vram_slot("whisper", timeout=WHISPER_VRAM_TIMEOUT_SECONDS):
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.post(whisper_url, files=files, data=data)
                    response.raise_for_status()
                    return response.json()
                except httpx.ReadTimeout as exc:
                    last_exc = exc
                    if attempt + 1 < max_attempts:
                        logger.warning(
                            "[WHISPER-160] Timeout bei Versuch %d, Retry in %.1fs...",
                            attempt + 1,
                            whisper_cfg.retry_backoff_seconds,
                        )
                        await asyncio.sleep(whisper_cfg.retry_backoff_seconds)
                        continue
                    logger.error(
                        "[WHISPER-160] Timeout nach %d Versuchen, gebe auf",
                        max_attempts,
                    )
                    raise

    # Unreachable (der Loop gibt entweder zurueck oder raist), aber mypy happy.
    assert last_exc is not None
    raise last_exc
