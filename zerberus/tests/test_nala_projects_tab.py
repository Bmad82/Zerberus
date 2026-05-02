"""Patch 201 — Tests fuer Nala-Tab "Projekte" + Header-Setter.

Drei Schichten:

1. **Endpoint-Tests** fuer ``GET /nala/projects`` — auth-pflichtig (request.state.profile_name),
   liefert nur nicht-archivierte Projekte ohne ``persona_overlay``.
2. **Source-Audit-Tests** fuer NALA_HTML — Tab-Button, Panel, JS-Funktionen,
   Header-Injektion in ``profileHeaders``, Active-Project-Chip im Header, CSS.
3. **XSS-Sicherheit** — Renderer escaped Project-Felder via ``escapeProjectText``.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Fixtures (Pattern aus test_projects_endpoints.py uebernommen)
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(monkeypatch):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    tmpdir = tempfile.mkdtemp()
    db_file = Path(tmpdir) / "test_nala_projects.db"
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


@pytest.fixture(autouse=True)
def _disable_auto_template(monkeypatch):
    """P198 Templates wuerden Counts verwaschen — fuer Nala-Tests irrelevant."""
    from zerberus.core import config as cfg
    s = cfg.get_settings()
    monkeypatch.setattr(s.projects, "auto_template", False)


@pytest.fixture(scope="module")
def nala_src() -> str:
    return (
        Path(__file__).resolve().parents[1] / "app" / "routers" / "nala.py"
    ).read_text(encoding="utf-8")


class _FakeState:
    def __init__(self, profile_name=None):
        self.profile_name = profile_name


class _FakeRequest:
    def __init__(self, profile_name=None):
        self.state = _FakeState(profile_name=profile_name)


# ---------------------------------------------------------------------------
# 1) Endpoint /nala/projects
# ---------------------------------------------------------------------------

class TestNalaProjectsEndpoint:
    def test_401_ohne_login(self, tmp_db):
        from zerberus.app.routers.nala import nala_projects_list

        with pytest.raises(HTTPException) as exc:
            asyncio.run(nala_projects_list(_FakeRequest(profile_name=None)))
        assert exc.value.status_code == 401

    def test_empty_list(self, tmp_db):
        from zerberus.app.routers.nala import nala_projects_list

        res = asyncio.run(nala_projects_list(_FakeRequest(profile_name="loki")))
        assert res == {"projects": [], "count": 0}

    def test_lists_created_projects(self, tmp_db):
        from zerberus.app.routers.hel import create_project_endpoint
        from zerberus.app.routers.nala import nala_projects_list

        class _CreateReq:
            def __init__(self, payload):
                self._p = payload
            async def json(self):
                return self._p

        async def run():
            await create_project_endpoint(_CreateReq({"name": "Erstes"}))
            await create_project_endpoint(_CreateReq({"name": "Zweites"}))
            return await nala_projects_list(_FakeRequest(profile_name="loki"))

        res = asyncio.run(run())
        assert res["count"] == 2
        slugs = sorted(p["slug"] for p in res["projects"])
        assert slugs == ["erstes", "zweites"]

    def test_archived_versteckt(self, tmp_db):
        from zerberus.app.routers.hel import (
            archive_project_endpoint,
            create_project_endpoint,
        )
        from zerberus.app.routers.nala import nala_projects_list

        class _CreateReq:
            def __init__(self, payload):
                self._p = payload
            async def json(self):
                return self._p

        async def run():
            await create_project_endpoint(_CreateReq({"name": "Aktiv"}))
            arch = await create_project_endpoint(_CreateReq({"name": "Archiviert"}))
            await archive_project_endpoint(arch["project"]["id"])
            return await nala_projects_list(_FakeRequest(profile_name="loki"))

        res = asyncio.run(run())
        assert res["count"] == 1
        assert res["projects"][0]["slug"] == "aktiv"

    def test_persona_overlay_NICHT_im_response(self, tmp_db):
        """Admin-Geheimnis: Nala-User darf persona_overlay nicht sehen."""
        from zerberus.app.routers.hel import create_project_endpoint
        from zerberus.app.routers.nala import nala_projects_list

        class _CreateReq:
            def __init__(self, payload):
                self._p = payload
            async def json(self):
                return self._p

        overlay = {"system_addendum": "GEHEIM", "tone_hints": ["intern"]}

        async def run():
            await create_project_endpoint(
                _CreateReq({"name": "Mit Overlay", "persona_overlay": overlay})
            )
            return await nala_projects_list(_FakeRequest(profile_name="loki"))

        res = asyncio.run(run())
        assert res["count"] == 1
        proj = res["projects"][0]
        assert "persona_overlay" not in proj
        # Sanity: keine Spur des Overlay-Inhalts im Response
        flat = repr(proj)
        assert "GEHEIM" not in flat
        assert "intern" not in flat

    def test_response_felder_minimal(self, tmp_db):
        """Nur die fuer den Picker noetigen Felder, kein Storage-Pfad o.ae."""
        from zerberus.app.routers.hel import create_project_endpoint
        from zerberus.app.routers.nala import nala_projects_list

        class _CreateReq:
            def __init__(self, payload):
                self._p = payload
            async def json(self):
                return self._p

        async def run():
            await create_project_endpoint(_CreateReq({"name": "X", "description": "kurze beschreibung"}))
            return await nala_projects_list(_FakeRequest(profile_name="loki"))

        res = asyncio.run(run())
        proj = res["projects"][0]
        # Pflichtfelder fuer den Picker
        assert "id" in proj and "slug" in proj and "name" in proj
        assert "description" in proj and "updated_at" in proj
        # Description durchgereicht
        assert proj["description"] == "kurze beschreibung"


# ---------------------------------------------------------------------------
# 2) Source-Audit NALA_HTML
# ---------------------------------------------------------------------------

class TestNalaHtmlProjectsTab:
    def test_tab_button_existiert(self, nala_src):
        assert 'data-tab="projects"' in nala_src
        assert "switchSettingsTab('projects')" in nala_src
        assert "📁 Projekte" in nala_src

    def test_tab_panel_existiert(self, nala_src):
        assert 'id="settings-tab-projects"' in nala_src
        assert 'id="nala-projects-list"' in nala_src
        assert 'id="nala-projects-active"' in nala_src

    def test_active_project_chip_im_header(self, nala_src):
        assert 'id="active-project-chip"' in nala_src
        assert "active-project-chip" in nala_src
        # CSS-Klasse muss definiert sein
        assert ".active-project-chip {" in nala_src or ".active-project-chip{" in nala_src

    def test_chip_klick_oeffnet_projects_tab(self, nala_src):
        # Onclick muss Settings oeffnen und auf Projects-Tab springen
        assert "openSettingsModal(); switchSettingsTab('projects');" in nala_src

    def test_chip_css_touch_target(self, nala_src):
        # Mobile-first: chip muss ueber 22px hoch sein (per padding/min-height)
        assert "min-height: 22px" in nala_src or "min-height:22px" in nala_src

    def test_lazy_load_in_switchSettingsTab(self, nala_src):
        # Wenn Projects-Tab aktiviert wird, soll loadNalaProjects laufen
        assert "if (tab === 'projects')" in nala_src
        assert "loadNalaProjects()" in nala_src


class TestNalaHtmlProjectsJs:
    def test_js_funktionen_definiert(self, nala_src):
        for fn in [
            "function getActiveProjectId",
            "function getActiveProjectMeta",
            "function setActiveProject",
            "function clearActiveProject",
            "function renderActiveProjectChip",
            "function renderNalaProjectsActive",
            "function renderNalaProjectsList",
            "function selectActiveProjectById",
            "async function loadNalaProjects",
        ]:
            assert fn in nala_src, f"Fehlt: {fn}"

    def test_localStorage_keys(self, nala_src):
        assert "'nala_active_project_id'" in nala_src
        assert "'nala_active_project_meta'" in nala_src

    def test_fetch_endpoint(self, nala_src):
        assert "fetch('/nala/projects'" in nala_src

    def test_handle401_im_load(self, nala_src):
        # Auth-Expired sauber behandelt
        assert "res.status === 401" in nala_src or "handle401()" in nala_src


class TestProfileHeadersInjection:
    def test_x_active_project_id_header(self, nala_src):
        assert "'X-Active-Project-Id'" in nala_src

    def test_header_in_profileHeaders_zentral(self, nala_src):
        # Die Injektion muss IN profileHeaders sein, damit ALLE Calls
        # (Chat, Voice, Whisper, ...) den Header bekommen — nicht nur der Chat.
        idx = nala_src.find("function profileHeaders(extra)")
        assert idx > 0
        # Suche das Ende der Funktion (naechstes "function " auf einer Zeile)
        end_idx = nala_src.find("function ", idx + 30)
        assert end_idx > idx
        body = nala_src[idx:end_idx]
        assert "'X-Active-Project-Id'" in body
        assert "getActiveProjectId" in body


class TestNalaHtmlProjectsRendererXss:
    def test_escape_funktion_existiert(self, nala_src):
        assert "function escapeProjectText" in nala_src

    def test_renderer_nutzt_escape(self, nala_src):
        # Listing render escaped name + slug + description
        idx = nala_src.find("function renderNalaProjectsList")
        assert idx > 0
        end = nala_src.find("function ", idx + 30)
        body = nala_src[idx:end]
        # Mindestens drei Aufrufe (name, slug, description)
        count = body.count("escapeProjectText(")
        assert count >= 3, f"escapeProjectText nur {count}x — XSS-Risiko"


# ---------------------------------------------------------------------------
# 3) Zombie-ID-Schutz: aktives Projekt fliegt raus, wenn nicht mehr in Liste
# ---------------------------------------------------------------------------

class TestZombieIdHandling:
    def test_load_clears_zombie_active_id(self, nala_src):
        """JS muss aktives Projekt loeschen, wenn es nicht mehr in der Liste auftaucht."""
        idx = nala_src.find("async function loadNalaProjects")
        assert idx > 0
        end = nala_src.find("function ", idx + 30)
        body = nala_src[idx:end]
        assert "items.find(p => p.id === activeId)" in body
        assert "clearActiveProject()" in body
