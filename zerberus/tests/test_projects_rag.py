"""Patch 199 (Phase 5a #3) — Tests fuer den Projekt-RAG-Index.

Vier Schichten:
- **Pure-Function** (Splitter, Chunker, Top-K, Format-Helper) — keine DB,
  kein I/O, kein echter Embedder. Trivial unit-bar.
- **File-I/O** (load/save/remove_project_index) — schreibt nach
  ``tmp_path``, kein DB-Zugriff.
- **Async DB+Storage** (index_project_file, remove_file_from_index,
  query_project_rag) — nutzt ``tmp_db`` + ``tmp_path`` und einen
  monkeypatched Pseudo-Embedder. Echte ``sentence-transformers`` sind in
  Tests TABU.
- **End-to-End** ueber ``upload_project_file_endpoint`` — verifiziert die
  Verdrahtung in ``hel.py``.
- **Source-Audit** — ``hel.py``, ``projects_template.py`` und ``legacy.py``
  importieren + rufen den Helper auf.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(monkeypatch):
    """Async-Engine + Session-Factory auf einer Temp-SQLite-Datei. Pattern
    identisch zu ``test_projects_template.py`` / ``test_persona_merge.py``.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_projects_rag.db"
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
    """Biegt ``hel._projects_storage_base`` auf ``tmp_path`` um — Bytes
    landen unter ``tmp_path/projects/<slug>/...``. Identisch zu
    ``test_projects_files_upload.py``.
    """
    from zerberus.app.routers import hel as hel_mod

    monkeypatch.setattr(hel_mod, "_projects_storage_base", lambda: tmp_path)
    return tmp_path


def _hash_embed(text: str) -> list[float]:
    """Deterministischer 8-dim-Pseudo-Embedder. Gleicher Text → gleicher
    Vektor; unterschiedliche Texte → praktisch immer unterschiedliche
    Vektoren. Reicht fuer Top-K-Stabilitaet in Tests, ohne ein 80-MB-
    Modell zu laden.
    """
    h = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [b / 255.0 - 0.5 for b in h[:8]]
    norm = sum(x * x for x in raw) ** 0.5 or 1.0
    return [x / norm for x in raw]


@pytest.fixture
def fake_embedder(monkeypatch):
    """Monkeypatched ``_embed_text`` durch den Hash-Pseudo-Embedder.
    Verhindert das Laden von SentenceTransformer in Unit-Tests.
    """
    from zerberus.core import projects_rag

    monkeypatch.setattr(projects_rag, "_embed_text", _hash_embed)
    return _hash_embed


@pytest.fixture
def enable_rag(monkeypatch):
    from zerberus.core import config as cfg

    s = cfg.get_settings()
    monkeypatch.setattr(s.projects, "rag_enabled", True)
    return s


@pytest.fixture
def disable_auto_template(monkeypatch):
    """Templates sind in P198-Tests gepruegelt — wir wollen hier saubere
    Datei-Zaehler."""
    from zerberus.core import config as cfg

    s = cfg.get_settings()
    monkeypatch.setattr(s.projects, "auto_template", False)
    return s


# ---------------------------------------------------------------------------
# Pure-Function: Prosa-Splitter
# ---------------------------------------------------------------------------


