"""
Datenbank-Setup mit SQLAlchemy 2.0 Async.
"""
import logging
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, joinedload
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, select, func, text
from datetime import datetime

from zerberus.core.config import get_settings

logger = logging.getLogger(__name__)

Base = declarative_base()

# Sentiment-Analyse (Patch 57: graceful – kein Crash wenn Modul/Modell nicht verfügbar)
def _compute_sentiment(text: str) -> float:
    """Gibt sentiment compound zurück (0.0 als Fallback).
    Patch 84: Score-gewichtet statt nur Extreme (-1/0/1).
    Patch 85: BERT-Konfidenz gedämpft — BERT gibt fast immer >0.9 zurück,
    was zu binären 0/1-Extremen im Chart führte. Jetzt: Konfidenz-basierte
    Skalierung mit Dämpfung: sentiment = direction * (0.3 + 0.7 * (score - 0.5) / 0.5)
    Ergibt Werte im Bereich [-1.0, +1.0] mit besserer Abstufung.
    """
    try:
        from zerberus.modules.sentiment.router import analyze_sentiment
        result = analyze_sentiment(text)
        label = result.get("label", "neutral")
        score = float(result.get("score", 0.5))
        if label == "neutral":
            return 0.0
        # Dämpfung: score 0.5→0.3, score 0.75→0.65, score 1.0→1.0
        dampened = 0.3 + 0.7 * max(0.0, (score - 0.5)) / 0.5
        dampened = min(dampened, 1.0)
        if label == "positive":
            return round(dampened, 4)
        elif label == "negative":
            return round(-dampened, 4)
        return 0.0
    except Exception:
        return 0.0


class Interaction(Base):
    __tablename__ = "interactions"

    id = Column(Integer, primary_key=True)
    session_id = Column(String(36), index=True, nullable=True)
    profile_name = Column(String(100), nullable=True, default="")  # Patch 60: User-Tag (legacy)
    profile_key = Column(String(100), nullable=True, index=True)   # Patch 92: zuverlässiger User-Schlüssel
    timestamp = Column(DateTime, default=datetime.utcnow)
    role = Column(String(50))
    content = Column(Text)
    sentiment = Column(Float, nullable=True)
    word_count = Column(Integer, nullable=True)
    integrity = Column(Float, default=1.0)


class MessageMetrics(Base):
    __tablename__ = "message_metrics"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, index=True, nullable=False)
    word_count = Column(Integer, nullable=True)
    sentence_count = Column(Integer, nullable=True)
    character_count = Column(Integer, nullable=True)
    avg_word_length = Column(Float, nullable=True)
    unique_word_count = Column(Integer, nullable=True)
    ttr = Column(Float, nullable=True)
    hapax_count = Column(Integer, nullable=True)
    yule_k = Column(Float, nullable=True)
    shannon_entropy = Column(Float, nullable=True)
    vader_compound = Column(Float, nullable=True)


class Cost(Base):
    __tablename__ = "costs"

    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, index=True, nullable=True)
    session_id = Column(String(36), index=True, nullable=True)
    model = Column(String(100), nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    cost = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)


_engine = None
_async_session_maker = None

async def init_db():
    global _engine, _async_session_maker
    settings = get_settings()
    db_url = settings.database.url
    logger.info(f"📊 Initialisiere Datenbank: {db_url}")

    engine_kwargs = {"echo": settings.database.echo}
    if "sqlite" not in db_url:
        engine_kwargs["pool_size"] = settings.database.pool_size
        engine_kwargs["max_overflow"] = settings.database.max_overflow

    _engine = create_async_engine(db_url, **engine_kwargs)
    _async_session_maker = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Patch 60: profile_name Spalte graceful hinzufügen (kein Schema-Reset, kein Datenverlust)
        col_result = await conn.execute(text("PRAGMA table_info(interactions)"))
        existing_cols = {row[1] for row in col_result.fetchall()}
        if "profile_name" not in existing_cols:
            await conn.execute(text("ALTER TABLE interactions ADD COLUMN profile_name TEXT DEFAULT ''"))
            logger.info("✅ interactions.profile_name Spalte hinzugefügt (Patch 60)")
        # Patch 92: profile_key Spalte hinzufügen (zuverlässiger User-Schlüssel, JWT-bound)
        if "profile_key" not in existing_cols:
            await conn.execute(text("ALTER TABLE interactions ADD COLUMN profile_key TEXT DEFAULT NULL"))
            # Bestehende Daten migrieren: profile_name → profile_key wenn vorhanden
            await conn.execute(text(
                "UPDATE interactions SET profile_key = profile_name "
                "WHERE profile_name IS NOT NULL AND profile_name != ''"
            ))
            logger.warning("✅ [PATCH-92] interactions.profile_key Spalte hinzugefügt + profile_name migriert")
        # Patch 92: Index für schnelle Per-User-Queries
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_interactions_profile_key "
            "ON interactions(profile_key, timestamp DESC)"
        ))
    logger.info("✅ Datenbank bereit")

