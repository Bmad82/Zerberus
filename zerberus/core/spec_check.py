"""Patch 208 (Phase 5a #8) — Spec-Contract / Ambiguitaets-Check.

Erst verstehen, dann coden. Vor dem ersten Haupt-LLM-Call schaetzt eine
Pure-Function-Heuristik die Ambiguitaet der User-Eingabe; bei
Score >= Threshold faehrt ein schmaler "Spec-Probe"-LLM-Call (ein Call,
eine Frage) und das Frontend rendert eine Klarstellungs-Karte. Der User
tippt eine Antwort, klickt "Trotzdem versuchen" oder bricht ab. Erst
danach laeuft der eigentliche Code-/Antwort-Pfad mit ggf. angereichertem
Prompt weiter.

Architektur folgt der HitL-Vorlage (P206), aber mit drei Decision-Werten
statt zwei:

- ``answered`` mit ``answer_text`` → Original-Prompt wird mit
  ``[KLARSTELLUNG]``-Block angereichert (substring-disjunkt zu allen
  bestehenden Markern).
- ``bypassed`` → Original durch (User akzeptiert das Risiko).
- ``cancelled`` → Chat endet mit Hinweis-Antwort, kein LLM-Call.

Timeout = bypassed-equivalent (defensiver fuer User-Experience: eher
durchlassen als frustrieren). Reject-Pfad ist explizit ``cancelled``,
nicht ``rejected`` — das macht im Audit-Log klar, ob der User die Frage
ignoriert (timeout) oder bewusst verworfen hat.

Audit-Trail in ``clarifications``-Tabelle (Best-Effort, Schreibfehler
blockieren den Hauptpfad nicht).
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


logger = logging.getLogger("zerberus.spec_check")


# ── Pure-Function-Schicht ────────────────────────────────────────────────

# Sprach-Hinweise (de+en gemischt). Bewusst breit, damit auch
# umgangssprachliche Varianten ("auf Python") matchen.
_LANGUAGE_HINTS = (
    "python", "javascript", "java script", " js ", " js,", " js.", " js?",
    "typescript", " ts ", " ts,", " ts.",
    "java ", "kotlin", "swift", "objective-c", "objc",
    "rust", "golang", " go ", " go,", " go.",
    "bash", "shell", "zsh", "powershell",
    "c++", "c#", "csharp", " c ", " c,",
    "ruby", "php", "perl", "scala", "lua", "dart",
    "html", "css", "sql",
    "react", "vue", "angular", "svelte",
    "fastapi", "django", "flask", "express", "nestjs",
)

# Code-Verben (de+en). Wenn eines auftaucht ohne Sprache → +0.20.
_CODE_VERBS = (
    "schreib", "schreibe", "code", "implementier", "programmier",
    "bau", "baue", "erstell", "erstelle", "generier", "generiere",
    "write", "implement", "build", "create", "generate",
    "refactor", "fix", "debug", "test", "teste",
)

# Generische Verben — sehr verdaechtig wenn sie OHNE Substantiv-Anker
# kommen ("mach das mal", "tu was", "bau mir").
_GENERIC_VERBS = (
    "mach", "mache", "tu", "tue", "machst",
    "do ", "make ",
)

# Substantiv-Anker — wenn einer dieser Begriffe in der Message steckt,
# senken sie den Generic-Verb-Penalty (es gibt etwas konkretes).
_NOUN_ANCHORS = (
    "skript", "script", "funktion", "function", "methode", "method",
    "klasse", "class", "modul", "module", "package", "library",
    "api", "endpoint", "endpunkt", "route", "request",
    "test", "unittest", "fixture",
    "datei", "file", "ordner", "folder", "directory",
    "datenbank", "database", "tabelle", "table", "query",
    "service", "handler", "decorator", "middleware",
    "client", "server",
)

# Pronomen ohne Antezedens (de). Englische sind seltener problematisch,
# weil "it"/"this" haeufig sind und nicht zwingend mehrdeutig.
_AMBIGUOUS_PRONOUNS = (
    "es", "das", "dies", "diese", "dieser", "diesen", "dieses",
    "der", "die", "den", "dem", "deren", "dessen",
    "ihn", "ihm", "ihr", "ihre", "ihren", "ihrem", "ihres",
    "sowas", "sowas.", "das?", "das.",
)

# IO-Spec-Hinweise — wenn vorhanden, weiss das LLM Input/Output und der
# Penalty entfaellt.
_IO_HINTS = (
    "input", "eingabe", "parameter", "argument", "arg ", "args ",
    "output", "ausgabe", "return", "rueckgabe", "rückgabe", "result",
    "json", "csv", "yaml", "xml", "string", "int ", "integer", "float",
    "list ", "liste ", "dict ", "array",
)


def _word_count(text: str) -> int:
    """Anzahl whitespace-separater Tokens, leere Tokens zaehlen nicht."""
    return len([w for w in (text or "").split() if w])


def _has_any(text: str, needles: tuple) -> bool:
    """Case-insensitive substring-Match. Mit padded space-Markern wie ' js '
    bleibt ``has_any`` sinnvoll, wenn der Caller das so reingibt."""
    if not text:
        return False
    lower = text.lower()
    return any(n in lower for n in needles)


def _count_ambiguous_pronouns(text: str) -> int:
    """Pronomen-Tokens, die ohne klares Antezedens stehen.

    Sehr simple Heuristik: Wir zaehlen Pronomen, die isoliert auftreten
    (durch Whitespace/Satzzeichen abgegrenzt). Echtes Antezedens-Tracking
    waere ein NLP-Projekt fuer sich; hier reicht "gibt's viele Pronomen
    in einem kurzen Satz".
    """
    if not text:
        return 0
    # Tokenize an Whitespace + Satzzeichen-Strip
    tokens = re.findall(r"\b\w+\b", text.lower())
    return sum(1 for t in tokens if t in _AMBIGUOUS_PRONOUNS)


def compute_ambiguity_score(
    user_message: str,
    *,
    source: str = "text",
) -> float:
    """Heuristik 0.0-1.0 fuer "wie ambig ist der Eingabe-Prompt fuer Code-Generierung".

    Nullsignal-Defaults:
        - leere Message → 1.0 (maximal ambig)
        - reines "ja"/"ok"/"hi" → hoch ambig
        - lange Message mit Sprachangabe + IO-Spec → niedrig

    ``source="voice"`` addiert +0.20 — Whisper-Transkripte haben mehr
    Druck-/Rausch-Artefakte und der User hatte keine zweite Lese-Chance.

    Ergebnis ist ein Float, intern auf [0, 1] geclampt.
    """
    text = (user_message or "").strip()
    if not text:
        return 1.0

    score = 0.0
    n_words = _word_count(text)

    # 1. Length penalty — sehr kurze Sätze geben dem LLM zu wenig Material.
    if n_words < 4:
        score += 0.40
    elif n_words < 8:
        score += 0.20
    elif n_words < 14:
        score += 0.05

    # 2. Pronomen-Dichte
    p_count = _count_ambiguous_pronouns(text)
    if p_count >= 1 and n_words > 0:
        # Bei sehr kurzen Saetzen wiegen Pronomen schwerer (1 Pronomen
        # auf 5 Woerter ist viel; auf 50 nicht).
        density = p_count / max(n_words, 1)
        score += min(density * 1.5, 0.30)

    # 3. Code-Verb ohne Sprache
    has_code_verb = _has_any(text, _CODE_VERBS)
    has_language = _has_any(" " + text + " ", _LANGUAGE_HINTS)
    if has_code_verb and not has_language:
        score += 0.20

    # 4. Generic verb ohne Substantiv-Anker
    has_generic = _has_any(text, _GENERIC_VERBS)
    has_noun = _has_any(text, _NOUN_ANCHORS)
    if has_generic and not has_noun:
        score += 0.15

    # 5. IO-Spec
    if has_code_verb and not _has_any(text, _IO_HINTS):
        # Code soll geschrieben werden, aber kein Hinweis auf Input/Output:
        # +0.10 (kleinerer Penalty als die Sprach-Frage, weil nicht jede
        # Code-Anfrage explizit Inputs braucht).
        score += 0.10

    # 6. Voice bonus
    if source == "voice":
        score += 0.20

    if score < 0.0:
        score = 0.0
    if score > 1.0:
        score = 1.0
    return score


def should_ask_clarification(
    score: float,
    *,
    threshold: float = 0.65,
) -> bool:
    """Trigger-Gate: True wenn Score >= Threshold.

    Der Caller liest das Threshold idealerweise aus
    ``settings.projects.spec_check_threshold``.
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        return False
    return s >= float(threshold)