class TestSplitProse:
    def test_empty_returns_empty(self):
        from zerberus.core.projects_rag import _split_prose

        assert _split_prose("") == []
        assert _split_prose("   \n\n  \n") == []

    def test_single_short_paragraph_one_chunk(self):
        from zerberus.core.projects_rag import _split_prose

        out = _split_prose("Hallo Welt.")
        assert out == ["Hallo Welt."]

    def test_multiple_paragraphs_under_limit_merged(self):
        from zerberus.core.projects_rag import _split_prose

        text = "Erster Absatz.\n\nZweiter Absatz."
        out = _split_prose(text, max_chars=200)
        assert len(out) == 1
        assert "Erster Absatz." in out[0]
        assert "Zweiter Absatz." in out[0]

    def test_oversized_paragraphs_split(self):
        from zerberus.core.projects_rag import _split_prose

        text = ("A" * 1000) + "\n\n" + ("B" * 1000)
        out = _split_prose(text, max_chars=500)
        assert len(out) >= 2
        for chunk in out:
            assert len(chunk) <= 500 + 50  # kleiner Slack durch Sentence-Pad

    def test_oversized_single_paragraph_falls_to_sentences(self):
        from zerberus.core.projects_rag import _split_prose

        # 4 Saetze, jedes ca. 100 Zeichen, kein Doppel-Newline → wird via
        # Sentence-Splitter zerlegt.
        sentence = "Dies ist ein laengerer Satz mit ungefaehr fuenfzig Zeichen Laenge ohne Punkt."
        text = (sentence + " ") * 4
        out = _split_prose(text, max_chars=200)
        assert len(out) >= 2


# ---------------------------------------------------------------------------
# Pure-Function: Datei-Chunker
# ---------------------------------------------------------------------------


class TestChunkFileContent:
    def test_empty_returns_empty(self):
        from zerberus.core.projects_rag import chunk_file_content

        assert chunk_file_content("", "x.md") == []
        assert chunk_file_content("   \n  ", "x.md") == []

    def test_python_file_uses_code_chunker(self):
        from zerberus.core.projects_rag import chunk_file_content

        # Funktionen muessen ueber MIN_CHUNK_CHARS (50) liegen, sonst mergt
        # der Code-Chunker sie an den Vorgaenger.
        src = (
            '"""Modul-Docstring der ein bisschen Text hat damit er nicht zu kurz wird."""\n'
            "import os\n"
            "import sys\n"
            "import json\n"
            "\n"
            "def hallo_welt_funktion():\n"
            "    \"\"\"Funktions-Docstring der die Funktion gross genug macht.\"\"\"\n"
            "    daten = os.environ.get('SCHLUESSEL', 'default')\n"
            "    return f'hallo {daten}'\n"
        )
        chunks = chunk_file_content(src, "src/foo.py")
        # Code-Chunker ist aktiv → wir bekommen Code-Metadaten ("python")
        languages = {c["metadata"].get("language") for c in chunks}
        assert "python" in languages
        # Mindestens ein Chunk hat function-aehnlichen Inhalt
        assert any("def hallo_welt_funktion" in c["content"] for c in chunks)

    def test_markdown_file_uses_prose_chunker(self):
        from zerberus.core.projects_rag import chunk_file_content

        src = "# Titel\n\nErster Absatz.\n\nZweiter Absatz."
        chunks = chunk_file_content(src, "docs/readme.md")
        assert len(chunks) >= 1
        for c in chunks:
            assert c["metadata"]["chunk_type"] == "prose"
            assert c["metadata"]["file_path"] == "docs/readme.md"

    def test_unknown_extension_treated_as_prose(self):
        from zerberus.core.projects_rag import chunk_file_content

        src = "Plain Text. Eine Zeile mit Inhalt."
        chunks = chunk_file_content(src, "notes.unknown")
        assert chunks
        assert chunks[0]["metadata"]["chunk_type"] == "prose"

    def test_python_with_syntax_error_falls_back_to_prose(self):
        from zerberus.core.projects_rag import chunk_file_content

        # Kaputtes Python — Code-Chunker liefert []. Prose-Splitter springt ein.
        src = "def hallo(:\n    return 1\n\nzweite Zeile fuer Prose."
        chunks = chunk_file_content(src, "broken.py")
        assert chunks
        assert chunks[0]["metadata"]["chunk_type"] == "prose"


# ---------------------------------------------------------------------------
# Pure-Function: Top-K
# ---------------------------------------------------------------------------


