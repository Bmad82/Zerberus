"""Patch 197 (Phase 5a — Decision 3) — Persona-Merge-Layer.

Verheiratet die User-Persona (`system_prompt_<profile>.json`, vom Profil-
Setting "Mein Ton" gepflegt) mit dem optionalen Projekt-Overlay
(`projects.persona_overlay`, von P194/P195 gepflegt). Die Reihenfolge
folgt der Decision-3-Festlegung vom 2026-05-01:

    System-Default → User-Persona ("Mein Ton") → Projekt-Overlay

System-Default und User-Persona stecken bereits zusammen in der
``system_prompt_<profile>.json`` (eine Datei pro Profil) — dieser Helper
hängt nur den Projekt-Block hinten dran. Der wird als eigener,
explizit markierter Block formatiert, damit er für das LLM klar
erkennbar bleibt und nicht in der allgemeinen Persona untergeht.

Ein bewusst flacher Helper: kein State, keine I/O, kein Logging
innerhalb des Merges. Das macht Tests trivial und erlaubt es, den
Helper sowohl synchron (Aufrufer hat das Overlay bereits) als auch
async (Aufrufer holt es per ``projects_repo.get_project``) zu nutzen.

Der Header-Reader ``read_active_project_id`` lebt hier mit, weil er
semantisch zum Merge-Kontext gehört — aktives Projekt pro Request.
Persistente Auswahl (z.B. Spalte ``active_project_id`` an
``chat_sessions``) ist später möglich; dann ist der Reader die einzige
Stelle, die geändert werden muss.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

ACTIVE_PROJECT_HEADER = "X-Active-Project-Id"

# Marker des Projekt-Blocks im finalen System-Prompt. Bewusst eindeutig,
# damit der Block per substring-Check in Tests/Logs auffindbar ist und
# bei späterer Doppel-Injection (z.B. zwei Merger-Aufrufe in einer
# Pipeline) eine Schutzklausel greifen kann.
PROJECT_BLOCK_MARKER = "[PROJEKT-KONTEXT — verbindlich für diese Session]"


def _normalize_tone_hints(raw: Any) -> list[str]:
    """Säubert die ``tone_hints`` aus einem Overlay-Dict.

    - Nicht-Listen → leere Liste (defensive — Hel-UI liefert immer
      eine Liste, externer Caller könnte aber String reichen)
    - Strings werden getrimmt
    - Leere Strings + Duplikate (case-insensitive) werden entfernt,
      Reihenfolge bleibt erhalten (erstes Vorkommen gewinnt)
    """
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _format_project_block(
    addendum: str,
    tone_hints: list[str],
    project_slug: Optional[str] = None,
) -> str:
    """Baut den Projekt-Block. Gibt ``""`` zurück wenn weder Addendum
    noch Hints vorhanden sind — Caller hängt dann nichts an.
    """
    addendum = addendum.strip() if isinstance(addendum, str) else ""
    if not addendum and not tone_hints:
        return ""

    # Doppel-Leerstring → echte Leerzeile vor dem Trennstrich nach
    # ``"\n".join``. Macht den Uebergang vom Persona-Text zum Projekt-
    # Block visuell klar (fuer Logs/Debug) und semantisch klar fuers LLM.
    parts: list[str] = ["", "", "---", PROJECT_BLOCK_MARKER]
    if project_slug:
        parts.append(f"Projekt: {project_slug}")
    if addendum:
        parts.append(addendum)
    if tone_hints:
        parts.append("")
        parts.append("Tonfall-Hinweise:")
        for hint in tone_hints:
            parts.append(f"- {hint}")
    return "\n".join(parts)


def merge_persona(
    base_prompt: str,
    project_overlay: Optional[Mapping[str, Any]] = None,
    project_slug: Optional[str] = None,
) -> str:
    """Hängt den Projekt-Overlay-Block an einen vorhandenen System-Prompt.

    ``base_prompt`` enthält bereits die Kombination System-Default + User-
    Persona (so wie ``load_system_prompt`` sie liefert). Diese Funktion
    fügt ausschließlich den Projekt-Layer hinzu.

    Verhalten:
    - ``project_overlay is None`` oder leeres Dict → ``base_prompt``
      unverändert zurückgeben.
    - Overlay vorhanden, aber sowohl ``system_addendum`` leer als auch
      ``tone_hints`` leer → ``base_prompt`` unverändert.
    - ``base_prompt`` leer + Overlay vorhanden → nur den Projekt-Block
      zurückgeben (ohne führende Leerzeile / Trenner).
    - Doppel-Injection-Schutz: Wenn ``PROJECT_BLOCK_MARKER`` schon in
      ``base_prompt`` steckt, gibt's keinen zweiten Block — der erste
      gewinnt (Idempotenz für versehentliche Doppel-Aufrufe in derselben
      Pipeline).

    ``project_slug`` ist optional und wandert in eine ``Projekt: <slug>``-
    Zeile am Anfang des Blocks, wenn gesetzt — hilft dem LLM beim Self-
    Talk ("für Projekt X gilt ...").
    """
    if not project_overlay:
        return base_prompt

    if base_prompt and PROJECT_BLOCK_MARKER in base_prompt:
        return base_prompt

    addendum = project_overlay.get("system_addendum", "") if isinstance(project_overlay, Mapping) else ""
    raw_hints = project_overlay.get("tone_hints", []) if isinstance(project_overlay, Mapping) else []
    hints = _normalize_tone_hints(raw_hints)

    block = _format_project_block(addendum, hints, project_slug)
    if not block:
        return base_prompt

    if not base_prompt:
        # Block beginnt sonst mit zwei Leerzeilen — strippen.
        return block.lstrip("\n")
    return f"{base_prompt}{block}"


def read_active_project_id(headers: Mapping[str, str]) -> Optional[int]:
    """Liest den ``X-Active-Project-Id``-Header und konvertiert zu ``int``.

    Gibt ``None`` zurück, wenn der Header fehlt, leer ist oder keine
    valide Integer-Zahl enthält. Negative IDs werden ebenfalls als
    ``None`` behandelt (defensive — Projekt-IDs sind in SQLite immer
    positiv).

    FastAPI's ``Request.headers`` ist case-insensitive (Mapping-Protokoll
    entspricht Starlette's ``Headers``-Klasse). Ein ``dict`` aus Tests
    funktioniert ebenfalls, solange es das Mapping-Protokoll erfüllt —
    wir versuchen sowohl die Original-Schreibweise als auch lowercase.
    """
    if headers is None:
        return None
    raw = headers.get(ACTIVE_PROJECT_HEADER)
    if raw is None:
        # Fallback fuer reine dict-Mappings, die Case-Sensitive sind.
        raw = headers.get(ACTIVE_PROJECT_HEADER.lower())
    if raw is None or not str(raw).strip():
        return None
    try:
        value = int(str(raw).strip())
    except (ValueError, TypeError):
        return None
    return value if value > 0 else None


async def resolve_project_overlay(
    project_id: Optional[int],
    *,
    skip_archived: bool = True,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Holt das Projekt anhand der ID und liefert ``(overlay, slug)``.

    Trennt den DB-Zugriff vom reinen Merge — der ``merge_persona``-Helper
    bleibt I/O-frei und damit synchron testbar. Diese Coroutine ist die
    "richtige" Verwendungs-Schicht im Endpoint.

    Rückgabe:
    - ``(None, None)`` wenn ``project_id`` None ist oder kein Projekt
      gefunden wird (kein Fehler — das ist der Default-Fall ohne aktives
      Projekt).
    - ``(None, slug)`` wenn das Projekt existiert, aber archiviert ist
      und ``skip_archived=True``. Slug wird trotzdem zurückgegeben, damit
      der Caller eine Warnung loggen kann.
    - ``(overlay_dict, slug)`` im Erfolgsfall.

    Importiert ``projects_repo`` lazy, um zirkuläre Importe zu vermeiden
    (das Repo importiert selbst aus ``database``, das wiederum andere
    Helper holt).
    """
    if project_id is None:
        return None, None
    from zerberus.core.projects_repo import get_project  # lazy

    proj = await get_project(project_id)
    if proj is None:
        return None, None
    slug = proj.get("slug")
    if skip_archived and proj.get("is_archived"):
        return None, slug
    overlay = proj.get("persona_overlay") or {}
    return overlay, slug
