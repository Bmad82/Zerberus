"""Patch 194 (Phase 5a #1) — Projekt-Repository.

Async CRUD-Funktionen fuer ``projects`` und ``project_files``. Liegt
absichtlich neben ``database.py`` (gleiche Engine/Session-Factory) statt
in ``modules/`` — Projekte sind Core-Entitaet, kein optionales Modul.

Konvention: keine Klassen, keine Relations. Roh-IDs, Pure-Functions,
Async-Sessions. Matcht das Muster der bestehenden ``store_interaction``-
und Memory-Helper.

JSON-Serialisierung der Persona-Overlay liegt im Repo-Layer (nicht im
Model), damit das Schema dependency-frei bleibt — der Hel-Endpoint und
spaeter der Persona-Merge-Layer reichen Dicts ein, bekommen Dicts zurueck.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select, text as sa_text

from zerberus.core import database as db_mod
from zerberus.core.database import Project, ProjectFile

logger = logging.getLogger(__name__)


# Persona-Overlay-Default — was die Hel-UI ueber leere Felder zeigt.
EMPTY_OVERLAY: dict[str, Any] = {"system_addendum": "", "tone_hints": []}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """``"AI Research v2"`` -> ``"ai-research-v2"``.

    Schneidet auf 64 Zeichen (Schema-Limit) und faellt bei leerem
    Ergebnis auf ``"projekt"`` zurueck — der Caller haengt im
    Konfliktfall einen Counter dran.
    """
    s = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return (s or "projekt")[:64]


async def _resolve_unique_slug(base: str) -> str:
    """Sorgt fuer eine eindeutige Slug — bei Kollision ``-2``, ``-3``, ..."""
    candidate = base
    counter = 2
    while await get_project_by_slug(candidate) is not None:
        suffix = f"-{counter}"
        candidate = (base[: 64 - len(suffix)] + suffix)
        counter += 1
        if counter > 1000:
            raise RuntimeError(f"Konnte keine eindeutige Slug fuer '{base}' finden")
    return candidate


def _project_to_dict(p: Project) -> dict[str, Any]:
    overlay: dict[str, Any] = dict(EMPTY_OVERLAY)
    if p.persona_overlay:
        try:
            parsed = json.loads(p.persona_overlay)
            if isinstance(parsed, dict):
                overlay.update(parsed)
        except json.JSONDecodeError:
            logger.warning(f"[PROJECTS-194] persona_overlay nicht parsebar fuer project {p.id}")
    return {
        "id": p.id,
        "slug": p.slug,
        "name": p.name,
        "description": p.description or "",
        "persona_overlay": overlay,
        "is_archived": bool(p.is_archived),
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _file_to_dict(f: ProjectFile) -> dict[str, Any]:
    return {
        "id": f.id,
        "project_id": f.project_id,
        "relative_path": f.relative_path,
        "sha256": f.sha256,
        "size_bytes": f.size_bytes,
        "mime_type": f.mime_type,
        "storage_path": f.storage_path,
        "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
    }


# ---------------------------------------------------------------------------
# Projekt-CRUD
# ---------------------------------------------------------------------------


async def create_project(
    name: str,
    description: str = "",
    persona_overlay: Optional[dict[str, Any]] = None,
    slug: Optional[str] = None,
) -> dict[str, Any]:
    """Legt ein Projekt an. Slug wird aus Name abgeleitet (wenn nicht gesetzt)
    und bei Kollision automatisch eindeutig gemacht.
    """
    if not name.strip():
        raise ValueError("Projekt-Name darf nicht leer sein")

    final_slug = await _resolve_unique_slug(slugify(slug or name))
    overlay_json = json.dumps(persona_overlay) if persona_overlay else None

    async with db_mod._async_session_maker() as session:
        p = Project(
            slug=final_slug,
            name=name.strip(),
            description=description.strip() or None,
            persona_overlay=overlay_json,
            is_archived=0,
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
        logger.info(f"[PROJECTS-194] created id={p.id} slug={p.slug}")
        return _project_to_dict(p)


async def get_project(project_id: int) -> Optional[dict[str, Any]]:
    async with db_mod._async_session_maker() as session:
        result = await session.execute(select(Project).where(Project.id == project_id))
        p = result.scalar_one_or_none()
        return _project_to_dict(p) if p else None


async def get_project_by_slug(slug: str) -> Optional[dict[str, Any]]:
    async with db_mod._async_session_maker() as session:
        result = await session.execute(select(Project).where(Project.slug == slug))
        p = result.scalar_one_or_none()
        return _project_to_dict(p) if p else None


async def list_projects(include_archived: bool = False) -> list[dict[str, Any]]:
    async with db_mod._async_session_maker() as session:
        stmt = select(Project)
        if not include_archived:
            stmt = stmt.where(Project.is_archived == 0)
        stmt = stmt.order_by(Project.updated_at.desc())
        result = await session.execute(stmt)
        return [_project_to_dict(p) for p in result.scalars().all()]


async def update_project(
    project_id: int,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    persona_overlay: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Partial-Update. ``None`` = nicht aendern. Slug ist immutable
    (waere sonst URL-instabil); Rename per Drop+Recreate.
    """
    async with db_mod._async_session_maker() as session:
        result = await session.execute(select(Project).where(Project.id == project_id))
        p = result.scalar_one_or_none()
        if p is None:
            return None
        if name is not None:
            if not name.strip():
                raise ValueError("Projekt-Name darf nicht leer sein")
            p.name = name.strip()
        if description is not None:
            p.description = description.strip() or None
        if persona_overlay is not None:
            p.persona_overlay = json.dumps(persona_overlay) if persona_overlay else None
        p.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(p)
        return _project_to_dict(p)