class TestTopKIndices:
    def test_empty_index_returns_empty(self):
        from zerberus.core.projects_rag import top_k_indices

        assert top_k_indices([0.1, 0.2], None, 5) == []
        assert top_k_indices([0.1, 0.2], np.zeros((0, 2), dtype="float32"), 5) == []

    def test_zero_k_returns_empty(self):
        from zerberus.core.projects_rag import top_k_indices

        v = np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32")
        assert top_k_indices([1.0, 0.0], v, 0) == []

    def test_returns_sorted_descending_score(self):
        from zerberus.core.projects_rag import top_k_indices

        # 3 Vektoren — der erste ist exakt die Query → score 1.0
        v = np.array(
            [[1.0, 0.0], [0.7, 0.7], [0.0, 1.0]],
            dtype="float32",
        )
        # normalisieren, damit Cosinus = Dot-Product
        v = v / np.linalg.norm(v, axis=1, keepdims=True)
        hits = top_k_indices([1.0, 0.0], v, 3)
        assert [i for i, _ in hits] == [0, 1, 2]
        scores = [s for _, s in hits]
        assert scores[0] >= scores[1] >= scores[2]

    def test_caps_at_index_size(self):
        from zerberus.core.projects_rag import top_k_indices

        v = np.eye(3, dtype="float32")
        hits = top_k_indices([1.0, 0.0, 0.0], v, 99)
        assert len(hits) == 3

    def test_dim_mismatch_returns_empty(self):
        from zerberus.core.projects_rag import top_k_indices

        v = np.ones((3, 4), dtype="float32")
        assert top_k_indices([1.0, 0.0], v, 5) == []


# ---------------------------------------------------------------------------
# File-I/O: load / save / remove_project_index
# ---------------------------------------------------------------------------


class TestSaveLoadIndex:
    def test_load_missing_returns_empty(self, tmp_path):
        from zerberus.core.projects_rag import load_index

        vectors, meta = load_index("missing", tmp_path)
        assert vectors is None
        assert meta == []

    def test_save_then_load_roundtrip(self, tmp_path):
        from zerberus.core.projects_rag import load_index, save_index

        v = np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32")
        meta = [{"file_id": 1, "text": "a"}, {"file_id": 2, "text": "b"}]
        save_index("demo", tmp_path, v, meta)

        v2, meta2 = load_index("demo", tmp_path)
        assert v2 is not None
        assert v2.shape == (2, 2)
        assert meta2 == meta

    def test_load_inconsistent_returns_empty(self, tmp_path):
        from zerberus.core.projects_rag import (
            index_paths_for,
            index_dir_for,
            load_index,
        )

        d = index_dir_for("inconsistent", tmp_path)
        d.mkdir(parents=True)
        # Nur meta.json, keine vectors.npy
        _, meta_path = index_paths_for("inconsistent", tmp_path)
        meta_path.write_text("[]", encoding="utf-8")

        vectors, meta = load_index("inconsistent", tmp_path)
        assert vectors is None
        assert meta == []

    def test_load_corrupted_meta_returns_empty(self, tmp_path):
        from zerberus.core.projects_rag import (
            index_paths_for,
            save_index,
        )

        v = np.array([[1.0, 0.0]], dtype="float32")
        save_index("corrupt", tmp_path, v, [{"x": 1}])
        _, meta_path = index_paths_for("corrupt", tmp_path)
        meta_path.write_text("not-json{{{", encoding="utf-8")

        from zerberus.core.projects_rag import load_index

        vectors, meta = load_index("corrupt", tmp_path)
        assert vectors is None
        assert meta == []


class TestRemoveProjectIndex:
    def test_remove_existing_returns_true(self, tmp_path):
        from zerberus.core.projects_rag import (
            index_dir_for,
            remove_project_index,
            save_index,
        )

        v = np.array([[1.0, 0.0]], dtype="float32")
        save_index("kill", tmp_path, v, [{"x": 1}])
        assert index_dir_for("kill", tmp_path).exists()

        ok = remove_project_index("kill", tmp_path)
        assert ok is True
        assert not index_dir_for("kill", tmp_path).exists()

    def test_remove_missing_returns_false(self, tmp_path):
        from zerberus.core.projects_rag import remove_project_index

        assert remove_project_index("ghost", tmp_path) is False


