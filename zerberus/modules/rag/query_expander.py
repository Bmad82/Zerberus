"""
RAG Query Expander – Patch 97 (R-04).

Vor dem eigentlichen FAISS-Retrieval wird die User-Query durch ein
kleines LLM gejagt, das 2-3 alternative Formulierungen und Stichworte
erzeugt. Für Aggregat-Queries ("Nenn alle Momente wo ...") liefert
das typischerweise synonyme Umschreibungen, die andere Chunks treffen
als der Original-Wortlaut.

Fail-Safe: bei Timeout, LLM-Fehler oder Parse-Error → nur die
Original-Query wird zurückgegeben (RAG bleibt immer funktional).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 3.0
_SYSTEM_PROMPT = (
    "Du bist ein Suchassistent. Gegeben eine Suchanfrage, erzeuge 2-3 "
    "alternative Formulierungen und Stichworte die dasselbe Thema "
    "betreffen. Antworte NUR mit einer JSON-Liste von Strings, kein "
    "anderer Text."
)


def _parse_expansions(raw: str) -> List[str]:
    """Extrahiert eine JSON-Liste aus der LLM-Antwort.

    Toleriert Umschläge wie ```json ... ``` oder Prefix-/Suffix-Text —
    sucht die erste `[...]`-Struktur und parst sie. Gibt [] zurück bei
    Parse-Fehler.
    """
    if not raw:
        return []
    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [str(x).strip() for x in data if isinstance(x, (str, int, float)) and str(x).strip()]


async def expand_query(query: str, config: dict) -> List[str]:
    """Erzeugt eine Liste aus [original, expansion_1, expansion_2, ...].

    Args:
        query: Original-Suchanfrage
        config: `settings.modules["rag"]`-Dict (liest `query_expansion_model`
                und nutzt OpenRouter wie `LLMService.call`)

    Returns:
        Liste mit mindestens einem Element (der Original-Query). Bei
        Erfolg zusätzlich 2-3 synonyme Formulierungen. Dedupliziert
        (case-insensitive), behält Reihenfolge.
    """
    original = (query or "").strip()
    if not original:
        return []

    try:
        from zerberus.core.config import get_settings
        settings = get_settings()
    except Exception as e:
        logger.warning(f"[EXPAND-97] settings unavailable, skip: {e}")
        return [original]

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("[EXPAND-97] OPENROUTER_API_KEY missing, skip expansion")
        return [original]

    model = config.get("query_expansion_model") or settings.legacy.models.cloud_model
    url = settings.legacy.urls.cloud_api_url

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": original},
        ],
        "temperature": 0.3,
        "max_tokens": 200,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
    except asyncio.TimeoutError:
        logger.warning("[EXPAND-97] timeout — falling back to original query")
        return [original]
    except httpx.HTTPError as e:
        logger.warning(f"[EXPAND-97] http error ({type(e).__name__}): {e}")
        return [original]
    except (KeyError, ValueError) as e:
        logger.warning(f"[EXPAND-97] response parse error: {e}")
        return [original]
    except Exception as e:
        logger.warning(f"[EXPAND-97] unexpected error ({type(e).__name__}): {e}")
        return [original]

    expansions = _parse_expansions(raw)
    seen = {original.lower()}
    out: List[str] = [original]
    for e in expansions:
        key = e.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(e)
    logger.info(f"[EXPAND-97] '{original}' → {len(out)} queries: {out[1:]}")
    return out
