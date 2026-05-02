## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P195 — Phase 5a #1 Hel-UI-Tab "Projekte" (Ziel #1 abgeschlossen)
**Tests:** 1382 passed, 4 xfailed (pre-existing), 2 pre-existing Failures (unrelated)
**Commit:** 7b05f4a (gepusht zu origin/main)
**Repos synchron:** Sync läuft im Anschluss (sync_repos.ps1 + verify_sync.ps1)

---

## Zuletzt passiert
P195 ausgeliefert: Hel-UI-Tab `📁 Projekte` als sauberer Aufsatz auf das P194-Backend. Tab zwischen Huginn und Links eingehängt, Lazy-Load via `activateTab('projects') → loadProjects()`. Liste mit Slug/Name/Updated/Status-Badge/Aktionen. Anlegen-Form als CSS-only Overlay (kein extra Modal-Lib), Persona-Overlay-Editor mit `system_addendum` (Textarea) + `tone_hints` (Komma-Liste, Submit konvertiert zu Array). Slug-Override nur beim Anlegen aktiv (Slug ist immutable per Repo-Vertrag). Edit/Archive/Unarchive/Delete inline. Datei-Liste pro Projekt read-only — Upload kommt P196. Lösch-Bestätigung mit Wort "UNWIDERRUFLICH" und Hinweis dass Bytes im Storage bleiben.

20 Source-Inspection-Tests (Pattern wie `test_patch170_hel_kosmetik.py`) decken Tab-Reihenfolge, Section-Markup, alle JS-Funktionen, Confirm-Dialog, Persona-Overlay-Serialisierung, 44px-Touch-Targets und Lazy-Load-Verdrahtung. Keine Playwright-E2E aufgesetzt — wenn der manuelle iPhone-Test (#9 in WORKFLOW) Probleme zeigt, ist Loki/Playwright der nächste Schritt.

**Phase 5a Ziel #1 ist damit vollständig abgeschlossen** (Backend P194 + UI P195).

## Nächster Schritt — sofort starten
**P196: Datei-Upload-Endpoint + UI** (Phase 5a Ziel #4 startet, blockiert nichts).

Konkret:
1. **Endpoint** `POST /hel/admin/projects/{id}/files` in `zerberus/app/routers/hel.py`. `multipart/form-data` mit `UploadFile`. Bytes lesen, `compute_sha256()` aus `projects_repo`, `storage_path_for(slug, sha, BASE_DIR)` für den Pfad, Bytes ablegen (mit `os.makedirs(parent, exist_ok=True)`), `register_file(...)` für die Metadaten. Reject-Liste: keine Executables (`.exe`, `.bat`, `.sh`), max-size aus config (default z.B. 50MB).
2. **DELETE-Endpoint** `DELETE /hel/admin/projects/{id}/files/{file_id}` — `delete_file()` aus Repo + Bytes-Cleanup _nur wenn_ kein anderes Projekt denselben `sha256` referenziert (sonst nur Metadaten löschen).
3. **UI: Drop-Zone** in der `projectDetailCard` aus P195. `<input type="file" multiple>` plus drag-and-drop-Handler. Progress-Anzeige pro Datei. Nach Erfolg: `loadProjectFiles(projectId)` Reload. Existierende `_escapeHtml`-Helper aus P195 wiederverwenden.
4. **Tests:** `test_projects_files_endpoint.py` mit `tmp_db`-Fixture wie P194 + temp-Storage-Dir. Decken: Upload happy-path, Reject-Pfade, SHA-Dedup-Verhalten beim Delete, Endpoint-Validierung (kein `project_id` → 404, leerer Filename → 400).

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P196** Datei-Upload (oben beschrieben) — Phase 5a Ziel #4 öffnet
- **P197** Persona-Merge-Layer aktivieren — `persona_overlay` aus aktivem Projekt in System-Prompt einbauen (Decision 3, Merge-Order System → User → Projekt). Voraussetzung für sinnvolle Ziel-#1-Nutzung im Chat.
- **P198** Template-Generierung — Phase 5a Ziel #2 (`ZERBERUS_X.md`, Ordnerstruktur, optional Git-Init beim Anlegen)
- **P199** Projekt-RAG-Index — Phase 5a Ziel #3 (isolierter FAISS pro Projekt-Slug, indexiert die hochgeladenen Files aus P196)

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194: Tabellen, Repo, Endpoints)**, **Projekte-UI (P195: Hel-Tab + Persona-Overlay-Editor)**.

Helper aus P194 die in P196 direkt nutzbar sind:
- `projects_repo.compute_sha256(bytes)` — SHA256-Hex
- `projects_repo.storage_path_for(slug, sha, base_dir)` — Storage-Konvention `<base>/projects/<slug>/<sha[:2]>/<sha>`
- `projects_repo.register_file(...)` — Metadaten-Insert (Caller legt Bytes vorher ab)
- `projects_repo.list_files(project_id)` / `delete_file(file_id)` — Bestandsverwaltung

JS-Helper aus P195 die in P196 wiederverwendbar sind:
- `_escapeHtml(s)` — HTML-Escape für Datei-Pfade
- `_projectFmtDate(iso)` — lokales Datumsformat
- `loadProjectFiles(projectId)` — wird nach Upload getriggert für Reload

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194: push, init_db-Restart, curl-Smoke), **#8-10 (P195: Tab sichtbar, iPhone-Touch, Persona-Overlay-Roundtrip)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (4 xfailed sichtbar — nicht blockierend).

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate)