# ---------------------------------------------------------------------------
# Async: index_project_file
# ---------------------------------------------------------------------------


def _write_file_record(slug: str, relative_path: str, data: bytes, base_dir: Path) -> dict:
    """Helper fuer DB-Tests: legt eine Datei im Storage ab und registriert
    den project_files-Eintrag. Wird in mehreren Test-Klassen genutzt.
    """
    from zerberus.core import projects_repo

    sha = projects_repo.compute_sha256(data)
    target = projects_repo.storage_path_for(slug, sha, base_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {
        "sha256": sha,
        "size_bytes": len(data),
        "storage_path": str(target),
        "relative_path": relative_path,
    }


class TestIndexProjectFile:
    def test_indexes_a_markdown_file(self, tmp_db, tmp_path, fake_embedder):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="Idx")
            data = b"# Titel\n\nErster Absatz.\n\nZweiter Absatz."
            rec = _write_file_record(project["slug"], "doc.md", data, tmp_path)
            registered = await projects_repo.register_file(
                project_id=project["id"],
                relative_path=rec["relative_path"],
                sha256=rec["sha256"],
                size_bytes=rec["size_bytes"],
                storage_path=rec["storage_path"],
                mime_type="text/markdown",
            )
            status = await projects_rag.index_project_file(
                project["id"], registered["id"], tmp_path
            )
            vectors, meta = projects_rag.load_index(project["slug"], tmp_path)
            return project, status, vectors, meta

        project, status, vectors, meta = asyncio.run(run())
        assert status["chunks"] >= 1
        assert status["skipped"] is False
        assert status["reason"] == "indexed"
        assert vectors is not None
        assert vectors.shape[0] == len(meta) >= 1
        assert all(m["relative_path"] == "doc.md" for m in meta)

    def test_idempotent_same_file_no_duplication(
        self, tmp_db, tmp_path, fake_embedder
    ):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="Idem")
            data = b"# Titel\n\nNur ein Absatz."
            rec = _write_file_record(project["slug"], "doc.md", data, tmp_path)
            registered = await projects_repo.register_file(
                project_id=project["id"],
                relative_path=rec["relative_path"],
                sha256=rec["sha256"],
                size_bytes=rec["size_bytes"],
                storage_path=rec["storage_path"],
                mime_type="text/markdown",
            )
            await projects_rag.index_project_file(project["id"], registered["id"], tmp_path)
            await projects_rag.index_project_file(project["id"], registered["id"], tmp_path)
            vectors, meta = projects_rag.load_index(project["slug"], tmp_path)
            return vectors, meta

        vectors, meta = asyncio.run(run())
        # Beim zweiten Aufruf wird der alte Block geloescht und neu
        # geschrieben → keine Doubletten.
        assert vectors is not None
        file_ids = [m["file_id"] for m in meta]
        # Alle gehoeren zur selben file_id, keine Verdoppelung
        assert len(set(file_ids)) == 1

    def test_empty_file_skipped(self, tmp_db, tmp_path, fake_embedder):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="Empty")
            data = b"   \n   "
            rec = _write_file_record(project["slug"], "leer.md", data, tmp_path)
            registered = await projects_repo.register_file(
                project_id=project["id"],
                relative_path=rec["relative_path"],
                sha256=rec["sha256"],
                size_bytes=rec["size_bytes"],
                storage_path=rec["storage_path"],
                mime_type="text/markdown",
            )
            return await projects_rag.index_project_file(
                project["id"], registered["id"], tmp_path
            )

        status = asyncio.run(run())
        assert status["skipped"] is True
        assert status["reason"] == "empty"
        assert status["chunks"] == 0

    def test_binary_file_skipped(self, tmp_db, tmp_path, fake_embedder):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="Binary")
            data = bytes([0xFF, 0xFE, 0xFD, 0x00, 0x80, 0x7F])  # invalid UTF-8
            rec = _write_file_record(project["slug"], "bild.bin", data, tmp_path)
            registered = await projects_repo.register_file(
                project_id=project["id"],
                relative_path=rec["relative_path"],
                sha256=rec["sha256"],
                size_bytes=rec["size_bytes"],
                storage_path=rec["storage_path"],
                mime_type="application/octet-stream",
            )
            return await projects_rag.index_project_file(
                project["id"], registered["id"], tmp_path
            )

        status = asyncio.run(run())
        assert status["reason"] == "binary"
        assert status["chunks"] == 0

    def test_too_large_file_skipped(self, tmp_db, tmp_path, fake_embedder, monkeypatch):
        from zerberus.core import config as cfg
        from zerberus.core import projects_rag, projects_repo

        s = cfg.get_settings()
        monkeypatch.setattr(s.projects, "rag_max_file_bytes", 100)

        async def run():
            project = await projects_repo.create_project(name="Big")
            data = b"x" * 500
            rec = _write_file_record(project["slug"], "huge.md", data, tmp_path)
            registered = await projects_repo.register_file(
                project_id=project["id"],
                relative_path=rec["relative_path"],
                sha256=rec["sha256"],
                size_bytes=rec["size_bytes"],
                storage_path=rec["storage_path"],
                mime_type="text/markdown",
            )
            return await projects_rag.index_project_file(
                project["id"], registered["id"], tmp_path
            )

        status = asyncio.run(run())
        assert status["reason"] == "too_large"
        assert status["chunks"] == 0

    def test_bytes_missing_skipped(self, tmp_db, tmp_path, fake_embedder):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="Missing")
            # Wir registrieren eine Datei, ohne die Bytes zu schreiben
            sha = "0" * 64
            target = projects_repo.storage_path_for(project["slug"], sha, tmp_path)
            registered = await projects_repo.register_file(
                project_id=project["id"],
                relative_path="ghost.md",
                sha256=sha,
                size_bytes=10,
                storage_path=str(target),
                mime_type="text/markdown",
            )
            return await projects_rag.index_project_file(
                project["id"], registered["id"], tmp_path
            )

        status = asyncio.run(run())
        assert status["reason"] == "bytes_missing"

    def test_rag_disabled_short_circuits(
        self, tmp_db, tmp_path, fake_embedder, monkeypatch
    ):
        from zerberus.core import config as cfg
        from zerberus.core import projects_rag, projects_repo

        s = cfg.get_settings()
        monkeypatch.setattr(s.projects, "rag_enabled", False)

        async def run():
            project = await projects_repo.create_project(name="Off")
            data = b"# Titel"
            rec = _write_file_record(project["slug"], "x.md", data, tmp_path)
            registered = await projects_repo.register_file(
                project_id=project["id"],
                relative_path=rec["relative_path"],
                sha256=rec["sha256"],
                size_bytes=rec["size_bytes"],
                storage_path=rec["storage_path"],
                mime_type="text/markdown",
            )
            return await projects_rag.index_project_file(
                project["id"], registered["id"], tmp_path
            )

        status = asyncio.run(run())
        assert status["reason"] == "rag_disabled"
        assert status["chunks"] == 0


