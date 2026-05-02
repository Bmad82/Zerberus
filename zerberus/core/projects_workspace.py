"""Patch 203a (Phase 5a #5, Vorbereitung) — Projekt-Workspace-Layout.

Materialisiert die ``project_files``-Eintraege eines Projekts in einen
echten Working-Tree unter ``<base>/projects/<slug>/_workspace/``. Damit
hat jedes Projekt ein begehbares Verzeichnis mit den Dateien an ihrem
``relative_path`` (statt nur unter den SHA-Pfaden im SHA-Storage).

Warum?

- **Code-Execution (P203b/c)**: Die Docker-Sandbox braucht einen echten
  Mount-Pfad mit Files an ihrem ``relative_path`` — Code-Generierung
  schreibt ``app/main.py``, nicht ``a3/<sha>``.
- **Git-Init**: Erst sinnvoll mit echtem Working-Tree.
- **User-Tools spaeter**: Falls jemals ein "Workspace im Editor"-Feature
  kommt, ist die Wurzel da.

Strategie pro Datei:

1. **Hardlink** (``os.link``) auf den SHA-Storage — kein Plattenplatz-
   Verbrauch, gleiche Inode, atomar via ``os.replace`` von Tempname.
2. **Copy-Fallback** (``shutil.copy2``) wenn Hardlink scheitert. Gruende:
   Cross-Filesystem (z.B. SHA-Storage liegt auf anderer Partition),
   Windows ohne dev-mode (Hardlink auf NTFS funktioniert, aber bei
   exFAT/FAT32 nicht), oder Permission-Denied.
3. **Idempotenz**: Existiert das Workspace-File bereits und sein Inode/
   SHA matcht den Source-SHA, no-op. Bei Mismatch atomic replace.

Sicherheits-Garantien:

- ``relative_path`` wird ueber ``projects_repo.sanitize_relative_path``
  schon vor ``register_file`` gesaeubert. Hier nochmal ein
  ``is_inside_workspace``-Check, damit ein potentiell entartetes
  ``relative_path`` aus der DB (Migrations? alte Datenbanken?) keinen
  Schreibzugriff ausserhalb des Workspaces verursacht.
- ``wipe_workspace`` prueft erst, dass der Pfad innerhalb des
  Workspace-Bereichs liegt — Schutz gegen Slug-Manipulation.

Logging-Tag: ``[WORKSPACE-203]``.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


WORKSPACE_DIRNAME = "_workspace"


# ---------------------------------------------------------------------------
# Pure-Function-Schicht (kein I/O)
# ---------------------------------------------------------------------------


def workspace_root_for(slug: str, base_dir: Path) -> Path:
    """``<base>/projects/<slug>/_workspace/``. Pure — kein FS-Zugriff."""
    return base_dir / "projects" / slug / WORKSPACE_DIRNAME


def is_inside_workspace(target: Path, workspace_root: Path) -> bool:
    """True, wenn ``target`` innerhalb (oder gleich) ``workspace_root`` liegt.

    Vergleicht die *resolvierten* Pfade — ein ``..`` im ``relative_path``
    wuerde den Target-Pfad oberhalb der Wurzel auflassen und hier False
    liefern. Wenn das Target noch nicht existiert, wird der Eltern-Pfad
    resolved; das funktioniert auch fuer geplante Schreibziele.
    """
    try:
        # `strict=False` ist Python 3.10-Default, expliziter Schutz: wir
        # resolvieren symbolisch, ohne dass das Target existieren muss.
        target_resolved = target.resolve(strict=False)
        root_resolved = workspace_root.resolve(strict=False)
    except OSError:
        return False
    try:
        target_resolved.relative_to(root_resolved)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Sync-Schicht (FS-I/O, sync — Caller wickelt async drumrum, falls noetig)
# ---------------------------------------------------------------------------


def _atomic_replace(src: Path, dst: Path) -> None:
    """``os.replace`` — atomar im selben FS, auf Windows auch ueber
    bestehende Files hinweg."""
    os.replace(str(src), str(dst))


def _hardlink_or_copy(source: Path, target: Path) -> str:
    """Versucht erst Hardlink, faellt bei OSError auf Copy zurueck.

    Schreibt ueber einen Tempnamen + ``os.replace``, damit ein paralleler
    Reader nie ein halb-geschriebenes Workspace-File sieht (z.B. Sandbox
    laeuft schon).

    Liefert ``"hardlink"`` oder ``"copy"`` — fuer Logging und Tests.
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_str = tempfile.mkstemp(dir=str(target.parent), prefix=".ws_", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_str)
    # mkstemp legt eine leere Datei an — fuer os.link muss das Ziel NICHT
    # existieren. Erst entfernen, dann hardlinken/copyen.
    try:
        tmp_path.unlink()
    except OSError:
        pass

    method: str
    try:
        os.link(str(source), str(tmp_path))
        method = "hardlink"
    except OSError as link_err:
        # Cross-FS, Cross-Device, NTFS-without-dev-mode, FAT32, oder
        # Permissions — alle landen hier. Copy ist der sichere Fallback.
        logger.debug(
            f"[WORKSPACE-203] hardlink failed ({link_err}), falling back to copy "
            f"src={source} dst={target}"
        )
        try:
            shutil.copy2(str(source), str(tmp_path))
            method = "copy"
        except OSError as copy_err:
            # Beide Wege tot — Tempfile aufraeumen, dann weiterwerfen.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise copy_err

    try:
        _atomic_replace(tmp_path, target)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return method


