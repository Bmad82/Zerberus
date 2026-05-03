"""Patch 212 (Phase 5a #12) — Secrets bleiben geheim.

Filter, der env-Variablen-Werte aus Sandbox-stdout/-stderr und LLM-
Synthese-Prompts maskiert, damit ``OPENAI_API_KEY=sk-...``,
``OPENROUTER_API_KEY=...``, ``DATABASE_PASSWORD=...`` etc. nie im
sichtbaren Output, in der Synthese-Pipeline oder in der finalen Response
des Chat-Endpunkts landen.

Architektur:

* **Pure-Function-Schicht** — testbar ohne I/O:
  * ``is_secret_key(name)`` — Heuristik fuer env-Var-Namen.
  * ``extract_secret_values(env_dict)`` — sammelt alle Werte mit
    Secret-Indikator im Key, ignoriert zu kurze Werte (Falsch-Positive
    durch leere/short-Werte vermeiden).
  * ``mask_secrets_in_text(text, secrets)`` — substring-Replace,
    longest-first damit ``OPENAI_API_KEY=sk-…`` nicht zu ``…_KEY=…``
    ueberlebt.

* **Cache + Side-Effect-Schicht**:
  * ``load_secret_values(env=None)`` — lazy-cached Snapshot der Secrets.
    Tests koennen via ``reset_cache_for_tests()`` invalidieren oder
    ein eigenes ``env``-Dict reinreichen.
  * ``mask_and_audit(text, *, source, session_id=None)`` — Convenience-
    API fuer die Verdrahtung: maskiert + schreibt Audit-Zeile (nur wenn
    count > 0).

* **Audit-Trail in ``secret_redactions``** — Best-Effort. Wenn jemals
  ein Klartext-Secret im Output landen wuerde, taucht hier eine Zeile
  auf — und das ist ein Bug-Indikator, der nachweisbar werden soll.

Was P212 bewusst NICHT tut:

* ``.env``-Verschluesselung (separater Patch P212b).
* Pattern-basiertes Matching ohne env-Lookup (z.B. `sk-…`-Prefix). Wenn
  ein Secret nicht in ``.env`` steht, kennen wir es nicht — Defense-in-
  Depth ueber zusaetzliche Pattern waere ein eigener Patch.
* Multi-Pass-LLM-Filter. Eine Maskierung am Output reicht — der
  Synthese-LLM sieht den Klartext-Secret nie.

Logging-Tag: ``[SECRETS-212]``.
"""
from __future__ import annotations

import logging
import os
from typing import Iterable, Mapping, Optional

logger = logging.getLogger("zerberus.secrets_filter")


# ── Pure-Function-Schicht ────────────────────────────────────────────────

# Heuristik fuer Secret-Indikatoren im env-Var-Namen. Konservativ — wir
# wollen lieber einen Wert zu viel maskieren als einen Klartext-Schluessel
# in der Response zu hinterlassen.
SECRET_KEY_SUFFIXES: tuple[str, ...] = (
    "_KEY",
    "_SECRET",
    "_TOKEN",
    "_PASSWORD",
    "_PASS",
    "_PASSPHRASE",
    "_CREDENTIAL",
    "_CREDENTIALS",
)
SECRET_KEY_PREFIXES: tuple[str, ...] = (
    "API_",
    "AUTH_",
)
SECRET_KEY_NAMES: frozenset[str] = frozenset({
    "PASSWORD",
    "TOKEN",
    "SECRET",
    "API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "JWT_SECRET",
    "DATABASE_URL",  # kann user:pass@host enthalten
})

# Standard-Replacement im Output. ASCII, eindeutig erkennbar in Logs.
DEFAULT_REPLACEMENT = "***REDACTED***"