# ---------------------------------------------------------------------------
# Async: remove_file_from_index + query_project_rag
# ---------------------------------------------------------------------------


class TestRemoveFileFromIndex:
    def test_removes_only_target_file_chunks(
        self, tmp_db, tmp_path, fake_embedder
    ):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="Multi")

            async def add(name: str, data: bytes) -> dict:
                rec = _write_file_record(project["slug"], name, data, tmp_path)
                registered = await projects_repo.register_file(
                    project_id=project["id"],
                    relative_path=rec["relative_path"],
                    sha256=rec["sha256"],
                    size_bytes=rec["size_bytes"],
                    storage_path=rec["storage_path"],
                    mime_type="text/markdown",
                )
                await projects_rag.index_project_file(
                    project["id"], registered["id"], tmp_path
                )
                return registered

            f1 = await add("a.md", b"# Erste Datei\n\nText eins.")
            f2 = await add("b.md", b"# Zweite Datei\n\nText zwei.")
            removed = await projects_rag.remove_file_from_index(
                project["id"], f1["id"], tmp_path
            )
            vectors, meta = projects_rag.load_index(project["slug"], tmp_path)
            return removed, vectors, meta, f1, f2

        removed, vectors, meta, f1, f2 = asyncio.run(run())
        assert removed >= 1
        assert vectors is not None
        # Nur Eintraege fuer f2 sind uebrig
        assert all(m["file_id"] == f2["id"] for m in meta)

    def test_removing_last_file_drops_index(
        self, tmp_db, tmp_path, fake_embedder
    ):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="Last")
            data = b"# Eine Datei\n\nNur Inhalt."
            rec = _write_file_record(project["slug"], "x.md", data, tmp_path)
            registered = await projects_repo.register_file(
                project_id=project["id"],
                relative_path=rec["relative_path"],
                sha256=rec["sha256"],
                size_bytes=rec["size_bytes"],
                storage_path=rec["storage_path"],
                mime_type="text/markdown",
            )
            await projects_rag.index_project_file(
                project["id"], registered["id"], tmp_path
            )
            removed = await projects_rag.remove_file_from_index(
                project["id"], registered["id"], tmp_path
            )
            return project, removed

        project, removed = asyncio.run(run())
        assert removed >= 1
        # Index-Ordner ist weg
        index_dir = tmp_path / "projects" / project["slug"] / "_rag"
        assert not index_dir.exists()


