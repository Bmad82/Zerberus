"""Patch 197 (Phase 5a — Decision 3) — Tests fuer den Persona-Merge-Layer.

Drei Test-Klassen:
- ``TestMergePersona`` — reine String-Funktion ohne I/O. Edge-Cases
  decken ab: kein Overlay, leeres Overlay, nur Addendum, nur Hints,
  beide, leere Hints, Doppel-Hint-Dedupe, Doppel-Injection-Schutz,
  leerer Base-Prompt, Slug-Anzeige.
- ``TestReadActiveProjectId`` — Header-Reader mit kaputten/fehlenden/
  negativen Werten. Akzeptiert sowohl Original- als auch lowercase-
  Schreibweise (FastAPI's ``Headers`` ist case-insensitive, ein
  Test-``dict`` nicht).
- ``TestResolveProjectOverlay`` — async, nutzt die ``tmp_db``-Fixture
  aus ``test_projects_repo.py`` (gleiches Muster).
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# tmp_db Fixture (gleiches Muster wie test_projects_repo.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(monkeypatch):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_persona_merge.db"
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


# ---------------------------------------------------------------------------
# merge_persona — pure function
# ---------------------------------------------------------------------------


class TestMergePersona:
    def test_no_overlay_returns_base_unchanged(self):
        from zerberus.core.persona_merge import merge_persona

        base = "Du bist Nala."
        assert merge_persona(base, None) == base
        assert merge_persona(base, {}) == base

    def test_empty_overlay_fields_returns_base_unchanged(self):
        from zerberus.core.persona_merge import merge_persona

        base = "Du bist Nala."
        result = merge_persona(base, {"system_addendum": "", "tone_hints": []})
        assert result == base

    def test_only_addendum_appended(self):
        from zerberus.core.persona_merge import (
            PROJECT_BLOCK_MARKER,
            merge_persona,
        )

        base = "Du bist Nala."
        result = merge_persona(
            base,
            {"system_addendum": "Verwende SQL-Terminologie.", "tone_hints": []},
        )
        assert result.startswith(base)
        assert PROJECT_BLOCK_MARKER in result
        assert "Verwende SQL-Terminologie." in result
        # Keine Tonfall-Sektion, wenn keine Hints.
        assert "Tonfall-Hinweise:" not in result

    def test_only_tone_hints_appended(self):
        from zerberus.core.persona_merge import (
            PROJECT_BLOCK_MARKER,
            merge_persona,
        )

        base = "Du bist Nala."
        result = merge_persona(
            base,
            {"system_addendum": "", "tone_hints": ["foermlich", "praezise"]},
        )
        assert result.startswith(base)
        assert PROJECT_BLOCK_MARKER in result
        assert "Tonfall-Hinweise:" in result
        assert "- foermlich" in result
        assert "- praezise" in result

    def test_both_addendum_and_hints(self):
        from zerberus.core.persona_merge import merge_persona

        base = "Du bist Nala."
        result = merge_persona(
            base,
            {
                "system_addendum": "Kontext: Backend-Refactor.",
                "tone_hints": ["technisch", "knapp"],
            },
        )
        assert "Kontext: Backend-Refactor." in result
        assert "- technisch" in result
        assert "- knapp" in result
        # Addendum-Position vor Hints-Sektion
        assert result.index("Kontext: Backend-Refactor.") < result.index("Tonfall-Hinweise:")

    def test_tone_hints_dedupe_case_insensitive(self):
        from zerberus.core.persona_merge import merge_persona

        result = merge_persona(
            "base",
            {"system_addendum": "", "tone_hints": ["foermlich", "Foermlich", "FOERMLICH", "praezise"]},
        )
        # Erstes Vorkommen gewinnt (Schreibweise behalten).
        assert result.count("foermlich") == 1
        assert result.count("Foermlich") == 0
        assert result.count("FOERMLICH") == 0
        assert "- praezise" in result

    def test_tone_hints_strips_empty_and_whitespace(self):
        from zerberus.core.persona_merge import merge_persona

        result = merge_persona(
            "base",
            {"system_addendum": "", "tone_hints": ["", "  ", "  ok  ", None, 42]},
        )
        # Nur "ok" (getrimmt) ueberlebt.
        assert "- ok" in result
        # Keine "- " Zeile mit leerem Inhalt.
        assert "- \n" not in result
        assert "- 42" not in result  # int wird gefiltert

    def test_double_injection_protection(self):
        """Wenn der Block-Marker schon im Base steht, kein zweiter Block."""
        from zerberus.core.persona_merge import (
            PROJECT_BLOCK_MARKER,
            merge_persona,
        )

        base = f"Du bist Nala.\n\n---\n{PROJECT_BLOCK_MARKER}\nschon da."
        result = merge_persona(base, {"system_addendum": "neu", "tone_hints": []})
        # Marker kommt nur einmal vor.
        assert result.count(PROJECT_BLOCK_MARKER) == 1
        assert "neu" not in result

    def test_empty_base_with_overlay_returns_only_block(self):
        from zerberus.core.persona_merge import (
            PROJECT_BLOCK_MARKER,
            merge_persona,
        )

        result = merge_persona(
            "",
            {"system_addendum": "Nur Projekt.", "tone_hints": ["scharf"]},
        )
        # Kein Leading-Newline, sonst stoert es das LLM.
        assert not result.startswith("\n")
        assert PROJECT_BLOCK_MARKER in result
        assert "Nur Projekt." in result
        assert "- scharf" in result

    def test_project_slug_appears_in_block(self):
        from zerberus.core.persona_merge import merge_persona

        result = merge_persona(
            "base",
            {"system_addendum": "x", "tone_hints": []},
            project_slug="ai-research",
        )
        assert "Projekt: ai-research" in result

    def test_project_slug_omitted_when_none(self):
        from zerberus.core.persona_merge import merge_persona

        result = merge_persona(
            "base",
            {"system_addendum": "x", "tone_hints": []},
        )
        assert "Projekt:" not in result

    def test_overlay_with_unexpected_types_does_not_crash(self):
        """Defensive: tone_hints koennte ein String sein (UI-Fehler)."""
        from zerberus.core.persona_merge import merge_persona

        result = merge_persona(
            "base",
            {"system_addendum": "x", "tone_hints": "not-a-list"},
        )
        # String wird zu leerer Liste → keine Tonfall-Sektion.
        assert "Tonfall-Hinweise:" not in result
        assert "x" in result  # Addendum trotzdem da

    def test_separator_format(self):
        """Block beginnt mit Leerzeile + Trennstrich, damit er sich vom
        Base-Prompt absetzt (kosmetisch + LLM-Lesbarkeit)."""
        from zerberus.core.persona_merge import merge_persona

        result = merge_persona("base", {"system_addendum": "x", "tone_hints": []})
        assert "\n\n---\n" in result


# ---------------------------------------------------------------------------
# read_active_project_id — header-reader
# ---------------------------------------------------------------------------


class TestReadActiveProjectId:
    def test_missing_header(self):
        from zerberus.core.persona_merge import read_active_project_id

        assert read_active_project_id({}) is None

    def test_none_headers(self):
        from zerberus.core.persona_merge import read_active_project_id

        assert read_active_project_id(None) is None

    def test_empty_string(self):
        from zerberus.core.persona_merge import read_active_project_id

        assert read_active_project_id({"X-Active-Project-Id": ""}) is None
        assert read_active_project_id({"X-Active-Project-Id": "   "}) is None

    def test_valid_id(self):
        from zerberus.core.persona_merge import read_active_project_id

        assert read_active_project_id({"X-Active-Project-Id": "42"}) == 42
        assert read_active_project_id({"X-Active-Project-Id": "  7  "}) == 7

    def test_lowercase_fallback(self):
        """Plain-dict (case-sensitive) mit lowercase-Key — Reader fallback."""
        from zerberus.core.persona_merge import read_active_project_id

        assert read_active_project_id({"x-active-project-id": "5"}) == 5

    def test_non_numeric(self):
        from zerberus.core.persona_merge import read_active_project_id

        assert read_active_project_id({"X-Active-Project-Id": "abc"}) is None
        assert read_active_project_id({"X-Active-Project-Id": "1.5"}) is None

    def test_negative_or_zero(self):
        """Projekt-IDs sind in SQLite immer positiv — defensive."""
        from zerberus.core.persona_merge import read_active_project_id

        assert read_active_project_id({"X-Active-Project-Id": "-1"}) is None
        assert read_active_project_id({"X-Active-Project-Id": "0"}) is None


# ---------------------------------------------------------------------------
# resolve_project_overlay — async, mit DB
# ---------------------------------------------------------------------------


class TestResolveProjectOverlay:
    def test_none_id_returns_none(self, tmp_db):
        from zerberus.core.persona_merge import resolve_project_overlay

        overlay, slug = asyncio.run(resolve_project_overlay(None))
        assert overlay is None
        assert slug is None

    def test_unknown_id_returns_none(self, tmp_db):
        from zerberus.core.persona_merge import resolve_project_overlay

        overlay, slug = asyncio.run(resolve_project_overlay(99999))
        assert overlay is None
        assert slug is None

    def test_existing_project_returns_overlay(self, tmp_db):
        from zerberus.core.persona_merge import resolve_project_overlay
        from zerberus.core.projects_repo import create_project

        async def run():
            proj = await create_project(
                "Backend Refactor",
                description="",
                persona_overlay={
                    "system_addendum": "SQL-Style: ANSI.",
                    "tone_hints": ["technisch"],
                },
            )
            return await resolve_project_overlay(proj["id"])

        overlay, slug = asyncio.run(run())
        assert overlay is not None
        assert overlay["system_addendum"] == "SQL-Style: ANSI."
        assert overlay["tone_hints"] == ["technisch"]
        assert slug == "backend-refactor"

    def test_archived_project_returns_none_overlay_but_slug(self, tmp_db):
        """Archivierte Projekte liefern ``(None, slug)`` — Caller kann loggen."""
        from zerberus.core.persona_merge import resolve_project_overlay
        from zerberus.core.projects_repo import archive_project, create_project

        async def run():
            proj = await create_project(
                "Alt",
                persona_overlay={"system_addendum": "x", "tone_hints": []},
            )
            await archive_project(proj["id"])
            return await resolve_project_overlay(proj["id"])

        overlay, slug = asyncio.run(run())
        assert overlay is None
        assert slug == "alt"

    def test_archived_project_with_skip_false(self, tmp_db):
        """Mit ``skip_archived=False`` kommt der Overlay trotzdem zurueck."""
        from zerberus.core.persona_merge import resolve_project_overlay
        from zerberus.core.projects_repo import archive_project, create_project

        async def run():
            proj = await create_project(
                "Alt",
                persona_overlay={"system_addendum": "trotzdem", "tone_hints": []},
            )
            await archive_project(proj["id"])
            return await resolve_project_overlay(proj["id"], skip_archived=False)

        overlay, slug = asyncio.run(run())
        assert overlay is not None
        assert overlay["system_addendum"] == "trotzdem"
        assert slug == "alt"

    def test_project_without_overlay_returns_empty_dict(self, tmp_db):
        """Project ohne Overlay → leerer Dict (nicht None) — die UI-Logik
        darf direkt drauf zugreifen ohne None-Check."""
        from zerberus.core.persona_merge import resolve_project_overlay
        from zerberus.core.projects_repo import create_project

        async def run():
            proj = await create_project("OhneOverlay")
            return await resolve_project_overlay(proj["id"])

        overlay, slug = asyncio.run(run())
        # EMPTY_OVERLAY = {"system_addendum": "", "tone_hints": []}
        assert isinstance(overlay, dict)
        assert overlay.get("system_addendum") == ""
        assert overlay.get("tone_hints") == []
        assert slug == "ohneoverlay"


# ---------------------------------------------------------------------------
# End-to-End: chat_completions mit Header → System-Prompt enthaelt Overlay
# ---------------------------------------------------------------------------


def _build_request_with_project(project_id: int | None, profile_name: str = "alice"):
    """Mock fuer FastAPI Request — wie in test_patch184_persona, plus
    optionaler ``X-Active-Project-Id``-Header."""
    from types import SimpleNamespace

    state = SimpleNamespace(
        profile_name=profile_name,
        permission_level="admin",
        allowed_model=None,
        temperature=None,
    )
    headers: dict[str, str] = {"X-Session-ID": "test-session"}
    if project_id is not None:
        headers["X-Active-Project-Id"] = str(project_id)
    return SimpleNamespace(state=state, headers=headers)


class TestE2EChatCompletionsWithProjectOverlay:
    """End-to-End-Test ueber den ``/v1/chat/completions``-Pfad: aktives
    Projekt per Header → finaler System-Prompt enthaelt Overlay-Block."""

    @pytest.fixture
    def env(self, tmp_path, monkeypatch, tmp_db):
        """Kombiniert Settings-Cache, ``chdir`` + Persona-Files + tmp_db."""
        from pathlib import Path as P

        from zerberus.core.config import get_settings

        get_settings()  # Singleton-Cache befuellen VOR chdir
        monkeypatch.chdir(tmp_path)
        P("system_prompt_alice.json").write_text(
            '{"prompt": "Du bist Alice, praezise und knapp."}',
            encoding="utf-8",
        )
        P("system_prompt.json").write_text(
            '{"prompt": "Default Nala-Stil."}',
            encoding="utf-8",
        )
        return tmp_path

    def test_overlay_appears_in_system_prompt(self, env, monkeypatch):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.config import get_settings
        from zerberus.core.llm import LLMService
        from zerberus.core.persona_merge import PROJECT_BLOCK_MARKER
        from zerberus.core.projects_repo import create_project

        captured: dict = {}

        async def fake_call(self, messages, session_id, model_override=None, temperature_override=None):
            captured["messages"] = list(messages)
            return ("ok", "test-model", 1, 1, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        async def run():
            proj = await create_project(
                "Backend-Refactor",
                persona_overlay={
                    "system_addendum": "Verwende ANSI-SQL.",
                    "tone_hints": ["technisch", "knapp"],
                },
            )
            req = legacy_mod.ChatCompletionRequest(
                messages=[legacy_mod.Message(role="user", content="Wie gehts?")]
            )
            request = _build_request_with_project(proj["id"], profile_name="alice")
            await legacy_mod.chat_completions(request, req, get_settings())
            return proj

        proj = asyncio.run(run())

        sys_msgs = [m for m in captured["messages"] if m["role"] == "system"]
        assert sys_msgs, "Kein System-Message im LLM-Call"
        sys_content = sys_msgs[0]["content"]

        # User-Persona kommt durch
        assert "Alice, praezise" in sys_content
        # Overlay-Block ist drin
        assert PROJECT_BLOCK_MARKER in sys_content
        assert "Verwende ANSI-SQL." in sys_content
        assert "- technisch" in sys_content
        assert "- knapp" in sys_content
        # Slug-Zeile ist drin
        assert f"Projekt: {proj['slug']}" in sys_content
        # AKTIVE-PERSONA-Wrap umschliesst auch das Overlay (Reihenfolge:
        # AKTIVE PERSONA Marker zuerst, dann Persona+Overlay).
        assert sys_content.find("AKTIVE PERSONA") < sys_content.find(PROJECT_BLOCK_MARKER)

    def test_no_header_means_no_overlay(self, env, monkeypatch):
        """Ohne ``X-Active-Project-Id``-Header bleibt der Prompt
        wie vor P197 (kein Block-Marker)."""
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.config import get_settings
        from zerberus.core.llm import LLMService
        from zerberus.core.persona_merge import PROJECT_BLOCK_MARKER

        captured: dict = {}

        async def fake_call(self, messages, session_id, model_override=None, temperature_override=None):
            captured["messages"] = list(messages)
            return ("ok", "m", 1, 1, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        req = legacy_mod.ChatCompletionRequest(
            messages=[legacy_mod.Message(role="user", content="Hi")]
        )
        request = _build_request_with_project(None, profile_name="alice")
        asyncio.run(legacy_mod.chat_completions(request, req, get_settings()))

        sys_msgs = [m for m in captured["messages"] if m["role"] == "system"]
        assert sys_msgs
        assert PROJECT_BLOCK_MARKER not in sys_msgs[0]["content"]
        # Persona ist trotzdem da (Regression-Schutz fuer P184)
        assert "Alice, praezise" in sys_msgs[0]["content"]

    def test_unknown_project_id_does_not_crash(self, env, monkeypatch):
        """Unbekannte Projekt-ID → Endpoint laeuft normal, nur ohne Overlay."""
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.config import get_settings
        from zerberus.core.llm import LLMService
        from zerberus.core.persona_merge import PROJECT_BLOCK_MARKER

        captured: dict = {}

        async def fake_call(self, messages, session_id, model_override=None, temperature_override=None):
            captured["messages"] = list(messages)
            return ("ok", "m", 1, 1, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        req = legacy_mod.ChatCompletionRequest(
            messages=[legacy_mod.Message(role="user", content="Hi")]
        )
        request = _build_request_with_project(99999, profile_name="alice")
        asyncio.run(legacy_mod.chat_completions(request, req, get_settings()))

        sys_msgs = [m for m in captured["messages"] if m["role"] == "system"]
        assert sys_msgs
        assert PROJECT_BLOCK_MARKER not in sys_msgs[0]["content"]

    def test_archived_project_skipped(self, env, monkeypatch):
        from zerberus.app.routers import legacy as legacy_mod
        from zerberus.core.config import get_settings
        from zerberus.core.llm import LLMService
        from zerberus.core.persona_merge import PROJECT_BLOCK_MARKER
        from zerberus.core.projects_repo import archive_project, create_project

        captured: dict = {}

        async def fake_call(self, messages, session_id, model_override=None, temperature_override=None):
            captured["messages"] = list(messages)
            return ("ok", "m", 1, 1, 0.0)

        monkeypatch.setattr(LLMService, "call", fake_call)
        monkeypatch.setattr(legacy_mod, "_ORCH_PIPELINE_OK", False)

        async def run():
            proj = await create_project(
                "ArchiveTest",
                persona_overlay={"system_addendum": "darf nicht durch", "tone_hints": []},
            )
            await archive_project(proj["id"])
            req = legacy_mod.ChatCompletionRequest(
                messages=[legacy_mod.Message(role="user", content="Hi")]
            )
            request = _build_request_with_project(proj["id"], profile_name="alice")
            await legacy_mod.chat_completions(request, req, get_settings())

        asyncio.run(run())

        sys_msgs = [m for m in captured["messages"] if m["role"] == "system"]
        assert sys_msgs
        assert PROJECT_BLOCK_MARKER not in sys_msgs[0]["content"]
        assert "darf nicht durch" not in sys_msgs[0]["content"]


# ---------------------------------------------------------------------------
# Source-Audit: Verdrahtung in legacy.py ist wirklich aktiv
# ---------------------------------------------------------------------------


class TestSourceAudit:
    @pytest.fixture(scope="class")
    def legacy_src(self) -> str:
        return Path(__file__).resolve().parents[1].joinpath(
            "app", "routers", "legacy.py"
        ).read_text(encoding="utf-8")

    def test_persona197_log_marker_present(self, legacy_src):
        """Der ``[PERSONA-197]``-Log muss in legacy.py stehen, damit kuenftige
        Persona-Merge-Bugs im Server-Log diagnostizierbar sind."""
        assert "[PERSONA-197]" in legacy_src

    def test_persona_merge_imported(self, legacy_src):
        assert "from zerberus.core.persona_merge import" in legacy_src
        assert "merge_persona" in legacy_src
        assert "read_active_project_id" in legacy_src
        assert "resolve_project_overlay" in legacy_src

    def test_merge_runs_before_wrap(self, legacy_src):
        """Reihenfolge: erst Overlay anhaengen, DANN _wrap_persona — sonst
        umschliesst der AKTIVE-PERSONA-Marker das Overlay nicht."""
        idx_merge = legacy_src.find("merge_persona(sys_prompt")
        idx_wrap = legacy_src.find("_wrap_persona(sys_prompt)")
        assert idx_merge > 0 and idx_wrap > 0
        assert idx_merge < idx_wrap, (
            "merge_persona muss VOR _wrap_persona laufen, damit der "
            "AKTIVE-PERSONA-Marker auch das Overlay umschliesst"
        )