async def archive_project(project_id: int) -> Optional[dict[str, Any]]:
    """Soft-delete. Datei-Eintraege bleiben erhalten."""
    async with db_mod._async_session_maker() as session:
        result = await session.execute(select(Project).where(Project.id == project_id))
        p = result.scalar_one_or_none()
        if p is None:
            return None
        p.is_archived = 1
        p.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(p)
        logger.info(f"[PROJECTS-194] archived id={p.id} slug={p.slug}")
        return _project_to_dict(p)


async def unarchive_project(project_id: int) -> Optional[dict[str, Any]]:
    async with db_mod._async_session_maker() as session:
        result = await session.execute(select(Project).where(Project.id == project_id))
        p = result.scalar_one_or_none()
        if p is None:
            return None
        p.is_archived = 0
        p.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(p)
        return _project_to_dict(p)


async def delete_project(project_id: int) -> bool:
    """Harte Loeschung — kaskadiert ueber project_files. Storage-Dateien
    werden NICHT entfernt (das uebernimmt ein separater Cleanup-Job, weil
    derselbe sha256 in einem anderen Projekt referenziert sein kann)."""
    async with db_mod._async_session_maker() as session:
        result = await session.execute(select(Project).where(Project.id == project_id))
        p = result.scalar_one_or_none()
        if p is None:
            return False
        # Cascade per SQL — kein ORM-Relation, also explizit.
        await session.execute(
            sa_text("DELETE FROM project_files WHERE project_id = :pid"),
            {"pid": project_id},
        )
        await session.delete(p)
        await session.commit()
        logger.info(f"[PROJECTS-194] hard-deleted id={project_id} slug={p.slug}")
        return True


# ---------------------------------------------------------------------------
# Datei-CRUD (Metadaten — Bytes-Upload kommt in P195/P196)
# ---------------------------------------------------------------------------


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def storage_path_for(project_slug: str, sha256: str, base_dir: Path) -> Path:
    """Pfad-Konvention: ``<base>/projects/<slug>/<sha-prefix>/<sha>``.

    Sha-Prefix (erste 2 Zeichen) als Sub-Verzeichnis verhindert, dass
    bei vielen Dateien ein einzelner Ordner zum Hotspot wird.
    """
    return base_dir / "projects" / project_slug / sha256[:2] / sha256