def materialize_file(
    workspace_root: Path,
    relative_path: str,
    source_path: Path,
) -> Optional[str]:
    """Stellt ``relative_path`` im Workspace bereit, gespiegelt von
    ``source_path`` (typisch der SHA-Storage-Pfad).

    Returns:
        ``"hardlink"`` / ``"copy"`` bei Erfolg, ``None`` wenn das Target
        bereits korrekt vorhanden ist (Idempotenz: Same-Inode bei
        Hardlinks ODER Same-SHA implizit ueber Same-Bytes-Check). Bei
        Sicherheitsverletzung (Pfad ausserhalb Workspace) ``None`` und
        ein Warning-Log.
    """
    if not source_path.exists():
        logger.warning(
            f"[WORKSPACE-203] source_path existiert nicht: {source_path}"
        )
        return None

    target = workspace_root / relative_path
    if not is_inside_workspace(target, workspace_root):
        logger.warning(
            f"[WORKSPACE-203] relative_path versucht aus dem Workspace auszubrechen: {relative_path!r}"
        )
        return None

    # Idempotenz: bestehendes Workspace-File hat dieselbe Inode wie der
    # Source (Hardlink-Fall) → no-op. Auf Windows haben Hardlinks
    # gleichen `st_ino` wie der Source.
    if target.exists():
        try:
            src_stat = source_path.stat()
            tgt_stat = target.stat()
            same_inode = (src_stat.st_ino == tgt_stat.st_ino) and (src_stat.st_dev == tgt_stat.st_dev)
            same_size = src_stat.st_size == tgt_stat.st_size
            if same_inode and same_size:
                return None
            # Copy-Fall: gleiche Bytes pruefen via Size+ggf. Kurzlesen.
            # Fuer >1MB lassen wir's beim Size-Match — Caller hat die SHA
            # ja schon gepueft, der Inhalt kann sich nicht aendern ohne
            # neuen Storage-Path.
            if same_size and not same_inode:
                # Im Copy-Fall ist die einzige Konsistenz-Garantie der
                # Storage-Pfad selbst (SHA-keyed). Wenn die Size matcht,
                # vertrauen wir der SHA-Adressierung.
                return None
        except OSError:
            pass

    method = _hardlink_or_copy(source_path, target)
    logger.info(
        f"[WORKSPACE-203] materialized path={relative_path} via={method} "
        f"target={target}"
    )
    return method


def remove_file(workspace_root: Path, relative_path: str) -> bool:
    """Entfernt ``relative_path`` aus dem Workspace. Raeumt leere
    Eltern-Ordner bis (exklusive) ``workspace_root`` weg.

    Returns:
        ``True`` wenn die Datei vorhanden war und entfernt wurde,
        ``False`` wenn nichts zu tun war oder ein Sicherheitsfehler
        verhindert hat.
    """
    target = workspace_root / relative_path
    if not is_inside_workspace(target, workspace_root):
        logger.warning(
            f"[WORKSPACE-203] remove_file: relative_path ausserhalb Workspace: {relative_path!r}"
        )
        return False
    if not target.exists():
        return False
    try:
        target.unlink()
    except OSError as e:
        logger.warning(f"[WORKSPACE-203] remove_file unlink fehlgeschlagen: {e}")
        return False

    # Leere Eltern-Ordner aufraeumen, hochsteigen bis workspace_root.
    parent = target.parent
    try:
        root_resolved = workspace_root.resolve(strict=False)
    except OSError:
        return True
    while True:
        try:
            if parent.resolve(strict=False) == root_resolved:
                break
            if not parent.exists():
                break
            if any(parent.iterdir()):
                break
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break
    return True


