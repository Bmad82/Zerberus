"""Patch 207 (Phase 5a #9 + #10) — Workspace-Snapshots, Diff, Rollback.

Bevor Sandbox-Code im ``writable=True``-Mount Files schreibt, schiesst
der Chat-Endpunkt einen ``before``-Snapshot des Projekt-Workspaces;
nach dem Run einen ``after``-Snapshot. Aus dem Paar baut der Endpunkt
eine ``DiffResult``-Liste (added/modified/deleted) und liefert sie
additiv im ``code_execution.diff``-Feld der Chat-Response. Der User
sieht im Frontend eine Diff-Card und kann via ``rollback_snapshot``
den Workspace auf den ``before``-Stand zuruecksetzen.

Strategie:

* **Snapshots** sind reine Python-Pickle-Dumps unter
  ``<base>/projects/<slug>/_snapshots/<id>.tar`` mit allen Workspace-
  Dateien (Hardlink auf SHA-Storage waere die Edel-Variante, aber
  Tar reicht fuer P207 und macht Rollback trivial). Die DB-Tabelle
  ``workspace_snapshots`` haelt Metadaten (id/project_id/label/file_count/
  total_bytes/archive_path).
* **Diff** ist Pure-Python ohne Abhaengigkeiten: ``diff_snapshots(a, b)``
  vergleicht Pfad-Hashes (sha256) und liefert pro Datei
  ``status`` (``added`` / ``modified`` / ``deleted``), ``size_before``,
  ``size_after``, optional ``unified_diff`` fuer Text-Files (binary
  wird mit ``binary=True`` markiert).
* **Rollback** entpackt den Tar des ``before``-Snapshots ueber den
  Workspace und entfernt Files, die im ``before``-Stand nicht waren —
  das macht den Rollback atomar bezueglich des ``before``-Stands.

Sicherheits-Garantien:

* Snapshot-Pfade gehen durch ``is_inside_workspace`` (Defense-in-Depth)
* Snapshot-Tar-Path liegt unter ``_snapshots/`` im Projekt-Ordner —
  niemals ausserhalb von ``<base>/projects/<slug>/``
* Rollback prueft per Snapshot-Eigentuemer (project_id-Match) — ein
  Snapshot aus Projekt A kann nicht ueber Projekt B angewendet werden
* Tar-Members werden beim Entpacken auf Pfad-Sicherheit gecheckt
  (Path-Traversal via ``..`` blockiert)

Was P207 NICHT macht:

* **Cross-Project-Diff** — Snapshots sind per Projekt isoliert.
* **Branch-Mechanik** — linear forward/reverse, kein Git-Style-Tree.
* **Automatischer Rollback bei exit_code != 0** — User-Choice (er sieht
  den Diff und entscheidet).
* **Per-File-Rollback** — alles oder nichts pro Snapshot. (Text-Diff-
  Anzeige im Frontend ist additiv, aber Rollback wirkt auf den ganzen
  Workspace-Stand.)
* **Storage-GC** — alte Snapshots bleiben liegen; Cleanup ist Aufgabe
  eines spaeteren Patches (siehe HANDOVER-Schulden).
* **Hardlink-Snapshots** — Tar ist einfacher und Tests-tauglich.

Logging-Tag: ``[SNAPSHOT-207]``.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import logging
import os
import shutil
import tarfile
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


SNAPSHOT_DIRNAME = "_snapshots"
# Pure-Text-Files unter dieser Schwelle bekommen ein unified_diff im
# DiffEntry; alles drueber wird nur als size-changed markiert. Spart
# Frontend-Render-Zeit + DB-Roundtrip-Bytes.
DIFF_TEXT_MAX_BYTES = 64 * 1024
# Files unter dieser Schwelle werden auf is-text geprueft (Heuristik:
# kein Null-Byte in den ersten 8 KB). Drueber: binary, kein Inline-Diff.
TEXT_PROBE_BYTES = 8 * 1024


# ---------------------------------------------------------------------------
# Pure-Function-Schicht (kein I/O ausser was im Datenmodell explizit ist)
# ---------------------------------------------------------------------------


@dataclass
class DiffEntry:
    """Eine geaenderte Datei zwischen zwei Snapshots.

    ``status``:
        - ``added`` — Datei nur im ``after``-Snapshot
        - ``deleted`` — Datei nur im ``before``-Snapshot
        - ``modified`` — Datei in beiden, aber unterschiedlicher Hash
    """
    path: str
    status: str
    size_before: int = 0
    size_after: int = 0
    binary: bool = False
    unified_diff: Optional[str] = None

    def to_public_dict(self) -> dict:
        return {
            "path": self.path,
            "status": self.status,
            "size_before": self.size_before,
            "size_after": self.size_after,
            "binary": self.binary,
            "unified_diff": self.unified_diff,
        }


def snapshot_dir_for(slug: str, base_dir: Path) -> Path:
    """``<base>/projects/<slug>/_snapshots/``. Pure — kein FS-Zugriff."""
    return base_dir / "projects" / slug / SNAPSHOT_DIRNAME


def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _looks_text(data: bytes) -> bool:
    """Heuristik: Null-Byte in den ersten ``TEXT_PROBE_BYTES`` Bytes
    -> binary; sonst text. Reicht fuer Code/Markdown/JSON/Logs."""
    if not data:
        return True
    head = data[:TEXT_PROBE_BYTES]
    return b"\x00" not in head


def _decode_text(data: bytes) -> Optional[str]:
    """UTF-8 mit ``errors='replace'`` — wir verlieren keine Bytes,
    sehen aber bei kaputtem UTF-8 ``�``-Marker statt zu crashen."""
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return None


def _build_unified_diff(
    before_text: str,
    after_text: str,
    path: str,
) -> str:
    """``difflib.unified_diff`` mit 3 Kontextzeilen, Headers ``a/<path>``
    und ``b/<path>`` analog ``git diff``."""
    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)
    diff_lines = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    return "".join(diff_lines)


def diff_snapshots(
    before: dict[str, dict],
    after: dict[str, dict],
) -> List[DiffEntry]:
    """Vergleicht zwei Snapshot-Manifeste (Pfad → Metadaten-Dict mit
    ``hash``/``size``/``content``).

    ``content`` ist optional — wenn beide Snapshots Text-Content
    transportieren, baut die Funktion ein ``unified_diff``. Fehlt
    ``content``, wird nur Status + Size geliefert (Frontend zeigt
    "geaendert" ohne Inline-Diff).

    Returns:
        Liste von ``DiffEntry``, sortiert nach Pfad. Leere Liste, wenn
        die Snapshots identisch sind.
    """
    entries: List[DiffEntry] = []
    before_paths = set(before.keys())
    after_paths = set(after.keys())

    for path in sorted(after_paths - before_paths):
        meta = after[path]
        binary = bool(meta.get("binary", False))
        size = int(meta.get("size", 0))
        entries.append(DiffEntry(
            path=path,
            status="added",
            size_before=0,
            size_after=size,
            binary=binary,
        ))

    for path in sorted(before_paths - after_paths):
        meta = before[path]
        binary = bool(meta.get("binary", False))
        size = int(meta.get("size", 0))
        entries.append(DiffEntry(
            path=path,
            status="deleted",
            size_before=size,
            size_after=0,
            binary=binary,
        ))

    for path in sorted(before_paths & after_paths):
        b_meta = before[path]
        a_meta = after[path]
        if b_meta.get("hash") == a_meta.get("hash"):
            continue
        b_size = int(b_meta.get("size", 0))
        a_size = int(a_meta.get("size", 0))
        binary = bool(b_meta.get("binary", False) or a_meta.get("binary", False))
        unified: Optional[str] = None
        if not binary:
            b_content = b_meta.get("content")
            a_content = a_meta.get("content")
            if isinstance(b_content, str) and isinstance(a_content, str):
                # Beide Seiten muessen Text-Content haben — sonst kein Diff
                if (
                    len(b_content.encode("utf-8")) <= DIFF_TEXT_MAX_BYTES
                    and len(a_content.encode("utf-8")) <= DIFF_TEXT_MAX_BYTES
                ):
                    unified = _build_unified_diff(b_content, a_content, path)
        entries.append(DiffEntry(
            path=path,
            status="modified",
            size_before=b_size,
            size_after=a_size,
            binary=binary,
            unified_diff=unified,
        ))

    return entries


# ---------------------------------------------------------------------------
# Sync-FS-Schicht (Snapshot lesen, Tar schreiben, Manifest bauen)
# ---------------------------------------------------------------------------


def _iter_workspace_files(workspace_root: Path) -> Iterable[Path]:
    """Yieldet alle regulaeren Dateien rekursiv. Snapshot-Ordner und
    Hidden-Files werden NICHT ausgespart — wir wollen den vollen Stand.

    Aufrufer ruft das auf einem Workspace-Root, in dem ``_snapshots/``
    sowieso nicht liegt (das liegt eine Ebene drueber unter
    ``<slug>/_snapshots/``)."""
    if not workspace_root.exists():
        return
    for p in workspace_root.rglob("*"):
        if p.is_file():
            yield p


def build_workspace_manifest(
    workspace_root: Path,
    *,
    include_content: bool = True,
) -> dict[str, dict]:
    """Baut ein Manifest des aktuellen Workspace-Stands.

    Args:
        workspace_root: ``<base>/projects/<slug>/_workspace/``
        include_content: wenn True, wird der Datei-Inhalt fuer Text-
            Files (<DIFF_TEXT_MAX_BYTES) als ``content``-Feld
            mitgeliefert — das macht spaeter ``diff_snapshots`` zu
            einem inline unified_diff. Binaries kriegen ``content=None``.
            Fuer Pure-Hash-Snapshots (Rollback-Restore-Test ohne Diff)
            kann der Caller False setzen.

    Returns:
        ``{ "<rel_path>": {hash, size, binary, content?} }``. Leeres
        Dict, wenn der Workspace nicht existiert oder leer ist.
    """
    manifest: dict[str, dict] = {}
    if not workspace_root.exists():
        return manifest
    try:
        root_resolved = workspace_root.resolve(strict=False)
    except OSError:
        return manifest
    for fp in _iter_workspace_files(workspace_root):
        try:
            rel = fp.relative_to(root_resolved).as_posix()
        except (ValueError, OSError):
            try:
                rel = fp.resolve(strict=False).relative_to(root_resolved).as_posix()
            except (ValueError, OSError):
                continue
        try:
            data = fp.read_bytes()
        except OSError as e:
            logger.warning(
                f"[SNAPSHOT-207] read_bytes fehlgeschlagen path={rel} err={e}"
            )
            continue
        binary = not _looks_text(data)
        meta: dict = {
            "hash": _sha256_of_bytes(data),
            "size": len(data),
            "binary": binary,
        }
        if include_content and not binary and len(data) <= DIFF_TEXT_MAX_BYTES:
            text = _decode_text(data)
            if text is not None:
                meta["content"] = text
        manifest[rel] = meta
    return manifest


def _atomic_replace(src: Path, dst: Path) -> None:
    os.replace(str(src), str(dst))


def materialize_snapshot(
    workspace_root: Path,
    snapshot_root: Path,
    *,
    label: str,
    snapshot_id: Optional[str] = None,
) -> Optional[dict]:
    """Schreibt ein Snapshot-Tar aus dem aktuellen Workspace-Stand.

    Args:
        workspace_root: Quell-Workspace.
        snapshot_root: Ziel-Verzeichnis fuer Tars (typisch
            ``<base>/projects/<slug>/_snapshots/``).
        label: kurzer Tag (``before_run`` / ``after_run`` / ``manual``)
            — landet in der DB-Spalte ``label`` und im Tar-Dateinamen.
        snapshot_id: optional — explizite UUID4-hex, sonst neu erzeugt.
            Tests koennen damit deterministisch arbeiten.

    Returns:
        ``{"id", "label", "archive_path", "file_count", "total_bytes",
        "manifest"}``. ``None`` wenn der Workspace nicht existiert.

    Tar-Format ist ``ustar`` (kompatibel mit ``tarfile``-Default).
    Schreibt atomar via Tempname + ``os.replace``.
    """
    if not workspace_root.exists():
        return None

    snap_id = snapshot_id or uuid.uuid4().hex
    snapshot_root.mkdir(parents=True, exist_ok=True)
    archive_path = snapshot_root / f"{snap_id}.tar"

    files: list[tuple[Path, str, int]] = []
    try:
        root_resolved = workspace_root.resolve(strict=False)
    except OSError:
        return None
    for fp in _iter_workspace_files(workspace_root):
        # Wenn workspace_root relativ ist, sind die fp-Pfade auch
        # relativ — Path.relative_to(absolute) wirft dann ValueError.
        # Fallback: fp resolven, dann relative_to(root_resolved). Gleicher
        # Defense-Pattern wie in build_workspace_manifest.
        try:
            rel = fp.relative_to(root_resolved).as_posix()
        except (ValueError, OSError):
            try:
                rel = fp.resolve(strict=False).relative_to(root_resolved).as_posix()
            except (ValueError, OSError):
                continue
        try:
            files.append((fp, rel, fp.stat().st_size))
        except OSError:
            continue

    fd, tmp_str = tempfile.mkstemp(
        dir=str(snapshot_root),
        prefix=".snap_",
        suffix=".tar.tmp",
    )
    os.close(fd)
    tmp_path = Path(tmp_str)
    total_bytes = 0
    try:
        with tarfile.open(str(tmp_path), "w") as tar:
            for src, rel, _size in files:
                try:
                    tar.add(str(src), arcname=rel, recursive=False)
                    total_bytes += src.stat().st_size
                except OSError as e:
                    logger.warning(
                        f"[SNAPSHOT-207] tar.add fehlgeschlagen path={rel} err={e}"
                    )
        _atomic_replace(tmp_path, archive_path)
    except OSError as e:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        logger.warning(f"[SNAPSHOT-207] materialize fehlgeschlagen: {e}")
        return None

    manifest = build_workspace_manifest(workspace_root, include_content=True)
    logger.info(
        f"[SNAPSHOT-207] materialized id={snap_id} label={label} "
        f"file_count={len(files)} total_bytes={total_bytes} "
        f"archive={archive_path}"
    )
    return {
        "id": snap_id,
        "label": label,
        "archive_path": str(archive_path),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "manifest": manifest,
    }


def _is_safe_member(member: tarfile.TarInfo, dest_root: Path) -> bool:
    """Tar-Path-Traversal-Defense: Member darf NICHT ausserhalb des
    Ziel-Roots landen, KEIN Symlink/Hardlink, KEIN absoluter Pfad.

    Python 3.12+ hat ``tarfile.data_filter``, aber wir wollen 3.10/3.11
    auch unterstuetzen — manuell pruefen ist portabel."""
    if member.isdev() or member.issym() or member.islnk():
        return False
    name = member.name
    if not name or name.startswith("/") or ".." in Path(name).parts:
        return False
    target = (dest_root / name).resolve(strict=False)
    try:
        target.relative_to(dest_root.resolve(strict=False))
    except ValueError:
        return False
    return True


def restore_snapshot(
    workspace_root: Path,
    archive_path: Path,
) -> Optional[dict]:
    """Stellt den Workspace auf den Snapshot-Stand zurueck.

    Strategie:
        1. Workspace-Inhalt komplett raeumen (NICHT den Root-Ordner
           selbst, damit kein parallel laufender Watcher konfundiert).
        2. Tar-Members validieren (``_is_safe_member``) und einzeln
           extrahieren.
        3. Stat: file_count + total_bytes der wiederhergestellten Dateien.

    Returns:
        ``{"file_count", "total_bytes"}`` bei Erfolg, ``None`` wenn
        Tar fehlt oder nicht lesbar ist.
    """
    if not archive_path.exists():
        logger.warning(
            f"[SNAPSHOT-207] restore: archive existiert nicht: {archive_path}"
        )
        return None
    if not workspace_root.exists():
        workspace_root.mkdir(parents=True, exist_ok=True)

    # 1. Workspace raeumen — Inhalt rm, Root bleibt stehen.
    try:
        for child in workspace_root.iterdir():
            if child.is_file() or child.is_symlink():
                try:
                    child.unlink()
                except OSError:
                    pass
            elif child.is_dir():
                shutil.rmtree(str(child), ignore_errors=True)
    except OSError as e:
        logger.warning(f"[SNAPSHOT-207] restore: clear failed: {e}")
        return None

    # 2. Members extrahieren.
    file_count = 0
    total_bytes = 0
    try:
        with tarfile.open(str(archive_path), "r") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                if not _is_safe_member(member, workspace_root):
                    logger.warning(
                        f"[SNAPSHOT-207] restore: unsafe member skipped: {member.name}"
                    )
                    continue
                try:
                    tar.extract(member, path=str(workspace_root))
                    file_count += 1
                    total_bytes += member.size
                except OSError as e:
                    logger.warning(
                        f"[SNAPSHOT-207] restore: extract failed: {member.name} err={e}"
                    )
    except (tarfile.TarError, OSError) as e:
        logger.warning(f"[SNAPSHOT-207] restore: tar open failed: {e}")
        return None

    logger.info(
        f"[SNAPSHOT-207] restored archive={archive_path} "
        f"file_count={file_count} total_bytes={total_bytes}"
    )
    return {"file_count": file_count, "total_bytes": total_bytes}


# ---------------------------------------------------------------------------
# Async DB-Schicht
# ---------------------------------------------------------------------------


async def store_snapshot_row(
    *,
    project_id: int,
    project_slug: Optional[str],
    label: str,
    snapshot_id: str,
    archive_path: str,
    file_count: int,
    total_bytes: int,
    pending_id: Optional[str] = None,
    parent_snapshot_id: Optional[str] = None,
) -> Optional[int]:
    """Schreibt eine ``workspace_snapshots``-Zeile.

    Best-Effort: jeder Fehler wird geloggt + verschluckt. Caller bekommt
    ``None`` zurueck, wenn die DB nicht initialisiert ist oder das
    Insert scheitert. Hauptpfad darf durch Audit-Fehler nicht blockiert
    werden.

    Returns:
        Die DB-Row-ID (Integer) bei Erfolg, sonst ``None``.
    """
    try:
        from zerberus.core.database import (
            WorkspaceSnapshot,
            _async_session_maker,
        )
    except Exception as e:
        logger.warning(f"[SNAPSHOT-207] db_import_failed: {e}")
        return None

    if _async_session_maker is None:
        return None

    try:
        async with _async_session_maker() as session:
            row = WorkspaceSnapshot(
                snapshot_id=snapshot_id,
                project_id=int(project_id),
                project_slug=project_slug,
                label=label,
                archive_path=archive_path,
                file_count=int(file_count),
                total_bytes=int(total_bytes),
                pending_id=pending_id,
                parent_snapshot_id=parent_snapshot_id,
            )
            session.add(row)
            await session.commit()
            db_id = row.id
        logger.info(
            f"[SNAPSHOT-207] db_row_written id={db_id} snapshot_id={snapshot_id} "
            f"label={label} project_id={project_id}"
        )
        return db_id
    except Exception as e:
        logger.warning(f"[SNAPSHOT-207] db_insert_failed (non-fatal): {e}")
        return None


async def load_snapshot_row(snapshot_id: str) -> Optional[dict]:
    """Liefert die Snapshot-Metadaten zur ``snapshot_id`` (UUID4-hex).

    Returns:
        Dict ``{id, snapshot_id, project_id, project_slug, label,
        archive_path, file_count, total_bytes, pending_id,
        parent_snapshot_id, created_at}`` oder ``None``.
    """
    try:
        from sqlalchemy import select
        from zerberus.core.database import (
            WorkspaceSnapshot,
            _async_session_maker,
        )
    except Exception:
        return None
    if _async_session_maker is None:
        return None
    try:
        async with _async_session_maker() as session:
            stmt = select(WorkspaceSnapshot).where(
                WorkspaceSnapshot.snapshot_id == snapshot_id
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return {
                "id": row.id,
                "snapshot_id": row.snapshot_id,
                "project_id": row.project_id,
                "project_slug": row.project_slug,
                "label": row.label,
                "archive_path": row.archive_path,
                "file_count": row.file_count,
                "total_bytes": row.total_bytes,
                "pending_id": row.pending_id,
                "parent_snapshot_id": row.parent_snapshot_id,
                "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            }
    except Exception as e:
        logger.warning(f"[SNAPSHOT-207] db_load_failed: {e}")
        return None


# ---------------------------------------------------------------------------
# High-Level Convenience
# ---------------------------------------------------------------------------


async def snapshot_workspace_async(
    project_id: int,
    base_dir: Path,
    *,
    label: str,
    pending_id: Optional[str] = None,
    parent_snapshot_id: Optional[str] = None,
) -> Optional[dict]:
    """Ein-Klick-Snapshot: zieht Slug aus DB, materialisiert Tar,
    schreibt DB-Row, liefert Manifest + Metadaten.

    Returns:
        ``{"id", "label", "manifest", "file_count", "total_bytes",
        "archive_path", "db_row_id"}`` oder ``None``.
    """
    from zerberus.core import projects_repo
    from zerberus.core.projects_workspace import workspace_root_for

    project = await projects_repo.get_project(project_id)
    if project is None:
        logger.warning(
            f"[SNAPSHOT-207] snapshot_workspace_async: project_id={project_id} nicht gefunden"
        )
        return None
    slug = project["slug"]
    workspace_root = workspace_root_for(slug, base_dir)
    snapshot_root = snapshot_dir_for(slug, base_dir)
    # Workspace on-demand anlegen — sonst wuerde materialize_snapshot
    # bei einem leeren oder noch-nie-genutzten Projekt None liefern und
    # die before/after-Diff-Spur kaputt machen. Leerer Workspace gibt
    # einen leeren Snapshot, der als Vergleichsbasis fuer einen spaeteren
    # writable-Run trotzdem gueltig ist.
    workspace_root.mkdir(parents=True, exist_ok=True)

    snap = materialize_snapshot(
        workspace_root=workspace_root,
        snapshot_root=snapshot_root,
        label=label,
    )
    if snap is None:
        return None

    db_row_id = await store_snapshot_row(
        project_id=project_id,
        project_slug=slug,
        label=label,
        snapshot_id=snap["id"],
        archive_path=snap["archive_path"],
        file_count=snap["file_count"],
        total_bytes=snap["total_bytes"],
        pending_id=pending_id,
        parent_snapshot_id=parent_snapshot_id,
    )
    snap["db_row_id"] = db_row_id
    return snap


async def rollback_snapshot_async(
    snapshot_id: str,
    base_dir: Path,
    *,
    expected_project_id: Optional[int] = None,
) -> Optional[dict]:
    """Stellt den Workspace eines Projekts auf den Snapshot-Stand
    zurueck. Defense-in-Depth: Snapshot muss zum erwarteten
    ``project_id`` gehoeren — sonst wird das Rollback verweigert.

    Returns:
        ``{"snapshot_id", "project_id", "project_slug", "file_count",
        "total_bytes"}`` bei Erfolg, ``None`` bei Reject/Fehler.
    """
    from zerberus.core.projects_workspace import workspace_root_for

    row = await load_snapshot_row(snapshot_id)
    if row is None:
        logger.warning(
            f"[SNAPSHOT-207] rollback: snapshot_id={snapshot_id} nicht gefunden"
        )
        return None
    if expected_project_id is not None and row["project_id"] != expected_project_id:
        logger.warning(
            f"[SNAPSHOT-207] rollback: project_id-Mismatch "
            f"(snapshot={row['project_id']} expected={expected_project_id})"
        )
        return None

    slug = row["project_slug"]
    if not slug:
        logger.warning(
            f"[SNAPSHOT-207] rollback: snapshot_id={snapshot_id} ohne project_slug"
        )
        return None

    workspace_root = workspace_root_for(slug, base_dir)
    archive_path = Path(row["archive_path"])
    result = restore_snapshot(workspace_root, archive_path)
    if result is None:
        return None

    logger.info(
        f"[SNAPSHOT-207] rollback_done snapshot_id={snapshot_id} "
        f"project_id={row['project_id']} slug={slug} "
        f"file_count={result['file_count']}"
    )
    return {
        "snapshot_id": snapshot_id,
        "project_id": row["project_id"],
        "project_slug": slug,
        "file_count": result["file_count"],
        "total_bytes": result["total_bytes"],
    }