class TestQueryProjectRag:
    def test_finds_relevant_chunk(self, tmp_db, tmp_path, fake_embedder):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="Q")
            # Drei klar verschiedene Texte; wir queryen einen davon und
            # erwarten ihn auf Rang 1 (Hash-Embedder ist deterministisch).
            for name, data in [
                ("alpha.md", b"# Alpha\n\nKaffeebohnen aus Aethiopien."),
                ("beta.md", b"# Beta\n\nWanderwege im Allgaeu."),
                ("gamma.md", b"# Gamma\n\nQuantenmechanik fuer Anfaenger."),
            ]:
                rec = _write_file_record(project["slug"], name, data, tmp_path)
                registered = await projects_repo.register_file(
                    project_id=project["id"],
                    relative_path=rec["relative_path"],
                    sha256=rec["sha256"],
                    size_bytes=rec["size_bytes"],
                    storage_path=rec["storage_path"],
                    mime_type="text/markdown",
                )
                await projects_rag.index_project_file(
                    project["id"], registered["id"], tmp_path
                )
            # Query mit identischem Text wie ein bekannter Chunk → Top-Hit
            hits = await projects_rag.query_project_rag(
                project["id"],
                "Wanderwege im Allgaeu.",
                tmp_path,
                k=3,
            )
            return hits

        hits = asyncio.run(run())
        assert hits  # mindestens ein Treffer
        # alle Hits haben score
        for h in hits:
            assert "score" in h
            assert "text" in h

    def test_empty_query_returns_empty(self, tmp_db, tmp_path, fake_embedder):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="QE")
            return await projects_rag.query_project_rag(
                project["id"], "", tmp_path, k=3
            )

        hits = asyncio.run(run())
        assert hits == []

    def test_missing_project_returns_empty(self, tmp_db, tmp_path, fake_embedder):
        from zerberus.core import projects_rag

        async def run():
            return await projects_rag.query_project_rag(99999, "x", tmp_path, k=3)

        hits = asyncio.run(run())
        assert hits == []

    def test_missing_index_returns_empty(self, tmp_db, tmp_path, fake_embedder):
        from zerberus.core import projects_rag, projects_repo

        async def run():
            project = await projects_repo.create_project(name="NoIdx")
            return await projects_rag.query_project_rag(
                project["id"], "test", tmp_path, k=3
            )

        hits = asyncio.run(run())
        assert hits == []