# ── LLM-Probe ────────────────────────────────────────────────────────────

SPEC_PROBE_SYSTEM = (
    "Du bist Nalas Spec-Probe. Deine einzige Aufgabe: pruefe, ob die "
    "User-Anfrage praezise genug fuer eine Antwort ist. Stelle EINE "
    "knappe Rueckfrage, die die wichtigste Mehrdeutigkeit klaert. "
    "Antworte ausschliesslich mit der Frage selbst — kein Code, keine "
    "Vorrede, keine Begruessung, kein 'Gerne'. Maximal ein Satz, "
    "maximal 160 Zeichen."
)


def build_spec_probe_messages(user_message: str) -> List[dict]:
    """Pure-Function: baut die ``messages``-Liste fuer den Probe-LLM-Call.

    Bewusst minimaler Kontext — wir wollen NUR die Frage formulieren,
    kein Persona-Leak, kein RAG. Der Probe-Call ist ein Werkzeug, kein
    Gespraech.
    """
    safe = (user_message or "").strip()
    return [
        {"role": "system", "content": SPEC_PROBE_SYSTEM},
        {
            "role": "user",
            "content": (
                "Diese Anfrage koennte mehrdeutig sein. Was ist die EINE "
                "wichtigste Klarstellungs-Frage, die du dem User stellen "
                "wuerdest, bevor du anfaengst zu arbeiten?\n\n"
                "Anfrage:\n---\n"
                f"{safe}\n"
                "---\n\n"
                "Antworte nur mit der Frage. Maximal ein Satz."
            ),
        },
    ]


