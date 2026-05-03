"""Patch 209 (Phase 5a #7) — Zweite Meinung vor Ausfuehrung (Sancho Panza).

Veto-Logik: vor dem HitL-Gate aus P206 bewertet ein zweites Modell den
vom ersten Modell generierten Code auf "macht das wirklich was der User
will + ist es sicher". Bei Veto landet der Code nicht im HitL-Gate
sondern in einem Wandschlag-Banner mit Veto-Begruendung — kein
HitL-Pending, kein Sandbox-Run, kein Snapshot.

Architektur:

- ``should_run_veto(code, language)`` — Pure-Function Trigger-Gate.
  Triviale Code-Blocks (single-line print, return, var assignment)
  passen durch ohne LLM-Call (Token-Spar). Mehrzeiliger Code oder
  Code mit gefaehrlichen Keywords (subprocess, eval, requests.*, rm,
  unlink, shutil.rmtree, ...) triggert den Veto-Probe.

- ``build_veto_messages(code, language, user_prompt)`` — minimaler
  Probe-Prompt. System-Prompt verlangt EIN Token (``PASS`` oder
  ``VETO``) plus optional eine kurze Begruendung im selben String.
  Kein Persona-Leak, kein RAG, kein Sentiment.

- ``parse_veto_verdict(text)`` — robust gegen Whitespace, Doppelpunkte,
  Anfuehrungszeichen rund um das Verdict-Token. ``PASS`` → veto=False,
  ``VETO`` → veto=True. Unklarer Output → veto=False (fail-open).

- ``run_veto(...)`` — Async-Wrapper, ``temperature=0.1`` (deterministisch).
  Fail-open auf jeder Stufe: LLM-Crash, Timeout, Non-Tuple → veto=False.

- ``store_veto_audit(...)`` — Best-Effort-Insert in ``code_vetoes``.

Logging-Tag: ``[VETO-209]``.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional


logger = logging.getLogger("zerberus.code_veto")


# ── Pure-Function-Schicht ────────────────────────────────────────────────

# "Gefaehrliche" Keywords, die fast immer einen Veto-Check rechtfertigen.
# Substring-Match (case-insensitive); pythonic mit `_re.search` waere
# praeziser, aber Substrings reichen fuer das Trigger-Gate. Falsch-positive
# (wie ``"open"`` in ``"opening"``) sind OK — wir wollen LIEBER pruefen
# als skippen.
_RISKY_TOKENS = (
    # Process / shell
    "subprocess", "os.system", "os.popen",
    "shell=true", "shell=True",
    "eval(", "exec(", "compile(",
    # Filesystem destruction
    "rm -rf", "rm -r ", "rmdir", "unlink", "shutil.rmtree",
    "os.remove", "os.unlink", "pathlib.path.unlink",
    "open(", ".write(", "writelines(",
    "del /f", "del /q", "format c:", "mkfs",
    # Network
    "requests.post", "requests.put", "requests.delete", "requests.patch",
    "urllib.request", "httpx.post", "httpx.put", "httpx.delete",
    "fetch(", "axios.post", "axios.put",
    "child_process", "execsync", "execasync",
    # Privilege / git destructive
    "sudo ", "chmod ", "chown ",
    "git push --force", "git push -f", "--no-verify", "git reset --hard",
    "git clean -f", "rebase -i",
    # Serialization risks
    "pickle.load", "marshal.load", "yaml.load(",
    # JS-FS
    "fs.unlink", "fs.rmdir", "fs.rm(",
)


# Triviale 1-Zeiler die wir ohne LLM-Call durchwinken (Token-Spar).
_TRIVIAL_PATTERNS = (
    re.compile(r"^\s*print\s*\(", re.IGNORECASE),
    re.compile(r"^\s*return\s+", re.IGNORECASE),
    re.compile(r"^\s*pass\s*$", re.IGNORECASE),
    re.compile(r"^\s*console\.log\s*\(", re.IGNORECASE),
    re.compile(r"^\s*[a-z_]\w*\s*=\s*[\d\"\'\[\{]", re.IGNORECASE),
)


def _non_empty_lines(code: str) -> List[str]:
    """Zeilen ohne leere/whitespace-only Zeilen."""
    if not code:
        return []
    return [ln for ln in code.splitlines() if ln.strip()]


def _has_risky_token(code: str) -> bool:
    if not code:
        return False
    lower = code.lower()
    return any(tok.lower() in lower for tok in _RISKY_TOKENS)


def _is_trivial_oneliner(code: str) -> bool:
    """Single-line, harmlos (print/return/var/pass)."""
    lines = _non_empty_lines(code)
    if len(lines) != 1:
        return False
    line = lines[0]
    if len(line) > 120:
        return False
    return any(p.match(line) for p in _TRIVIAL_PATTERNS)


def should_run_veto(code: str, language: str) -> bool:
    """Trigger-Gate fuer den Veto-LLM-Call.

    Returns True wenn:
        - Code hat >=2 nicht-leere Zeilen (Komplexitaet rechtfertigt Pruefung).
        - ODER Code enthaelt risiko-relevante Tokens (subprocess, rm, eval, ...).

    Returns False wenn:
        - Code ist leer / nur Whitespace.
        - ODER Code ist trivialer 1-Zeiler (print/return/pass/var-assign)
          OHNE risiko-relevante Tokens — dann sparen wir den LLM-Call.

    ``language`` ist aktuell nicht benutzt, bleibt aber im Interface fuer
    spaetere Sprach-spezifische Heuristiken (z.B. shell-Code aggressiver
    triggern als Python).
    """
    if not code or not code.strip():
        return False
    if _has_risky_token(code):
        return True
    lines = _non_empty_lines(code)
    if len(lines) >= 2:
        return True
    if _is_trivial_oneliner(code):
        return False
    # 1-Zeiler ohne Trivial-Pattern und ohne Risk-Token — Borderline, lieber pruefen.
    return True


# ── LLM-Probe ────────────────────────────────────────────────────────────

VETO_SYSTEM_PROMPT = (
    "Du bist Sancho Panza — die zweite Meinung vor der Code-Ausfuehrung. "
    "Pruefe knapp: macht dieser Code-Vorschlag das, was der User wirklich "
    "will, UND ist er sicher (kein Datenverlust, keine ungewollte "
    "Netzwerk-Aktion, keine Rechte-Eskalation)?\n"
    "\n"
    "Antworte mit GENAU einem Token am Anfang:\n"
    "  PASS  — Code ist OK, kann ausgefuehrt werden.\n"
    "  VETO  — Code soll geblockt werden.\n"
    "\n"
    "Danach optional EINE Begruendung in einem Satz, getrennt durch "
    "Doppelpunkt. Beispiel: ``VETO: rm -rf / loescht das gesamte System "
    "— der User wollte nur eine Datei.`` Halte die Begruendung kurz, "
    "konkret und ohne Vorrede."
)


# Maximale Laenge des im Prompt eingebetteten Codes (Bytes, UTF-8).
VETO_CODE_MAX_BYTES = 4_000

# Maximale Laenge der Begruendung im Verdict (Bytes nach UTF-8).
VETO_REASON_MAX_BYTES = 400


def _truncate_code_for_prompt(code: str) -> str:
    s = code or ""
    encoded = s.encode("utf-8")
    if len(encoded) <= VETO_CODE_MAX_BYTES:
        return s
    head = encoded[:VETO_CODE_MAX_BYTES].decode("utf-8", errors="ignore")
    return head + "\n…[gekuerzt]"


def _truncate_reason(reason: str) -> str:
    s = (reason or "").strip()
    if not s:
        return ""
    encoded = s.encode("utf-8")
    if len(encoded) <= VETO_REASON_MAX_BYTES:
        return s
    return encoded[:VETO_REASON_MAX_BYTES].decode("utf-8", errors="ignore").rstrip()


def build_veto_messages(
    code: str,
    language: str,
    user_prompt: str,
) -> List[dict]:
    """Pure-Function: baut die ``messages``-Liste fuer den Veto-LLM-Call.

    Bewusst minimaler Kontext — wir wollen NUR ein Verdict, kein
    Persona-Leak, kein RAG. Der Veto-Call ist ein Werkzeug, kein
    Gespraech.
    """
    safe_code = _truncate_code_for_prompt(code)
    safe_lang = (language or "unknown").strip().lower() or "unknown"
    safe_prompt = (user_prompt or "").strip()
    return [
        {"role": "system", "content": VETO_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"User-Wunsch:\n{safe_prompt}\n"
                "\n"
                f"Code-Vorschlag (Sprache: {safe_lang}):\n"
                "---\n"
                f"{safe_code}\n"
                "---\n"
                "\n"
                "Verdict?"
            ),
        },
    ]


# ── Verdict-Parser ───────────────────────────────────────────────────────

@dataclass
class VetoVerdict:
    """Resultat des Veto-Calls.

    ``veto=True`` heisst: Code wird geblockt, kein HitL, kein Sandbox-Run.
    ``veto=False`` heisst: Code passt zum Wunsch + ist sicher → weiter
    zum HitL-Gate (P206).

    ``reason`` ist die Wandschlag-Begruendung (nur bei veto=True
    user-relevant). ``raw`` ist die rohe LLM-Antwort fuer Debugging.

    ``latency_ms`` ist optional und wird vom Async-Wrapper gesetzt.
    """
    veto: bool
    reason: str = ""
    raw: Optional[str] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None  # bei Pipeline-Fehlern fail-open

    def to_payload_dict(self) -> dict:
        """Schema fuer das ``code_execution.veto``-Sub-Field der Response."""
        return {
            "vetoed": bool(self.veto),
            "reason": self.reason or "",
            "latency_ms": self.latency_ms,
        }


_VERDICT_PATTERN = re.compile(r"^[\s\"'`*_]*(PASS|VETO)\b\s*[:\-—]?\s*(.*)$",
                              re.IGNORECASE | re.DOTALL)


def parse_veto_verdict(text: str) -> VetoVerdict:
    """Parst das LLM-Verdict.

    Robust gegen Whitespace, Anfuehrungszeichen, Asterisken (Markdown-
    Boldness), Doppelpunkte/Bindestriche zwischen Token und Begruendung.
    Unklarer Output → veto=False (fail-open).
    """
    raw = (text or "").strip()
    if not raw:
        return VetoVerdict(veto=False, reason="", raw=text or "")

    # Erste Zeile prueft das Verdict-Token.
    first_line = raw.splitlines()[0].strip()
    m = _VERDICT_PATTERN.match(first_line)
    if not m:
        # Fallback: irgendwo am Anfang VETO/PASS suchen.
        m_any = re.search(r"\b(PASS|VETO)\b", raw[:64], re.IGNORECASE)
        if m_any is None:
            return VetoVerdict(veto=False, reason="", raw=raw, error="parse_failed")
        verdict = m_any.group(1).upper()
        rest = raw[m_any.end():].strip(" :-—\"'`*")
        return VetoVerdict(
            veto=(verdict == "VETO"),
            reason=_truncate_reason(rest) if verdict == "VETO" else "",
            raw=raw,
        )

    verdict = m.group(1).upper()
    rest_first_line = (m.group(2) or "").strip()
    # Falls der Begruendungsstrang ueber mehrere Zeilen geht, auch die
    # naechsten Zeilen als Reason mitnehmen — bis zur ersten Leerzeile.
    extra: List[str] = []
    for line in raw.splitlines()[1:]:
        if not line.strip():
            break
        extra.append(line.strip())
    full_reason = rest_first_line
    if extra:
        full_reason = (full_reason + " " + " ".join(extra)).strip()

    return VetoVerdict(
        veto=(verdict == "VETO"),
        reason=_truncate_reason(full_reason) if verdict == "VETO" else "",
        raw=raw,
    )


# ── Async-Wrapper ────────────────────────────────────────────────────────

# Default-Temperatur fuer den Veto-Call — sehr deterministisch, weil
# Verdict ein klares ja/nein ist.
DEFAULT_VETO_TEMPERATURE = 0.1


async def run_veto(
    code: str,
    language: str,
    user_prompt: str,
    llm_service: Any,
    session_id: str,
    *,
    temperature: float = DEFAULT_VETO_TEMPERATURE,
) -> VetoVerdict:
    """Async-Wrapper: ruft den Veto-LLM auf und parst das Verdict.

    Fail-open: jede Pipeline-Stoerung resultiert in
    ``VetoVerdict(veto=False, error=...)`` — der Caller behandelt das wie
    "kein Veto, weiter zum HitL-Gate".
    """
    started = datetime.utcnow()
    messages = build_veto_messages(code, language, user_prompt)

    try:
        result = await llm_service.call(
            messages,
            session_id,
            temperature_override=float(temperature),
        )
    except Exception as e:
        logger.warning(f"[VETO-209] llm_call_failed (fail-open): {e}")
        return VetoVerdict(veto=False, error=f"llm_call_failed: {e}", raw=None)

    if not isinstance(result, tuple) or not result:
        logger.info("[VETO-209] llm_returned_unexpected_type type=%r",
                    type(result).__name__)
        return VetoVerdict(veto=False, error="unexpected_type", raw=None)

    raw_text = result[0] if len(result) > 0 else None
    if not isinstance(raw_text, str) or not raw_text.strip():
        return VetoVerdict(veto=False, error="empty_response", raw=raw_text)

    verdict = parse_veto_verdict(raw_text)
    elapsed = (datetime.utcnow() - started).total_seconds()
    verdict.latency_ms = int(elapsed * 1000)
    logger.info(
        "[VETO-209] decision veto=%s reason_len=%d code_len=%d lang=%s "
        "session=%s latency_ms=%d",
        verdict.veto, len(verdict.reason or ""),
        len(code or ""), language, session_id, verdict.latency_ms,
    )
    return verdict


# ── Audit-Trail ──────────────────────────────────────────────────────────

# Audit-Truncate-Limit (Bytes). Lange Codes/Outputs sollen die DB nicht
# fluten — User-sichtbarer Reason liegt im ``code_execution.veto``-Feld
# und im Frontend.
AUDIT_MAX_TEXT_BYTES = 8_000


def _truncate_for_audit(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    s = str(text)
    encoded = s.encode("utf-8")
    if len(encoded) <= AUDIT_MAX_TEXT_BYTES:
        return s
    head = encoded[:AUDIT_MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
    return head + "\n…[gekuerzt]"


async def store_veto_audit(
    *,
    audit_id: Optional[str],
    session_id: Optional[str],
    project_id: Optional[int],
    project_slug: Optional[str],
    language: Optional[str],
    code_text: Optional[str],
    user_prompt: Optional[str],
    verdict: str,  # pass | veto | skipped | error
    reason: Optional[str],
    latency_ms: Optional[int],
) -> None:
    """Schreibt eine ``code_vetoes``-Zeile als Audit-Trail.

    Best-Effort: jeder Fehler wird geloggt + verschluckt. Hauptpfad
    blockiert nicht durch Audit-Probleme.
    """
    try:
        from zerberus.core.database import (
            CodeVeto,
            _async_session_maker,
        )
    except Exception as e:
        logger.warning("[VETO-209] audit_import_failed: %s", e)
        return

    if _async_session_maker is None:
        # DB nicht initialisiert (Unit-Tests ohne init_db) — silent skip
        return

    try:
        async with _async_session_maker() as session:
            row = CodeVeto(
                audit_id=audit_id,
                session_id=session_id,
                project_id=project_id,
                project_slug=project_slug,
                language=language,
                code_text=_truncate_for_audit(code_text),
                user_prompt=_truncate_for_audit(user_prompt),
                verdict=verdict,
                reason=_truncate_for_audit(reason),
                latency_ms=latency_ms,
            )
            session.add(row)
            await session.commit()
        logger.info(
            "[VETO-209] audit_written session=%s project_id=%s verdict=%s "
            "language=%s latency_ms=%s",
            session_id, project_id, verdict, language, latency_ms,
        )
    except Exception as e:
        logger.warning("[VETO-209] audit_failed (non-fatal): %s", e)


# ── Convenience: UUID4-Audit-IDs ────────────────────────────────────────

def new_audit_id() -> str:
    """UUID4-hex als Audit-ID. Eigene Funktion fuer Test-Mocks."""
    return uuid.uuid4().hex