# Werte unter dieser Laenge ignorieren — kurze Werte sind fast immer
# Falsch-Positive (leerer String, einzelne Buchstaben, Zahlen). Echte
# API-Keys sind in der Regel >= 20 Zeichen. 8 ist ein konservativer
# Mindestwert, der trotzdem kurze Passwoerter abdeckt.
MIN_SECRET_LENGTH = 8


def is_secret_key(key: str) -> bool:
    """``True`` wenn der env-Var-Name auf ein Secret hindeutet.

    Pure Function — case-insensitive Match gegen die Whitelist + Suffix-
    + Prefix-Tabellen. Leerer Name → ``False``.
    """
    name = (key or "").upper().strip()
    if not name:
        return False
    if name in SECRET_KEY_NAMES:
        return True
    if any(name.endswith(s) for s in SECRET_KEY_SUFFIXES):
        return True
    if any(name.startswith(p) for p in SECRET_KEY_PREFIXES):
        return True
    return False


def extract_secret_values(
    env_dict: Mapping[str, str],
    *,
    min_length: int = MIN_SECRET_LENGTH,
) -> set[str]:
    """Sammelt Werte aller env-Vars mit Secret-Indikator im Key.

    Ignoriert leere Werte und Werte unter ``min_length`` (Falsch-Positive
    durch z.B. ``DEBUG_TOKEN=1`` oder ``PASSWORD=``).

    Pure Function — kein I/O, kein ``os.environ``-Zugriff.
    """
    secrets: set[str] = set()
    for key, value in env_dict.items():
        if not is_secret_key(key):
            continue
        if not value:
            continue
        text = str(value)
        if len(text) < min_length:
            continue
        secrets.add(text)
    return secrets


def mask_secrets_in_text(
    text: str,
    secrets: Iterable[str],
    *,
    replacement: str = DEFAULT_REPLACEMENT,
) -> tuple[str, int]:
    """Maskiert alle Secret-Werte im Text. Returns ``(masked_text, count)``.

    Longest-first ist invariant: wenn ein Secret ``ABC-LONG-KEY`` lautet
    und ein anderes ``ABC``, MUSS erst ``ABC-LONG-KEY`` maskiert werden,
    sonst wuerde der Replace daraus ``***REDACTED***-LONG-KEY``.

    ``count`` ist die Summe aller substring-Treffer ueber alle Secrets
    BEFORE Replacement (jedes Vorkommen zaehlt einzeln). Leeres Set,
    leerer Text oder leeres Secret → keine Aenderung, count=0.

    Pure Function.
    """
    if not text:
        return text, 0
    cleaned = [s for s in secrets if s]
    if not cleaned:
        return text, 0
    sorted_secrets = sorted(set(cleaned), key=len, reverse=True)
    count = 0
    out = text
    for s in sorted_secrets:
        if not s or s == replacement:
            continue
        if s in out:
            count += out.count(s)
            out = out.replace(s, replacement)
    return out, count


# ── Cache + Side-Effect-Schicht ─────────────────────────────────────────

_cached_secrets: Optional[frozenset[str]] = None


def load_secret_values(
    *,
    env: Optional[Mapping[str, str]] = None,
    force_reload: bool = False,
) -> frozenset[str]:
    """Lazy-cached Snapshot der env-Secrets.

    Erst-Aufruf liest ``os.environ`` (oder das gegebene ``env``-Dict),
    extrahiert die Secret-Werte, friert sie als ``frozenset`` ein.
    Folgende Aufrufe geben den Cache zurueck — env-Aenderungen zur
    Laufzeit werden NICHT beobachtet (das ist gewollt: ``.env`` wird
    einmal beim Start geladen, danach ist die Liste stabil).

    Tests koennen ``force_reload=True`` setzen oder
    ``reset_cache_for_tests()`` aufrufen.
    """
    global _cached_secrets
    if _cached_secrets is not None and not force_reload:
        return _cached_secrets
    source = env if env is not None else os.environ
    _cached_secrets = frozenset(extract_secret_values(source))
    return _cached_secrets


