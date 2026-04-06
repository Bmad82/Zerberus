"""
Overnight-Sentiment-Job – Patch 57
APScheduler-Job: läuft täglich um 04:30 und berechnet BERT-Sentiment
für alle Messages der letzten 24h, die noch keinen bert_sentiment-Wert haben.
Neue Spalten in message_metrics werden per raw SQL angelegt (kein Alembic).
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_SCHEDULER_OK = False
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    _SCHEDULER_OK = True
except ImportError:
    logger.warning("[Overnight] APScheduler nicht installiert – Overnight-Job deaktiviert.")


async def _ensure_bert_columns() -> bool:
    """
    Legt bert_sentiment_label und bert_sentiment_score in message_metrics an,
    falls die Spalten noch nicht existieren. SQLite unterstützt kein
    ADD COLUMN IF NOT EXISTS – daher PRAGMA table_info als Prüfung.
    Gibt True zurück wenn OK, False bei Fehler.
    """
    try:
        from zerberus.core.database import _async_session_maker
        from sqlalchemy import text

        async with _async_session_maker() as session:
            result = await session.execute(text("PRAGMA table_info(message_metrics)"))
            cols = {row[1] for row in result.fetchall()}

        async with _async_session_maker() as session:
            if "bert_sentiment_label" not in cols:
                await session.execute(text(
                    "ALTER TABLE message_metrics ADD COLUMN bert_sentiment_label TEXT"
                ))
                logger.info("[Overnight] Spalte bert_sentiment_label angelegt.")
            if "bert_sentiment_score" not in cols:
                await session.execute(text(
                    "ALTER TABLE message_metrics ADD COLUMN bert_sentiment_score REAL"
                ))
                logger.info("[Overnight] Spalte bert_sentiment_score angelegt.")
            await session.commit()
        return True
    except Exception as e:
        logger.error(f"[Overnight] Fehler beim Anlegen der DB-Spalten: {e}")
        return False


async def run_overnight_sentiment():
    """
    Hauptjob: Holt alle Messages der letzten 24h ohne bert_sentiment_label
    und schreibt BERT-Sentiment in die DB.
    """
    from zerberus.modules.sentiment.router import analyze_sentiment
    from zerberus.core.database import _async_session_maker
    from sqlalchemy import text

    logger.info("[Overnight] ===== Start BERT-Sentiment-Auswertung =====")

    # Spalten sicherstellen
    if not await _ensure_bert_columns():
        logger.error("[Overnight] Abgebrochen: DB-Spalten konnten nicht angelegt werden.")
        return

    since = datetime.utcnow() - timedelta(hours=24)

    try:
        async with _async_session_maker() as session:
            result = await session.execute(text("""
                SELECT i.id, i.content
                FROM interactions i
                JOIN message_metrics mm ON i.id = mm.message_id
                WHERE i.timestamp >= :since
                  AND mm.bert_sentiment_label IS NULL
                ORDER BY i.timestamp ASC
            """), {"since": since.isoformat()})
            rows = result.fetchall()
    except Exception as e:
        logger.error(f"[Overnight] Fehler beim Abfragen der Messages: {e}")
        return

    logger.info(f"[Overnight] {len(rows)} Messages zur Auswertung gefunden.")

    count = 0
    for row in rows:
        msg_id, content = row[0], row[1]
        try:
            sentiment = analyze_sentiment(content or "")
            async with _async_session_maker() as session:
                await session.execute(text("""
                    UPDATE message_metrics
                    SET bert_sentiment_label = :label,
                        bert_sentiment_score  = :score
                    WHERE message_id = :msg_id
                """), {
                    "label": sentiment["label"],
                    "score": sentiment["score"],
                    "msg_id": msg_id,
                })
                await session.commit()
            count += 1
        except Exception as e:
            logger.warning(f"[Overnight] Fehler bei Message id={msg_id}: {e}")

    logger.info(f"[Overnight] ===== Fertig: {count}/{len(rows)} Messages ausgewertet =====")


def create_scheduler():
    """
    Erstellt den AsyncIOScheduler mit dem Overnight-Job (täglich 04:30).
    Gibt None zurück wenn APScheduler nicht installiert ist.
    """
    if not _SCHEDULER_OK:
        return None
    scheduler = AsyncIOScheduler(timezone="Europe/Berlin")
    scheduler.add_job(
        run_overnight_sentiment,
        trigger="cron",
        hour=4,
        minute=30,
        id="overnight_bert_sentiment",
        replace_existing=True,
        misfire_grace_time=3600,  # bis zu 1h verspäteter Start OK
    )
    logger.info("[Overnight] Scheduler erstellt – Job läuft täglich um 04:30 Europe/Berlin.")
    return scheduler