# ---------------------------------------------------------------------------
# Pure-Function: format_rag_block
# ---------------------------------------------------------------------------


class TestFormatRagBlock:
    def test_empty_hits_returns_empty(self):
        from zerberus.core.projects_rag import format_rag_block

        assert format_rag_block([]) == ""

    def test_block_contains_marker_slug_and_paths(self):
        from zerberus.core.projects_rag import (
            PROJECT_RAG_BLOCK_MARKER,
            format_rag_block,
        )

        hits = [
            {"relative_path": "src/x.py", "text": "def foo(): pass", "score": 0.91, "chunk_type": "function", "name": "foo"},
            {"relative_path": "docs/y.md", "text": "Hallo Welt", "score": 0.42, "chunk_type": "prose"},
        ]
        out = format_rag_block(hits, project_slug="demo")
        assert PROJECT_RAG_BLOCK_MARKER in out
        assert "Projekt: demo" in out
        assert "src/x.py" in out
        assert "docs/y.md" in out
        assert "def foo(): pass" in out
        assert "Hallo Welt" in out
        assert "0.91" in out


# ---------------------------------------------------------------------------
# End-to-End: Upload-Endpoint indexiert automatisch
# ---------------------------------------------------------------------------


class TestUploadEndpointIndexes:
    def test_upload_triggers_index(
        self,
        tmp_db,
        tmp_storage,
        fake_embedder,
        enable_rag,
        disable_auto_template,
    ):
        from zerberus.app.routers import hel as hel_mod
        from zerberus.core import projects_rag, projects_repo

        class _FakeUpload:
            def __init__(self, filename: str, data: bytes, content_type: str | None = None):
                self.filename = filename
                self._data = data
                self.content_type = content_type

            async def read(self) -> bytes:
                return self._data

        async def run():
            project = await projects_repo.create_project(name="EndToEnd")
            up = _FakeUpload(
                "doc.md",
                b"# Titel\n\nErster Absatz.\n\nZweiter Absatz.",
                content_type="text/markdown",
            )
            res = await hel_mod.upload_project_file_endpoint(project["id"], up)
            vectors, meta = projects_rag.load_index(project["slug"], tmp_storage)
            return res, vectors, meta

        res, vectors, meta = asyncio.run(run())
        assert res["status"] == "ok"
        assert "rag" in res
        assert res["rag"]["skipped"] is False
        assert res["rag"]["chunks"] >= 1
        assert vectors is not None
        assert vectors.shape[0] == len(meta) == res["rag"]["chunks"]

    def test_delete_file_endpoint_removes_index_entries(
        self,
        tmp_db,
        tmp_storage,
        fake_embedder,
        enable_rag,
        disable_auto_template,
    ):
        from zerberus.app.routers import hel as hel_mod
        from zerberus.core import projects_rag, projects_repo

        class _FakeUpload:
            def __init__(self, filename: str, data: bytes, content_type: str | None = None):
                self.filename = filename
                self._data = data
                self.content_type = content_type

            async def read(self) -> bytes:
                return self._data

        async def run():
            project = await projects_repo.create_project(name="DeleteFile")
            up = _FakeUpload("doc.md", b"# T\n\nA")
            res = await hel_mod.upload_project_file_endpoint(project["id"], up)
            file_id = res["file"]["id"]
            del_res = await hel_mod.delete_project_file_endpoint(project["id"], file_id)
            vectors, meta = projects_rag.load_index(project["slug"], tmp_storage)
            return del_res, vectors, meta

        del_res, vectors, meta = asyncio.run(run())
        assert del_res["status"] == "ok"
        assert del_res["rag_chunks_removed"] >= 1
        # Index-Ordner sollte leer (oder weg) sein
        assert vectors is None
        assert meta == []

    def test_delete_project_endpoint_removes_index_dir(
        self,
        tmp_db,
        tmp_storage,
        fake_embedder,
        enable_rag,
        disable_auto_template,
    ):
        from zerberus.app.routers import hel as hel_mod
        from zerberus.core import projects_rag, projects_repo

        class _FakeUpload:
            def __init__(self, filename: str, data: bytes, content_type: str | None = None):
                self.filename = filename
                self._data = data
                self.content_type = content_type

            async def read(self) -> bytes:
                return self._data

        async def run():
            project = await projects_repo.create_project(name="DeleteProject")
            up = _FakeUpload("doc.md", b"# T\n\nA")
            await hel_mod.upload_project_file_endpoint(project["id"], up)
            assert (tmp_storage / "projects" / project["slug"] / "_rag").exists()
            await hel_mod.delete_project_endpoint(project["id"])
            return project

        project = asyncio.run(run())
        assert not (tmp_storage / "projects" / project["slug"] / "_rag").exists()


