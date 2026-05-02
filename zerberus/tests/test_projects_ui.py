"""Patch 195 (Phase 5a #1) — Source-Inspection-Tests fuer den Hel-UI-Tab "Projekte".

Pattern wie ``test_patch170_hel_kosmetik.py``: liest ``hel.py`` als Text und
assertet auf Strings/Markups. So wird das HTML/JS getestet, ohne echten
Browser/Playwright. Funktionale Tests fuer die Endpoints decken bereits
``test_projects_endpoints.py`` ab.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def hel_src() -> str:
    path = Path(__file__).resolve().parents[1] / "app" / "routers" / "hel.py"
    return path.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────
# Tab-Button in der Navigation
# ──────────────────────────────────────────────────────────────────────


class TestTabButton:
    def test_tab_button_existiert(self, hel_src):
        assert 'data-tab="projects"' in hel_src
        assert "activateTab('projects')" in hel_src

    def test_tab_button_hat_ordner_icon(self, hel_src):
        # &#128193; = 📁 (file folder)
        assert "&#128193; Projekte" in hel_src

    def test_tab_button_zwischen_huginn_und_links(self, hel_src):
        # Reihenfolge der Tabs muss stimmen
        idx_huginn = hel_src.index('data-tab="huginn"')
        idx_projects = hel_src.index('data-tab="projects"')
        idx_nav = hel_src.index('data-tab="nav"')
        assert idx_huginn < idx_projects < idx_nav


# ──────────────────────────────────────────────────────────────────────
# Section-Body
# ──────────────────────────────────────────────────────────────────────


class TestSectionBody:
    def test_section_existiert(self, hel_src):
        assert 'id="section-projects"' in hel_src
        assert 'id="body-projects"' in hel_src

    def test_section_hat_anlegen_button(self, hel_src):
        assert "openProjectForm()" in hel_src
        assert "+ Projekt anlegen" in hel_src

    def test_archivierte_anzeigen_checkbox(self, hel_src):
        assert 'id="projectsShowArchived"' in hel_src
        assert "Archivierte anzeigen" in hel_src

    def test_tabelle_mit_pflichtspalten(self, hel_src):
        # Header-Spalten in der Reihenfolge: Slug, Name, Updated, Status, Aktionen
        assert 'id="projectsTable"' in hel_src
        assert 'id="projectsTableBody"' in hel_src
        for col in ("Slug", "Name", "Updated", "Status", "Aktionen"):
            assert f">{col}<" in hel_src, f"Spalte '{col}' fehlt"

    def test_form_overlay_existiert(self, hel_src):
        assert 'id="projectFormOverlay"' in hel_src
        assert 'id="projectFormName"' in hel_src
        assert 'id="projectFormDescription"' in hel_src
        assert 'id="projectFormSlug"' in hel_src

    def test_persona_overlay_felder(self, hel_src):
        assert 'id="projectFormSystemAddendum"' in hel_src
        assert 'id="projectFormToneHints"' in hel_src
        # Hinweis auf Komma-Liste fuer Tone-Hints
        assert "Komma-Liste" in hel_src

    def test_detail_card_fuer_dateien(self, hel_src):
        assert 'id="projectDetailCard"' in hel_src
        assert 'id="projectFilesList"' in hel_src
        # Patch 196: Drop-Zone hat den "Upload kommt in P196"-Platzhalter aus P195 abgeloest
        assert 'id="projectDropZone"' in hel_src


# ──────────────────────────────────────────────────────────────────────
# JS-Funktionen
# ──────────────────────────────────────────────────────────────────────


class TestJsFunctions:
    def test_load_projects(self, hel_src):
        assert "async function loadProjects()" in hel_src
        assert "/hel/admin/projects?include_archived=" in hel_src

    def test_create_project_via_post(self, hel_src):
        # saveProjectForm macht POST auf /hel/admin/projects
        assert "async function saveProjectForm()" in hel_src
        assert "'POST'" in hel_src.split("async function saveProjectForm()")[1][:2000]

    def test_edit_project_via_patch(self, hel_src):
        # Edit-Pfad in saveProjectForm verwendet PATCH
        save_block = hel_src.split("async function saveProjectForm()")[1][:2000]
        assert "'PATCH'" in save_block
        assert "function editProject(" in hel_src

    def test_archive_unarchive_delete(self, hel_src):
        assert "async function archiveProject(" in hel_src
        assert "async function unarchiveProject(" in hel_src
        assert "async function deleteProject(" in hel_src
        # Endpoints
        assert "'/archive'" in hel_src or "/archive'" in hel_src
        assert "/unarchive'" in hel_src
        # DELETE-Methode fuer deleteProject
        del_block = hel_src.split("async function deleteProject(")[1][:1500]
        assert "'DELETE'" in del_block

    def test_delete_hat_confirm_dialog(self, hel_src):
        del_block = hel_src.split("async function deleteProject(")[1][:1500]
        assert "confirm(" in del_block
        # Wort "UNWIDERRUFLICH" als Warnung
        assert "UNWIDERRUFLICH" in del_block

    def test_load_project_files(self, hel_src):
        assert "async function loadProjectFiles(" in hel_src
        assert "/files'" in hel_src or "/files'," in hel_src

    def test_form_funktionen(self, hel_src):
        assert "function openProjectForm(" in hel_src
        assert "function closeProjectForm(" in hel_src

    def test_persona_overlay_serialisierung(self, hel_src):
        # tone_hints werden aus Komma-Liste in Array konvertiert
        save_block = hel_src.split("async function saveProjectForm()")[1][:2000]
        assert "split(','" in save_block
        assert "system_addendum" in save_block
        assert "tone_hints" in save_block


# ──────────────────────────────────────────────────────────────────────
# Lazy-Load-Verdrahtung in activateTab
# ──────────────────────────────────────────────────────────────────────


class TestActivateTabIntegration:
    def test_lazy_load_aufruf_in_activate_tab(self, hel_src):
        # activateTab muss loadProjects() einmal triggern, wenn 'projects' aktiviert wird
        assert "if (id === 'projects') loadProjects();" in hel_src


# ──────────────────────────────────────────────────────────────────────
# Mobile-First-Konventionen (44px Touch-Targets)
# ──────────────────────────────────────────────────────────────────────


class TestMobileFirst:
    def test_haupt_buttons_haben_44px_touch_target(self, hel_src):
        # Hol den projects-Section-Block raus und pruefe min-height:44px
        section = hel_src.split('id="body-projects"')[1].split('id="section-projects-end"')[0] \
            if 'id="section-projects-end"' in hel_src else hel_src.split('id="body-projects"')[1][:6000]
        # Mindestens das Anlegen-Button und Form-Inputs muessen 44px haben
        assert "min-height:44px" in section
        # Mehrfach (Anlegen + Reload + Form-Felder + Buttons im Form)
        assert section.count("min-height:44px") >= 4


# ──────────────────────────────────────────────────────────────────────
# Patch 196 — Datei-Upload-UI
# ──────────────────────────────────────────────────────────────────────


class TestP196DropZone:
    def test_drop_zone_existiert(self, hel_src):
        assert 'id="projectDropZone"' in hel_src
        assert 'id="projectFileInput"' in hel_src
        # Multiple-Upload erlaubt
        assert 'id="projectFileInput" multiple' in hel_src

    def test_drop_zone_zeigt_max_size_und_blocklist(self, hel_src):
        zone_block = hel_src.split('id="projectDropZone"')[1][:1000]
        assert "MB" in zone_block
        assert ".exe" in zone_block

    def test_progress_container_existiert(self, hel_src):
        assert 'id="projectUploadProgress"' in hel_src

    def test_alte_p196_ankuendigung_entfernt(self, hel_src):
        # Die "Upload kommt in P196." Note aus P195 muss raus sein
        assert "Upload kommt in P196" not in hel_src


class TestP196JsFunctions:
    def test_setup_drop_function(self, hel_src):
        assert "function _setupProjectFileDrop(" in hel_src

    def test_upload_function_uses_xhr_progress(self, hel_src):
        assert "function _uploadProjectFiles(" in hel_src
        # Progress-Event-Listener fuer den Upload
        assert "xhr.upload.addEventListener('progress'" in hel_src
        # FormData fuer Multipart
        assert "new FormData()" in hel_src
        # POST auf den richtigen Endpoint
        upload_block = hel_src.split("function _uploadProjectFiles(")[1][:3000]
        assert "/hel/admin/projects/" in upload_block
        assert "/files'" in upload_block

    def test_drag_and_drop_events_wired(self, hel_src):
        setup_block = hel_src.split("function _setupProjectFileDrop(")[1][:2500]
        for ev in ("dragenter", "dragover", "dragleave", "drop"):
            assert ev in setup_block

    def test_load_files_triggers_drop_setup(self, hel_src):
        # loadProjectFiles muss _setupProjectFileDrop aufrufen, damit die
        # Drop-Zone an die richtige Projekt-ID gebunden ist
        load_block = hel_src.split("async function loadProjectFiles(")[1][:2500]
        assert "_setupProjectFileDrop(projectId)" in load_block

    def test_delete_file_function(self, hel_src):
        assert "async function deleteProjectFile(" in hel_src
        del_block = hel_src.split("async function deleteProjectFile(")[1][:1500]
        assert "'DELETE'" in del_block
        # Bestaetigungs-Dialog
        assert "confirm(" in del_block
        # Ruft Endpoint korrekt
        assert "/files/' + fileId" in del_block

    def test_file_list_has_delete_button(self, hel_src):
        load_block = hel_src.split("async function loadProjectFiles(")[1][:3000]
        assert "deleteProjectFile(" in load_block

    def test_uploads_are_sequential(self, hel_src):
        # Sequenziell statt Promise.all — Server schonen + saubere Progress
        upload_block = hel_src.split("function _uploadProjectFiles(")[1][:3000]
        assert "for (let i = 0; i < files.length" in upload_block
        # await pro File, nicht Promise.all
        assert "await uploadOne(" in upload_block

    def test_upload_progress_handles_errors(self, hel_src):
        upload_block = hel_src.split("function _uploadProjectFiles(")[1][:3000]
        assert "xhr.onerror" in upload_block
        # Fehler-Detail aus JSON-Body
        assert "responseText" in upload_block


class TestP196Backend:
    """Source-Inspection auf den Server-Code — vergewissert, dass der Endpoint
    existiert und Schluessel-Validierungen drin sind. Funktional kommt der
    Test ueber ``test_projects_files_upload.py``.
    """

    def test_upload_endpoint_existiert(self, hel_src):
        assert 'POST("/admin/projects/{project_id}/files")' in hel_src.replace(".post", "POST")
        # Function-Definition
        assert "async def upload_project_file_endpoint" in hel_src

    def test_delete_file_endpoint_existiert(self, hel_src):
        assert "async def delete_project_file_endpoint" in hel_src

    def test_atomic_write_helper(self, hel_src):
        # Atomic Write ueber tempfile + os.replace — verhindert halbe Files
        assert "_store_uploaded_bytes" in hel_src
        assert "os.replace" in hel_src

    def test_storage_base_funktion(self, hel_src):
        # Indirektion ueber Funktion — Tests koennen sie monkeypatchen
        assert "def _projects_storage_base(" in hel_src