# Maximale Laenge der zurueckgegebenen Probe-Frage (Bytes nach UTF-8).
SPEC_PROBE_MAX_BYTES = 400


def _truncate_question(text: str) -> str:
    """Kappt die Probe-Frage auf SPEC_PROBE_MAX_BYTES, ohne Multi-Byte-
    Zeichen zu zerlegen."""
    s = (text or "").strip()
    if not s:
        return ""
    encoded = s.encode("utf-8")
    if len(encoded) <= SPEC_PROBE_MAX_BYTES:
        return s
    return encoded[:SPEC_PROBE_MAX_BYTES].decode("utf-8", errors="ignore").rstrip()


async def run_spec_probe(
    user_message: str,
    llm_service: Any,
    session_id: str,
) -> Optional[str]:
    """Async-Wrapper: ruft den Probe-LLM auf und liefert die formulierte Frage.

    Return-Werte:
        - String mit der Frage → Erfolg
        - None → Probe-Call ist fehlgeschlagen, leer oder ungueltig

    Fail-open auf jeder Stufe — der Caller behandelt None wie "kein
    Probe-Pfad" und faehrt mit dem Original-Prompt fort.
    """
    messages = build_spec_probe_messages(user_message)
    try:
        result = await llm_service.call(
            messages,
            session_id,
            temperature_override=0.3,
        )
    except Exception as e:
        logger.warning(f"[SPEC-208] probe_call_failed (fail-open): {e}")
        return None

    if not isinstance(result, tuple) or not result:
        logger.info("[SPEC-208] probe_returned_unexpected_type type=%r", type(result).__name__)
        return None

    raw = result[0] if len(result) > 0 else None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    return _truncate_question(text)


# ── Prompt-Anreicherung ─────────────────────────────────────────────────

# Marker substring-disjunkt zu [PROJEKT-RAG], [PROJEKT-KONTEXT],
# [PROSODIE], [CODE-EXECUTION] (P199/P197/P204/P203d-2).
CLARIFICATION_MARKER_OPEN = "[KLARSTELLUNG]"
CLARIFICATION_MARKER_CLOSE = "[/KLARSTELLUNG]"


def build_clarification_block(question: str, answer_text: str) -> str:
    """Pure-Function: produziert den ``[KLARSTELLUNG]``-Block, der an die
    Original-User-Message angehaengt wird.

    Format ist bewusst kompakt — der Haupt-LLM bekommt das als
    Zusatz-Kontext zur User-Message, nicht als separate Turn.
    """
    q = (question or "").strip()
    a = (answer_text or "").strip()
    if not q and not a:
        return ""
    parts = [CLARIFICATION_MARKER_OPEN]
    if q:
        parts.append(f"Rueckfrage: {q}")
    if a:
        parts.append(f"Antwort: {a}")
    parts.append(CLARIFICATION_MARKER_CLOSE)
    return "\n".join(parts)


