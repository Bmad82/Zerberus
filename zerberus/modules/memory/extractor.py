"""Background Memory Extraction – Patch 115.

Liest die letzten 24h User-Nachrichten aus `interactions`, lässt ein Cloud-LLM
strukturierte Fakten extrahieren und schreibt neue Fakten als eigene Chunks in
den FAISS-Index. Läuft als Teil des 04:30-Cron-Jobs (nach BERT-Sentiment) und
kann manuell via `POST /hel/admin/memory/extract` getriggert werden.

Fail-Safe: LLM-Timeouts, Parse-Fehler, fehlender API-Key führen zu leerem
Ergebnis statt Exception — der Overnight-Job bleibt robust.

Duplikat-Erkennung: Vor Indexierung wird das Embedding des Fakts gegen den
bestehenden Index gesucht; bei Cosine-Similarity ≥ threshold wird übersprungen.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)


MEMORY_EXTRACTION_PROMPT = """Du bist ein Fakten-Extraktor. Analysiere die folgenden Chat-Nachrichten \
und extrahiere ALLE konkreten Fakten, Vorlieben, Beziehungen und Informationen \u00fcber den User.

Regeln:
- Nur KONKRETE Fakten, keine Meinungen oder Stimmungen
- Jeder Fakt als eigenst\u00e4ndiger Satz, der ohne Kontext verst\u00e4ndlich ist
- Keine Duplikate innerhalb der Extraktion
- Category pro Fakt: personal, technical, preference, relationship, event
- Ignoriere System-Nachrichten und Bot-Antworten \u2014 nur User-Aussagen z\u00e4hlen

Ausgabe als JSON-Array:
[{{"fact": "...", "category": "..."}}, ...]

Falls keine extrahierbaren Fakten: leeres Array []

Chat-Nachrichten:
{messages}
"""


_ALLOWED_CATEGORIES = {"personal", "technical", "preference", "relationship", "event"}


def _parse_facts(raw: str) -> list[dict]:
    """Extrahiert JSON-Array aus LLM-Antwort. Fail-Safe: [] bei Fehler."""
    if not raw:
        return []
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact", "")).strip()
        cat = str(item.get("category", "personal")).strip().lower()
        if not fact:
            continue
        if cat not in _ALLOWED_CATEGORIES:
            cat = "personal"
        out.append({"fact": fact, "category": cat})
    return out


def _batch_messages(rows: list[tuple], max_words: int) -> list[str]:
    """Baut Text-Batches aus (timestamp, content)-Tuples unter der Wort-Obergrenze."""
    batches: list[str] = []
    current: list[str] = []
    current_words = 0
    for ts, content in rows:
        if not content:
            continue
        line = f"[{ts}] {content}".strip()
        words = len(line.split())
        if current and current_words + words > max_words:
            batches.append("\n".join(current))
            current = [line]
            current_words = words
        else:
            current.append(line)
            current_words += words
    if current:
        batches.append("\n".join(current))
    return batches


async def _call_extraction_llm(messages_text: str, mem_cfg: dict) -> list[dict]:
    """Ruft OpenRouter mit dem Extraction-Prompt auf. Fail-Safe: [] bei Fehler."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("[MEM-115] OPENROUTER_API_KEY fehlt — \u00fcberspringe Extraktion")
        return []

    try:
        from zerberus.core.config import get_settings
        settings = get_settings()
        default_model = settings.legacy.models.cloud_model
        url = settings.legacy.urls.cloud_api_url
    except Exception as e:
        logger.warning(f"[MEM-115] Settings nicht verf\u00fcgbar: {e}")
        return []

    model = mem_cfg.get("extraction_model") or default_model
    timeout_s = float(mem_cfg.get("extraction_timeout", 45.0))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": MEMORY_EXTRACTION_PROMPT.format(messages=messages_text)},
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"]
    except asyncio.TimeoutError:
        logger.warning("[MEM-115] LLM-Timeout \u2014 Batch \u00fcbersprungen")
        return []
    except httpx.HTTPError as e:
        logger.warning(f"[MEM-115] HTTP-Fehler ({type(e).__name__}): {e}")
        return []
    except (KeyError, ValueError) as e:
        logger.warning(f"[MEM-115] Response-Parse-Fehler: {e}")
        return []
    except Exception as e:
        logger.warning(f"[MEM-115] Unerwarteter Fehler ({type(e).__name__}): {e}")
        return []

    return _parse_facts(raw)


