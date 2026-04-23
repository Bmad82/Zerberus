"""
Overnight-Sentiment-Job – Patch 57
APScheduler-Job: läuft täglich um 04:30 und berechnet BERT-Sentiment
für alle Messages der letzten 24h, die noch keinen bert_sentiment-Wert haben.
Neue Spalten in message_metrics werden per raw SQL angelegt (kein Alembic).
"""
import logging

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

    # Patch 104 (B-24): Bisher wurde `datetime.utcnow().isoformat()` als
    # Vergleichswert übergeben — das produziert `2026-04-21T08:42:29.116695`
    # mit `T`-Separator. SQLAlchemy schreibt Timestamps aber als
    # `2026-04-21 18:50:50.611657` (Space-Separator) in SQLite. Beim
    # lexikografischen Vergleich gilt `T` (0x54) > ` ` (0x20), wodurch alle
    # Zeilen aus dem Vortag (UTC) lautlos rausfallen — exakt das Fenster,
    # das der 04:30-Cron auswerten soll. Fix: SQLite-natives
    # `datetime('now', '-24 hours')` (Space-formatiert, korrekter Vergleich)
    # statt Python-side ISO-String.
    try:
        async with _async_session_maker() as session:
            result = await session.execute(text("""
                SELECT i.id, i.content
                FROM interactions i
                JOIN message_metrics mm ON i.id = mm.message_id
                WHERE i.timestamp >= datetime('now', '-24 hours')
                  AND i.role = 'user'
                  AND mm.bert_sentiment_label IS NULL
                ORDER BY i.timestamp ASC
            """))
            rows = result.fetchall()
    except Exception as e:
        logger.error(f"[Overnight] Fehler beim Abfragen der Messages: {e}")
        return

    logger.warning(f"[B24-104] Overnight-Query lieferte {len(rows)} Messages (Filter: role=user, bert_sentiment_label NULL, last 24h)")
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

    # Patch 115: Im Anschluss Memory-Extraction — Fakten aus 24h-Dialog
    # ins FAISS-Index schreiben. Fail-Safe: Exceptions loggen, Overnight
    # nicht abbrechen lassen.
    try:
        from zerberus.core.config import get_settings
        from zerberus.modules.memory.extractor import extract_memories

        settings = get_settings()
        mem_cfg = settings.modules.get("memory", {}) or {}
        if mem_cfg.get("extraction_enabled", True):
            mem_result = await extract_memories(mem_cfg)
            logger.warning(
                f"[MEM-115] Overnight-Extraction: {mem_result['extracted']} Fakten, "
                f"{mem_result['indexed']} neu, {mem_result['skipped']} Duplikate, "
                f"{mem_result['batches']} Batch(es)"
            )
        else:
            logger.info("[MEM-115] Memory-Extraction deaktiviert (config)")
    except Exception as e:
        logger.error(f"[MEM-115] Memory-Extraction fehlgeschlagen: {e}")


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