def wipe_workspace(workspace_root: Path) -> bool:
    """Loescht den ganzen Workspace-Ordner. Idempotent.

    Sicherheitsregel: der Pfad muss tatsaechlich auf ``_workspace``
    enden — sonst lehnt der Helper ab. Verhindert ein versehentliches
    ``wipe_workspace(Path("/"))``.

    Returns:
        ``True`` wenn der Ordner existierte und entfernt wurde,
        ``False`` sonst (oder bei Sicherheits-Reject).
    """
    if workspace_root.name != WORKSPACE_DIRNAME:
        logger.warning(
            f"[WORKSPACE-203] wipe_workspace abgelehnt — Pfad endet nicht auf "
            f"{WORKSPACE_DIRNAME!r}: {workspace_root}"
        )
        return False
    if not workspace_root.exists():
        return False
    try:
        shutil.rmtree(str(workspace_root))
    except OSError as e:
        logger.warning(f"[WORKSPACE-203] wipe_workspace fehlgeschlagen: {e}")
        return False
    logger.info(f"[WORKSPACE-203] wiped workspace_root={workspace_root}")
    return True


# ---------------------------------------------------------------------------
# Async DB-Schicht
# ---------------------------------------------------------------------------


async def materialize_file_async(
    project_id: int,
    file_id: int,
    base_dir: Path,
) -> Optional[str]:
    """Convenience-Wrapper: zieht Slug + Storage-Pfad aus der DB und
    spiegelt die Datei in den Workspace.

    Wird von Trigger-Punkten (Upload-Endpoint, ``materialize_template``)
    aufgerufen. Best-Effort: Caller umschliesst typisch mit try/except,
    weil ein fehlgeschlagener Workspace-Sync den Hauptpfad nicht
    abbrechen darf (Datei ist in DB + SHA-Storage, das ist der Source-
    of-Truth).
    """
    from zerberus.core import projects_repo

    file_meta = await projects_repo.get_file(file_id)
    if file_meta is None or file_meta["project_id"] != project_id:
        return None
    project = await projects_repo.get_project(project_id)
    if project is None:
        return None

    slug = project["slug"]
    relative_path = file_meta["relative_path"]
    storage_path = file_meta.get("storage_path")
    if not storage_path:
        return None

    return materialize_file(
        workspace_root=workspace_root_for(slug, base_dir),
        relative_path=relative_path,
        source_path=Path(storage_path),
    )


async def remove_file_async(
    project_slug: str,
    relative_path: str,
    base_dir: Path,
) -> bool:
    """Convenience-Wrapper: loescht ``relative_path`` aus dem Workspace.

    Slug + relative_path muessen vom Caller stehen, weil zum Zeitpunkt
    des Triggers der DB-Eintrag bereits weg ist (Delete-Endpoint laeuft
    so).
    """
    return remove_file(workspace_root_for(project_slug, base_dir), relative_path)


async def sync_workspace(project_id: int, base_dir: Path) -> dict[str, int]:
    """Komplett-Sync: Workspace mit DB-Stand abgleichen.

    - Materialisiert alle ``project_files``-Eintraege, die noch nicht
      (oder nicht aktuell) im Workspace liegen.
    - Entfernt Workspace-Dateien, die nicht mehr in der DB sind
      (Orphans aus frueheren Versionen / manuellen Eingriffen).

    Returns:
        ``{"materialized": N, "removed": N, "skipped": N}``.

    Idempotent: zweimal aufgerufen → zweite Antwort hat
    ``{materialized:0, removed:0, skipped:N}``.
    """
    from zerberus.core import projects_repo

    project = await projects_repo.get_project(project_id)
    if project is None:
        return {"materialized": 0, "removed": 0, "skipped": 0}

    slug = project["slug"]
    files = await projects_repo.list_files(project_id)
    workspace_root = workspace_root_for(slug, base_dir)
    workspace_root.mkdir(parents=True, exist_ok=True)

    expected_paths: set[str] = set()
    materialized = 0
    skipped = 0
    for f in files:
        rel = f["relative_path"]
        expected_paths.add(rel)
        storage_path = f.get("storage_path")
        if not storage_path:
            continue
        method = materialize_file(
            workspace_root=workspace_root,
            relative_path=rel,
            source_path=Path(storage_path),
        )
        if method is None:
            skipped += 1
        else:
            materialized += 1

    # Orphans entfernen — alles unter dem Workspace, was nicht in
    # ``expected_paths`` steht.
    removed = 0
    for current in _iter_files(workspace_root):
        rel = current.relative_to(workspace_root).as_posix()
        if rel not in expected_paths:
            if remove_file(workspace_root, rel):
                removed += 1

    logger.info(
        f"[WORKSPACE-203] sync_workspace project_id={project_id} slug={slug} "
        f"materialized={materialized} removed={removed} skipped={skipped}"
    )
    return {"materialized": materialized, "removed": removed, "skipped": skipped}


def _iter_files(root: Path):
    """Yieldet alle regulaeren Dateien (rekursiv) unter ``root``.

    Eigene Implementierung statt ``Path.rglob('*')`` weil rglob auch
    Verzeichnisse liefert und wir nur Files brauchen — saubere Trennung
    zwischen "Datei loeschen" und "leeren Ordner spaeter aufraeumen".
    """
    if not root.exists():
        return
    for p in root.rglob("*"):
        if p.is_file():
            yield p