def _is_duplicate(fact_vec, threshold: float) -> bool:
    """Pr\u00fcft via FAISS-Search ob ein sehr \u00e4hnlicher Chunk existiert.

    FAISS L2-Distance f\u00fcr normalisierte Vektoren: L2 = sqrt(2 - 2*cos).
    Umrechnung: cos = 1 - L2^2 / 2. Wenn cos >= threshold \u2192 Duplikat.
    """
    from zerberus.modules.rag.router import _index
    if _index is None or _index.ntotal == 0:
        return False
    distances, indices = _index.search(fact_vec, 1)
    if len(distances) == 0 or len(distances[0]) == 0:
        return False
    l2 = float(distances[0][0])
    idx = int(indices[0][0])
    if idx == -1:
        return False
    cosine = 1.0 - (l2 * l2) / 2.0
    return cosine >= threshold


async def _store_memory_structured(
    fact_text: str,
    category: str,
    source_tag: str,
    confidence: float = 0.8,
    embedding_index: int | None = None,
) -> int | None:
    """Patch 132: Schreibt einen Fakt zus\u00e4tzlich in die strukturierte `memories`-Tabelle.

    Returns: ID der neu eingef\u00fcgten Zeile oder None bei Fehler.
    Duplikat-Check: Exakter content-Match auf category+fact wird \u00fcbersprungen.
    """
    try:
        from zerberus.core.database import _async_session_maker
        from sqlalchemy import text as sa_text
    except Exception as e:
        logger.warning(f"[MEM-132] DB-Import fehlgeschlagen: {e}")
        return None

    try:
        async with _async_session_maker() as session:
            # Exakte Duplikat-Pr\u00fcfung
            existing = await session.execute(sa_text(
                "SELECT id FROM memories WHERE category = :cat AND fact = :fact AND is_active = 1 LIMIT 1"
            ), {"cat": category, "fact": fact_text})
            row = existing.fetchone()
            if row is not None:
                return None
            result = await session.execute(sa_text(
                "INSERT INTO memories "
                "(category, fact, confidence, source_tag, embedding_index, extracted_at, is_active) "
                "VALUES (:cat, :fact, :conf, :src, :emb, datetime('now'), 1)"
            ), {
                "cat": category,
                "fact": fact_text,
                "conf": float(confidence),
                "src": source_tag,
                "emb": embedding_index,
            })
            await session.commit()
            return int(result.lastrowid) if hasattr(result, "lastrowid") else None
    except Exception as e:
        logger.warning(f"[MEM-132] Strukturierter Memory-Insert fehlgeschlagen: {e}")
        return None