def reset_cache_for_tests() -> None:
    """Test-Helper: Cache verwerfen. NICHT in Produktion aufrufen."""
    global _cached_secrets
    _cached_secrets = None


# ── Audit-Trail ──────────────────────────────────────────────────────────


async def store_secret_redaction(
    *,
    redaction_count: int,
    source: str,
    session_id: Optional[str] = None,
) -> None:
    """Schreibt eine ``secret_redactions``-Zeile als Audit-Trail.

    Best-Effort: jeder Fehler wird geloggt + verschluckt. Hauptpfad
    blockiert nicht durch Audit-Probleme.

    Skipt Eintraege mit ``redaction_count <= 0`` — dann ist nichts
    passiert, was auditiert werden muesste.
    """
    if redaction_count <= 0:
        return
    try:
        from zerberus.core.database import (
            SecretRedactionAudit,
            _async_session_maker,
        )
    except Exception as e:
        logger.warning("[SECRETS-212] audit_import_failed: %s", e)
        return

    if _async_session_maker is None:
        return

    try:
        async with _async_session_maker() as session:
            row = SecretRedactionAudit(
                redaction_count=int(redaction_count),
                source=str(source)[:32],
                session_id=session_id,
            )
            session.add(row)
            await session.commit()
        logger.info(
            "[SECRETS-212] audit_written source=%s count=%d session=%s",
            source, redaction_count, session_id,
        )
    except Exception as e:
        logger.warning("[SECRETS-212] audit_failed (non-fatal): %s", e)


# ── Verdrahtungs-Convenience ────────────────────────────────────────────


async def mask_and_audit(
    text: str,
    *,
    source: str,
    session_id: Optional[str] = None,
) -> str:
    """Maskiert Secrets im Text + schreibt Audit-Eintrag (wenn count > 0).

    Default-API fuer die Verdrahtung in Sandbox / Synthese / Response.
    Fail-open: jeder Fehler im Audit-Pfad wird geloggt + verschluckt,
    der maskierte Text kommt trotzdem zurueck.

    Args:
        text: Der zu maskierende Text (stdout, stderr, Code-Block, …).
        source: Identifier fuer den Audit-Trail (``sandbox``,
            ``synthesis``, ``response``).
        session_id: Session-Korrelation fuer den Audit; ``None`` wenn
            der Caller die Session nicht kennt (z.B. SandboxManager).

    Returns:
        Maskierter Text. Wenn nichts maskiert wurde, ist der Output
        identisch zum Input.
    """
    if not text:
        return text
    try:
        secrets = load_secret_values()
    except Exception as e:
        logger.warning("[SECRETS-212] load failed (fail-open): %s", e)
        return text
    masked, count = mask_secrets_in_text(text, secrets)
    if count > 0:
        logger.info(
            "[SECRETS-212] masked source=%s count=%d session=%s",
            source, count, session_id,
        )
        try:
            await store_secret_redaction(
                redaction_count=count,
                source=source,
                session_id=session_id,
            )
        except Exception as e:
            logger.warning("[SECRETS-212] audit-call failed (fail-open): %s", e)
    return masked


def mask_and_audit_sync(text: str, *, source: str) -> tuple[str, int]:
    """Sync-Variante ohne Audit-Schreibung — fuer Pfade ausserhalb des
    asyncio-Loops oder fuer reine Pure-Function-Maskierung.

    Returns ``(masked_text, count)``. Caller kann das ``count`` selbst
    auditieren (z.B. via ``asyncio.create_task(store_secret_redaction(...))``).
    """
    if not text:
        return text, 0
    try:
        secrets = load_secret_values()
    except Exception as e:
        logger.warning("[SECRETS-212] load failed (fail-open): %s", e)
        return text, 0
    masked, count = mask_secrets_in_text(text, secrets)
    if count > 0:
        logger.info(
            "[SECRETS-212] masked_sync source=%s count=%d",
            source, count,
        )
    return masked, count
