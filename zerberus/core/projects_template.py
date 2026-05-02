"""Patch 198 (Phase 5a #2) — Template-Generierung beim Anlegen.

Ein neu angelegtes Projekt soll nicht leer starten. ``materialize_template``
legt eine Mindest-Skelett-Struktur ab: Eine Projekt-Bibel
(``ZERBERUS_<SLUG>.md``) und eine ``README.md``. Die Files landen im
SHA-Storage (gleicher Pfad wie P196-Uploads:
``<base>/projects/<slug>/<sha[:2]>/<sha>``) und werden ueber
``projects_repo.register_file`` als ``project_files``-Eintrag registriert,
sodass sie in der Hel-Datei-Liste, im RAG-Index (P199) und in der
Code-Execution-Pipeline (P200) ohne Sonderpfad sichtbar sind.

Pure-Python-String-Templates (kein Jinja, weil das den Stack nicht
rechtfertigt — wir haben kein Jinja als Dependency und der Bedarf ist
trivial). Render-Funktionen sind synchron + I/O-frei und damit unit-bar;
die Persistenz (Bytes schreiben + DB-Registrierung) liegt in der async
``materialize_template``.

Idempotenz: Existierende ``relative_path``-Eintraege werden NICHT
ueberschrieben. Wenn der User in einer frueheren Session schon eigene
Inhalte angelegt hat, bleiben die unangetastet. Der Helper liefert die
Liste der TATSAECHLICH neu angelegten Files zurueck — leer, wenn alle
Templates schon existieren.

Git-Init bewusst weggelassen: Der SHA-Storage ist kein Working-Tree (Bytes
liegen unter Hash-Pfaden, nicht unter ``relative_path``). ``git init``
ergibt erst Sinn mit einem echten ``_workspace/``-Layout, das mit der
Code-Execution-Pipeline (P200, Phase 5a #5) kommt. Bis dahin: kein
halbgares Git-Init.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


PROJECT_BIBLE_FILENAME_TEMPLATE = "ZERBERUS_{slug_upper}.md"
README_FILENAME = "README.md"

PROJECT_BIBLE_MIME = "text/markdown"
README_MIME = "text/markdown"


def render_project_bible(project: dict[str, Any], *, now: Optional[datetime] = None) -> str:
    """Erzeugt den Inhalt der Projekt-Bibel ``ZERBERUS_<SLUG>.md``.

    Sektionen analog ZERBERUS_MARATHON_WORKFLOW.md (das User-bekannte
    Format): "Ziel", "Stack", "Offene Entscheidungen", "Dateien", "Letzter
    Stand". Wird das LLM beim Project-Open mitlesen.

    ``now`` ueberschreibbar fuer deterministische Tests.
    """
    slug = project.get("slug", "unbekannt")
    name = project.get("name", slug)
    description = (project.get("description") or "").strip()
    stamp = (now or datetime.utcnow()).strftime("%Y-%m-%d")
    desc_block = description if description else "_Noch keine Beschreibung. Editiere in Hel."
    return (
        f"# ZERBERUS_{slug.upper()}.md\n"
        f"\n"
        f"**Projekt:** {name}\n"
        f"**Slug:** `{slug}`\n"
        f"**Angelegt:** {stamp}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Ziel\n"
        f"\n"
        f"{desc_block}\n"
        f"\n"
        f"## Stack\n"
        f"\n"
        f"_Hier eintragen: Sprachen, Frameworks, externe Services._\n"
        f"\n"
        f"## Offene Entscheidungen\n"
        f"\n"
        f"_Architektur-Fragen die der User noch entscheiden muss._\n"
        f"\n"
        f"## Dateien\n"
        f"\n"
        f"_Wichtige Dateien + ihr Zweck. Wird beim Upload manuell gepflegt._\n"
        f"\n"
        f"## Letzter Stand\n"
        f"\n"
        f"_Was wurde zuletzt gemacht, was kommt als naechstes._\n"
    )


def render_readme(project: dict[str, Any]) -> str:
    """Kurze Prosa-README mit Name + Description. Default-Stub, der User
    ueberschreibt ihn typisch sofort."""
    slug = project.get("slug", "unbekannt")
    name = project.get("name", slug)
    description = (project.get("description") or "").strip()
    body = description if description else "Beschreibung folgt."
    return (
        f"# {name}\n"
        f"\n"
        f"{body}\n"
        f"\n"
        f"_Slug: `{slug}` — verwaltet ueber Hel (`/hel/admin/projects`)._\n"
    )


def template_files_for(project: dict[str, Any], *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Liefert die Liste der zu materialisierenden Template-Files.

    Pure-Function — kein I/O, kein DB-Zugriff. Genau ein Eintrag pro
    Datei mit ``relative_path``, ``content`` (str), ``mime_type``. Caller
    schreibt die Bytes selbst.
    """
    slug = project.get("slug", "unbekannt")
    bible_name = PROJECT_BIBLE_FILENAME_TEMPLATE.format(slug_upper=slug.upper())
    return [
        {
            "relative_path": bible_name,
            "content": render_project_bible(project, now=now),
            "mime_type": PROJECT_BIBLE_MIME,
        },
        {
            "relative_path": README_FILENAME,
            "content": render_readme(project),
            "mime_type": README_MIME,
        },
    ]