def enrich_user_message(
    original: str,
    question: str,
    answer_text: str,
) -> str:
    """Haengt den Klarstellungs-Block an die Original-User-Message."""
    base = (original or "").rstrip()
    block = build_clarification_block(question, answer_text)
    if not block:
        return original or ""
    return f"{base}\n\n{block}"


# ── Pending-Registry ─────────────────────────────────────────────────────

@dataclass
class ChatSpecPending:
    """In-Memory-Repraesentation einer wartenden Klarstellungs-Frage.

    ``status`` Lebenszyklus:
        pending → answered | bypassed | cancelled | timeout

    ``answer_text`` ist nur bei status=``answered`` gesetzt.
    """
    id: str
    session_id: str
    project_id: Optional[int]
    project_slug: Optional[str]
    original_message: str
    question: str
    score: float
    source: str  # "text" | "voice"
    status: str = "pending"
    answer_text: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "project_slug": self.project_slug,
            "original_message": self.original_message,
            "question": self.question,
            "score": round(float(self.score), 3),
            "source": self.source,
            "created_at": self.created_at.isoformat() + "Z",
        }


# Maximal-Laenge der User-Antwort (Bytes), Defense gegen Megabyte-Bombs.
SPEC_ANSWER_MAX_BYTES = 2_000


