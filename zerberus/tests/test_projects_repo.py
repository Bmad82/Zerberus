"""Patch 194 (Phase 5a #1) — Tests fuer ``zerberus.core.projects_repo``.

Deckt Slug-Generierung, CRUD-Roundtrips, Soft- vs. Hard-Delete und
Cascade auf project_files ab. Nutzt eine isolierte tmp-DB (gleiches
Muster wie ``test_memory_store.py``).
"""
from __future__ import annotations

import asyncio
import tempfile
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
    db_file = Path(tmpdir) / "test_projects.db"
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


# ----- slugify --------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        from zerberus.core.projects_repo import slugify
        assert slugify("AI Research") == "ai-research"

    def test_strips_special_chars(self):
        from zerberus.core.projects_repo import slugify
        assert slugify("Hello, World! v2.0") == "hello-world-v2-0"

    def test_collapses_whitespace(self):
        from zerberus.core.projects_repo import slugify
        assert slugify("a   b   c") == "a-b-c"

    def test_empty_falls_back(self):
        from zerberus.core.projects_repo import slugify
        assert slugify("") == "projekt"
        assert slugify("!!!") == "projekt"

    def test_truncates_to_64(self):
        from zerberus.core.projects_repo import slugify
        long = "x" * 200
        assert len(slugify(long)) == 64


# ----- create / read --------------------------------------------------------


