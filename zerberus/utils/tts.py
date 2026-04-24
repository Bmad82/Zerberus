"""
Patch 143 (B-014): Text-to-Speech via edge-tts.

Wrapper um `edge_tts.Communicate`, liefert MP3-Bytes zurück. Fail-safe:
bei Fehler im Modul (Netzausfall, Rate-Limit, invalide Stimme) wird eine
saubere Exception geworfen — die Router-Schicht übersetzt sie in 503/400.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    import edge_tts  # type: ignore
    _EDGE_OK = True
except Exception as e:
    logger.warning("edge-tts nicht verfügbar: %s", e)
    edge_tts = None  # type: ignore
    _EDGE_OK = False


DEFAULT_VOICE = "de-DE-ConradNeural"


def is_available() -> bool:
    """True wenn edge-tts importierbar ist."""
    return _EDGE_OK


async def text_to_speech(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
) -> bytes:
    """
    Konvertiert Text zu MP3-Bytes.

    Args:
        text: Zu sprechender Text (max sinnvoll ~5000 Zeichen).
        voice: edge-tts ShortName, z.B. "de-DE-ConradNeural".
        rate: Sprechgeschwindigkeit als Prozentstring, z.B. "+10%", "-20%".
              Erlaubt: -50% bis +100% (edge-tts Grenze ist +200%, aber
              wir halten die UI darunter).

    Returns:
        MP3-Bytes (audio/mpeg).

    Raises:
        RuntimeError: wenn edge-tts nicht verfügbar ist.
        ValueError: bei leerem Text oder unsinnigem rate-Format.
    """
    if not _EDGE_OK:
        raise RuntimeError("edge-tts nicht installiert")
    if not text or not text.strip():
        raise ValueError("text darf nicht leer sein")
    # Rate muss Prozentstring sein mit Vorzeichen
    rate = rate.strip()
    if not (rate.startswith("+") or rate.startswith("-")) or not rate.endswith("%"):
        raise ValueError(f"rate-Format ungültig: {rate!r} (erwartet z.B. '+10%' oder '-20%')")

    communicate = edge_tts.Communicate(text, voice, rate=rate)
    audio = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            audio.extend(chunk["data"])
    if not audio:
        raise RuntimeError(f"edge-tts lieferte keine Audio-Daten (voice={voice!r})")
    return bytes(audio)


async def list_voices(language: str = "de") -> list[dict[str, Any]]:
    """Listet verfügbare edge-tts-Stimmen für eine Sprache (Default: deutsch)."""
    if not _EDGE_OK:
        return []
    voices = await edge_tts.list_voices()
    if not language:
        return voices
    prefix = language.lower()
    return [v for v in voices if str(v.get("Locale", "")).lower().startswith(prefix)]