def _truncate_answer(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    encoded = s.encode("utf-8")
    if len(encoded) <= SPEC_ANSWER_MAX_BYTES:
        return s
    return encoded[:SPEC_ANSWER_MAX_BYTES].decode("utf-8", errors="ignore").rstrip()


class ChatSpecGate:
    """Singleton-Registry fuer wartende Spec-Probes.

    Analog ``ChatHitlGate`` (P206) — In-Memory only, transient,
    Long-Poll-Resolver via ``asyncio.Event``.
    """

    def __init__(self) -> None:
        self._pendings: Dict[str, ChatSpecPending] = {}
        self._events: Dict[str, asyncio.Event] = {}

    async def create_pending(
        self,
        *,
        session_id: str,
        project_id: Optional[int],
        project_slug: Optional[str],
        original_message: str,
        question: str,
        score: float,
        source: str,
    ) -> ChatSpecPending:
        pending = ChatSpecPending(
            id=uuid.uuid4().hex,
            session_id=session_id,
            project_id=project_id,
            project_slug=project_slug,
            original_message=original_message or "",
            question=question or "",
            score=float(score),
            source=source if source in ("text", "voice") else "text",
        )
        self._pendings[pending.id] = pending
        self._events[pending.id] = asyncio.Event()
        logger.info(
            "[SPEC-208] pending_create id=%s session=%s score=%.3f source=%s "
            "question_len=%d msg_len=%d",
            pending.id, session_id, pending.score, pending.source,
            len(pending.question or ""),
            len(pending.original_message or ""),
        )
        return pending

    def get(self, pending_id: str) -> Optional[ChatSpecPending]:
        return self._pendings.get(pending_id)

    def list_for_session(self, session_id: str) -> List[ChatSpecPending]:
        if not session_id:
            return []
        return [
            p for p in self._pendings.values()
            if p.session_id == session_id and p.status == "pending"
        ]

    async def resolve(
        self,
        pending_id: str,
        decision: str,
        *,
        session_id: Optional[str] = None,
        answer_text: Optional[str] = None,
    ) -> bool:
        """Setzt den Pending auf ``answered``, ``bypassed`` oder ``cancelled``.

        - ``answered`` braucht non-empty ``answer_text``, sonst False.
        - ``bypassed`` und ``cancelled`` ignorieren ``answer_text``.
        - Cross-Session-Resolve via ``session_id``-Mismatch wird geblockt.
        - Doppel-Resolve ist idempotent (zweiter Klick → False).
        """
        if decision not in ("answered", "bypassed", "cancelled"):
            logger.warning(
                "[SPEC-208] resolve_invalid_decision id=%s decision=%r",
                pending_id, decision,
            )
            return False
        pending = self._pendings.get(pending_id)
        if pending is None:
            logger.info("[SPEC-208] resolve_unknown id=%s", pending_id)
            return False
        if pending.status != "pending":
            logger.info(
                "[SPEC-208] resolve_already_done id=%s status=%s",
                pending_id, pending.status,
            )
            return False
        if session_id is not None and session_id != pending.session_id:
            logger.warning(
                "[SPEC-208] resolve_session_mismatch id=%s expected=%s got=%s",
                pending_id, pending.session_id, session_id,
            )
            return False

        if decision == "answered":
            truncated = _truncate_answer(answer_text)
            if not truncated:
                logger.info(
                    "[SPEC-208] resolve_answered_empty id=%s — leere Antwort abgelehnt",
                    pending_id,
                )
                return False
            pending.answer_text = truncated

        pending.status = decision
        pending.resolved_at = datetime.utcnow()
        ev = self._events.get(pending_id)
        if ev is not None:
            ev.set()
        logger.info(
            "[SPEC-208] resolve id=%s decision=%s session=%s",
            pending_id, decision, pending.session_id,
        )
        return True

    async def wait_for_decision(
        self,
        pending_id: str,
        timeout: float,
    ) -> str:
        """Blockt bis Resolve oder Timeout.

        Returns: ``answered`` | ``bypassed`` | ``cancelled`` | ``timeout`` | ``unknown``.
        """
        pending = self._pendings.get(pending_id)
        if pending is None:
            return "unknown"
        if pending.status != "pending":
            return pending.status

        event = self._events.setdefault(pending_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            if pending.status == "pending":
                pending.status = "timeout"
                pending.resolved_at = datetime.utcnow()
                logger.info(
                    "[SPEC-208] timeout id=%s after=%.1fs", pending_id, timeout,
                )
        return pending.status

    def cleanup(self, pending_id: str) -> None:
        self._pendings.pop(pending_id, None)
        self._events.pop(pending_id, None)


# ── Singleton ────────────────────────────────────────────────────────────

_GATE: Optional[ChatSpecGate] = None


def get_chat_spec_gate() -> ChatSpecGate:
    global _GATE
    if _GATE is None:
        _GATE = ChatSpecGate()
    return _GATE


def reset_chat_spec_gate() -> None:
    """Test-Helper. Niemals im Produktiv-Pfad aufrufen."""
    global _GATE
    _GATE = None


# ── Audit-Trail ──────────────────────────────────────────────────────────

AUDIT_MAX_TEXT_BYTES = 4_000


def _truncate_for_audit(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    s = str(text)
    encoded = s.encode("utf-8")
    if len(encoded) <= AUDIT_MAX_TEXT_BYTES:
        return s
    head = encoded[:AUDIT_MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
    return head + "\n…[gekuerzt]"


async def store_clarification_audit(
    *,
    pending_id: Optional[str],
    session_id: Optional[str],
    project_id: Optional[int],
    project_slug: Optional[str],
    original_message: Optional[str],
    question: Optional[str],
    answer_text: Optional[str],
    score: float,
    source: str,
    status: str,
) -> None:
    """Schreibt eine ``clarifications``-Zeile als Audit-Trail.

    Best-Effort: jeder Fehler wird geloggt + verschluckt. Hauptpfad
    blockiert nicht durch Audit-Probleme.
    """
    try:
        from zerberus.core.database import (
            Clarification,
            _async_session_maker,
        )
    except Exception as e:
        logger.warning("[SPEC-208] audit_import_failed: %s", e)
        return

    if _async_session_maker is None:
        return

    try:
        async with _async_session_maker() as session:
            row = Clarification(
                pending_id=pending_id,
                session_id=session_id,
                project_id=project_id,
                project_slug=project_slug,
                original_message=_truncate_for_audit(original_message),
                question=_truncate_for_audit(question),
                answer_text=_truncate_for_audit(answer_text),
                score=float(score) if score is not None else None,
                source=source if source in ("text", "voice") else "text",
                status=status,
                resolved_at=datetime.utcnow()
                if status in ("answered", "bypassed", "cancelled", "timeout", "error")
                else None,
            )
            session.add(row)
            await session.commit()
        logger.info(
            "[SPEC-208] audit_written session=%s project_id=%s status=%s "
            "source=%s score=%.3f",
            session_id, project_id, status, source,
            float(score) if score is not None else 0.0,
        )
    except Exception as e:
        logger.warning("[SPEC-208] audit_failed (non-fatal): %s", e)
