"""
Datenbank-Setup mit SQLAlchemy 2.0 Async.
"""
import logging
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, joinedload
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, UniqueConstraint, select, func, text
from datetime import datetime, timedelta

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


class Memory(Base):
    """Patch 132 — Strukturierter Memory-Store (neben FAISS-Vector-Store).

    Erlaubt gezielte Abfragen wie "alle PREFERENCE-Fakten zu Chris" ohne
    Semantic-Search. Wird vom Overnight-Extractor (Patch 115/132) befüllt.
    """
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True)
    category = Column(String(32), index=True)          # personal/technical/preference/relationship/event
    subject = Column(String(100), nullable=True, index=True)
    fact = Column(Text)
    confidence = Column(Float, default=1.0)
    source_conversation_id = Column(Integer, nullable=True)
    source_tag = Column(String(100), nullable=True)    # z.B. "memory_extraction_2026-04-24"
    embedding_index = Column(Integer, nullable=True)   # Referenz zur FAISS-Position (optional)
    extracted_at = Column(DateTime, default=datetime.utcnow, index=True)
    is_active = Column(Integer, default=1)             # SQLite Boolean as Integer; 1=active, 0=soft-deleted


class Project(Base):
    """Patch 194 — Phase 5a #1: Projekte als Entitaet.

    Fundament fuer Code-Sandbox, projekt-spezifischen RAG-Index, isolierte
    Persona-Layer und HitL-Gates pro Projekt. Lebt in ``bunker_memory.db``
    (Decision 1, 2026-05-01) statt eigener SQLite-Datei.

    ``persona_overlay`` ist ein JSON-Text, der im Persona-Merge-Layer
    (Decision 3, 2026-05-01) ueber System-Default und User-Persona gelegt
    wird. Format: ``{"system_addendum": "...", "tone_hints": [...]}``.

    Soft-delete via ``is_archived``; harte Loeschungen kaskadieren in
    ``project_files`` (FK ON DELETE CASCADE).
    """
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    persona_overlay = Column(Text, nullable=True)
    is_archived = Column(Integer, default=0, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProjectFile(Base):
    """Patch 194 — Datei-Eintraege pro Projekt.

    Bytes liegen NICHT in der DB, sondern unter
    ``data/projects/<slug>/<sha256-prefix>/<sha256>``. Die DB haelt nur
    Metadaten + Pfad. ``UNIQUE(project_id, relative_path)`` verhindert
    Doppel-Uploads derselben Datei im selben Projekt; ``sha256`` erlaubt
    Inhalts-Dedup ueber Projekte hinweg (separater Index).

    Cascade-Delete an ``projects.id`` haengt — Foreign-Keys-Pragma muss in
    SQLite aktiv sein (vgl. ``init_db``).
    """
    __tablename__ = "project_files"
    __table_args__ = (
        UniqueConstraint("project_id", "relative_path", name="uq_project_files_project_path"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, nullable=False, index=True)
    relative_path = Column(String(500), nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    size_bytes = Column(Integer, nullable=False)
    mime_type = Column(String(100), nullable=True)
    storage_path = Column(String(500), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)


class HitlTask(Base):
    """Patch 167 — Persistente HitL-Tasks (Phase C, Block 1).

    Loest die RAM-basierte Mechanik aus Patch 123 ab: Tasks ueberleben jetzt
    Server-Restarts. Der Sweep-Task (Patch 167, Block 3) markiert alte
    Pending-Tasks als ``expired``; der Callback-Handler (Block 2) prueft
    Ownership anhand der Task-ID.

    ``intent`` haelt entweder einen ``HuginnIntent`` (CODE/FILE/ADMIN) oder
    eine System-Kategorie (``group_join``); der Router setzt das Feld passend.
    """
    __tablename__ = "hitl_tasks"

    id = Column(String(36), primary_key=True)              # UUID4 hex (32 chars)
    requester_id = Column(Integer, nullable=False, index=True)  # Telegram user_id
    chat_id = Column(Integer, nullable=False, index=True)       # Telegram chat_id
    intent = Column(String(32), nullable=False, index=True)
    payload_json = Column(Text, nullable=True)
    status = Column(String(16), default="pending", index=True)  # pending|approved|rejected|expired
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(Integer, nullable=True)
    admin_comment = Column(Text, nullable=True)
    # Optionale Anzeige-Felder (Admin-DM, Gruppen-Echo). Werden nicht fuer
    # die Logik verwendet, nur fuer ``build_admin_message``.
    requester_username = Column(String(100), nullable=True)
    details = Column(Text, nullable=True)


class WorkspaceSnapshot(Base):
    """Patch 207 (Phase 5a #9 + #10) — Workspace-Snapshots fuer Diff/
    Rollback nach Sandbox-Code-Execution.

    Pro Roundtrip mit ``writable=True``-Mount entstehen zwei Zeilen:
    ein ``before_run``-Snapshot vor dem Sandbox-Run, ein ``after_run``-
    Snapshot danach. Der ``snapshot_id`` ist UUID4-hex und korrespondiert
    mit dem Tar-Archiv unter ``data/projects/<slug>/_snapshots/<id>.tar``.

    ``parent_snapshot_id`` zeigt vom ``after``-Snapshot zurueck auf den
    ``before``-Snapshot derselben Ausfuehrung — Frontend kann damit den
    Roll-Back-Pfad eindeutig identifizieren ("rollback to parent").
    ``pending_id`` korreliert mit ``hitl_chat`` (P206) und
    ``code_executions`` (P206), sodass die ganze Spur (HitL → Snapshot
    → Code-Run → Snapshot → Rollback?) sich rekonstruieren laesst.

    Bewusst KEINE Foreign-Keys auf ``code_executions`` oder ``projects``
    — die Models bleiben dependency-frei (Repo-Layer garantiert
    Cascade), und die ``snapshot_id`` ist kollisionsfrei genug fuer den
    Cross-Table-Lookup.
    """
    __tablename__ = "workspace_snapshots"

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(String(36), unique=True, nullable=False, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    project_slug = Column(String(120), nullable=True)
    label = Column(String(64), nullable=False)  # before_run|after_run|manual|...
    archive_path = Column(String(500), nullable=False)
    file_count = Column(Integer, default=0)
    total_bytes = Column(Integer, default=0)
    pending_id = Column(String(36), nullable=True, index=True)  # P206-Korrelation
    parent_snapshot_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class CodeExecution(Base):
    """Patch 206 (Phase 5a #6) — Audit-Trail fuer Sandbox-Code-Execution
    aus dem Chat-Endpunkt.

    Erfasst pro Roundtrip: Projekt, Sprache, exit_code, Laufzeit, plus den
    HitL-Status des Patch-206-Gates (``approved`` / ``rejected`` /
    ``timeout`` / ``bypassed`` wenn HitL deaktiviert war / ``error`` bei
    Pipeline-Fehlern). ``code_text``/``stdout_text``/``stderr_text`` sind
    optional — bei langen Outputs trunciert vor dem Insert. Die
    User-sichtbare Synthese aus P203d-2 wird hier NICHT gespiegelt
    (steht in ``interactions`` als assistant-Row).

    P203d-1-Schuld geschlossen: ``code_execution`` ist jetzt in der DB
    (HANDOVER-Backlog "P203d-1: code_execution ist nicht in der DB").
    """
    __tablename__ = "code_executions"

    id = Column(Integer, primary_key=True)
    pending_id = Column(String(36), nullable=True, index=True)  # UUID4 hex aus hitl_chat
    session_id = Column(String(64), nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    project_slug = Column(String(120), nullable=True)
    language = Column(String(32), nullable=True)
    exit_code = Column(Integer, nullable=True)
    execution_time_ms = Column(Integer, nullable=True)
    truncated = Column(Integer, default=0)  # 0/1 boolean (SQLite-friendly)
    skipped = Column(Integer, default=0)
    hitl_status = Column(String(16), nullable=True, index=True)  # approved|rejected|timeout|bypassed|error
    code_text = Column(Text, nullable=True)
    stdout_text = Column(Text, nullable=True)
    stderr_text = Column(Text, nullable=True)
    error_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)


class Clarification(Base):
    """Patch 208 (Phase 5a #8) — Audit-Trail fuer Spec-Contract-/Ambiguity-
    Probes vor dem Haupt-LLM-Call.

    Erfasst pro Probe: Pending-ID (UUID4 hex), Session, Projekt, die
    Original-User-Message + die formulierte Rueckfrage + ggf. die User-
    Antwort, plus Heuristik-Score, Source ("text"/"voice") und das
    finale Ergebnis (``answered``/``bypassed``/``cancelled``/``timeout``/
    ``error``).

    Persistente Spur, damit auswertbar wird: wie oft trifft die
    Heuristik, wie oft hilft die Rueckfrage tatsaechlich, wie oft
    bypassed der User. Das ist die Grundlage fuer Threshold-Tuning.
    """
    __tablename__ = "clarifications"

    id = Column(Integer, primary_key=True)
    pending_id = Column(String(36), nullable=True, index=True)  # UUID4 hex aus spec_check
    session_id = Column(String(64), nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    project_slug = Column(String(120), nullable=True)
    original_message = Column(Text, nullable=True)
    question = Column(Text, nullable=True)
    answer_text = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    source = Column(String(16), nullable=True)  # text|voice
    status = Column(String(16), nullable=True, index=True)  # answered|bypassed|cancelled|timeout|error
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)


class CodeVeto(Base):
    """Patch 209 (Phase 5a #7) — Audit-Trail fuer Veto-Entscheidungen vor
    der Sandbox-Code-Execution (Sancho Panza).

    Erfasst pro Veto-Call: Audit-ID (UUID4 hex), Session, Projekt, die
    Sprache, den Code-Vorschlag, den User-Wunsch, das Verdict
    (``pass``/``veto``/``skipped``/``error``), die Begruendung (nur bei
    ``veto`` user-relevant) und die Probe-Latenz in Millisekunden.

    Persistente Spur, damit auswertbar wird: wie oft greift Veto, wie
    oft falsch-positiv (User wollte den Code wirklich), wie oft
    falsch-negativ (Code war problematisch und wurde nicht geveto't).
    Grundlage fuer System-Prompt-Tuning des Veto-LLM.
    """
    __tablename__ = "code_vetoes"

    id = Column(Integer, primary_key=True)
    audit_id = Column(String(36), nullable=True, index=True)  # UUID4 hex (eigene ID, kein HitL-Pending)
    session_id = Column(String(64), nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    project_slug = Column(String(120), nullable=True)
    language = Column(String(32), nullable=True)
    code_text = Column(Text, nullable=True)
    user_prompt = Column(Text, nullable=True)
    verdict = Column(String(16), nullable=True, index=True)  # pass|veto|skipped|error
    reason = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class GpuQueueAudit(Base):
    """Patch 211 (Phase 5a #11) — Audit-Trail fuer GPU-Queue-Slots.

    Pro Slot eine Zeile: welcher Konsument (whisper/gemma/embedder/
    reranker), wieviel VRAM reserviert, an welcher Position in der Queue
    er saß, wie lange er gewartet hat, wie lange er den Slot gehalten
    hat, und ob ein Timeout aufgetreten ist.

    Auswertung erlaubt: wo entstehen Engpaesse, wie oft greift die Queue
    ueberhaupt (Position > 0), reichen die Budget-Annahmen, wie oft
    laeuft Whisper/Gemma in Timeouts. Grundlage fuer Budget-Tuning.
    """
    __tablename__ = "gpu_queue_audits"

    id = Column(Integer, primary_key=True)
    audit_id = Column(String(36), nullable=True, index=True)  # UUID4 hex
    consumer_name = Column(String(32), nullable=True, index=True)
    requested_mb = Column(Integer, nullable=True)
    queue_position = Column(Integer, nullable=True)  # 0 = sofort, >0 = N-ter Waiter
    wait_ms = Column(Integer, nullable=True)
    held_ms = Column(Integer, nullable=True)
    timed_out = Column(Integer, nullable=True, default=0)  # 0/1 als Integer (SQLite-portabel)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


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
        # Patch 194: FK-Pragma fuer SQLite. Cascade wird per Repo
        # (``delete_project``) garantiert, weil die Models bewusst
        # dependency-frei bleiben (keine ORM-Relations).
        await conn.execute(text("PRAGMA foreign_keys = ON"))
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
        # Patch 113a: Dedup-Guard — identische Nachricht innerhalb 30 s für dieselbe Session
        # überspringen. Verhindert Doppel-Inserts bei Timeout-Retries + Parallelpfaden
        # (legacy.py + orchestrator.py). Kein Guard für `whisper_input` mit session_id=NULL,
        # weil das Diagnose-Logging aus der Dictate-Tastatur ist (eigene Pipeline).
        if session_id and role in ("user", "assistant"):
            dup_stmt = (
                select(Interaction.id)
                .where(
                    Interaction.session_id == session_id,
                    Interaction.role == role,
                    Interaction.content == content,
                    Interaction.timestamp >= datetime.utcnow() - timedelta(seconds=30),
                )
                .limit(1)
            )
            existing = (await session.execute(dup_stmt)).scalar_one_or_none()
            if existing is not None:
                logger.warning(
                    f"[DEDUP-113] Duplikat erkannt (role={role}, session={session_id[:8]}, "
                    f"id={existing}), überspringe Insert"
                )
                return

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

async def get_all_sessions(limit: int = 50, exclude_profiles: list[str] | None = None) -> list:
    """
    Listet alle Sessions sortiert nach letzter Aktivität.

    Patch 138 (B-004): `exclude_profiles` filtert Sessions von bestimmten
    profile_keys raus (z.B. Test-Profile loki/fenrir, damit Chris's Sidebar
    nicht mit Playwright-Läufen zugemüllt wird).
    """
    exclude_profiles = exclude_profiles or []
    async with _async_session_maker() as session:
        # Unterabfrage: pro Session die minimale und maximale Zeit.
        # Patch 138: Sessions filtern, bei denen alle Interaktionen
        # zu einem ausgeschlossenen Profil gehören.
        where_clauses = [Interaction.session_id.isnot(None)]
        if exclude_profiles:
            where_clauses.append(
                ~Interaction.profile_key.in_(exclude_profiles)
            )
        subq = (
            select(
                Interaction.session_id,
                func.min(Interaction.timestamp).label("first_message_time"),
                func.max(Interaction.timestamp).label("last_message_time")
            )
            .where(*where_clauses)
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