async def register_file(
    project_id: int,
    relative_path: str,
    sha256: str,
    size_bytes: int,
    storage_path: str,
    mime_type: Optional[str] = None,
) -> dict[str, Any]:
    """Registriert eine Datei (Metadaten-Eintrag). Caller ist dafuer
    verantwortlich, die Bytes vorher in ``storage_path`` abzulegen.
    """
    if not relative_path.strip():
        raise ValueError("relative_path darf nicht leer sein")
    if size_bytes < 0:
        raise ValueError("size_bytes darf nicht negativ sein")
    if len(sha256) != 64:
        raise ValueError(f"sha256 muss 64 Hex-Zeichen haben (got {len(sha256)})")

    async with db_mod._async_session_maker() as session:
        f = ProjectFile(
            project_id=project_id,
            relative_path=relative_path.strip(),
            sha256=sha256,
            size_bytes=size_bytes,
            mime_type=mime_type,
            storage_path=storage_path,
        )
        session.add(f)
        await session.commit()
        await session.refresh(f)
        return _file_to_dict(f)


async def list_files(project_id: int) -> list[dict[str, Any]]:
    async with db_mod._async_session_maker() as session:
        result = await session.execute(
            select(ProjectFile)
            .where(ProjectFile.project_id == project_id)
            .order_by(ProjectFile.relative_path)
        )
        return [_file_to_dict(f) for f in result.scalars().all()]


async def get_file(file_id: int) -> Optional[dict[str, Any]]:
    async with db_mod._async_session_maker() as session:
        result = await session.execute(select(ProjectFile).where(ProjectFile.id == file_id))
        f = result.scalar_one_or_none()
        return _file_to_dict(f) if f else None


async def delete_file(file_id: int) -> bool:
    async with db_mod._async_session_maker() as session:
        result = await session.execute(select(ProjectFile).where(ProjectFile.id == file_id))
        f = result.scalar_one_or_none()
        if f is None:
            return False
        await session.delete(f)
        await session.commit()
        return True


# ---------------------------------------------------------------------------
# Patch 196 (Phase 5a #4) — Datei-Upload-Helper
# ---------------------------------------------------------------------------


async def count_sha_references(sha256: str, exclude_file_id: Optional[int] = None) -> int:
    """Wie viele ``project_files``-Zeilen referenzieren denselben Inhalt?

    Wird vom Delete-Endpoint benutzt, um zu entscheiden ob die Bytes im
    Storage entfernt werden duerfen — wenn ein anderes Projekt denselben
    sha256 referenziert, bleiben die Bytes liegen und nur der DB-Eintrag
    geht weg.

    ``exclude_file_id`` blendet einen bestimmten Eintrag aus (typisch:
    den Eintrag, der gerade geloescht werden soll) — sonst muesste der
    Caller selbst nochmal -1 rechnen.
    """
    async with db_mod._async_session_maker() as session:
        stmt = select(ProjectFile).where(ProjectFile.sha256 == sha256)
        if exclude_file_id is not None:
            stmt = stmt.where(ProjectFile.id != exclude_file_id)
        result = await session.execute(stmt)
        return len(result.scalars().all())


def is_extension_blocked(filename: str, blocked: list[str]) -> bool:
    """Case-insensitiver Suffix-Check. ``filename`` darf bereits sanitized
    sein (Pfad-Teile entfernt) — die Funktion guckt nur auf das letzte
    ``.ext`` und vergleicht mit der Blacklist.
    """
    name = filename.lower()
    for ext in blocked:
        if name.endswith(ext.lower()):
            return True
    return False


def sanitize_relative_path(filename: str) -> str:
    """Macht aus einem hochgeladenen Filename einen sicheren ``relative_path``.

    - Trennzeichen werden auf ``/`` normalisiert
    - Path-Traversal-Komponenten (``..``, absolute Pfade, leere Segmente
      durch doppelte Slashes) werden gestrippt
    - Fuehrende ``/`` weg, Whitespace getrimmt
    - Leerer/nur-aus-Punkten-bestehender Filename → ``ValueError``

    Wirft bewusst statt einen Default-Namen zu vergeben — der Caller soll
    400 zurueckgeben und der User soll einen Filename liefern.
    """
    if not filename or not filename.strip():
        raise ValueError("Filename darf nicht leer sein")

    normalized = filename.replace("\\", "/").strip()
    parts = [p for p in normalized.split("/") if p and p not in (".", "..")]
    if not parts:
        raise ValueError(f"Ungueltiger Filename: {filename!r}")

    cleaned = "/".join(parts)
    if not cleaned or cleaned.replace(".", "") == "":
        raise ValueError(f"Ungueltiger Filename: {filename!r}")
    return cleaned
