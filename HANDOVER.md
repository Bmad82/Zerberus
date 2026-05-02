## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P194 — Phase 5a #1 Backend (Projekte als Entität)
**Tests:** 1365 passed, 4 xfailed (pre-existing), 0 neue Failures
**Commit:** fd47a8c (gepusht zu origin/main)
**Repos synchron:** Zerberus / Ratatoskr / Claude ✅ (verify_sync.ps1: 3/3 clean, 0 unpushed)

---

## Zuletzt passiert
P194 ausgeliefert: Tabellen `projects` + `project_files` in `bunker_memory.db` (Decision 1), `zerberus/core/projects_repo.py` mit async Pure-Functions, Hel-CRUD-Endpoints unter `/hel/admin/projects/*` (admin-only via Basic-Auth — bewusst NICHT unter `/v1/`, das ist Dictate-Lane), Alembic-Migration `b03fbb0bd5e3`, 46 neue Tests (28 Repo + 18 Endpoints).

Routing-Korrektur ggü. dem ursprünglichen Bootstrap-HANDOVER: `/v1/projects/*` war falsch. `/v1/` ist hartkodierte Dictate-Tastatur-Lane (Hotfix 103a, `_JWT_EXCLUDED_PREFIXES` in middleware.py). Admin-CRUD lebt jetzt sauber unter `/hel/admin/projects/*`.

Lesson dokumentiert in `lessons.md`/Datenbank: Composite-UNIQUE-Constraints MÜSSEN als `__table_args__` im Model deklariert werden, nicht nur als raw `CREATE INDEX` in `init_db` — sonst greift der Constraint nicht in Test-Fixtures (`Base.metadata.create_all` ohne `init_db`).

## Nächster Schritt — sofort starten
**P195: Hel-UI-Tab "📁 Projekte"** (Phase 5a Ziel #1 abschließen).

Konkret in `zerberus/app/routers/hel.py` im `ADMIN_HTML`-String:
1. Neuen Tab-Button in `nav.hel-tab-nav` (`data-tab="projects"`, Icon 📁).
2. Neuen Tab-Body `<section id="projects-section" class="hel-section-body">` mit:
   - Liste (Tabelle: Slug, Name, Updated, Status-Badge archiviert/aktiv, Actions Edit/Archive/Unarchive/Delete)
   - "+ Projekt anlegen"-Button öffnet Modal mit Feldern Name, Description, Slug-Override (optional), Persona-Overlay-Editor (zwei Felder: `system_addendum` Textarea, `tone_hints` Comma-List)
   - Detail-Modal (gleiche Form, prefilled — beim Speichern PATCH)
   - Datei-Liste pro Projekt (read-only — Upload kommt in P196)
3. JS-Funktionen `loadProjects()`, `createProject()`, `editProject()`, `archiveProject()`, `unarchiveProject()`, `deleteProject()` (mit `confirm()`), `loadProjectFiles()`. Endpoints sind alle da, nur fetchen.
4. `activateTab()`-Erweiterung für den neuen Tab (siehe bestehende Tabs als Vorlage).
5. Mobile-first: 44px Touch-Targets, gleiches Design-System wie die anderen Tabs (`.hel-section-body`, `.zb-select`, etc.).

Tests: `test_projects_ui.py` mit Source-Inspection-Pattern wie `test_patch170_hel_kosmetik.py` (assertet auf Strings im `hel.py`-Source). Optional Playwright-E2E in Loki, wenn Zeit ist.

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen, dokumentieren):
- **P195** Hel-UI-Tab Projekte (oben beschrieben) — schließt Ziel #1 vollständig ab
- **P196** Datei-Upload-Endpoint `POST /hel/admin/projects/{id}/files` (Bytes nach `data/projects/<slug>/<sha[:2]>/<sha>`, dann `register_file`-Call) + UI-Drop-Zone
- **P197** Persona-Merge-Layer aktivieren — `persona_overlay` aus aktivem Projekt in System-Prompt einbauen (Decision 3, Merge-Order System → User → Projekt)
- Danach nach Workflow-Tabelle: Ziel #2 (Templates), #3 (RAG-Index pro Projekt) etc.

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194: Tabellen, Repo, Endpoints)**.

Helper aus P194 die in Folge-Patches direkt nutzbar sind:
- `projects_repo.compute_sha256(bytes)` — SHA256-Hex
- `projects_repo.storage_path_for(slug, sha, base_dir)` — Storage-Konvention `<base>/projects/<slug>/<sha[:2]>/<sha>`
- `projects_repo.register_file(...)` — Metadaten-Insert (Caller legt Bytes vorher ab)
- `projects_repo.slugify(name)` — URL-stabile Slug

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194: push, init_db-Restart, curl-Smoke).
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (jetzt als 4 xfailed sichtbar — nicht blockierend).

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db`