def _write_atomic(target: Path, data: bytes) -> None:
    """Atomic Write — analog zu ``hel._store_uploaded_bytes``.

    Liegt hier dupliziert (statt Import aus ``hel``), weil der Template-
    Helper auch ohne FastAPI-Stack laufen koennen muss (Tests, zukuenftige
    CLI-Migrations-Tools).
    """
    import os
    import tempfile

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), prefix=".tpl_", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, str(target))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def materialize_template(
    project: dict[str, Any],
    base_dir: Path,
    *,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Schreibt die Skelett-Files ins Projekt-Storage und registriert
    DB-Eintraege.

    - ``dry_run=True`` schreibt keine Bytes und legt keine DB-Eintraege an,
      liefert aber dieselbe Liste der File-Beschreibungen zurueck (zum
      Inspizieren in Tests/Migrations).
    - Idempotenz: Existiert der ``relative_path`` schon im ``project_files``-
      Index, wird der Eintrag uebersprungen — User-Inhalte bleiben
      unangetastet.
    - Schreibt Bytes via ``storage_path_for`` (SHA-Pfad, konsistent mit
      P196-Uploads). Wenn die Bytes schon existieren (anderes Projekt mit
      identischem Inhalt), wird nicht doppelt geschrieben (SHA-Dedup).

    Liefert die Liste der NEU angelegten Eintraege. Bei ``dry_run`` ist das
    die volle Template-Liste (ohne side-effect).
    """
    from zerberus.core import projects_repo

    project_id = project["id"]
    slug = project["slug"]
    files = template_files_for(project, now=now)

    if dry_run:
        return [
            {"relative_path": f["relative_path"], "size_bytes": len(f["content"].encode("utf-8"))}
            for f in files
        ]

    existing = await projects_repo.list_files(project_id)
    existing_paths = {f["relative_path"] for f in existing}

    created: list[dict[str, Any]] = []
    for tpl in files:
        rel = tpl["relative_path"]
        if rel in existing_paths:
            logger.info(f"[TEMPLATE-198] skip slug={slug} path={rel} (already exists)")
            continue

        data = tpl["content"].encode("utf-8")
        sha256 = projects_repo.compute_sha256(data)
        target = projects_repo.storage_path_for(slug, sha256, base_dir)

        if not target.exists():
            _write_atomic(target, data)

        registered = await projects_repo.register_file(
            project_id=project_id,
            relative_path=rel,
            sha256=sha256,
            size_bytes=len(data),
            storage_path=str(target),
            mime_type=tpl["mime_type"],
        )
        created.append(registered)
        logger.info(
            f"[TEMPLATE-198] created slug={slug} path={rel} size={len(data)} sha={sha256[:8]}"
        )

    return created
