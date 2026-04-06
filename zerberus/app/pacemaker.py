"""
Pacemaker – Startet nach einer Interaktion und hält VRAM 30 Minuten warm.
"""
import logging
import asyncio
import struct
import httpx
import time

from zerberus.core.config import get_settings

logger = logging.getLogger(__name__)

last_interaction_time = 0
pacemaker_task = None
pacemaker_running = False

def create_silent_wav() -> bytes:
    sample_rate = 16000
    num_samples = sample_rate
    num_channels = 1
    bits_per_sample = 16
    bytes_per_sample = bits_per_sample // 8
    
    riff_header = b'RIFF'
    file_size = 36 + num_samples * num_channels * bytes_per_sample
    riff_header += struct.pack('<I', file_size)
    riff_header += b'WAVE'
    
    fmt_chunk = b'fmt '
    fmt_chunk += struct.pack('<I', 16)
    fmt_chunk += struct.pack('<H', 1)
    fmt_chunk += struct.pack('<H', num_channels)
    fmt_chunk += struct.pack('<I', sample_rate)
    fmt_chunk += struct.pack('<I', sample_rate * num_channels * bytes_per_sample)
    fmt_chunk += struct.pack('<H', num_channels * bytes_per_sample)
    fmt_chunk += struct.pack('<H', bits_per_sample)
    
    data_chunk = b'data'
    data_size = num_samples * num_channels * bytes_per_sample
    data_chunk += struct.pack('<I', data_size)
    silence = bytes([0] * data_size)
    
    return riff_header + fmt_chunk + data_chunk + silence

async def pacemaker_worker():
    settings = get_settings()
    pm = settings.legacy.pacemaker
    interval = pm.interval_seconds
    whisper_url = settings.legacy.urls.whisper_url
    keep_alive = pm.keep_alive_minutes * 60

    logger.info("💓 Pacemaker-Worker gestartet (Laufzeit: %d min)", pm.keep_alive_minutes)
    global pacemaker_running, last_interaction_time

    # Erstpuls sofort beim Start – kein Warten auf erstes Intervall
    try:
        wav_data = create_silent_wav()
        files = {"file": ("silence.wav", wav_data, "audio/wav")}
        data = {"model": "whisper-1"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(whisper_url, files=files, data=data)
        logger.info("💓 Pacemaker-Erstpuls gesendet (Container aufgeweckt)")
    except Exception as e:
        logger.warning(f"⚠️ Pacemaker-Erstpuls fehlgeschlagen (nicht kritisch): {e}")

    while pacemaker_running:
        await asyncio.sleep(interval)
        if time.time() - last_interaction_time > keep_alive:
            logger.info("⏸️ Keine Interaktion für %d Minuten – Pacemaker stoppt", pm.keep_alive_minutes)
            pacemaker_running = False
            break

        try:
            wav_data = create_silent_wav()
            files = {"file": ("silence.wav", wav_data, "audio/wav")}
            data = {"model": "whisper-1"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(whisper_url, files=files, data=data)
            logger.info("💓 Pacemaker-Puls gesendet")
        except Exception as e:
            logger.error(f"❌ Pacemaker-Fehler: {e}")

def update_interaction():
    global last_interaction_time, pacemaker_running, pacemaker_task
    last_interaction_time = time.time()
    
    if not pacemaker_running:
        logger.info("▶️ Pacemaker wird gestartet (erste Interaktion)")
        pacemaker_running = True
    pacemaker_task = asyncio.create_task(pacemaker_worker())