class TestCreateProject:
    def test_basic_create(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            return await projects_repo.create_project(
                name="Test Projekt",
                description="Eine Beschreibung",
            )

        p = asyncio.run(run())
        assert p["id"] > 0
        assert p["slug"] == "test-projekt"
        assert p["name"] == "Test Projekt"
        assert p["description"] == "Eine Beschreibung"
        assert p["is_archived"] is False
        assert p["persona_overlay"] == {"system_addendum": "", "tone_hints": []}

    def test_create_with_overlay(self, tmp_db):
        from zerberus.core import projects_repo

        overlay = {"system_addendum": "Sei praezise.", "tone_hints": ["fachsprache"]}

        async def run():
            return await projects_repo.create_project(name="Overlay-Test", persona_overlay=overlay)

        p = asyncio.run(run())
        assert p["persona_overlay"] == overlay

    def test_empty_name_rejected(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            await projects_repo.create_project(name="   ")

        with pytest.raises(ValueError):
            asyncio.run(run())

    def test_slug_collision_appends_counter(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            a = await projects_repo.create_project(name="Foo")
            b = await projects_repo.create_project(name="Foo")
            c = await projects_repo.create_project(name="Foo")
            return a["slug"], b["slug"], c["slug"]

        a, b, c = asyncio.run(run())
        assert a == "foo"
        assert b == "foo-2"
        assert c == "foo-3"

    def test_explicit_slug_used(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            return await projects_repo.create_project(name="Anything", slug="custom-slug")

        p = asyncio.run(run())
        assert p["slug"] == "custom-slug"


class TestReadProject:
    def test_get_by_id(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            created = await projects_repo.create_project(name="Read-Me")
            fetched = await projects_repo.get_project(created["id"])
            return created, fetched

        created, fetched = asyncio.run(run())
        assert fetched is not None
        assert fetched["slug"] == created["slug"]

    def test_get_by_slug(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            await projects_repo.create_project(name="By-Slug")
            return await projects_repo.get_project_by_slug("by-slug")

        p = asyncio.run(run())
        assert p is not None
        assert p["name"] == "By-Slug"

    def test_get_nonexistent_returns_none(self, tmp_db):
        from zerberus.core import projects_repo
        assert asyncio.run(projects_repo.get_project(9999)) is None
        assert asyncio.run(projects_repo.get_project_by_slug("ghost")) is None

    def test_list_excludes_archived_by_default(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            a = await projects_repo.create_project(name="Active")
            b = await projects_repo.create_project(name="Archived")
            await projects_repo.archive_project(b["id"])
            visible = await projects_repo.list_projects()
            all_p = await projects_repo.list_projects(include_archived=True)
            return visible, all_p

        visible, all_p = asyncio.run(run())
        assert {p["name"] for p in visible} == {"Active"}
        assert {p["name"] for p in all_p} == {"Active", "Archived"}


# ----- update / archive / delete --------------------------------------------


class TestUpdateProject:
    def test_partial_update(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            p = await projects_repo.create_project(name="Original", description="alt")
            updated = await projects_repo.update_project(p["id"], name="Neu")
            return updated

        u = asyncio.run(run())
        assert u["name"] == "Neu"
        assert u["description"] == "alt"

    def test_overlay_replace(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            p = await projects_repo.create_project(
                name="Overlay-Update",
                persona_overlay={"system_addendum": "alt"},
            )
            return await projects_repo.update_project(
                p["id"],
                persona_overlay={"system_addendum": "neu", "tone_hints": ["x"]},
            )

        u = asyncio.run(run())
        assert u["persona_overlay"] == {"system_addendum": "neu", "tone_hints": ["x"]}

    def test_update_missing_returns_none(self, tmp_db):
        from zerberus.core import projects_repo
        assert asyncio.run(projects_repo.update_project(9999, name="x")) is None

    def test_update_empty_name_rejected(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            p = await projects_repo.create_project(name="Has-Name")
            await projects_repo.update_project(p["id"], name="   ")

        with pytest.raises(ValueError):
            asyncio.run(run())


class TestArchiveAndDelete:
    def test_archive_then_unarchive(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            p = await projects_repo.create_project(name="Toggle")
            archived = await projects_repo.archive_project(p["id"])
            restored = await projects_repo.unarchive_project(p["id"])
            return archived, restored

        archived, restored = asyncio.run(run())
        assert archived["is_archived"] is True
        assert restored["is_archived"] is False

    def test_archive_missing(self, tmp_db):
        from zerberus.core import projects_repo
        assert asyncio.run(projects_repo.archive_project(9999)) is None

    def test_hard_delete_cascades_files(self, tmp_db):
        from zerberus.core import projects_repo

        sha = "a" * 64

        async def run():
            p = await projects_repo.create_project(name="To-Delete")
            await projects_repo.register_file(
                project_id=p["id"],
                relative_path="src/main.py",
                sha256=sha,
                size_bytes=42,
                storage_path=f"data/projects/to-delete/aa/{sha}",
                mime_type="text/x-python",
            )
            files_before = await projects_repo.list_files(p["id"])
            ok = await projects_repo.delete_project(p["id"])
            after = await projects_repo.get_project(p["id"])
            files_after = await projects_repo.list_files(p["id"])
            return ok, files_before, after, files_after

        ok, before, after, files_after = asyncio.run(run())
        assert ok is True
        assert len(before) == 1
        assert after is None
        assert files_after == []  # Cascade

    def test_delete_missing_returns_false(self, tmp_db):
        from zerberus.core import projects_repo
        assert asyncio.run(projects_repo.delete_project(9999)) is False


# ----- file registration ----------------------------------------------------


class TestProjectFiles:
    def test_register_and_list(self, tmp_db):
        from zerberus.core import projects_repo

        sha = "b" * 64

        async def run():
            p = await projects_repo.create_project(name="With-Files")
            f = await projects_repo.register_file(
                project_id=p["id"],
                relative_path="README.md",
                sha256=sha,
                size_bytes=100,
                storage_path=f"data/projects/with-files/bb/{sha}",
            )
            files = await projects_repo.list_files(p["id"])
            return f, files

        f, files = asyncio.run(run())
        assert f["relative_path"] == "README.md"
        assert len(files) == 1
        assert files[0]["sha256"] == sha

    def test_invalid_sha256_rejected(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            p = await projects_repo.create_project(name="Bad-Sha")
            await projects_repo.register_file(
                project_id=p["id"],
                relative_path="x.py",
                sha256="too-short",
                size_bytes=1,
                storage_path="x",
            )

        with pytest.raises(ValueError):
            asyncio.run(run())

    def test_negative_size_rejected(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            p = await projects_repo.create_project(name="Bad-Size")
            await projects_repo.register_file(
                project_id=p["id"],
                relative_path="x.py",
                sha256="c" * 64,
                size_bytes=-1,
                storage_path="x",
            )

        with pytest.raises(ValueError):
            asyncio.run(run())

    def test_duplicate_path_in_project_rejected(self, tmp_db):
        from zerberus.core import projects_repo

        async def run():
            p = await projects_repo.create_project(name="Dup-Path")
            await projects_repo.register_file(
                project_id=p["id"],
                relative_path="src/x.py",
                sha256="d" * 64,
                size_bytes=10,
                storage_path="a",
            )
            # Selber Pfad wieder — UNIQUE-Constraint sollte greifen
            await projects_repo.register_file(
                project_id=p["id"],
                relative_path="src/x.py",
                sha256="e" * 64,
                size_bytes=20,
                storage_path="b",
            )

        with pytest.raises(Exception):  # IntegrityError unter SQLAlchemy
            asyncio.run(run())

    def test_storage_path_helper(self):
        from zerberus.core.projects_repo import storage_path_for

        sha = "f" * 64
        path = storage_path_for("my-project", sha, Path("/data"))
        assert path == Path("/data") / "projects" / "my-project" / "ff" / sha

    def test_compute_sha256_helper(self):
        from zerberus.core.projects_repo import compute_sha256
        assert compute_sha256(b"hello") == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )


# ----- Patch 196 — Upload-Helper --------------------------------------------


class TestSanitizeRelativePath:
    def test_basic_filename(self):
        from zerberus.core.projects_repo import sanitize_relative_path
        assert sanitize_relative_path("README.md") == "README.md"

    def test_subdirectory_kept(self):
        from zerberus.core.projects_repo import sanitize_relative_path
        assert sanitize_relative_path("src/main.py") == "src/main.py"

    def test_backslashes_normalized(self):
        from zerberus.core.projects_repo import sanitize_relative_path
        assert sanitize_relative_path("src\\main.py") == "src/main.py"

    def test_dotdot_stripped(self):
        from zerberus.core.projects_repo import sanitize_relative_path
        assert sanitize_relative_path("../etc/passwd") == "etc/passwd"

    def test_leading_slash_stripped(self):
        from zerberus.core.projects_repo import sanitize_relative_path
        assert sanitize_relative_path("/abs/path.txt") == "abs/path.txt"

    def test_double_slash_collapsed(self):
        from zerberus.core.projects_repo import sanitize_relative_path
        assert sanitize_relative_path("a//b//c.txt") == "a/b/c.txt"

    def test_empty_raises(self):
        from zerberus.core.projects_repo import sanitize_relative_path
        with pytest.raises(ValueError):
            sanitize_relative_path("")
        with pytest.raises(ValueError):
            sanitize_relative_path("   ")

    def test_only_dots_raises(self):
        from zerberus.core.projects_repo import sanitize_relative_path
        with pytest.raises(ValueError):
            sanitize_relative_path("../..")
        with pytest.raises(ValueError):
            sanitize_relative_path("./.")


class TestIsExtensionBlocked:
    def test_blocked_extension_lowercase(self):
        from zerberus.core.projects_repo import is_extension_blocked
        assert is_extension_blocked("malware.exe", [".exe", ".bat"]) is True

    def test_blocked_case_insensitive(self):
        from zerberus.core.projects_repo import is_extension_blocked
        assert is_extension_blocked("BIG.EXE", [".exe"]) is True

    def test_safe_extension(self):
        from zerberus.core.projects_repo import is_extension_blocked
        assert is_extension_blocked("README.md", [".exe", ".bat"]) is False

    def test_no_extension(self):
        from zerberus.core.projects_repo import is_extension_blocked
        assert is_extension_blocked("Makefile", [".exe"]) is False

    def test_extension_in_path(self):
        from zerberus.core.projects_repo import is_extension_blocked
        assert is_extension_blocked("subdir/script.sh", [".sh"]) is True


class TestCountShaReferences:
    def test_zero_refs(self, tmp_db):
        from zerberus.core import projects_repo
        assert asyncio.run(projects_repo.count_sha_references("0" * 64)) == 0

    def test_counts_across_projects(self, tmp_db):
        from zerberus.core import projects_repo

        sha = "1" * 64

        async def run():
            a = await projects_repo.create_project(name="Ref-A")
            b = await projects_repo.create_project(name="Ref-B")
            await projects_repo.register_file(
                project_id=a["id"], relative_path="x.txt", sha256=sha,
                size_bytes=10, storage_path="A",
            )
            await projects_repo.register_file(
                project_id=b["id"], relative_path="x.txt", sha256=sha,
                size_bytes=10, storage_path="B",
            )
            return await projects_repo.count_sha_references(sha)

        assert asyncio.run(run()) == 2

    def test_exclude_file_id(self, tmp_db):
        from zerberus.core import projects_repo

        sha = "2" * 64

        async def run():
            p = await projects_repo.create_project(name="Excl")
            f1 = await projects_repo.register_file(
                project_id=p["id"], relative_path="a.txt", sha256=sha,
                size_bytes=10, storage_path="A",
            )
            f2 = await projects_repo.register_file(
                project_id=p["id"], relative_path="b.txt", sha256=sha,
                size_bytes=10, storage_path="B",
            )
            return (
                await projects_repo.count_sha_references(sha),
                await projects_repo.count_sha_references(sha, exclude_file_id=f1["id"]),
                await projects_repo.count_sha_references(sha, exclude_file_id=f2["id"]),
            )

        total, excl_first, excl_second = asyncio.run(run())
        assert total == 2
        assert excl_first == 1
        assert excl_second == 1
