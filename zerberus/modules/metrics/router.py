"""
Metrics Router – Patch 59
Endpunkte:
  POST /metrics/analyze  – berechnet alle Metriken für einen Text
  GET  /metrics/history  – liest message_metrics aus der DB
  GET  /metrics/health   – Health-Check
Neue DB-Spalten werden per PRAGMA-Check-Pattern (wie Overnight-Scheduler) angelegt.
Metrik-Berechnung läuft via asyncio.to_thread (blocking).
"""
import logging
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Metrics"])


class AnalyzeRequest(BaseModel):
    text: str
    session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Neue Spalten sicherstellen (PRAGMA-Check wie Overnight-Scheduler)
# ---------------------------------------------------------------------------

_METRIC_COLUMNS = [
    ("ttr", "REAL"),
    ("mattr", "REAL"),
    ("hapax_ratio", "REAL"),
    ("avg_sentence_length", "REAL"),
    ("shannon_entropy", "REAL"),
    ("hedging_freq", "REAL"),
    ("self_ref_freq", "REAL"),
    ("causal_ratio", "REAL"),
]

_columns_ensured = False


async def _ensure_metric_columns():
    global _columns_ensured
    if _columns_ensured:
        return
    try:
        from zerberus.core.database import _async_session_maker
        from sqlalchemy import text

        async with _async_session_maker() as session:
            result = await session.execute(text("PRAGMA table_info(message_metrics)"))
            existing_cols = {row[1] for row in result.fetchall()}

        async with _async_session_maker() as session:
            for col_name, col_type in _METRIC_COLUMNS:
                if col_name not in existing_cols:
                    await session.execute(text(
                        f"ALTER TABLE message_metrics ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info(f"[Metrics] Spalte {col_name} angelegt.")
            await session.commit()
        _columns_ensured = True
    except Exception as e:
        logger.error(f"[Metrics] Fehler beim Anlegen der DB-Spalten: {e}")


# ---------------------------------------------------------------------------
# Metriken berechnen (blocking → asyncio.to_thread)
# ---------------------------------------------------------------------------

def _compute_all(text: str) -> dict:
    from zerberus.modules.metrics.engine import (
        compute_ttr,
        compute_mattr,
        compute_hapax_ratio,
        compute_avg_sentence_length,
        compute_shannon_entropy,
        compute_hedging_frequency,
        compute_self_reference_frequency,
        compute_causal_ratio,
    )
    return {
        "ttr": compute_ttr(text),
        "mattr": compute_mattr(text),
        "hapax_ratio": compute_hapax_ratio(text),
        "avg_sentence_length": compute_avg_sentence_length(text),
        "shannon_entropy": compute_shannon_entropy(text),
        "hedging_freq": compute_hedging_frequency(text),
        "self_ref_freq": compute_self_reference_frequency(text),
        "causal_ratio": compute_causal_ratio(text),
    }


# ---------------------------------------------------------------------------
# Endpunkte
# ---------------------------------------------------------------------------

@router.post("/analyze")
async def analyze_text(req: AnalyzeRequest):
    """Berechnet alle Metriken für einen Text und speichert sie in message_metrics."""
    await _ensure_metric_columns()

    try:
        metrics = await asyncio.to_thread(_compute_all, req.text)
    except Exception as e:
        logger.error(f"[Metrics] Berechnungsfehler: {e}")
        raise HTTPException(status_code=500, detail=f"Metrik-Berechnung fehlgeschlagen: {e}")

    # In DB speichern wenn session_id angegeben
    if req.session_id:
        try:
            from zerberus.core.database import _async_session_maker
            from sqlalchemy import text

            async with _async_session_maker() as session:
                # Letzten Interaction-Eintrag dieser Session finden
                result = await session.execute(text(
                    "SELECT id FROM interactions WHERE session_id = :sid ORDER BY id DESC LIMIT 1"
                ), {"sid": req.session_id})
                row = result.fetchone()

            if row:
                msg_id = row[0]
                async with _async_session_maker() as session:
                    # Prüfen ob message_metrics-Eintrag existiert
                    mm_result = await session.execute(text(
                        "SELECT id FROM message_metrics WHERE message_id = :mid"
                    ), {"mid": msg_id})
                    mm_row = mm_result.fetchone()

                    if mm_row:
                        await session.execute(text("""
                            UPDATE message_metrics SET
                                ttr = :ttr, mattr = :mattr, hapax_ratio = :hapax_ratio,
                                avg_sentence_length = :avg_sentence_length,
                                shannon_entropy = :shannon_entropy,
                                hedging_freq = :hedging_freq,
                                self_ref_freq = :self_ref_freq,
                                causal_ratio = :causal_ratio
                            WHERE message_id = :mid
                        """), {**metrics, "mid": msg_id})
                    else:
                        await session.execute(text("""
                            INSERT INTO message_metrics
                                (message_id, ttr, mattr, hapax_ratio, avg_sentence_length,
                                 shannon_entropy, hedging_freq, self_ref_freq, causal_ratio)
                            VALUES
                                (:mid, :ttr, :mattr, :hapax_ratio, :avg_sentence_length,
                                 :shannon_entropy, :hedging_freq, :self_ref_freq, :causal_ratio)
                        """), {**metrics, "mid": msg_id})
                    await session.commit()
        except Exception as e:
            logger.warning(f"[Metrics] DB-Schreibfehler (non-fatal): {e}")

    return {"session_id": req.session_id, "metrics": metrics}


@router.get("/history")
async def metrics_history(session_id: Optional[str] = None, limit: int = 20):
    """Liest message_metrics aus der DB, optional gefiltert nach session_id."""
    await _ensure_metric_columns()
    try:
        from zerberus.core.database import _async_session_maker
        from sqlalchemy import text

        async with _async_session_maker() as session:
            if session_id:
                result = await session.execute(text("""
                    SELECT mm.*, i.session_id, i.timestamp, i.content
                    FROM message_metrics mm
                    JOIN interactions i ON i.id = mm.message_id
                    WHERE i.session_id = :sid
                    ORDER BY mm.id DESC
                    LIMIT :limit
                """), {"sid": session_id, "limit": limit})
            else:
                result = await session.execute(text("""
                    SELECT mm.*, i.session_id, i.timestamp, i.content
                    FROM message_metrics mm
                    JOIN interactions i ON i.id = mm.message_id
                    ORDER BY mm.id DESC
                    LIMIT :limit
                """), {"limit": limit})
            rows = result.fetchall()
            keys = list(result.keys())
        return {"history": [dict(zip(keys, row)) for row in rows]}
    except Exception as e:
        logger.error(f"[Metrics] History-Fehler: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "metrics"}