async def extract_memories(mem_cfg: dict | None = None) -> dict:
    """Hauptfunktion: liest 24h-Nachrichten, extrahiert Fakten, indiziert Neues.

    Args:
        mem_cfg: Optional `settings.modules["memory"]`-Dict. Wenn None, werden
                 die Settings geladen.

    Returns:
        dict mit Keys: extracted, indexed, skipped, batches, errors.
    """
    from zerberus.core.config import get_settings
    from zerberus.core.database import _async_session_maker
    from sqlalchemy import text as sa_text

    settings = get_settings()
    if mem_cfg is None:
        mem_cfg = settings.modules.get("memory", {}) or {}

    result = {"extracted": 0, "indexed": 0, "skipped": 0, "batches": 0, "errors": []}

    if not mem_cfg.get("extraction_enabled", True):
        logger.info("[MEM-115] extraction_enabled=false \u2014 \u00fcbersprungen")
        result["errors"].append("disabled")
        return result

    rag_cfg = settings.modules.get("rag", {}) or {}
    if not rag_cfg.get("enabled", False):
        logger.warning("[MEM-115] RAG-Modul deaktiviert \u2014 Memory-Extraktion nicht m\u00f6glich")
        result["errors"].append("rag_disabled")
        return result

    try:
        from zerberus.modules.rag.router import (
            RAG_AVAILABLE, _ensure_init, _encode, _add_to_index,
        )
    except ImportError as e:
        logger.error(f"[MEM-115] RAG-Router-Import fehlgeschlagen: {e}")
        result["errors"].append(f"rag_import: {e}")
        return result

    if not RAG_AVAILABLE:
        logger.warning("[MEM-115] faiss/sentence-transformers nicht installiert")
        result["errors"].append("rag_deps_missing")
        return result

    await _ensure_init(settings)

    try:
        async with _async_session_maker() as session:
            query = sa_text(
                "SELECT timestamp, content FROM interactions "
                "WHERE timestamp >= datetime('now', '-24 hours') "
                "  AND role = 'user' "
                "  AND content IS NOT NULL AND content != '' "
                "ORDER BY timestamp ASC"
            )
            rows = (await session.execute(query)).fetchall()
            rows = [(str(r[0]), r[1]) for r in rows]
    except Exception as e:
        logger.error(f"[MEM-115] DB-Query fehlgeschlagen: {e}")
        result["errors"].append(f"db: {e}")
        return result

    if not rows:
        logger.info("[MEM-115] Keine User-Nachrichten in den letzten 24h")
        return result

    max_words = int(mem_cfg.get("max_batch_words", 2000))
    batches = _batch_messages(rows, max_words)
    result["batches"] = len(batches)
    logger.info(f"[MEM-115] {len(rows)} Nachrichten \u2192 {len(batches)} Batch(es)")

    threshold = float(mem_cfg.get("similarity_threshold", 0.9))
    today_tag = datetime.utcnow().strftime("%Y-%m-%d")
    source_tag = f"memory_extraction_{today_tag}"

    for batch_text in batches:
        facts = await _call_extraction_llm(batch_text, mem_cfg)
        if not facts:
            continue
        result["extracted"] += len(facts)

        for fact_entry in facts:
            fact_text = fact_entry["fact"]
            cat = fact_entry["category"]
            try:
                vec = await asyncio.to_thread(_encode, fact_text)
                is_dup = await asyncio.to_thread(_is_duplicate, vec, threshold)
                if is_dup:
                    result["skipped"] += 1
                    continue
                word_count = len(fact_text.split())
                vec_idx = await asyncio.to_thread(
                    _add_to_index, vec, fact_text,
                    {
                        "source": source_tag,
                        "word_count": word_count,
                        "category": cat,
                        "fact_category": cat,
                        "extracted_at": datetime.utcnow().isoformat(timespec="seconds"),
                    },
                    settings,
                )
                result["indexed"] += 1
                # Patch 132: Zus\u00e4tzlich in strukturierten Store schreiben.
                # vec_idx ist die neue FAISS-Gesamtgr\u00f6\u00dfe; der Eintrag selbst
                # liegt bei Index (vec_idx - 1).
                try:
                    await _store_memory_structured(
                        fact_text=fact_text,
                        category=cat,
                        source_tag=source_tag,
                        confidence=float(fact_entry.get("confidence", 0.8)),
                        embedding_index=(vec_idx - 1) if isinstance(vec_idx, int) else None,
                    )
                except Exception as e:
                    logger.warning(f"[MEM-132] Structured-Store-Write fehlgeschlagen: {e}")
            except Exception as e:
                logger.warning(f"[MEM-115] Fakt-Indexierung fehlgeschlagen: {e}")
                result["errors"].append(f"index: {type(e).__name__}")

    logger.warning(
        f"[MEM-115] Fertig: {result['extracted']} Fakten extrahiert, "
        f"{result['indexed']} neu indiziert, {result['skipped']} Duplikate"
    )
    return result
