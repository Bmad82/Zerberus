"""Patch 198 (Phase 5a #2) — Tests fuer Template-Generierung beim Anlegen.

Drei Schichten:
- **Pure-Function-Tests** auf ``render_project_bible`` / ``render_readme`` /
  ``template_files_for`` — keine DB, kein I/O, deterministisch via ``now``.
- **Async DB+Storage-Tests** auf ``materialize_template`` mit ``tmp_db``-
  Fixture (analog ``test_projects_repo``) und ``tmp_path`` als Storage.
- **End-to-End-Tests** auf ``create_project_endpoint`` mit eingeschaltetem
  ``auto_template``-Flag — verifiziert dass die Files in der File-Liste
  auftauchen.
- **Source-Audit-Tests** stellen sicher dass ``hel.py`` den Template-Helper
  importiert und das ``auto_template``-Flag honoriert.
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(monkeypatch):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_projects_template.db"
    url = f"sqlite+aiosqlite:///{db_file}"

    import zerberus.core.database as db_mod
    from zerberus.core.database import Base

    engine = create_async_engine(url, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())

    monkeypatch.setattr(db_mod, "_engine", engine)
    monkeypatch.setattr(db_mod, "_async_session_maker", sm)
    yield sm
    asyncio.run(engine.dispose())


@pytest.fixture
def tmp_storage(monkeypatch, tmp_path):
    """Biegt ``hel._projects_storage_base`` auf ``tmp_path`` um — der
    Endpoint schreibt Bytes nach ``tmp_path/projects/<slug>/...``.
    """
    from zerberus.app.routers import hel as hel_mod

    monkeypatch.setattr(hel_mod, "_projects_storage_base", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def enable_auto_template(monkeypatch):
    """Stellt sicher dass das Flag ``auto_template`` auf True steht — wir
    monkeypatchen nicht weg, weil das auch der Default ist, sondern setzen
    es explizit (resilient gegenueber lokalen ``config.yaml``-Overrides)."""
    from zerberus.core import config as cfg

    s = cfg.get_settings()
    monkeypatch.setattr(s.projects, "auto_template", True)
    return s


@pytest.fixture
def disable_auto_template(monkeypatch):
    from zerberus.core import config as cfg

    s = cfg.get_settings()
    monkeypatch.setattr(s.projects, "auto_template", False)
    return s


# ---------------------------------------------------------------------------
# Pure-Function: render_project_bible
# ---------------------------------------------------------------------------


class TestRenderProjectBible:
    def test_contains_slug_uppercase_and_name(self):
        from zerberus.core.projects_template import render_project_bible

        out = render_project_bible(
            {"slug": "ai-research", "name": "AI Research", "description": ""},
            now=datetime(2026, 5, 2),
        )
        assert "ZERBERUS_AI-RESEARCH.md" in out
        assert "AI Research" in out
        assert "`ai-research`" in out

    def test_contains_anlegedatum(self):
        from zerberus.core.projects_template import render_project_bible

        out = render_project_bible(
            {"slug": "x", "name": "X", "description": "y"},
            now=datetime(2026, 5, 2),
        )
        assert "2026-05-02" in out

    def test_section_headers_present(self):
        from zerberus.core.projects_template import render_project_bible

        out = render_project_bible({"slug": "s", "name": "N"})
        for header in ("## Ziel", "## Stack", "## Offene Entscheidungen", "## Dateien", "## Letzter Stand"):
            assert header in out, f"fehlt: {header}"

    def test_description_used_in_ziel_section(self):
        from zerberus.core.projects_template import render_project_bible

        out = render_project_bible(
            {"slug": "s", "name": "N", "description": "Mein Test-Ziel"},
            now=datetime(2026, 5, 2),
        )
        ziel_pos = out.index("## Ziel")
        stack_pos = out.index("## Stack")
        ziel_block = out[ziel_pos:stack_pos]
        assert "Mein Test-Ziel" in ziel_block

    def test_empty_description_uses_placeholder(self):
        from zerberus.core.projects_template import render_project_bible

        out = render_project_bible({"slug": "s", "name": "N", "description": ""})
        assert "Noch keine Beschreibung" in out

    def test_missing_keys_use_defaults(self):
        from zerberus.core.projects_template import render_project_bible

        # Sollte nicht KeyError werfen
        out = render_project_bible({})
        assert "ZERBERUS_UNBEKANNT.md" in out


# ---------------------------------------------------------------------------
# Pure-Function: render_readme
# ---------------------------------------------------------------------------


class TestRenderReadme:
    def test_contains_name_and_slug(self):
        from zerberus.core.projects_template import render_readme

        out = render_readme({"slug": "demo", "name": "Demo Projekt", "description": "kurz"})
        assert "# Demo Projekt" in out
        assert "`demo`" in out
        assert "kurz" in out

    def test_empty_description_uses_placeholder(self):
        from zerberus.core.projects_template import render_readme

        out = render_readme({"slug": "x", "name": "X", "description": ""})
        assert "Beschreibung folgt" in out


# ---------------------------------------------------------------------------
# Pure-Function: template_files_for
# ---------------------------------------------------------------------------


class TestTemplateFilesFor:
    def test_returns_two_files(self):
        from zerberus.core.projects_template import template_files_for

        files = template_files_for({"slug": "a", "name": "A"})
        paths = [f["relative_path"] for f in files]
        assert "ZERBERUS_A.md" in paths
        assert "README.md" in paths
        assert len(files) == 2

    def test_each_file_has_content_and_mime(self):
        from zerberus.core.projects_template import template_files_for

        files = template_files_for({"slug": "a", "name": "A"})
        for f in files:
            assert isinstance(f["content"], str) and f["content"]
            assert f["mime_type"] == "text/markdown"

    def test_slug_uppercased_in_bible_filename(self):
        from zerberus.core.projects_template import template_files_for

        files = template_files_for({"slug": "snake-case-slug", "name": "X"})
        bible = next(f for f in files if "ZERBERUS_" in f["relative_path"])
        assert bible["relative_path"] == "ZERBERUS_SNAKE-CASE-SLUG.md"


# ---------------------------------------------------------------------------
# Async: materialize_template
# ---------------------------------------------------------------------------


class TestMaterializeTemplate:
    def test_creates_two_files(self, tmp_db, tmp_path):
        from zerberus.core import projects_repo
        from zerberus.core.projects_template import materialize_template

        async def run():
            project = await projects_repo.create_project(name="Materialize")
            created = await materialize_template(project, tmp_path)
            files = await projects_repo.list_files(project["id"])
            return project, created, files

        project, created, files = asyncio.run(run())
        assert len(created) == 2
        assert len(files) == 2
        paths = {f["relative_path"] for f in files}
        assert paths == {f"ZERBERUS_{project['slug'].upper()}.md", "README.md"}

    def test_bytes_written_to_sha_storage(self, tmp_db, tmp_path):
        from zerberus.core import projects_repo
        from zerberus.core.projects_template import materialize_template

        async def run():
            project = await projects_repo.create_project(name="Bytes")
            await materialize_template(project, tmp_path)
            files = await projects_repo.list_files(project["id"])
            return project, files

        project, files = asyncio.run(run())
        for f in files:
            sp = Path(f["storage_path"])
            assert sp.exists(), f"storage_path fehlt: {sp}"
            # SHA-Pfad-Konvention: <base>/projects/<slug>/<sha[:2]>/<sha>
            assert "projects" in sp.parts
            assert project["slug"] in sp.parts
            assert sp.name == f["sha256"]

    def test_idempotent_skips_existing(self, tmp_db, tmp_path):
        from zerberus.core import projects_repo
        from zerberus.core.projects_template import materialize_template

        async def run():
            project = await projects_repo.create_project(name="Idem")
            first = await materialize_template(project, tmp_path)
            second = await materialize_template(project, tmp_path)
            files = await projects_repo.list_files(project["id"])
            return first, second, files

        first, second, files = asyncio.run(run())
        assert len(first) == 2
        assert second == []  # alles existiert schon
        assert len(files) == 2  # keine Doubletten

    def test_idempotent_does_not_overwrite_user_content(self, tmp_db, tmp_path):
        """Wenn der User die README selbst hochgeladen hat, darf das Template
        sie NICHT ueberschreiben."""
        from zerberus.core import projects_repo
        from zerberus.core.projects_template import materialize_template

        user_bytes = b"# Mein eigenes README\n"
        user_sha = projects_repo.compute_sha256(user_bytes)

        async def run():
            project = await projects_repo.create_project(name="UserContent")
            # User-Datei liegt schon — wir simulieren via register_file
            target = projects_repo.storage_path_for(project["slug"], user_sha, tmp_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(user_bytes)
            await projects_repo.register_file(
                project_id=project["id"],
                relative_path="README.md",
                sha256=user_sha,
                size_bytes=len(user_bytes),
                storage_path=str(target),
                mime_type="text/markdown",
            )
            created = await materialize_template(project, tmp_path)
            files = await projects_repo.list_files(project["id"])
            return project, created, files

        project, created, files = asyncio.run(run())
        # Nur die Bibel kam neu, README wurde nicht ueberschrieben
        new_paths = [f["relative_path"] for f in created]
        assert new_paths == [f"ZERBERUS_{project['slug'].upper()}.md"]
        # User-README ist noch da mit ihrem Inhalt
        readme = next(f for f in files if f["relative_path"] == "README.md")
        assert readme["sha256"] == user_sha

    def test_dry_run_writes_nothing(self, tmp_db, tmp_path):
        from zerberus.core import projects_repo
        from zerberus.core.projects_template import materialize_template

        async def run():
            project = await projects_repo.create_project(name="Dry")
            preview = await materialize_template(project, tmp_path, dry_run=True)
            files = await projects_repo.list_files(project["id"])
            return preview, files

        preview, files = asyncio.run(run())
        assert len(preview) == 2
        assert files == []  # nichts in DB
        # tmp_path/projects existiert nicht (oder leer)
        projects_dir = tmp_path / "projects"
        if projects_dir.exists():
            # Wenn der Ordner durch andere Tests existiert, darf er nicht
            # die Slug-Subdir enthalten
            assert not any(projects_dir.iterdir())

    def test_content_renders_with_project_data(self, tmp_db, tmp_path):
        from zerberus.core import projects_repo
        from zerberus.core.projects_template import materialize_template

        async def run():
            project = await projects_repo.create_project(
                name="Inhalt", description="Mein konkretes Ziel"
            )
            await materialize_template(project, tmp_path)
            files = await projects_repo.list_files(project["id"])
            # Bibel-Datei lesen
            bible = next(f for f in files if f["relative_path"].startswith("ZERBERUS_"))
            return Path(bible["storage_path"]).read_text(encoding="utf-8")

        content = asyncio.run(run())
        assert "Mein konkretes Ziel" in content
        assert "## Ziel" in content


# ---------------------------------------------------------------------------
# End-to-End ueber create_project_endpoint
# ---------------------------------------------------------------------------


class TestCreateProjectEndpointMaterializes:
    def test_endpoint_creates_template_files_when_flag_on(
        self, tmp_db, tmp_storage, enable_auto_template
    ):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            list_project_files_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            res = await create_project_endpoint(_FakeRequest({"name": "E2E-On"}))
            files = await list_project_files_endpoint(res["project"]["id"])
            return res, files

        res, files = asyncio.run(run())
        assert res["status"] == "ok"
        assert len(res["template_files"]) == 2
        assert files["count"] == 2
        slug = res["project"]["slug"]
        paths = {f["relative_path"] for f in files["files"]}
        assert paths == {f"ZERBERUS_{slug.upper()}.md", "README.md"}

    def test_endpoint_skips_when_flag_off(
        self, tmp_db, tmp_storage, disable_auto_template
    ):
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            list_project_files_endpoint,
        )
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def run():
            res = await create_project_endpoint(_FakeRequest({"name": "E2E-Off"}))
            files = await list_project_files_endpoint(res["project"]["id"])
            return res, files

        res, files = asyncio.run(run())
        assert res["template_files"] == []
        assert files["count"] == 0

    def test_endpoint_template_failure_does_not_abort_create(
        self, tmp_db, tmp_storage, enable_auto_template, monkeypatch
    ):
        """Wenn ``materialize_template`` crasht, soll das Projekt trotzdem
        angelegt sein (best-effort)."""
        from zerberus.app.routers.hel import (
            create_project_endpoint,
            list_projects_endpoint,
        )
        from zerberus.core import projects_template
        from zerberus.tests.test_projects_endpoints import _FakeRequest

        async def boom(*args, **kwargs):
            raise RuntimeError("simulated template failure")

        monkeypatch.setattr(projects_template, "materialize_template", boom)

        async def run():
            res = await create_project_endpoint(_FakeRequest({"name": "Boom"}))
            listing = await list_projects_endpoint()
            return res, listing

        res, listing = asyncio.run(run())
        assert res["status"] == "ok"
        assert res["template_files"] == []
        assert listing["count"] == 1


# ---------------------------------------------------------------------------
# Source-Audit
# ---------------------------------------------------------------------------


class TestSourceAudit:
    def test_hel_imports_projects_template(self):
        import inspect
        from zerberus.app.routers import hel as hel_mod

        src = inspect.getsource(hel_mod.create_project_endpoint)
        assert "projects_template" in src, "hel muss projects_template importieren"
        assert "materialize_template" in src

    def test_hel_honors_auto_template_flag(self):
        import inspect
        from zerberus.app.routers import hel as hel_mod

        src = inspect.getsource(hel_mod.create_project_endpoint)
        assert "auto_template" in src, "Flag muss im Endpoint geprueft werden"

    def test_template_module_has_constants(self):
        from zerberus.core import projects_template

        # Stable Konstanten fuer andere Module (RAG-Filter, Migrations)
        assert projects_template.PROJECT_BIBLE_FILENAME_TEMPLATE.endswith(".md")
        assert "{slug_upper}" in projects_template.PROJECT_BIBLE_FILENAME_TEMPLATE
        assert projects_template.README_FILENAME == "README.md"