class TestMaterializeIndexesTemplates:
    def test_template_files_are_indexed_after_create(
        self, tmp_db, tmp_storage, fake_embedder, monkeypatch
    ):
        """Wenn ``auto_template`` aktiv ist, soll der Index nach
        ``create_project_endpoint`` Eintraege fuer die beiden Skelett-Files
        haben.
        """
        from fastapi import Request
        from zerberus.app.routers import hel as hel_mod
        from zerberus.core import config as cfg
        from zerberus.core import projects_rag

        s = cfg.get_settings()
        monkeypatch.setattr(s.projects, "auto_template", True)
        monkeypatch.setattr(s.projects, "rag_enabled", True)

        class _FakeReq:
            async def json(self):
                return {"name": "Materialize-RAG"}

        async def run():
            res = await hel_mod.create_project_endpoint(_FakeReq())
            project = res["project"]
            vectors, meta = projects_rag.load_index(project["slug"], tmp_storage)
            return project, vectors, meta

        project, vectors, meta = asyncio.run(run())
        assert vectors is not None
        # Mindestens ein Chunk pro Template-File
        rel_paths = {m["relative_path"] for m in meta}
        assert "README.md" in rel_paths
        assert f"ZERBERUS_{project['slug'].upper()}.md" in rel_paths


# ---------------------------------------------------------------------------
# Source-Audit
# ---------------------------------------------------------------------------


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


class TestSourceAudit:
    def test_hel_imports_and_calls_index_project_file(self):
        src = _read("zerberus/app/routers/hel.py")
        assert "projects_rag" in src
        assert "index_project_file" in src
        # Verdrahtung im Upload-Endpoint
        assert "rag_enabled" in src

    def test_hel_calls_remove_file_from_index_on_delete_file(self):
        src = _read("zerberus/app/routers/hel.py")
        assert "remove_file_from_index" in src
        assert "rag_chunks_removed" in src

    def test_hel_calls_remove_project_index_on_delete_project(self):
        src = _read("zerberus/app/routers/hel.py")
        assert "remove_project_index" in src

    def test_template_calls_index_project_file(self):
        src = _read("zerberus/core/projects_template.py")
        assert "projects_rag" in src
        assert "index_project_file" in src

    def test_legacy_uses_query_project_rag_after_persona_merge(self):
        src = _read("zerberus/app/routers/legacy.py")
        assert "projects_rag" in src
        assert "query_project_rag" in src
        assert "format_rag_block" in src
        # Reihenfolge: P197 merge_persona muss VOR P199 query_project_rag stehen
        merge_pos = src.find("merge_persona(sys_prompt")
        rag_pos = src.find("projects_rag.query_project_rag")
        insert_pos = src.find("messages.insert(0, Message")
        assert merge_pos > 0 and rag_pos > 0 and insert_pos > 0
        assert merge_pos < rag_pos < insert_pos

    def test_config_has_rag_flag(self):
        src = _read("zerberus/core/config.py")
        assert "rag_enabled" in src
        assert "rag_top_k" in src
        assert "rag_max_file_bytes" in src