async def get_db() -> AsyncSession:
    async with _async_session_maker() as session:
        yield session

async def store_interaction(
    role: str,
    content: str,
    session_id: str = None,
    integrity: float = 1.0,
    profile_name: str = "",    # Patch 60: User-Tag (legacy, bleibt für Rückwärtskompatibilität)
    profile_key: str = None,   # Patch 92: zuverlässiger User-Schlüssel aus JWT
):
    sentiment = _compute_sentiment(content)
    word_count = len(content.split())

    # Patch 92: profile_key fällt auf profile_name zurück wenn nicht gesetzt
    effective_profile_key = profile_key if profile_key else (profile_name or None)

    async with _async_session_maker() as session:
        interaction = Interaction(
            session_id=session_id,
            profile_name=profile_name or "",
            profile_key=effective_profile_key,
            role=role,
            content=content,
            sentiment=sentiment,
            word_count=word_count,
            integrity=integrity
        )
        session.add(interaction)
        await session.commit()
        # Metriken berechnen und speichern
        metrics = compute_metrics(content)
        await save_metrics(interaction.id, metrics)
    logger.debug(f"💾 Gespeichert: {role}, {word_count} Wörter, Sentiment {sentiment:.2f}, profile={effective_profile_key or '–'}")

async def get_all_sessions(limit: int = 50) -> list:
    async with _async_session_maker() as session:
        # Unterabfrage: pro Session die minimale und maximale Zeit
        subq = (
            select(
                Interaction.session_id,
                func.min(Interaction.timestamp).label("first_message_time"),
                func.max(Interaction.timestamp).label("last_message_time")
            )
            .where(Interaction.session_id.isnot(None))
            .group_by(Interaction.session_id)
            .subquery()
        )
        # Korrelierte Unterabfrage für den Inhalt der ersten Nachricht (User)
        first_msg_subq = (
            select(Interaction.content)
            .where(
                Interaction.session_id == subq.c.session_id,
                Interaction.role == "user"
            )
            .order_by(Interaction.timestamp)
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            select(
                subq.c.session_id,
                subq.c.first_message_time,
                subq.c.last_message_time,
                first_msg_subq.label("first_message")
            )
            .order_by(subq.c.last_message_time.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()
        sessions = []
        for row in rows:
            sessions.append({
                "session_id": row.session_id,
                "first_message": row.first_message or "Neuer Chat",
                "created_at": row.first_message_time.isoformat() if row.first_message_time else None,
                "last_message_at": row.last_message_time.isoformat() if row.last_message_time else None,
            })
        return sessions

async def get_session_messages(session_id: str) -> list:
    async with _async_session_maker() as session:
        stmt = (
            select(Interaction)
            .where(Interaction.session_id == session_id)
            .order_by(Interaction.timestamp)
        )
        result = await session.execute(stmt)
        interactions = result.scalars().all()
        return [
            {
                "role": i.role,
                "content": i.content,
                "timestamp": i.timestamp.isoformat(),
                "sentiment": i.sentiment,
            }
            for i in interactions
        ]

async def delete_session(session_id: str):
    async with _async_session_maker() as session:
        stmt = select(Interaction).where(Interaction.session_id == session_id)
        result = await session.execute(stmt)
        for interaction in result.scalars():
            await session.delete(interaction)
        await session.commit()

def compute_metrics(text: str) -> dict:
    import re
    from collections import Counter
    import math
    if not text:
        return {k:0 for k in ["word_count","sentence_count","character_count","avg_word_length",
                "unique_word_count","ttr","hapax_count","yule_k","shannon_entropy","vader_compound"]}
    words = re.findall(r'\w+', text.lower())
    sentences = re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    word_count = len(words)
    sentence_count = len(sentences)
    character_count = len(text)
    avg_word_length = sum(len(w) for w in words) / word_count if word_count else 0
    freq = Counter(words)
    unique_word_count = len(freq)
    ttr = unique_word_count / word_count if word_count else 0
    hapax_count = sum(1 for v in freq.values() if v == 1)
    if word_count > 0:
        v_i = {}
        for f in freq.values():
            v_i[f] = v_i.get(f, 0) + 1
        sum_i2_v = sum((i ** 2) * cnt for i, cnt in v_i.items())
        yule_k = 10000 * (sum_i2_v - word_count) / (word_count ** 2) if word_count > 0 else 0
    else:
        yule_k = 0
    entropy = 0.0
    if word_count > 0:
        for f in freq.values():
            p = f / word_count
            entropy -= p * math.log2(p)
    vader_compound = _compute_sentiment(text)
    return {
        "word_count": word_count,
        "sentence_count": sentence_count,
        "character_count": character_count,
        "avg_word_length": round(avg_word_length, 2),
        "unique_word_count": unique_word_count,
        "ttr": round(ttr, 3),
        "hapax_count": hapax_count,
        "yule_k": round(yule_k, 2),
        "shannon_entropy": round(entropy, 3),
        "vader_compound": round(vader_compound, 3)
    }

async def save_metrics(message_id: int, metrics: dict):
    async with _async_session_maker() as session:
        mm = MessageMetrics(message_id=message_id, **metrics)
        session.add(mm)
        await session.commit()

async def save_cost(session_id: str, model: str, prompt_tokens: int, completion_tokens: int, cost: float):
    async with _async_session_maker() as session:
        c = Cost(
            session_id=session_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens+completion_tokens,
            cost=cost
        )
        session.add(c)
        await session.commit()

async def get_latest_metrics(limit: int = 10, session_id: str = None) -> list:
    async with _async_session_maker() as session:
        query = (
            select(Interaction.id, Interaction.role, Interaction.content, Interaction.timestamp,
                   MessageMetrics.word_count, MessageMetrics.sentence_count, MessageMetrics.ttr,
                   MessageMetrics.vader_compound)
            .outerjoin(MessageMetrics, Interaction.id == MessageMetrics.message_id)
        )
        if session_id:
            query = query.where(Interaction.session_id == session_id)
        query = query.order_by(Interaction.timestamp.desc()).limit(limit)
        result = await session.execute(query)
        rows = result.all()
        return [dict(row._mapping) for row in rows]

async def get_metrics_summary(session_id: str = None) -> dict:
    """Patch 84: Nur User-Eingaben in Metrik-Zusammenfassung (LLM-Outputs verfälschen Sentiment/TTR)."""
    async with _async_session_maker() as session:
        from sqlalchemy import func
        query = (
            select(
                func.avg(MessageMetrics.word_count).label("avg_word_count"),
                func.avg(MessageMetrics.sentence_count).label("avg_sentence_count"),
                func.avg(MessageMetrics.ttr).label("avg_ttr"),
                func.avg(MessageMetrics.vader_compound).label("avg_sentiment"),
                func.count(Interaction.id).label("total_messages")
            )
            .outerjoin(MessageMetrics, Interaction.id == MessageMetrics.message_id)
            .where(Interaction.role == "user")
        )
        if session_id:
            query = query.where(Interaction.session_id == session_id)
        result = await session.execute(query)
        row = result.one()
        return {k: v for k, v in row._mapping.items()}


async def get_message_costs(interaction_ids: list) -> dict:
    """Liefert für eine Liste von Interaction-IDs die zugehörigen Kosten (pro ID)."""
    if not interaction_ids:
        return {}
    async with _async_session_maker() as session:
        stmt = select(Cost).where(Cost.message_id.in_(interaction_ids))
        result = await session.execute(stmt)
        costs = result.scalars().all()
        return {c.message_id: c.cost for c in costs}

async def get_last_cost() -> float:
    """Gibt die Kosten der letzten gespeicherten LLM-Anfrage zurück (0.0 wenn keine vorhanden)."""
    async with _async_session_maker() as session:
        stmt = select(Cost.cost).order_by(Cost.id.desc()).limit(1)
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        return float(row) if row is not None else 0.0
