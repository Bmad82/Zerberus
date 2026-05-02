# CLAUDE_ZERBERUS.md – Zerberus Pro 4.0

## Code-Detection + Sandbox-Roundtrip im Chat (P203d-1 — Phase 5a #5 Backend-Pfad)
- Verdrahtet [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py) mit der Sandbox-Pipeline aus P171/P203a/P203c|nach dem LLM-Call wird die Antwort durch `first_executable_block(answer, allowed_languages)` (Pure-Function aus P171) geprüft, bei aktivem-nicht-archiviertem Projekt UND aktivierter Sandbox UND vorhandenem Code-Block läuft `execute_in_workspace(project_id, code, language, base_dir, writable=False)`|Result kommt als additives `code_execution`-Feld in der `ChatCompletionResponse`
- Schema-Erweiterung: `ChatCompletionResponse.code_execution: dict | None = None` (Pydantic-Field)|bei populated: `{language, code, exit_code, stdout, stderr, execution_time_ms, truncated, error}` — Mapping aus `SandboxResult`|OpenAI-SDK-Clients (Dictate, SillyTavern, generic) ignorieren das unbekannte Feld → kein Schema-Bruch
- **Sechs-Stufen-Gate** (alles fail-open ausser ein Gate verbietet's): (1) `active_project_id` aus `X-Active-Project-Id`-Header (P201) — sonst Datei-Fallback wie P171|(2) `project_slug` aus `resolve_project_overlay` vorhanden|(3) **`project_overlay is not None`** — bei archivierten Projekten liefert der Resolver `(None, slug)`, aktive ohne Overlay haben `({}, slug)` — wir blocken Code-Execution auf Eis-gelegten Projekten konservativ (sicherheits-Diskriminator zwischen `None`-archived und `{}`-empty-overlay)|(4) `sandbox.config.enabled`|(5) `first_executable_block` liefert Block (KEIN `fallback_language` — sonst würde Plain-Text als unknown-Code interpretiert)|(6) `execute_in_workspace` liefert `SandboxResult` (P203c-Wrapper) statt `None`
- Hardcoded `writable=False` — schreibender Mount erst nach explizitem Sync-After-Write-Pfad (P207, Phase-5a Ziel #9+#10)
- **Fail-Open** auf jeden Pfad-Fehler in der Sandbox-Pipeline: try/except um den ganzen Block, `logger.warning` ohne re-raise|`code_execution` bleibt `None`, `choices`/`model`/`finish_reason` sind weiter normal — Chat darf nicht durch Sandbox-Probleme blockiert werden
- Position im Endpunkt: NACH `store_interaction(user, assistant)` und VOR Sentiment-Triptychon-Block — die DB-Zeile für die LLM-Antwort steht bereits, bevor die Sandbox läuft|wenn die Sandbox abstürzt, ist der Chat-Verlauf trotzdem persistent
- Logging-Tag `[SANDBOX-203d]` — pro Call eine Zeile mit `project_id`/`slug`/`language`/`exit_code`/`stdout_len`/`stderr_len`/`time_ms`/`truncated`|Worker-Protection-konform: keine Code-Inhalte oder Output-Inhalte im Log
- Was P203d-1 NICHT macht (kommt in P203d-2/P203d-3): zweiter LLM-Call zur Output-Synthese (P203d-2 — Pattern-Vorlage `format_sandbox_result` aus P171/Telegram, nur als LLM-Eingabe statt direkter Telegram-Output), UI-Render im Nala-Frontend (P203d-3 — Code-Block + stdout/stderr-Block + ggf. neuer SSE-Event), HitL-Gate (P206), writable-Mount (P207)
- Tests: 19 in [`test_p203d_chat_sandbox.py`](zerberus/tests/test_p203d_chat_sandbox.py)|`TestP203d1SourceAudit` (7: Logging-Tag, Imports, Schema-Feld via `model_fields`, `writable=False`-im-Aufruf-Fenster, Field-Pass-Through im Konstruktor)|`TestE2ECodeExecution` (12 via asyncio.run+chat_completions mit Mock-LLM/Mock-Sandbox-Manager/gepatchtem `execute_in_workspace`): Happy-Paths Python+JS, exit_code != 0 mit stderr durchgereicht, multiple Blöcke → erster gewinnt; Skip-Cases (no project, no fence, disabled sandbox, archived, unknown language `rust`, execute returns None); Backwards-Compat (OpenAI-Schema-Felder unangetastet); Fail-Open (`RuntimeError` in Pipeline → `code_execution=None` ohne Crash)
- Helper für P203d-2: `code_execution_payload`-Dict-Schema steht — P203d-2 kann das Dict im Synthese-Prompt verkaufen, statt das `SandboxResult` neu zu fetchen|wenn ein eigener Logging-Tag `[SYNTH-203d-2]` für den Synthese-Call entsteht, ist die Operations-Sicht klar getrennt

## Prosodie-Kontext im LLM (P204 — Phase 5a #17, unabhängig)
- Modul: [`zerberus/modules/prosody/injector.py`](zerberus/modules/prosody/injector.py) — Pure-Function-Schicht (`build_prosody_block(prosody, *, bert_label, bert_score)`, Helper `_consensus_label` mit Mehrabian-Logik aus P192/sentiment_display repliziert, `_bert_qualitative` für qualitative-Adjektiv-Übersetzung, Lookup-Tables `_BERT_LABEL_DE`/`_PROSODY_MOOD_DE`/`_PROSODY_TEMPO_DE`) + Wrapper `inject_prosody_context(system_prompt, prosody_result, *, bert_label, bert_score)`
- Block-Format mit eigenem Marker analog `[PROJEKT-RAG]` (P199): `[PROSODIE — Stimmungs-Kontext aus Voice-Input]` / `[/PROSODIE]`|fünf Zeilen dazwischen: `Stimme:`, `Tempo:`, optional `Sentiment-Text: ... (BERT)` (nur wenn BERT-Label übergeben), `Sentiment-Stimme: ... (Gemma)`, `Konsens: ...`
- **Worker-Protection (P191): keine Zahlen im Block.** Confidence/Score/Valence/Arousal werden im Konsens-Label verkocht|qualitative Labels nur (`leicht positiv`, `deutlich negativ`, `ruhig`, `muede`, `inkongruent — Text positiv, Stimme negativ`)|`TestWorkerProtectionNoNumbers` parametrisiert: Regex-Check auf `\d+\.\d+`, `%`, `\b\d+\b` schlägt sofort an wenn versehentlich eine Zahl reinrutscht
- Mehrabian-Konsens-Logik (Pure, kein I/O): BERT positiv (`label=positive` UND `score>0.5`) + Prosody-Valenz negativ (`valence<-0.2`) → `"inkongruent — ..."` (Ironie-/Stress-Hinweis)|sonst Confidence > 0.5 → Stimme dominiert (Stimm-Mood gewinnt)|sonst BERT-Fallback (`deutlich/leicht positiv/negativ` oder `neutral`)|Schwellen identisch zu `utils/sentiment_display.py` (UI-Konsens und LLM-Konsens dürfen nicht voneinander abweichen)
- Gating (Fail-open in einer Pure-Function): `prosody=None` oder Stub-Source → leerer String|`confidence<0.3` → leerer String|kein BERT mitgegeben → Block ohne Sentiment-Text-Zeile (Stimm-only-Pfad)|Idempotenz: `PROSODY_BLOCK_MARKER` schon im Prompt → kein zweiter Block
- Verdrahtung in [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py): JSON-Parse von `X-Prosody-Context`-Header mit Type-Guard (nur `dict` akzeptiert) + Consent-Check (`X-Prosody-Consent: true`) + BERT-Call auf `last_user_msg` in try/except (fail-open: BERT-Fehler → kein Sentiment-Text-Zeile, Block läuft ohne) + `inject_prosody_context(...)` mit Keyword-Args
- Voice-only-Garantie zwei-stufig: (1) Frontend setzt `X-Prosody-Context`-Header nur nach Whisper-Roundtrip (kein Header bei getipptem Text → kein Block)|(2) Stub-Source-Check filtert versehentliche Pseudo-Contexts (defense-in-depth, falls Frontend-Bug einen alten Voice-Context bei einem getippten Turn mitsendet)
- BERT-Re-Use: server-seitige `analyze_sentiment(last_user_msg)` aus `zerberus.modules.sentiment.router` — KEIN neuer Header (`X-Sentiment-Context` o.ä.)|wenn Whisper-Endpoint schon BERT berechnet hat (P193), wäre ein Header-Reuse einen Roundtrip wert, ist aber aktuell nicht nötig: BERT auf `last_user_msg` ist O(ms) im selben Prozess
- Logging-Tag: `[PROSODY-204]` wenn BERT mitgegeben (`block_added bert_label=... mood=...`)|`[PROSODY-190]` bleibt für den Stimm-only-Pfad (`block_added mood=... (no bert)`)|Beide nur INFO-Level mit qualitativen Labels — keine Inhalte oder Confidence-Werte ins Log (P191)
- Tests: 33 in [`test_p204_prosody_context.py`](zerberus/tests/test_p204_prosody_context.py) — `TestBuildProsodyBlock` (9), `TestWorkerProtectionNoNumbers` (3 parametrisiert), `TestConsensusLabel` (6), `TestBertQualitative` (6), `TestInjectWithBert` (5), `TestP204LegacyVerdrahtung` (6 Source-Audit), `TestMarkerUniqueness` (3 distinct vom PROJEKT-RAG/PROJEKT-KONTEXT)|Plus 6 nachgeschärfte Tests in `test_prosody_pipeline.py::TestInjectProsodyContext` (Format-Assertions auf neue Marker, neuer Idempotenz-Test)

## Project-Workspace-Layout (P203a — Phase 5a #5 Vorbereitung)
- Modul: [`projects_workspace.py`](zerberus/core/projects_workspace.py) — Pure-Function-Schicht (`workspace_root_for(slug, base_dir)` → `<base>/projects/<slug>/_workspace/`, `is_inside_workspace(target, root)` resolved+`relative_to` für Pfad-Sicherheit), Sync-FS-Schicht (`materialize_file` mit Hardlink-primary + Copy-Fallback bei `OSError` + Idempotenz via Inode/Size-Check; `remove_file` räumt leere Eltern bis Workspace-Root weg; `wipe_workspace` mit Sicherheits-Reject auf Pfade die nicht auf `_workspace` enden), async DB-Schicht (`materialize_file_async`, `remove_file_async`, `sync_workspace` mit Materialize+Orphan-Removal)
- Strategie pro Datei: erst `os.link` (Hardlink — gleiche Inode wie SHA-Storage, kein Plattenplatz, instantan), bei `OSError` (cross-FS, FAT32, NTFS-without-dev-mode, Permission-Denied) → `shutil.copy2`|Methode kommt im Return zurück (`"hardlink"` / `"copy"` / `None`-bei-noop) und im Log — auf Windows-Maschinen ohne dev-mode wird damit auch der Copy-Pfad live mitgetestet (Monkeypatch-Test simuliert `os.link`-Failure)
- Atomic via tempfile-im-Ziel-Ordner + `os.replace` — analog `_store_uploaded_bytes` (P196), aber dupliziert (statt Import aus hel.py), weil das Workspace-Modul auch ohne FastAPI-Stack importierbar bleiben muss (Tests, künftige CLI)|verhindert dass parallele Sandbox-Reads (P203b) je ein halb-geschriebenes Workspace-File sehen
- Pfad-Sicherheit zwei-stufig: `is_inside_workspace` resolved beide Pfade und prüft `relative_to` (schützt gegen `../../etc/passwd`-Angriffe via entartete `relative_path`-Werte aus alten Datenbanken oder Migrations) PLUS `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet (verhindert versehentliches `wipe_workspace(Path("/"))` bei Slug-Manipulation)
- Idempotenz im `materialize_file`: existierendes Target hat dieselbe Inode wie der Source (Hardlink-Fall) → no-op|Copy-Fall: Size-Match reicht (SHA-Adressierung garantiert Inhalts-Konsistenz auf Storage-Ebene)|Returnt `None` ohne Schreiboperation
- Verdrahtung als Best-Effort-Side-Effect in vier Trigger-Punkten — alle mit Lazy-Import + try/except, Hauptpfad bleibt grün auch wenn Hardlink/Copy scheitert (Source-of-Truth ist der SHA-Storage + DB):
  - [`hel.py::upload_project_file_endpoint`](zerberus/app/routers/hel.py) nach `register_file` + nach RAG-Index → `materialize_file_async`
  - [`hel.py::delete_project_file_endpoint`](zerberus/app/routers/hel.py) nach `delete_file` → `remove_file_async` (mit Slug aus extra `get_project`-Call VOR dem DB-Delete, weil `file_meta` keinen Slug enthält)
  - [`hel.py::delete_project_endpoint`](zerberus/app/routers/hel.py) nach `delete_project` → `wipe_workspace(workspace_root_for(slug, base))` (nutzt das bereits vorhandene `project_pre`-Memorize aus dem RAG-Pfad)
  - [`projects_template.py::materialize_template`](zerberus/core/projects_template.py) nach jedem `register_file` → `materialize_file_async` (analog RAG-Verdrahtung in derselben Schleife)
- `sync_workspace` als Komplett-Resync: materialisiert alle DB-Files, entfernt Orphans (Files im Workspace, die nicht mehr in der DB sind)|idempotent, zweiter Aufruf liefert `{materialized:0, removed:0, skipped:N}`|nicht in den Endpoints verdrahtet (Single-File-Trigger reichen), aber als Recovery-API für künftige CLI/Reindex-Endpoint vorhanden — Pre-P203a-Files können damit in den Workspace nachgezogen werden
- Feature-Flag [`config.py::ProjectsConfig.workspace_enabled`](zerberus/core/config.py) Default `True`|Tests können abschalten (siehe `TestWorkspaceDisabled`-Klasse — nützlich falls CI auf einer FS-Sandbox läuft, die weder Hardlinks noch Copy zulässt)
- Logging-Tag `[WORKSPACE-203]` — `materialized path=... via=hardlink|copy target=...`, `wiped workspace_root=...`, `sync_workspace project_id=... slug=... materialized=N removed=N skipped=N`
- Tests: 36 in [`test_projects_workspace.py`](zerberus/tests/test_projects_workspace.py) — `TestPureFunctions` (4: layout, no-io, traversal-rejected, root-itself), `TestMaterializeFile` (6: create, nested-dirs, idempotent-second-call, traversal-rejected, missing-source, copy-fallback via monkeypatched `os.link`), `TestRemoveFile` (5: removes, cleans-empty-parents, keeps-non-empty-parent, missing-returns-false, traversal-rejected), `TestWipeWorkspace` (3: removes-existing, idempotent-when-missing, rejects-wrong-dirname), `TestSyncWorkspace` (4: unknown-project-zeros, materializes-all, idempotent-second-call, removes-orphans), `TestAsyncWrappers` (3: materialize_file_async, unknown-file-returns-none, remove_file_async), `TestEndpointIntegration` (4: upload, delete-file, delete-project, template-materialize), `TestWorkspaceDisabled` (1: upload skips when disabled), `TestSourceAudit` (5: hel-upload calls workspace, hel-delete-file calls remove, hel-delete-project calls wipe, template calls workspace, workspace-module uses WORKSPACE_DIRNAME ≥2x)
- Was P203a bewusst NICHT macht (kommt mit P203b/c): Sandbox-Mount auf den Workspace-Ordner — `SandboxManager` (P171) verbietet Volume-Mount aktuell explizit ("Kein Volume-Mount vom Host"), P203b muss `workspace_mount: Optional[Path]` einführen|LLM-Tool-Use für Code-Generation (P203c)|Frontend-Render von Code+Output-Blöcken (P203c)

## Nala-Projekt-Picker (P201 — Phase 5a #4 abgeschlossen)
- Endpoint: [`nala.py::nala_projects_list`](zerberus/app/routers/nala.py) — `GET /nala/projects`, JWT-pflichtig via `request.state.profile_name`|liefert `{projects: [{id, slug, name, description, updated_at}], count}`|`persona_overlay` ist BEWUSST NICHT im Response (Admin-Geheimnis, kann Prompt-Engineering-Spuren enthalten)|archivierte Projekte ausgeblendet (kein Param zum Sehen)
- Eigener Endpoint statt Wiederverwendung von `/hel/admin/projects`: (a) Hel ist Basic-Auth-gated, Nala ist JWT — zwei Auth-Welten, kein Bridge|(b) Slimmed Response schützt Overlay|(c) Archived-Default unterscheidet sich
- UI: vierter Settings-Tab "📁 Projekte" zwischen "Ausdruck" und "System"|Tab-Mechanik wie P142 (B-015), kein neues Modal|Panel mit Aktiv-Anzeige + Aktualisieren + Auswahl-loeschen + Listen-Container `id="nala-projects-list"`
- Active-Project-Chip im Chat-Header neben Profile-Badge|`<span id="active-project-chip" class="active-project-chip">`|sichtbar nur wenn Projekt aktiv|klick öffnet Settings + springt direkt auf Projects-Tab (`onclick="openSettingsModal(); switchSettingsTab('projects');"`)|gold-border Pill, hover-Glow, max-width + ellipsis für lange Slugs|min-height 22px für Touch-Target
- State-Persistence: `localStorage['nala_active_project_id']` (numerisch, für Header-Injektion) + `localStorage['nala_active_project_meta']` (JSON `{id, slug, name}`, für Chip-Renderer ohne Re-Fetch)|beim Logout (handle401) wird BEWUSST NICHT geräumt — gleicher User auf gleichem Browser will Auswahl behalten
- Header-Injektion ZENTRAL in [`profileHeaders(extra)`](zerberus/app/routers/nala.py)|drei zusätzliche Zeilen setzen `X-Active-Project-Id`-Header wenn `getActiveProjectId() !== null`|wirkt damit auf ALLE Nala-Calls (Chat, Voice, Whisper, Profile-Endpoints) — keine Möglichkeit, dass ein neuer Endpoint den Header "vergisst"
- JS-Funktionen (alle in NALA_HTML): `getActiveProjectId`, `getActiveProjectMeta`, `setActiveProject(p)`, `clearActiveProject`, `selectActiveProjectById(id)`, `renderActiveProjectChip`, `renderNalaProjectsActive`, `renderNalaProjectsList(items)`, `escapeProjectText(s)`, `loadNalaProjects()` — kompletter Lifecycle, alle pure-DOM
- Lazy-Load: `switchSettingsTab(tab)` ruft `loadNalaProjects()` wenn `tab === 'projects'`|spart Roundtrip wenn der User nur Theme ändern will|Cache in `window.__nalaProjectsCache` für In-Modal-Klicks ohne Re-Fetch
- Zombie-ID-Schutz: nach jedem `loadNalaProjects` wird geprüft ob die aktive ID noch in der Liste auftaucht|wenn nicht (gelöscht oder archiviert in der Zwischenzeit) → `clearActiveProject()`|verhindert dass der Header-Chip an einer Zombie-ID hängt und das Backend einen non-existing-project-Header bekommt
- XSS-Schutz: `escapeProjectText(s)` wandelt `&`, `<`, `>`, `"`, `'` in HTML-Entities|alle drei User-Felder (name, slug, description) laufen durch escapeProjectText im Listen-Renderer|Source-Audit-Test zählt mindestens drei Aufrufe in `renderNalaProjectsList`, damit ein vergessener Aufruf in zukünftigen Refactorings sofort auffällt
- Initialisierung: `renderActiveProjectChip()` läuft am Ende von `showChat()` (nach Login)|stellt Chip aus localStorage wieder her ohne Server-Roundtrip
- Tests: 21 in [`test_nala_projects_tab.py`](zerberus/tests/test_nala_projects_tab.py) (6 Endpoint inkl. 401, archived-versteckt, persona_overlay-NICHT-im-Response, minimal-Felder; 11 Source-Audit für Tab/Panel/Chip/JS/Header-Injektion/Lazy-Load; 2 XSS inkl. Min-Count escapeProjectText; 1 Zombie-ID; 1 Tab-Lazy-Load)|Plus 1 nachgeschärfter Test in `test_settings_umbau.py`: alter `openSettingsModal()`-Proxy-Check → spezifischer 🔧-Emoji + `icon-btn` mit openSettingsModal als alleinige Action|P201 erlaubt openSettingsModal im Header NUR via Project-Chip (gleichzeitig mit `switchSettingsTab('projects')`)

## PWA-Verdrahtung Nala + Hel (P200 — Phase 5a #16)
- Eigener Router [`zerberus/app/routers/pwa.py`](zerberus/app/routers/pwa.py) mit vier Endpoints: `/nala/manifest.json`, `/nala/sw.js`, `/hel/manifest.json`, `/hel/sw.js`|KEINE Auth-Dependencies (anders als `hel.router`)
- Reihenfolge zwingend: `app.include_router(pwa.router)` MUSS in `main.py` VOR `app.include_router(hel.router)` stehen — sonst gilt `verify_admin` für `/hel/manifest.json` und `/hel/sw.js` und der Browser kriegt Auth-Challenge beim Manifest-Fetch (Install-Prompt erscheint nie)
- SW-Scope folgt Pfad-Konvention: `/nala/sw.js` → scope `/nala/`, `/hel/sw.js` → scope `/hel/`|kein `Service-Worker-Allowed`-Header nötig|jede App cacht NUR ihre eigenen URLs
- Manifeste pro App, nicht ein gemeinsames: `NALA_MANIFEST` (theme `#0a1628`, short_name `Nala`, scope `/nala/`) + `HEL_MANIFEST` (theme `#1a1a1a`, short_name `Hel`, scope `/hel/`) als Pure-Python-Dicts in `pwa.py`
- Pflichtfelder pro Manifest: `name`, `short_name`, `start_url`, `scope`, `display: "standalone"`, `theme_color`, `background_color`, `icons` (Liste mit MIN 192+512 px, `type: "image/png"`, `purpose: "any maskable"`)
- Service-Worker-Logik via `render_service_worker(cache_name, shell)` Pure-Function: install precacht App-Shell-Liste, activate räumt alte Caches mit anderem Versionsschlüssel, fetch macht network-first mit cache-fallback (damit Updates direkt durchgehen)|non-GET-Requests passen unverändert durch (kein Cache für POST/PUT/etc.)|`skipWaiting()` + `clients.claim()` nehmen die neue SW-Version sofort online ohne Reload-Pflicht
- Icons unter `zerberus/static/pwa/{nala,hel}-{192,512}.png`, generiert via [`scripts/generate_pwa_icons.py`](scripts/generate_pwa_icons.py) — deterministisch, Re-Run liefert bytes-identische PNGs|Kintsugi-Stil: dunkler Hintergrund, großer goldener/roter Initial-Buchstabe, drei Bruchnaht-Adern in Akzentfarbe|Font-Suche: Georgia/Times/DejaVuSerif/Arial mit PIL-Default als Fallback
- HTML-Verdrahtung Nala (`<head>` von `NALA_HTML`): `<link rel="manifest" href="/nala/manifest.json">`, `<meta name="theme-color" content="#0a1628">`, `<meta name="apple-mobile-web-app-capable" content="yes">`, `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">`, `<meta name="apple-mobile-web-app-title" content="Nala">`, zwei `<link rel="apple-touch-icon">`-Einträge (192 + 512)
- HTML-Verdrahtung Hel analog mit Theme `#1a1a1a`, Title "Hel", Hel-Icons
- SW-Registrierung als kleines `<script>` vor `</body>` in beiden HTMLs: Feature-Detect (`'serviceWorker' in navigator`), `window.addEventListener('load', ...)`, `navigator.serviceWorker.register('/<app>/sw.js', { scope: '/<app>/' })`|`.catch()` loggt nach `console.warn` mit Tag `[PWA-200]`
- Bewusst weggelassen: Offline-Modus (Server muss eh laufen), Push-Notifications (Huginn besetzt das), Background-Sync, Service-Worker-Allowed-Header (Scope ergibt sich aus dem Pfad)
- Tests: 39 in [`test_pwa.py`](zerberus/tests/test_pwa.py) (5 Pure-Function für SW-Render, 6 Manifest-Dict-Validierung, 4 Endpoint-Direct-Calls, 8 Source-Audit pro HTML, 4 Icon-Existenz inkl. PNG-Magic-Byte-Check, 3 Routing-Order in `main.py`, 2 Generator-Skript-Existenz)|Keine TestClient-Setup nötig — alle Endpoints sind direkt als Coroutines aufrufbar (Pattern wie `test_projects_endpoints.py`)

## Projekte (P194 — Phase 5a #1, Backend)
- Tabellen: `projects(id, slug UNIQUE, name, description, persona_overlay JSON-TEXT, is_archived, created_at, updated_at)` + `project_files(id, project_id, relative_path, sha256, size_bytes, mime_type, storage_path, uploaded_at, UNIQUE(project_id, relative_path))` in `bunker_memory.db` (Decision 1, 2026-05-01)
- Repo: [`zerberus/core/projects_repo.py`](zerberus/core/projects_repo.py) — async Pure-Functions (`create_project`/`get_project`/`get_project_by_slug`/`list_projects`/`update_project`/`archive_project`/`unarchive_project`/`delete_project`/`register_file`/`list_files`/`get_file`/`delete_file`)|Helper: `slugify()`, `compute_sha256()`, `storage_path_for()`
- Models bewusst dependency-frei: keine ORM-Relations, keine `Column(ForeignKey)`|Cascade per Repo (`delete_project` führt explizites `DELETE FROM project_files WHERE project_id = ?` aus)
- Composite-UNIQUE `(project_id, relative_path)` MUSS als `__table_args__ = (UniqueConstraint(...),)` im Model — sonst greift Constraint nicht in Test-Fixtures (siehe lessons.md/Datenbank)
- Slug-Generator: lowercase, `[^a-z0-9]+` → `-`, max 64 Zeichen, Kollisions-Suffix `-2`/`-3`/...|Empty-Fallback `"projekt"`|Slug ist immutable (Rename per Drop+Recreate)
- Persona-Overlay: JSON-Dict `{"system_addendum": "...", "tone_hints": [...]}` für Decision 3 (Merge-Layer System → User → Projekt|kein Override)|Repo serialisiert/deserialisiert, Caller arbeitet mit Dicts
- Storage-Konvention: `data/projects/<slug>/<sha256[:2]>/<sha256>`|Sha-Prefix verhindert Hotspot-Ordner|Bytes liegen NICHT in DB|Cleanup-Job für Storage-Dateien beim Hard-Delete kommt separat (sha kann projektübergreifend referenziert sein)
- Endpoints: `/hel/admin/projects` (GET list, POST create) + `/hel/admin/projects/{id}` (GET, PATCH, DELETE) + `/.../archive` + `/.../unarchive` + `/.../files`|Admin-only via Hel-Basic-Auth (`verify_admin`)|NICHT unter `/v1/` — `/v1/` ist Dictate-App-Lane (Hotfix 103a)
- Migration: Alembic `b03fbb0bd5e3_patch194_projects` (down_revision `7feab49e6afe`)|idempotent via `_has_table()`-Guard|Indexe: `uq_projects_slug`, `idx_projects_is_archived`, `idx_project_files_project`, `idx_project_files_sha`
- Tests: 28 in [`test_projects_repo.py`](zerberus/tests/test_projects_repo.py) + 18 in [`test_projects_endpoints.py`](zerberus/tests/test_projects_endpoints.py)|`tmp_db`-Fixture analog `test_memory_store.py`|Endpoint-Tests rufen Coroutines direkt auf (kein TestClient, gleiches Muster wie `test_huginn_config_endpoint.py`)
- Logging-Tag: `[PROJECTS-194]`
- UI-Tab in Hel folgt in P195 (Backend-Patch bewusst von UI getrennt — Workflow-Regel "lieber 2 saubere Patches als 3 mit halb fertigem dritten")

## Projekt-RAG-Index (P199 — Phase 5a #3)
- Helper: [`zerberus/core/projects_rag.py`](zerberus/core/projects_rag.py) — Pure-Functions `_split_prose`, `chunk_file_content`, `top_k_indices`, `format_rag_block`|File-I/O `load_index`, `save_index`, `remove_project_index`, `index_paths_for`|Embedder-Wrapper `_embed_text` (lazy MiniLM-L6-v2)|async `index_project_file`, `remove_file_from_index`, `query_project_rag`
- Index-Struktur: pro Projekt-Slug eigener Store unter `<projects.data_dir>/projects/<slug>/_rag/{vectors.npy, meta.json}`|Vektoren als float32-Numpy-Array, Meta als JSON-Liste mit identischer Reihenfolge|Pure-Numpy-Linearscan via `argpartition`+`argsort` statt FAISS (Per-Projekt-Indizes sind klein, ~10-2000 Chunks → schneller + Tests dependency-frei)|Atomic Write via `tempfile.mkstemp` + `os.replace`
- Embedder: MiniLM-L6-v2 (384 dim) als Default|`_embed_text(text)` lazy-loaded — Tests monkeypatchen die Funktion direkt|Hash-basierter 8-dim-Pseudo-Embedder in `fake_embedder`-Fixture verhindert echtes Modell-Laden|Embedder-Dim-Wechsel zwischen Sessions → Index wird beim nächsten Index-Call neu aufgebaut (kein Crash)
- Chunker-Reuse: Code-Files (.py/.js/.ts/.html/.css/.json/.yaml/.sql) via `code_chunker.chunk_code` (P122)|Prosa (.md/.txt/Default) via lokalem Para-Splitter mit weichen Absatz-Grenzen (max 1500 Zeichen, snap an Doppel-Newline)|Sentence-Split-Fallback für überlange Absätze|Bei Python-SyntaxError → Fallback auf Prose-Splitter
- Trigger-Punkte: (a) `upload_project_file_endpoint` NACH `register_file`|(b) `materialize_template` ruft am Ende `index_project_file` für jede neu angelegte Skelett-Datei|(c) `delete_project_file_endpoint` ruft `remove_file_from_index` NACH dem DB-Delete|(d) `delete_project_endpoint` merkt Slug VOR Delete + ruft `remove_project_index` danach|alle Trigger sind best-effort (Indexing-Fehler brechen Hauptpfad NICHT ab)
- Idempotenz: pro `file_id` höchstens ein Chunk-Set im Index|beim Re-Index wird der alte Block für die file_id zuerst entfernt|gleicher `sha256` ergibt funktional dasselbe Ergebnis (deterministischer Embedder)|anderer `sha256` ersetzt den alten Block — vermeidet Doubletten beim Re-Upload mit gleichem `relative_path`
- Wirkung im Chat: in `legacy.py::chat_completions` NACH P197 (merge_persona) NACH P184 (`_wrap_persona`) NACH P185 (`append_runtime_info`) NACH P118a (`append_decision_box_hint`) NACH P190 (Prosodie), aber VOR `messages.insert(0, system)`|Block beginnt mit `[PROJEKT-RAG — Kontext aus Projektdateien]`|Top-K Hits in Markdown-Sektionen pro File mit `relevance=...`-Score|jeder Fehler → kein Block, Chat läuft normal weiter|max 1 Embed-Call (User-Query) + 1 Linearscan pro Request
- Feature-Flags: `ProjectsConfig.rag_enabled: bool = True`, `rag_top_k: int = 5`, `rag_max_file_bytes: int = 5 * 1024 * 1024` (5 MB; drüber → skip beim Indexen, weil typisch Bilder/Archive)|alle Defaults im Pydantic-Modell weil `config.yaml` gitignored
- Defensive Behaviors: leere Datei → skip `reason="empty"`|binäre Datei (UTF-8-Decode-Fehler) → `reason="binary"`|Datei zu groß → `reason="too_large"`|Bytes nicht im Storage → `reason="bytes_missing"`|Embed-Fehler → `reason="embed_failed"`|inkonsistenter Index (nur eine der zwei Dateien) → leere Basis, nächster Index-Call baut sauber auf|kaputtes meta.json → leere Basis|Dim-Mismatch im `top_k_indices` → leeres Ergebnis statt Crash
- Logging-Tag: `[RAG-199]` zeigt `slug`/`file_id`/`path`/`chunks`/`total` beim Indexen, `chunks_used` beim Chat-Query, `chunks_removed` beim Delete|inkonsistenter/kaputter Index → WARN|Embed/Query-Fehler → WARN mit Exception-Text
- Tests: 46 in [`test_projects_rag.py`](zerberus/tests/test_projects_rag.py) (5 Prose-Splitter + 5 Chunk-File + 5 Top-K + 4 Save-Load + 2 Remove-Index + 7 Index-File + 2 Remove-File + 4 Query + 2 Format-Block + 3 End-to-End + 1 Materialize-Indexes + 6 Source-Audit)|`fake_embedder`-Fixture mit Hash-basiertem Pseudo-Embedder

## Projekt-Templates (P198 — Phase 5a #2)
- Helper: [`zerberus/core/projects_template.py`](zerberus/core/projects_template.py) — `render_project_bible(project, *, now=None)` + `render_readme(project)` als Pure-Functions (synchron, deterministisch via `now`)|`template_files_for(project, *, now=None)` liefert Liste `[{relative_path, content, mime_type}]`|`materialize_template(project, base_dir, *, dry_run=False, now=None)` als async DB+Storage-Schicht
- Skelett-Files (zwei pro Projekt): `ZERBERUS_<SLUG>.md` (Projekt-Bibel mit Sektionen "Ziel", "Stack", "Offene Entscheidungen", "Dateien", "Letzter Stand") + `README.md` (kurze Prosa mit Name + Description)|Inhalt rendert Project-Daten ein (Name, Slug, Description, Anlegedatum)
- Storage: Bytes liegen unter `<projects.data_dir>/projects/<slug>/<sha[:2]>/<sha>` (gleiche Konvention wie P196-Uploads)|DB-Eintrag in `project_files` mit lesbarem `relative_path`|Templates landen damit nahtlos in der Hel-Datei-Liste, im RAG-Index (P199) und in der Code-Execution-Pipeline (P200)
- Idempotenz: existierende `relative_path`-Eintraege werden NICHT ueberschrieben (User-Inhalte bleiben unangetastet)|Helper liefert nur die TATSAECHLICH neu angelegten Files zurueck
- Verdrahtung in [`hel.py::create_project_endpoint`](zerberus/app/routers/hel.py): NACH `projects_repo.create_project()`, mit `await materialize_template(project, _projects_storage_base())`|best-effort: Fehler beim Materialisieren brechen das Anlegen NICHT ab (Projekt-Eintrag steht, Templates lassen sich notfalls nachgenerieren)|Response-Feld `template_files` listet die neu angelegten Eintraege
- Feature-Flag: `ProjectsConfig.auto_template: bool = True` (Default `True`, kann fuer Migrations-Tests/Bulk-Imports abgeschaltet werden)|Flag-Default in `config.py` weil `config.yaml` gitignored
- Atomic Write: `_write_atomic()` (lokale Kopie aus `hel._store_uploaded_bytes` — der Template-Helper soll auch ohne FastAPI-Stack laufen koennen, z.B. CLI-Migrations)
- Git-Init bewusst weggelassen: SHA-Storage ist kein Working-Tree (Bytes liegen unter Hash-Pfaden, nicht unter `relative_path`)|`git init` ergibt erst Sinn mit einem echten `_workspace/`-Layout, das mit der Code-Execution-Pipeline (P200) kommt|kein halbgares Git-Init
- Tests: 23 in [`test_projects_template.py`](zerberus/tests/test_projects_template.py) (6 Pure-Function-Cases, 6 Materialize-Cases inkl. Idempotenz/dry-run/User-Content-Schutz, 3 End-to-End-Cases inkl. Flag-on/off/Crash-Resilienz, 3 Source-Audit-Cases)|`disable_auto_template`-Autouse-Fixture in `test_projects_endpoints.py` + `test_projects_files_upload.py` haelt deren File-Counts stabil
- Logging-Tag: `[TEMPLATE-198]` zeigt `slug`/`path`/`size`/`sha[:8]` pro neu angelegter Datei + `skip slug=... path=... (already exists)` bei Idempotenz-Skip

## Sentiment-Triptychon (P192)
- Modul: [`zerberus/utils/sentiment_display.py`](zerberus/utils/sentiment_display.py) — `bert_emoji()`, `prosody_emoji()`, `consensus_emoji()`, `compute_consensus()`, `build_sentiment_payload()`
- Drei Kanaele: BERT 📝 (Text-Sentiment), Prosodie 🎙️ (Stimm-Analyse, nur bei Audio), Konsens 🎯 (Fusion)
- Mehrabian-Regel: `confidence > 0.5` → Prosodie dominiert|sonst Fallback auf BERT
- Inkongruenz-Erkennung: BERT positiv (`label=positive` UND `score > 0.5`) + Prosodie-Valenz `< -0.2` → 🤔 + `incongruent=true`
- BERT-Score-Schwellen: `> 0.7` = 😊/😟 (stark), `<= 0.7` = 🙂/😐 (mild), `neutral` immer 😶
- Prosodie-Mood-Mapping (10 Werte): `happy=😊`/`excited=🤩`/`calm=😌`/`sad=😢`/`angry=😠`/`stressed=😰`/`tired=😴`/`anxious=😬`/`sarcastic=😏`/`neutral=😶`
- Frontend: `.sentiment-triptych` mit drei `.sent-chip`-Spans (BERT/Prosodie/Konsens)|44px Touch-Targets|Hover/`:active` Sichtbarkeit analog `.msg-toolbar`-Pattern (P139)|User-Bubbles links (`flex-start`), Bot-Bubbles rechts (`flex-end`)|`.sent-incongruent` faerbt Konsens-Chip gold bei Widerspruch
- Backend: `/v1/chat/completions` liefert `sentiment: {user: {bert,prosody,consensus}, bot: {bert,prosody,consensus}}` ADDITIV — OpenAI-Clients ignorieren das Feld
- Logging-Tag: `[SENTIMENT-192]`

## Whisper-Endpoint Enrichment (P193)
- `/v1/audio/transcriptions` Response: `text` bleibt IMMER (Backward-Compat fuer Dictate/SillyTavern/Generic-Clients)|optional `prosody` (P190) und neu `sentiment.bert.{label,score}` + optional `sentiment.consensus.{emoji,incongruent,source}`
- Backward-Compat-Audit: `response = {"text": cleaned_transcript}` MUSS vor jedem additiven Feld initialisiert sein — Source-Audit-Test in [`test_whisper_enrichment.py`](zerberus/tests/test_whisper_enrichment.py) prueft die Reihenfolge
- `/nala/voice` JSON-Response identisch erweitert (zusaetzliches `enrichment`-Feld) + SSE-Events `event: prosody` + `event: sentiment` ueber `/nala/events`|Triptychon-Frontend kann sync (JSON) oder async (SSE) konsumieren
- SSE-Generator in [`nala.py::sse_events`](zerberus/app/routers/nala.py) emittiert named SSE-Events nur fuer `prosody` und `sentiment`|alle anderen Event-Types behalten das default-`data:`-only Format
- Fail-open: BERT-/Konsens-Fehler erzeugt Logger-Warnung mit Tag `[ENRICHMENT-193]`, `sentiment`-Feld bleibt weg, Endpoint laeuft sauber durch
- Logging-Tag: `[ENRICHMENT-193]`

## Auto-TTS (P186)
- Toggle: `Antworten automatisch vorlesen` im Settings-Tab `Ausdruck`|UNTER Stimmen-Dropdown + Rate-Slider|44px Touch-Target|`id="autoTtsToggle"`
- localStorage-Key: `nala_auto_tts` ("true"/"false")|Default `false`|Check via `=== 'true'` (alles andere → false)
- Trigger: NACH `addMessage(reply, 'bot')` im non-streaming Chat-Pfad (entspricht SSE-done-Moment)|nicht pro Chunk
- Endpoint: nutzt bestehenden `POST /nala/tts/speak` (edge-tts seit P143)|gleiche Stimme + Rate wie 🔊-Button
- Audio-Stop bei: `loadSession`, `doLogout`, `handle401`, Toggle-OFF (`onAutoTtsToggle(false)`)
- Audio-Referenz: `window.__nalaAutoTtsAudio` (analog SSE-Watchdog-Pattern)
- Stille Degradation bei Fehler: `console.warn('[AUTO-TTS-186] ...')`|kein Error-Popup
- KEIN Backend-Change|TTS-Endpunkte unveraendert seit P143

## RAG Dual-Embedder (P187)
- Flag: `modules.rag.use_dual_embedder` in config.yaml|Default `false` in `config.yaml.example` (Backward-Compat nach `git clone`)|lokal seit 2026-05-01 auf `true`
- DE-Modell: `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` auf GPU|EN-Modell: `intfloat/multilingual-e5-large` auf CPU
- Indices: `data/vectors/de.index` + `de_meta.json` (DE-Chunks)|`en.index` + `en_meta.json` (EN-Chunks, optional)|Legacy `faiss.index` bleibt erhalten als MiniLM-Fallback
- `_select_index_and_meta(language)` in [`router.py`](zerberus/modules/rag/router.py): waehlt DE/EN-Index|Fallback DE wenn EN-Index fehlt
- `_encode(text, language=None)` reicht erkannte Sprache an `DualEmbedder.embed()`|Dimensions-Mismatch zwischen Modellen bleibt im jeweiligen Index gekapselt
- Migration: `scripts/migrate_embedder.py --execute`|Backup nach `data/backups/pre_patch129_*` (auch fuer P187 wiederverwendet)
- Backward-Compat: Flag `false` → MiniLM-Pfad unveraendert (Pre-P133)
- Logging-Tags: `[DUAL-187]` (Init + Index-Load), `[RAG-187]` (Encode-Switch im DEBUG)

## Prosodie-Pipeline (P188 Foundation + P189 Backend + P190 Pipeline + P191 Consent)
- Modul: [`zerberus/modules/prosody/`](zerberus/modules/prosody/)|`manager.py` + `gemma_client.py` (P189) + `prompts.py` (P189) + `injector.py` (P190)
- Config: `modules.prosody.{enabled, model_path, mmproj_path, server_url, llama_cli_path, device, n_gpu_layers, timeout_seconds, vram_threshold_gb, output_format}`|alle Defaults im Code (config.yaml gitignored)
- Backend P189: `GemmaAudioClient` mit Dual-Path|CLI (`llama-mtmd-cli` Subprocess pro Call) ODER Server (`llama-server` HTTP)|`mode`-Property routet automatisch
- `mode` = `none` (Stub) / `cli` (Pfad A, JETZT) / `server` (Pfad B, wartet auf llama.cpp Issue #21868)
- Pfad A funktioniert JETZT|Pfad B nur Code-Skelett — `input_audio` Content-Block in `llama-server` noch nicht gemergt
- JSON-Parsing: robust gegen Markdown-Wrapper (```json ... ```), Freitext, kaputtes JSON|Fallback auf Stub
- `analyze()` routing|enabled=False oder mode=none → Stub (P188-Verhalten)|sonst → `client.analyze_audio()`
- `is_active`-Property (P190): enabled UND mode != none|Pipeline-Gate für `asyncio.gather`
- Pipeline P190: Whisper + Gemma PARALLEL via `asyncio.gather(return_exceptions=True)` in `/nala/voice` und `/v1/audio/transcriptions`
- Whisper-Fehler = harter Fehler (raise)|Prosodie-Fehler = weicher Fehler (Whisper läuft alleine)
- Frontend reicht Prosodie-Result aus `/nala/voice`-Response (`prosody`-Feld) als `X-Prosody-Context`-Header an `/v1/chat/completions` weiter
- Injector P190: `inject_prosody_context(sys_prompt, prosody_result)` in [`injector.py`](zerberus/modules/prosody/injector.py)|fügt Block HINTER System-Prompt
- Injector-Gating: kein Block bei source=stub oder confidence<0.3|valence<-0.3 → Inkongruenz-Warnung („Stimme klingt anders als Text vermuten lässt")
- Consent P191: Frontend-Toggle „Sprachstimmung analysieren (Prosodie)"|localStorage `nala_prosody_consent` (Default `false`)|Header `X-Prosody-Consent: true` nur bei aktiv
- Pipeline-Gate Endpoint: `is_active AND consent` (UND-Verknüpfung — beide müssen wahr sein)
- Visueller Indikator 🎭 neben Mikrofon-Button (nur sichtbar bei Consent=on)
- Hel-Admin-Endpoint `GET /hel/admin/prosody/status`: NUR Aggregate (mode, success_count, error_count, last_success_ts)|KEINE individuellen mood/valence/arousal Felder
- Worker-Protection: tmp-Audio-Datei wird im `finally:` per `unlink()` entsorgt|KEIN Schreiben in `interactions`-Tabelle|KEIN Logging von Audio-Rohdaten
- `healthcheck()` reasons: `disabled`/`no_model`/`model_not_found`/`no_cuda`/`not_enough_vram`/`ok`|nutzt `_cuda_state()` aus RAG-Device-Helper (P111)
- main.py-Lifespan: loggt Status `Prosodie ok / skip / fail` im Startup-Banner
- Modell: Gemma 4 E2B (Q4_K_M GGUF ~3.4 GB) + mmproj (BF16 ~940 MB)|liegen unter `C:\Users\chris\models\gemma4-e2b\`
- Logging-Tags: `[PROSODY-188]` (Startup/Healthcheck), `[PROSODY-STUB-188]` (Stub-Aufrufe im DEBUG), `[PROSODY-189]`/`[PROSODY-189-CLI]`/`[PROSODY-189-SRV]` (Backend), `[PROSODY-190]` (Pipeline+Injector), `[PROSODY-CONSENT-191]` (Frontend), `[PROSODY-ADMIN-191]` (Hel-Status)

## Globale Wissensbasis
- Repo: https://github.com/Bmad82/Claude (PUBLIC|keine Secrets/Keys/IPs/interne URLs)
- lessons/ nur bei Bedarf prüfen|nicht rituell bei jedem Patch
- Nach Abschluss: universelle Erkenntnisse dort eintragen

## Token-Effizienz
- Datei bereits im Kontext → NICHT nochmal lesen|nur lesen wenn (a) nicht sichtbar ODER (b) direkt vor Write
- Doku-Updates am Patch-Ende|ein Read→Write-Zyklus pro Datei
- CLAUDE_ZERBERUS.md + lessons.md: Bibel-Fibel-Format (Pipes|Stichpunkte|ArtikelWeg)
- SUPERVISOR/PROJEKTDOKU/README/Patch-Prompts: Prosa (menschliche Leser)
- Neue Einträge in CLAUDE_ZERBERUS.md + lessons.md IMMER im komprimierten Format schreiben

## Marathon-Workflow (Phase 5+)
- Session-Start: HANDOVER.md → ZERBERUS_MARATHON_WORKFLOW.md → loslegen
- Ziele statt Rezepte|WAS nicht WIE|eigene Architektur-Entscheidungen erwünscht
- Stopp bei ~400k Token oder Kontextvergiftung|aktuellen Patch sauber fertig → Doku → Handover → STOPP
- Blockiert → Frage in DECISIONS_PENDING parken → nächsten unabhängigen Patch nehmen
- Session-Ende: HANDOVER.md überschreiben|Manuelle-Tests-Liste pflegen|Patch-Status in Workflow aktualisieren
- Push + sync_repos.ps1 + scripts/verify_sync.ps1 als letzter Schritt

## Pflicht nach jedem Patch
- SUPERVISOR_ZERBERUS.md aktualisieren: Nummer|Datum|3-5 Zeilen Inhalt
- Offene Items: erledigte raus|neue rein
- Architektur-Warnungen: nur bei Änderung
- SUPERVISOR_ZERBERUS.md = einzige Datei für Supervisor-Claude beim Session-Start
- PROJEKTDOKUMENTATION.md = vollständiges Archiv, nur bei Bedarf konsultiert
- Vollständige Doku: `docs/PROJEKTDOKUMENTATION.md`

## Auto-Test-Policy (P165)
- GRUNDSATZ: Alles was Coda testen kann → Coda testet|Mensch nur für Untestbares
- CODA TESTET:
  - Unit/Integration-Tests (pytest)|API-Calls gegen echte Services (OpenRouter/Telegram/Whisper)
  - System-Prompt-Validation (LLM-Output-Format)|Config-Konsistenz (YAML-Keys vs Code)
  - Doku-Konsistenz (Patch-Nummern|Datei-Referenzen|tote Links|README-Footer)
  - Regressions-Sweep nach jedem Patch|Import/AST-Checks|Log-Tag-Konsistenz
  - Live-Validation-Scripts in `scripts/` (wie `validate_intent_router.py`)
- MENSCH TESTET (nicht delegierbar):
  - UI-Rendering auf echtem Gerät (iPhone/Android)|Touch-Feedback|visuelles Layout
  - Telegram-Gruppen-Dynamik mit echten Usern (Forwards|Edits|Multi-User)
  - Whisper mit echtem Mikrofon + Umgebungsgeräusche
  - UX-Gefühl ("fühlt sich richtig an")
- NACH JEDEM PATCH: Coda führt `pytest zerberus/tests/ -v --tb=short` aus|bei Failures: fixen BEVOR Commit
- LIVE-VALIDATION: Bei neuen Features die externe APIs nutzen → Validation-Script in `scripts/` anlegen + ausführen
- DOKU-CHECKER: `scripts/check_docs_consistency.py` (P165) prüft Patch-Nummer-Sync|Tote Links|Log-Tag-Konsistenz|Imports|Settings-Keys|nach jedem Patch laufen lassen, additiv zu pytest
- RETROAKTIV: Code-Stellen ohne Tests gefunden → Tests nachrüsten (kein separater Patch nötig)

## Huginn-RAG-Selbstwissen (P178)
- Huginn ruft vor jedem LLM-Call `_huginn_rag_lookup(user_msg, settings)` in [`zerberus/modules/telegram/router.py`](zerberus/modules/telegram/router.py)|Treffer landen via `_inject_rag_context` als "--- Systemwissen ---"-Block VOR der Intent-Instruction im System-Prompt|sowohl Legacy-Pfad (`_process_text_message`) als auch P174-Pipeline-Pfad (`handle_telegram_update`) angeschlossen
- **Category-Filter ist Datenschutz-Schicht**|Default `modules.telegram.rag_allowed_categories=["system"]`|persoenliche/narrative/lore/reference/general-Chunks werden HART verworfen, bevor sie das LLM sehen|Filter greift NACH `_search_index`, also nach Reranking — wir trauen dem Index nicht
- Neue Kategorie `system` in [`hel.py`](zerberus/app/routers/hel.py)|`_RAG_CATEGORIES` + `CHUNK_CONFIGS["system"]={chunk_size:200, overlap:40, min_chunk_words:30, split:"none"}` (P178b-Tuning fuer kleine FAQ-/Tabellen-Bloecke wie „Was Zerberus NICHT ist" / Mythologie-Tabelle, sonst schluckt der Residual-Merge sie)|ohne diese Erweiterung wuerde Upload mit `category=system` auf "general" zurueckfallen
- Test-Coverage P178: 16 neue Tests in [`test_huginn_rag.py`](zerberus/tests/test_huginn_rag.py)|Suite-Total nach P178b: 981 Tests gruen
- **Live-Test-Findings P178 — Status:** L-178a (Guard kennt RAG nicht) → ✅ ERLEDIGT in P180 via `rag_context`-Parameter|L-178b (Guard kennt Persona nicht) → ✅ ERLEDIGT in P180 via Persona-Cap 300→800|L-178c (ADMIN zu sensitiv) → ✅ ERLEDIGT in P182 via Plausi-Heuristik|L-178e (Allowlist fehlt) → ✅ ERLEDIGT in P181|L-178g (Voice-DM lautlos) → ✅ ERLEDIGT in P182 via Unsupported-Media-Handler|L-178d (system-Kategorie im Hel-Dropdown) → ✅ ERLEDIGT in P178c
- Huginn-Doku liegt in [`docs/huginn_kennt_zerberus.md`](docs/huginn_kennt_zerberus.md)|hochladen via `curl -u Chris:... -F file=@docs/huginn_kennt_zerberus.md -F category=system http://localhost:5000/hel/admin/rag/upload`|MUSS category=system sein, sonst greift der Filter nicht
- Graceful Degradation|RAG-Modul aus, RAG_AVAILABLE=False, Exception, leerer Query, keine erlaubte Kategorie im Top-K → leerer String → System-Prompt unveraendert → Fastlane-Fallback|Huginn antwortet wie Pre-P178
- Konfig|`modules.telegram.rag_enabled` (Default `True`)|`rag_allowed_categories: ["system"]` (Default-Liste)|`rag_top_k: 5` (Over-Fetch-Faktor 4 fuer Filter)|in config.yaml setzbar, Defaults stehen in `_HUGINN_RAG_DEFAULT_*` Konstanten am Top der Sektion in router.py
- Logs|`[HUGINN-178] RAG-Lookup: query=... → N system-chunks (M gefiltert)`|bei Exception: `[HUGINN-178] RAG-Lookup fehlgeschlagen: ...`|alles WARNING+ darunter INFO

## Pipeline-Cutover-Feature-Flag (P177)
- `modules.pipeline.use_message_bus: false` (Default)|`true` → `process_update` delegiert an `handle_telegram_update` (Adapter+Pipeline)|false → `_legacy_process_update` (Pre-P177-Body unveraendert)
- Live-Switch: Flag wird pro Call gelesen, kein Settings-Cache|uvicorn `--reload` greift sofort, kein Server-Neustart noetig|Test-Pattern in [`test_cutover.py`](zerberus/tests/test_cutover.py): zwei aufeinanderfolgende Calls mit unterschiedlichem Flag treffen unterschiedliche Pfade
- Komplexe Pfade an Legacy delegieren|`handle_telegram_update` hat 5 Early-Returns: `channel_post`/`edited_channel_post`, `edited_message`, `callback_query`, Photo-`message`, Gruppen-`chat.type ∈ {group, supergroup}` → alle `await _legacy_process_update(...)`|nur DM-Text laeuft durch Pipeline
- Legacy bleibt|`_legacy_process_update` ist KEIN Dead-Code|HitL-Callbacks, Vision, Gruppenbeitritt-HitL, autonomer Einwurf bleiben Telegram-spezifisch
- Default: AUS|Chris testet manuell durch config-Switch|nach Live-Verifikation kann Default in spaeterem Patch auf `true` gedreht werden
- Kein Nala-Cutover|nur Telegram|Nala-SSE-Pipeline ist zu anders (RAG/Memory/Sentiment/Streaming)
- Caller-Pfad: Webhook-Endpoint + Long-Polling-Loop rufen weiterhin `process_update` (nicht `handle_telegram_update` direkt)|process_update ist die Stable-API, handle_telegram_update ist Implementierungs-Detail

## Coda-Autonomie (P176)
- Coda übernimmt ALLES was er kann|`docker pull`, `pip install`, `curl`, Testdaten, Sync|Chris nur für physisch Unmögliches
- Vor Patch-Abschluss prüft Coda|Server startet sauber? Alle Images da? Dependencies aktuell?|Nicht hoffen — verifizieren
- Neue Dependencies|Coda installiert sie selbst (`pip install` im venv|nicht `--break-system-packages` global)|Nicht als „TODO für Chris" markieren
- Docker-Images|Coda pullt sie selbst (`docker pull`)|Kein „bitte docker pull ausführen" in der Checkliste
- Umgebungs-Checks|Coda führt healthchecks aus und fixt Probleme|Erst wenn unfixbar (Auth/Hardware/UX-Gefühl) → an Chris eskalieren
- Test-Marker (P176)|`@pytest.mark.e2e` für Server-abhängige Tests (Loki/Fenrir/Vidar)|Default-Run via `addopts = -m "not e2e"` (in [`pytest.ini`](pytest.ini))|`pytest -m e2e` nur mit laufendem Server

## Log-Level-Faustregel (P166)
- DEBUG: Routine-Heartbeats (Pacemaker-Puls|Watchdog-Healthcheck-OK|Long-Poll-Timeouts)|erwartbare transiente Fehler (DNS-Aussetzer, Long-Poll-Exception)|volle Audio-Transkripte (für Debugging)
- INFO: Start/Stop/Zustandsänderungen (Watchdog aktiv|Pacemaker gestartet|Container-Restart erfolgreich|Audio-Transkript-Einzeiler mit Längen)
- WARNING: jemand sollte das sehen + ggf. handeln (Whisper unresponsive|Pacemaker-Erstpuls fehlgeschlagen|≥5 aufeinanderfolgende Poll-Fehler)
- ERROR: Action Required (Container-Restart fehlgeschlagen|Whisper nach Restart nicht erreichbar)
- Faustregel-Test: „Wenn das jeden Patch im Terminal auftaucht und niemand was unternimmt — falsches Level"

## Repo-Sync (P166)
- Nach jedem Patch|`sync_repos.ps1` DANN [`scripts/verify_sync.ps1`](scripts/verify_sync.ps1)|beide Pflicht
- `verify_sync.ps1` prüft|`git status` clean + `git log origin/main..HEAD` leer|für alle 3 Repos (Zerberus|Ratatoskr|Claude)
- Bei ❌ Exit-Code 1|NICHT weitermachen|Sync-Problem erst lösen (Auth|Branch-Mismatch|Remote-Ref)
- Patch-Workflow Pflichtschritte (P164→P166):
  1. Code-Änderungen
  2. Tests grün (`pytest zerberus/tests/ -v --tb=short`)
  3. `git add` + `git commit` + `git push` (Zerberus)
  4. `sync_repos.ps1` (Ratatoskr + Claude-Repo)
  5. `scripts/verify_sync.ps1` ← Verifikation aller 3 Repos
  6. Erst bei ✅ Exit 0 → Patch gilt als abgeschlossen
- Hintergrund: bis P165 driftete Ratatoskr auf GitHub bis zu 65 Patches gegen Zerberus|`sync_repos.ps1` ohne Verifikation = Hoffnung, nicht Beweis

## Projektpfad
```
C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus
```

## Server starten
```bash
cd C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus
venv\Scripts\activate
uvicorn zerberus.main:app --host 0.0.0.0 --port 5000 --reload
```

## Regeln

1. Erst lesen, dann schreiben|keine blinden Überschreibungen
2. `bunker_memory.db` niemals löschen/ändern
3. `.env` niemals leaken/loggen
4. `config.yaml` = Single Source of Truth|`config.json` NICHT als Konfig-Quelle
5. Module mit `enabled: false` nicht anfassen
6. Dateinamen `CLAUDE_ZERBERUS.md`/`SUPERVISOR_ZERBERUS.md` FINAL|nicht umbenennen/verschieben/durch alte Namen ersetzen|gilt auch für Ratatoskr-Kopien
7. Mobile-first (iOS Safari + Android Chrome)|`:active` statt nur `:hover`|Touch-Target ≥44px|`keydown` statt `keypress`|`type="button"` auf Non-Submit-Buttons
8. `/v1/`-Endpoints (`/v1/chat/completions`, `/v1/audio/transcriptions`) IMMER auth-frei|Dictate-App kann keine Custom-Headers|Bypass: `_JWT_EXCLUDED_PREFIXES` in [`middleware.py`](zerberus/core/middleware.py)|NIEMALS entfernen (Hotfix 103a)

### HTTP-Header-Konventionen

| Header | Verwendung | Seit |
|---|---|---|
| `X-Session-ID` | Session für `/v1/chat/completions` + `/nala/voice`|Default `legacy-default`/`nala-default` | Früh |
| `X-Already-Cleaned: true` | Skip `clean_transcript()` in `/v1/audio/transcriptions`+`/nala/voice`|Dictate-App|Case-insensitive | P135 |

### Regel 9 — User-Entscheidungen als klickbare Box
- Aktionen mit User-Input (Datei löschen, Index leeren, Settings) → IMMER klickbare Entscheidungsbox in Nala-UI
- Format: Buttons + Klare Optionen + "Soll ich das für dich übernehmen?"
- Kein Freitext-Dialog bei binärer/ternärer Entscheidung

## RAG-Upload
- Endpoint: `POST /hel/admin/rag/upload` (`.txt`/`.md`/`.docx`/`.pdf`)
- Chunks: `chunk_size=800 Wörter`|`overlap=160 Wörter` (20%)|Einheit Wörter, NICHT Token
- Status: `GET /hel/admin/rag/status`
- Clear: `DELETE /hel/admin/rag/clear` → `faiss.index`+`metadata.json` reset
- Reindex: `POST /hel/admin/rag/reindex` → re-embed aller Chunks
- Helper: `_reset_sync(settings)` in `zerberus/modules/rag/router.py`

## Statischer API-Key
- `config.yaml` → `auth.static_api_key`|wenn gesetzt akzeptiert JWT-Middleware `X-API-Key` als Bearer-Alternative

## Telegram/Huginn (P155+158+162)

- `config.yaml` → `modules.telegram.mode`:
  - `polling` (Default): Long-Polling via `getUpdates`|funktioniert hinter Tailscale/NAT|Shutdown cancelt Task
  - `webhook`: nur mit öffentl. HTTPS-URL|braucht `webhook_url`|Shutdown deregistriert
- Background-Task in `lifespan` via `startup_huginn()`|`main.py` hält Referenz, cancelt bei Shutdown
- Update-Handler: `zerberus.modules.telegram.router.process_update(data, settings)`|Webhook+Polling rufen ihn
- `modules.telegram.system_prompt` (P158): Huginn-Persona (Default zynischer Rabe)|editierbar in Hel→Huginn-Tab|Default: [`DEFAULT_HUGINN_PROMPT`](zerberus/modules/telegram/bot.py)|3-Wege-Resolver `_resolve_huginn_prompt(settings)`: Key fehlt→Default|leer→leer (Opt-Out)|sonst Config
- BotFather GroupPrivacy=AUS|sonst kein `respond_to_name`/`autonomous_interjection`|Nach Toggle: Bot aus Gruppe entfernen+neu hinzufügen (TG cached pro Beitritt)|→ lessons.md
- **Update-Typ-Filter (P162):** `process_update()` verwirft ganz oben `channel_post`/`edited_channel_post`/`edited_message`/unbekannte Typen|`_POLL_ALLOWED_UPDATES` in [`bot.py`](zerberus/modules/telegram/bot.py) listet durchgereichte Typen|Bei neuen Typen: erst entscheiden ob Huginn verarbeitet, dann Filter+`_KNOWN_UPDATE_TYPES` ergänzen
- **Offset-Persistenz (P162):** `data/huginn_offset.json` speichert letzten `update_id`|Boot lädt via `_load_offset()`|gegen Doppelverarbeitung nach Restart|Tests müssen `OFFSET_FILE` per `monkeypatch.setattr(bot_module, "OFFSET_FILE", tmp_path/"off.json")` umlenken
- **Forum-Topics / `message_thread_id` (P162, D10):** `extract_message_info()` exposed `message_thread_id`+`is_forwarded`|Alle `send_telegram_message`-Calls in `router.py` reichen `message_thread_id=info.get("message_thread_id")` durch|sonst Antwort im General statt Topic|Telegram ignoriert bei None → nur truthy ins Payload

## Intent-Router (P164)
- Architektur: Intent kommt vom Haupt-LLM via JSON-Header in der Antwort|kein Regex (Whisper-Fehler) + kein extra Classifier-Call (Latenz)
- Format: `{"intent":"CHAT|CODE|FILE|SEARCH|IMAGE|ADMIN","effort":1-5,"needs_hitl":bool}` als allererste Zeile|optional in ```json-Fence|Body folgt
- Enum [`core/intent.py`](zerberus/core/intent.py): 6 Kern-Intents aktiv|Rosa-Future (EXECUTE/MEMORY/RAG/SCHEDULE/TRANSLATE/SUMMARIZE/CREATIVE/SYSTEM/MULTI) als Kommentar reserviert|`HuginnIntent.from_str()` Fallback auf CHAT bei None/Empty/Unknown
- Parser [`core/intent_parser.py`](zerberus/core/intent_parser.py): Brace-Counter (statt naivem `[^}]+`)|robuste Defaults bei kaputtem JSON, fehlendem Header, unbekanntem Intent, effort außerhalb 1-5
- `INTENT_INSTRUCTION` + `build_huginn_system_prompt(persona)` in [`bot.py`](zerberus/modules/telegram/bot.py)|wird in `_process_text_message` und im autonomen Gruppen-Einwurf an Persona angehängt|Persona darf leer sein, Intent-Block bleibt
- Router parsing: Body=`parsed.body` (ohne JSON-Header)|Guard sieht Body, User sieht Body|Edge: nur Header ohne Body → Roh-Antwort als Fallback (Log-Warnung)
- HitL-Policy [`core/hitl_policy.py`](zerberus/core/hitl_policy.py): NEVER_HITL={CHAT,SEARCH,IMAGE} überstimmt LLM-`needs_hitl=true` (K5)|BUTTON_REQUIRED={CODE,FILE,ADMIN} braucht ✅/❌-Inline-Keyboard|ADMIN erzwingt HitL auch bei `needs_hitl=false` (K6 — jailbroken-LLM-Schutz)|aktuell P164: Decision wird geloggt + Admin-DM-Hinweis|echter Button-Flow folgt Phase D
- K6-Regel: HitL-Bestätigung NIE per natürlicher Sprache („ja, mach kaputt" kein gültiger GO)|nur Inline-Keyboard
- Effort-Score: nur geloggt (Bucket low/mid/high)|aktive Routing-Entscheidung kommt mit Phase C (Aufwands-Kalibrierung)
- Gruppen-Einwurf-Filter (D3/D4/O6): autonome Antworten nur bei {CHAT,SEARCH,IMAGE}|CODE/FILE/ADMIN unterdrückt mit `skipped="autonomous_intent_blocked"`
- Logging-Tags: `[INTENT-164]` (Parsing+Routing), `[EFFORT-164]` (Effort-Logging), `[HITL-POLICY-164]` (Policy-Decisions)
- Test-Pattern: `parse_llm_response(raw)` direkt testen (16 Parser-Tests)|Policy `.evaluate(parsed)` direkt testen (11 Policy-Tests)|Integration via `_process_text_message`-Mock (Guard, Send, LLM gemockt)|6 Integration-Tests fuer Gruppen-Filter + Header-Strip

## Rate-Limiting + Graceful Degradation (P163)
- [`core/rate_limiter.py`](zerberus/core/rate_limiter.py): Interface `RateLimiter` (Rosa-Skelett für Redis) + `InMemoryRateLimiter` (Huginn-jetzt)|Singleton via `get_rate_limiter()`|Default 10 msg/min/User|Cooldown 60s
- Integration in [`process_update()`](zerberus/modules/telegram/router.py) GANZ oben (nach Update-Typ-Filter, vor Event-Bus)|nur für `message`-Updates|Callback-Queries ausgenommen (Admin-HitL-Klicks)
- `RateLimitResult.first_rejection`-Flag: genau EIN „Sachte, Keule"-Reply pro Cooldown-Periode|Folge-Nachrichten still ignorieren|sonst spammt der Bot selbst|Test-Reset via `_reset_rate_limiter_for_tests()` (Modul-Singleton)
- `cleanup()` entfernt Buckets nach 5min Inaktivität (Memory-Leak-Schutz)
- Guard-Fail-Policy `security.guard_fail_policy` ∈ {`allow`,`block`,`degrade`}|Default `allow` (Huginn — Antwort durchlassen + Log-Warnung)|`block` (Rosa — „⚠️ Sicherheitsprüfung nicht verfügbar.")|`degrade` reserviert (Future Ollama)|Helper `_resolve_guard_fail_policy(settings)` via `getattr(settings, "security", None)` (Pydantic `extra="allow"`)
- Trigger: Guard-Verdict `ERROR` (Guard-Call selbst raised nicht, returnt `{"verdict":"ERROR"}`)|beide Pfade respektieren Policy: `_process_text_message` + autonomer Gruppen-Einwurf
- OpenRouter-Retry `_call_llm_with_retry()` wrappt `call_llm`|`call_llm` raised NICHT → Error-String prüfen via `_is_retryable_llm_error`|Retryable: `429`/`503`/„rate"|Backoff 2s/4s/8s|max 3 Versuche|400/401/etc. SOFORT zurück (kein Retry-Sinn)
- Erschöpfung: DM → Fallback „Meine Kristallkugel ist gerade trüb. Versucht's später nochmal. 🔮"|autonom → still überspringen (niemand hat gefragt)
- Ausgangs-Throttle [`bot.py::send_telegram_message_throttled`](zerberus/modules/telegram/bot.py): 15 msg/min/Chat (konservativ unter TG ~20/min/Gruppe)|wartet via `asyncio.sleep` statt drop|Modul-Singleton `_outgoing_timestamps`|Test-Reset via `_reset_outgoing_throttle_for_tests()`|aktuell nur autonomer Gruppen-Einwurf|DMs bei `send_telegram_message` direkt
- Config-Keys VORBEREITET nicht aktiv: `limits.per_user_rpm`/`limits.cooldown_seconds` in `config.yaml`|aktives Reading mit Phase-B-Config-Refactor|jetzige Defaults im Code (max_rpm=10, cooldown=60)|`security.guard_fail_policy` IST aktiv gelesen
- Logging-Tags: `[RATELIMIT-163]` (Rate-Limiter), `[HUGINN-163]` (Router/Bot — Throttle/Retry/Guard-Fail/LLM-Unavailable)

## Input-Sanitizer (P162 + P173)
- [`input_sanitizer.py`](zerberus/core/input_sanitizer.py): Interface `InputSanitizer` (Rosa-Skelett) + `RegexSanitizer` (Huginn-jetzt)|Singleton via `get_sanitizer()`
- VOR jedem LLM-Call: in [`_process_text_message()`](zerberus/modules/telegram/router.py) für DMs + autonomer Gruppen-Einwurf|Neue LLM-Pfade: `get_sanitizer().sanitize(text, metadata={...})` davor
- Findings geloggt, NICHT geblockt (Tag `[SANITIZE-162]`)|Huginn-Modus: `blocked=False`|Guard (Mistral Small) entscheidet final|`blocked=True`-Pfad im Konsumenten („🚫 Nachricht blockiert.")|aktiv mit Rosa wenn `security.input_sanitizer.mode = "ml"`
- Patterns konservativ|Lieber ein Pattern weniger als False-Positive auf Deutsch („Kannst du das ignorieren?" darf NICHT triggern)|Neue Patterns: erst gegen [`test_guard_stress.py::TestKeineFalsePositives`](zerberus/tests/test_guard_stress.py) (11 FP-Boundary-Cases nach P173) und [`test_input_sanitizer.py`](zerberus/tests/test_input_sanitizer.py) verifizieren
- Metadata: `user_id`/`chat_type`/`is_forwarded`/`is_reply`|`is_forwarded=True` → Finding `FORWARDED_MESSAGE` (K3-Vektor: Chat-Übernahme via Reply-Chain)|Future: ML-Sanitizer kann je Chat-Typ/Reply unterschiedlich strikt sein
- **P173-Erweiterungen** (Detection-Rate 5/16 → 12/16): NFKC-Unicode-Normalisierung im Hauptpfad (Ⅰ → I, Homoglyph-Schutz) + Finding `UNICODE_NORMALIZED: NFKC` falls Text geändert|7 neue Patterns: DAN-DE (`(?-i:DAN)` case-sensitive für FP-Schutz vs. Vorname „Dan")|`developer/debug/god/admin mode` mit Aktivierungs-Verb-Kontext|ChatML-/Llama-Tokens (`<|im_start|>`, `[INST]` etc.)|`vergiss alles`|`javascript:` in Markdown-Link|`gib/nenne/verrate/sag deinen System-Prompt`

## Callback-Spoofing-Schutz (P162, O3)
- `HitlRequest.requester_user_id: Optional[int]`|`process_update()` validiert bei `callback_query`: clicker_id ∈ `{admin_chat_id, requester_user_id}`|sonst Popup („🚫 Nicht deine Anfrage.") via [`answer_callback_query()`](zerberus/modules/telegram/bot.py) mit `show_alert=True`
- Neue HitL-Pfade (Code-Exec, File-Ops): `requester_user_id=info.get("user_id")` an `create_request()`|sonst nur-Admin-Fallback (DM-only ok, In-Group-Buttons offen)
- String-Vergleich: TG liefert `from.id` als int|`admin_chat_id` oft String|`str(...)` auf beiden Seiten

## Self-Knowledge-RAG + Bug-Sweep (P169)
- Self-Knowledge-RAG-Doku [`docs/RAG Testdokumente/huginn_kennt_zerberus.md`](docs/RAG%20Testdokumente/huginn_kennt_zerberus.md)|natuerliche Sprache, kein Code-Block, keine Pfade|explizite Negationen fuer Kerberos-Protokoll/FIDO/Red-Hat-OpenShift|kategoriiert als `reference` (300/60/50)|Chris laedt manuell ueber Hel hoch, kein Auto-Import
- B1 Bubble-Farben (zwei Layer): Backend `nala.py::login` filtert schwarze `theme_color` aus Profilen → `#ec407a` Default + `[SETTINGS-169]` DEBUG|Frontend Boot-IIFE: `_cleanFav(v)` filtert FAV_BLACK (`#000000`/`#000`/`rgb(0,0,0)`/`rgba(0,0,0,1)`) im Favoriten-Loader BEVOR CSS-Var gesetzt wird|korrupten Favoriten persistent reparieren via `delete fav.bubble.userBg/llmBg` + `localStorage.setItem`
- B2 RAG-Status Lazy-Init: `GET /admin/rag/status` und `GET /admin/rag/documents` rufen `await _ensure_init(settings)` BEVOR `_index`/`_metadata` gelesen werden|sonst zeigt Hel „0 Dokumente" bis erstem Schreibvorgang|skip wenn `modules.rag.enabled=false`|Logging `[RAG-169] Index-Status: N Chunks, M aktive, K Quellen`
- B6 Cleaner-Null-Guards: `renderCleanerList()` mit `if (!host) return;` + `loadCleaner()` mit `if (!document.getElementById('cleanerList')) return;` als Frueh-Return + Catch-Block schreibt nur wenn `cleanerStatus` existiert|Hintergrund: P149 entfernte cleanerList-DOM, JS-Funktion blieb im Page-Boot
- Test-Isolation: `sys.modules["..."] = X` nie direkt — IMMER `monkeypatch.setitem(sys.modules, "...", X)`|sonst bleibt der Eintrag nach Test gesetzt und faengt nachfolgende Tests mit `cannot import name '...' from '<unknown module name>'`
- Logging-Tags: `[SETTINGS-169]` (Backend-Default-Defensive in nala.py)|`[RAG-169]` (Lazy-Init + Tab-Load-Status-Log in hel.py)
- Test-Pattern: Frontend-Bugfixes via Source-String-Match testen (Patch-Marker + Symbol-Names)|Backend-Bugfixes via `monkeypatch.setattr(rag_mod, "_ensure_init", fake)` + Modul-Globals direkt befuellen lassen

## Datei-Output + Effort-Kalibrierung (P168)
- Output-Router in [`_process_text_message`](zerberus/modules/telegram/router.py) nach Guard|`should_send_as_file(intent, len)` aus [`utils/file_output.py`](zerberus/utils/file_output.py): FILE/CODE → immer Datei|CHAT >2000 ZS → Datei-Fallback|sonst Text
- `determine_file_format(intent, content) -> (filename, mime)`: CODE+Python (`def`/`import`/`class`) → `huginn_code.py`|CODE+JS (`function`/`const`/`=>`/`console.log`) → `.js`|CODE+SQL (`SELECT`/`CREATE TABLE`) → `.sql`|CODE-default → `.txt`|FILE+Markdown (`#`/Listen/Fences) → `huginn_antwort.md`|FILE-plain → `.txt`|CHAT-fallback → `.md`|kein AST-Parsing, nur Regex-Heuristik
- MIME-Whitelist `.txt/.md/.py/.js/.ts/.sql/.json/.yaml/.yml/.csv`|Blocklist `.exe/.sh/.bat/.cmd/.ps1/.dll/.so/.dylib/.scr/.com/.vbs/.jar/.msi`|`is_extension_allowed(filename)` Belt-and-suspenders gegen Bug in `determine_file_format`
- 10-MB-Limit (`MAX_FILE_SIZE_BYTES`)|über Limit → User-Fehlermeldung „⚠️ Antwort waere zu gross (X.X MB)"|kein silent drop
- `send_document(bot_token, chat_id, content, filename, caption, reply_to, thread_id, mime_type, timeout=30s)` in [`telegram/bot.py`](zerberus/modules/telegram/bot.py)|httpx-multipart/form-data|Markdown-Caption mit Fallback ohne `parse_mode` bei HTTP-Fehler|Logging-Tag `[HUGINN-168]`
- `build_file_caption(intent, content, filename)` ≤1024 ZS (Telegram-Limit)|CODE: ``"📄 `huginn_code.py` — N Zeilen Python"``|FILE: „📄 Hier ist dein Dokument: ..."|CHAT-Fallback: „Die Antwort war zu lang ..."|Zeilenanzahl via `len(content.splitlines())`
- `EFFORT_CALIBRATION` universal in [`bot.py`](zerberus/modules/telegram/bot.py)|wird in `build_huginn_system_prompt(persona, effort=None)` an Persona angehängt|LLM moduliert Ton selbst basierend auf eigenem `effort`-Score (P164)|effort 1-2 → kommentarlos|3 → kurz neutral|4 → leicht genervt|5 → sarkastisch + „bist du sicher?"
- `build_effort_modifier(effort)` Helfer für Tests + zukünftige zweistufige Flows|None/invalid → ``""``
- WICHTIG (O5): Effort-Modifier NUR Persona-Block, NICHT Policy-Block|Guard läuft unabhängig vom Effort-Score
- FILE+effort=5 → HitL-Gate: `_send_as_file` spawnt `_deferred_file_send_after_hitl` als `asyncio.create_task` (NICHT direkt awaiten — sonst Long-Polling-Deadlock auf Click-Update)|Frage „🪶 Achtung, Riesenakt. ✅/❌"|nutzt P167 `build_admin_keyboard(task.id)`|Intent in DB: `FILE_EFFORT5`|Approve → send_document|Reject → „Krraa! Auch gut."|Expired → P167-Sweep schickt Timeout
- Guard-Sequenz unverändert: läuft auf gepartem Body (ohne JSON-Header) BEVOR Output-Router entscheidet|Datei-Inhalt = Guard-Inhalt
- Logging-Tags: `[FILE-168]` (Routing/Validation/HitL-Decision)|`[HUGINN-168]` (sendDocument-API)
- Test-Pattern: `httpx.AsyncClient`-Mock für sendDocument|`monkeypatch.setattr(router_mod, "send_document", ...)` + `_reset_telegram_singletons_for_tests()` für Pipeline-Tests

## Guard-Stresstests + Policy-Schichten (P172, Phase D)
- 31-Case-Testbatterie [`test_guard_stress.py`](zerberus/tests/test_guard_stress.py): T01–T16 offline gegen [`core/input_sanitizer.py`](zerberus/core/input_sanitizer.py)|T17–T25 live gegen `mistralai/mistral-small-24b-instruct-2501` mit `@pytest.mark.guard_live` + skipif kein `OPENROUTER_API_KEY`|Marker in [`conftest.py::pytest_configure`](zerberus/tests/conftest.py) registriert
- Detection-Bilanz P162-Sanitizer NACH P173: 12/16 (T01–T05, T09, T10–T15)|4 verbleibende xfails (T06/T07/T08/T16 — Sanitizer-Out-of-Scope, semantisch, gehören in den LLM-Guard)
- Bekannte Sanitizer-Lücken (xfail-dokumentiert): Leet-Speak/Punkt-Obfuskation/Wort-Rotation/Persona-Bypass — KEINE Regex-Lösung sinnvoll (entweder umgehbar oder FP auf normalem Deutsch)
- Empfehlung 5-Schichten-Architektur (Schicht 1+2 deterministisch+blocking|Schicht 3 Sandbox|LLM-Call|Schicht 4 LLM-Guard fail-open|Schicht 5 Audit-Trail)|Determinismus dominiert Semantik (kein Bypass durch „Guard sagt OK")|Sandbox-Schicht ist die einzige kernel-erzwungene
- Guard-Eskalations-Empfehlung (NICHT implementiert — Phase E): WARNUNG→BLOCK bei Jailbreak-Pattern/Persona-Exploitation/Prompt-Leak-Versuch/destruktiven Code-Patterns|3-WARNUNG-in-10min-Counter|Pre-Truncation Guard-Input auf 4000 Wörter|Admin-Notify-Format definiert|`guard_fail_policy: allow` Default beibehalten (Verfügbarkeit > Sicherheit)
- Architektur-Dokumente: [`docs/guard_escalation_analysis.md`](docs/guard_escalation_analysis.md) (10-Zeilen-Tabelle Szenario × Aktuell × Empfehlung × Begründung + YAML-Config-Vorschlag)|[`docs/guard_policy_limits.md`](docs/guard_policy_limits.md) (deterministisch vs. LLM-Guard vs. Grauzone, Schichten-Diagramm, Phase-E-Empfehlung)
- Live-Test-Erkenntnisse: Mistral-Guard erkennt erfundene Telefonnummern NICHT (T24, OK-Verdict trotz halluzinierter Bürgeramt-Nr)|Persona-Test besteht weil Hauptmodell-Antwort die Persona hält, NICHT weil Guard sie als Bypass-Versuch flaggt (T22)|Latenz wächst linear mit Input-Länge (T25, daher Pre-Truncation-Empfehlung)|Indeterminismus: T17/T18/T23 akzeptieren auch ERROR (transiente Mistral-Glitches)
- Logging-Tag im Test: KEIN neuer — wir reuse `[GUARD-120]` und `[SANITIZE-162]`. P172 hat keinen Runtime-Tag (nur Tests + Docs).

## Message-Bus-Interfaces (P173, Phase E)
- [`core/message_bus.py`](zerberus/core/message_bus.py) — transport-agnostische Datenmodelle: `Channel(TELEGRAM/NALA/ROSA_INTERNAL)`|`TrustLevel(PUBLIC/AUTHENTICATED/ADMIN)`|`Attachment(data, filename, mime_type, size)`|`IncomingMessage(text, user_id, channel, trust_level=PUBLIC, attachments=[], metadata={})`|`OutgoingMessage(text, file, file_name, mime_type, reply_to, keyboard, metadata)`
- [`core/transport.py`](zerberus/core/transport.py) — `TransportAdapter(ABC)` mit `async send(OutgoingMessage) -> bool`|`translate_incoming(raw_data: dict) -> IncomingMessage`|`translate_outgoing(OutgoingMessage) -> dict`
- IncomingMessage-`metadata` enthält je nach Transport: `thread_id`, `reply_to_message_id`, `is_forwarded`, `chat_id`, `message_id`|`is_forwarded` bleibt das Signal für den Sanitizer (P162-K3)|`trust_level=ADMIN` ersetzt langfristig die Direkt-Vergleiche gegen `admin_chat_id`
- Tests in [`test_message_bus.py`](zerberus/tests/test_message_bus.py): Enum-Werte|Default-Factories unabhängig (Regression-Schutz gegen shared mutable state für `attachments`/`metadata`)|ABC-Instanziierungs-Schutz|Subclass-Roundtrip
- Migrations-Reihenfolge: P174 Telegram-Adapter + Pipeline-Skelett (parallel verfügbar)|P175 Cutover `process_update` → `handle_telegram_update` + Migration komplexer Pfade (Group, Callbacks, Vision, HitL-BG)|danach Nala-Adapter, dann Rosa-Internal
- Wenn ein neuer Transport benötigt wird: TransportAdapter-Subclass anlegen, alle 3 Methoden implementieren, NICHT die Datenmodelle selbst erweitern (Felder + `metadata`-Dict reichen)

## Phase-E-Skelett komplett (P175)
- Alle Adapter-Klassen + alle Skelett-Dateien sind angelegt|`zerberus/adapters/{telegram,nala,rosa}_adapter.py` + `zerberus/core/{message_bus,transport,pipeline,policy_engine}.py` + [`docs/trust_boundary_diagram.md`](docs/trust_boundary_diagram.md)
- **Phase F übernimmt:** Cutover `process_update` → `handle_telegram_update`|schrittweise Migration der Nala-Pipeline (Guard/Intent über `core/pipeline`, RAG/Memory/SSE bleibt Nala-spezifisch)|RosaAdapter-Implementierung wenn Messenger-Stack steht|Audit-Trail (`PolicyDecision.severity ∈ {high, critical}` → `audit.log`)
- Wichtig: Pipeline-/Adapter-Cutover sind in Phase E BEWUSST nicht passiert wegen ~15 Tests die `_process_text_message`/`process_update`/`send_telegram_message` als Modul-Attribute monkey-patchen|Trennung: Phase E = Skelett, Phase F = Migration

## Nala-Adapter (P175, Phase E)
- [`adapters/nala_adapter.py`](zerberus/adapters/nala_adapter.py) — `NalaAdapter(TransportAdapter)`. Trust-Mapping aus dem JWT-Payload: `permission_level=admin` → `ADMIN`, gueltiger `profile_name` → `AUTHENTICATED`, sonst `PUBLIC`|Audio-Bytes werden direkt als `Attachment` gepackt (Gegensatz zu Telegram, wo Photo-Bytes lazy bleiben)|Erwartetes `raw_data`-Format: `{text, profile_name, permission_level, session_id, audio?, metadata?}` — das was Nala-Endpoints aus `request.state` (post-JWT-Middleware) ohnehin schon zusammenbauen
- `translate_outgoing` liefert `{kind: text|file, text, file, file_name, mime_type, reply_to, metadata}` — der Caller (legacy.py / nala.py) übersetzt das in `ChatCompletionResponse` oder SSE-Event|`send` raised `NotImplementedError` mit Hinweis auf SSE/EventBus — Nala antwortet nicht über Push
- Wichtig: Adapter ist OVERLAY, kein Ersatz für `legacy.py`/`nala.py`|SSE-Streaming, RAG, Memory, Sentiment, Audio-Pipeline, Query-Expansion bleiben unverändert|wer den Adapter nutzt: in einem Nala-Endpoint `NalaAdapter().translate_incoming({"text": ..., "profile_name": request.state.profile_name, ...})` → `IncomingMessage` an Pipeline (P174) → `OutgoingMessage` mit `translate_outgoing` zurück in eine Nala-Response

## Runtime-Info-Block (P185)
- Utility: [`zerberus/utils/runtime_info.py`](zerberus/utils/runtime_info.py) — `build_runtime_info(settings) -> str` baut den Block, `append_runtime_info(prompt, settings) -> str` haengt ihn an.
- Inhalt: Zerberus-Version, Cloud-LLM (Kurzname), Guard-Modell (Kurzname), RAG/Sandbox-Aktivierungs-Status. Marker-Header: `[Aktuelle System-Informationen — automatisch generiert]`.
- Robust: Pydantic-Settings, Dict-Settings, `SimpleNamespace`, sogar `None` werden akzeptiert — Lesefehler liefern `unbekannt` statt zu crashen.
- Injection-Punkte:
  - **Huginn:** [`telegram/router.py`](zerberus/modules/telegram/router.py) `_process_text_message` — Reihenfolge `_resolve_huginn_prompt` → `append_runtime_info` → `_inject_rag_context` → `build_huginn_system_prompt`.
  - **Nala:** [`legacy.py`](zerberus/app/routers/legacy.py) `chat_completions` — Reihenfolge `load_system_prompt` → `_wrap_persona` (P184) → `append_runtime_info` → `append_decision_box_hint` → insert.
- Statisch in RAG-Doku ([`huginn_kennt_zerberus.md`](docs/RAG%20Testdokumente/huginn_kennt_zerberus.md)): Architektur, Naming, Komponenten, Halluzinations-Negationen, Phasen-Geschichte. **Dynamisch (Live-Block):** Modellname, Guard-Modell, Modul-Status. RAG-Doku enthält einen Absatz "Aktuelle Konfiguration" der auf den Live-Block zeigt.
- Source-Audit-Test in [`test_patch185_runtime_info.py`](zerberus/tests/test_patch185_runtime_info.py) verifiziert Reihenfolge der Calls in beiden Routern — verhindert Drift wenn künftige Patches zwischen die Stufen rutschen.

## Nala Prompt-Assembly (P184 + P197)
- **Quellen-Hierarchie** (von Persona-Stärkste zu Schwächste):
  1. `system_prompt_{profil}.json` (z.B. `system_prompt_chris.json`) — vom User in Settings → Tab Ausdruck → "Mein Ton" via `POST /nala/profile/my_prompt` geschrieben. Atomares Write via tempfile + os.replace. Nur eigenes Profil zugreifbar (JWT-Check).
  2. `system_prompt.json` — Default Nala-Stil (warmer KI-Assistent), Fallback wenn profil-spezifisch fehlt.
  3. Empty (kein system-Prompt) — wenn keine Datei existiert.
- **Resolver:** [`load_system_prompt(profile_name)` in `legacy.py`](zerberus/app/routers/legacy.py)|Reihenfolge: profil-spezifisch (lower-case) → generic|Liest immer fresh von Disk, kein Cache.
- **Wrapping (P184):** `_wrap_persona()` legt einen `# AKTIVE PERSONA — VERBINDLICH`-Header vor profil-spezifische Prompts|Trigger: `_is_profile_specific_prompt(profile_name)` checkt Datei-Existenz|Generischer Default wird NICHT gewrappt (er IST der Default).
- **Projekt-Overlay-Merge (P197):** [`zerberus/core/persona_merge.py`](zerberus/core/persona_merge.py) — `merge_persona(base, overlay, project_slug=None)` hängt einen markierten Block `[PROJEKT-KONTEXT — verbindlich für diese Session]` mit `system_addendum` und `tone_hints` aus `projects.persona_overlay` an den Base-Prompt|Aktivierung über Header `X-Active-Project-Id: <int>` (`read_active_project_id` mit Lowercase-Fallback)|DB-Lookup über `resolve_project_overlay(project_id, *, skip_archived=True)` — archivierte Projekte werden übersprungen, Slug wird trotzdem geloggt|Reihenfolge in `chat_completions`: `load_system_prompt` → **`merge_persona`** → `_wrap_persona` (so umschließt der AKTIVE-PERSONA-Marker auch das Projekt-Overlay) → `append_runtime_info` → `append_decision_box_hint`|`tone_hints` werden case-insensitive dedupliziert, leere Strings/Nicht-Strings gefiltert|Doppel-Injection-Schutz via Marker-Substring-Check.
- **Reihenfolge im messages-Array:** `[system: AKTIVE-PERSONA-Wrapped (Persona + optionaler Projekt-Block) + decision-box-hint + allgemeinwissen-fallback, user: enriched_with_RAG]`|Persona steht IMMER vorne, RAG-Kontext wird in den USER-Turn injiziert (nicht in system).
- **Konflikt-Resolution:** Wenn Caller bereits eine `role=system` mitschickt (interne Pipeline-Aufrufe), wird die Persona NICHT eingefügt — Caller-Wahl gewinnt|Standard-Frontend-Calls schicken nur `[{role:user}]`, also greift der Persona-Inject immer.
- **Diagnose:** Persistenter `[PERSONA-184]`-INFO-Log zeigt `profile`/`persona_active`/`sys_prompt_len`/`first200`|`[PERSONA-197]`-INFO-Log zeigt `project_id`/`slug`/`base_len`/`project_block_len` (nur wenn Header gesetzt)|Bei Persona-Bug-Reports: erst Server-Log nach beiden Tags greppen, dann entscheiden ob Verdrahtung oder LLM-Verhalten.
- **Bekanntes Verhalten:** DeepSeek v3.2 ignorierte abstrakte System-Prompts bei kurzen User-Inputs ("wie geht's?")|`# AKTIVE PERSONA — VERBINDLICH`-Marker erzwingt höhere Aufmerksamkeit|Bei weiterhin generischen Antworten: ChatML-Wrapper (B-071) als nächste Eskalations-Stufe.
- **Telegram bewusst ausgeklammert (P197):** Huginn ([`telegram/bot.py`](zerberus/modules/telegram/bot.py)) hat eigene Persona-Welt (DEFAULT_HUGINN_PROMPT, optional Hel-überschrieben) ohne User-Profile und ohne Verbindung zu Nala-Projekten|Project-Awareness in Telegram braucht eigene UX (`/project <slug>`-Befehl oder persistente Bind-Tabelle)|kein Patch dafür geplant bis konkreter Bedarf.

## Policy-Engine (P175, Phase E)
- [`core/policy_engine.py`](zerberus/core/policy_engine.py) — abstraktes `PolicyEngine`-Interface + pragmatische `HuginnPolicy`-Fassade|`evaluate(message, parsed_intent=None) -> PolicyDecision`|`PolicyDecision{verdict, reason, requires_hitl, severity, sanitizer_findings, retry_after}`|`PolicyVerdict ∈ {ALLOW, DENY, ESCALATE}`
- HuginnPolicy aggregiert: 1) Rate-Limit (P163, billigster Check zuerst — bei DENY wird Sanitizer NICHT aufgerufen), 2) Sanitizer (P162/P173, `blocked=True` → DENY, Findings ohne `blocked` werden nur durchgereicht — kein Eskalations-Trigger, sonst rotten WARNUNG-Patterns), 3) HitL-Check (P164, NUR wenn `parsed_intent` mitgegeben — sonst skip, sonst würde Pre-Pass ohne Intent evaluieren)
- Severity-Mapping per Trust-Level (defense-in-depth, NICHT trust-blind): `PUBLIC` hebt eine Stufe (max bis `high` — `critical` ist Audit-Reserved), `AUTHENTICATED` Basis, `ADMIN` senkt eine Stufe (mind. `low`)|Trust-blinde Checks bleiben (auch ein Admin soll keinen Loop 1000x/sek durchjagen)
- Pipeline-Integration optional: `PipelineDeps` kann eine `PolicyEngine` aufnehmen (in P175 noch nicht verkabelt — kommt mit Phase F)|Tests in [`test_policy_engine.py`](zerberus/tests/test_policy_engine.py): ABC-Schutz, ALLOW/DENY/ESCALATE × Severity-Mapping × Reihenfolge

## Rosa-Adapter Stub + Trust-Boundary-Diagramm (P175, Phase E)
- [`adapters/rosa_adapter.py`](zerberus/adapters/rosa_adapter.py) — Stub-Klasse, alle 3 Methoden raisen `NotImplementedError` mit Hinweis auf [`docs/trust_boundary_diagram.md`](docs/trust_boundary_diagram.md)|wichtig: instanziierbar (alle abstrakten `TransportAdapter`-Methoden überschrieben), damit der Vertrag formal eingehalten ist und `__init__`-Tests trivial bleiben
- [`docs/trust_boundary_diagram.md`](docs/trust_boundary_diagram.md) — ASCII-Architektur-Diagramm aller Schichten (Adapter → Policy-Engine → Pipeline → Guard → Sandbox) + Trust-Stufen-Tabelle + Severity-Mapping + Daten-Flüsse (EXTERNAL/NEVER LEAVES/INTRA-SERVER) + Patch-Mapping welcher Patch hat welche Schicht gebaut

## Telegram-Adapter + Pipeline (P174, Phase E)
- [`adapters/telegram_adapter.py`](zerberus/adapters/telegram_adapter.py) — `TelegramAdapter(TransportAdapter)`. Trust-Mapping: `private+admin_chat_id` → `ADMIN`, `private` → `AUTHENTICATED`, `group/supergroup` → `PUBLIC` (Admin-in-Gruppe bleibt PUBLIC, konservativ). `translate_incoming` nutzt `extract_message_info` aus `bot.py` (kein eigener Parser)|`translate_outgoing` baut kwargs-Dict mit `method`-Discriminator (`sendMessage`/`sendDocument`)|`send` delegiert an die bestehenden `send_telegram_message`/`send_document`. Photo-Bytes werden NICHT vorgeladen — nur `photo_file_ids` in metadata, das Resolven via `get_file_url` bleibt legacy bis P175. Convenience-Factory `TelegramAdapter.from_settings(settings)`. Logging-Tag `[ADAPTER-174]`.
- [`core/pipeline.py`](zerberus/core/pipeline.py) — `process_message(IncomingMessage, PipelineDeps) -> PipelineResult`. Linearer Text-Pfad: Sanitize → LLM → Intent-Parse → Guard → Output-Routing (Text vs. File). DI-only, keine Telegram-/HTTP-/OpenRouter-Imports. `PipelineDeps`-Felder: `sanitizer`, `llm_caller`, `guard_caller` (optional), `system_prompt`, `guard_context`, `guard_fail_policy`, `should_send_as_file`, `determine_file_format`, `format_text` + Antwort-Texte (`llm_unavailable_text`/`sanitizer_blocked_text`/`guard_block_text`). `PipelineResult.reason ∈ {ok, sanitizer_blocked, llm_unavailable, guard_block, empty_input, empty_llm}` + `intent`/`effort`/`needs_hitl`/`guard_verdict`/`sanitizer_findings`/`llm_latency_ms` für Diagnostik
- WICHTIG: Pipeline behandelt KEINE HitL-BG-Tasks, KEIN Group-Routing, KEINE Callbacks, KEINE Vision, KEINE Admin-DM-Spiegelungen. Diese Pfade bleiben in [`telegram/router.py::_process_text_message`](zerberus/modules/telegram/router.py) bis P175. Wer sie braucht: legacy-Pfad nutzen
- `handle_telegram_update(raw_update, settings)` in [`router.py`](zerberus/modules/telegram/router.py) — neuer Phase-E-Entry-Point der Adapter + Pipeline zusammensteckt. **NICHT von `process_update` aufgerufen** — der legacy-Pfad bleibt der primäre. P175 macht den Cutover. Wer `handle_telegram_update` direkt aufruft kriegt bei Foto-Only-Updates `{ok: True, skipped: "no_text"}`
- Backward-Compat: `_process_text_message` und `process_update` UNVERÄNDERT|alle bestehenden Monkey-Patch-Punkte (`telegram_router.send_telegram_message`/`call_llm`/`_run_guard`/`_process_text_message`) bleiben funktional|`test_telegram_bot.py`/`test_hitl_hardening.py`/`test_rate_limiter.py` laufen unverändert grün
- Tests: [`test_pipeline.py`](zerberus/tests/test_pipeline.py) (17 DI-basierte Cases) + [`test_telegram_adapter.py`](zerberus/tests/test_telegram_adapter.py) (24 Cases — translate_incoming/outgoing + send mit gemockten Bot-API-Funktionen)
- DI-Pattern für neue Adapter: `PipelineDeps` befüllen mit Sanitizer/LLM/Guard/Output-Routing-Callables — der Adapter selbst implementiert nur die 3 `TransportAdapter`-Methoden; die Business-Logik kommt über `process_message`

## Docker-Sandbox (P171, Phase D)
- Opt-In via `modules.sandbox.enabled: true` ([`SandboxConfig`](zerberus/core/config.py))|Defaults im Pydantic-Model (config.yaml gitignored)|`enabled=False` per Default — `execute()` liefert dann `None`, Caller fällt auf P168-Datei-Pfad zurück
- [`SandboxManager`](zerberus/modules/sandbox/manager.py) baut `docker run --rm --network none --read-only --tmpfs /tmp:size=64m,exec --memory 256m --cpus 0.5 --pids-limit 64 --security-opt no-new-privileges` mit eindeutigem Container-Namen `zerberus-sandbox-<uuid>`|kein Volume-Mount|Cleanup IMMER (auch bei Crash/Timeout via `docker rm -f`)
- Code-Blockliste (Belt+Suspenders, **NICHT** primärer Schutz — der ist Docker): Python `import os/subprocess/socket`, `__import__`, `eval`, `exec`, `open(... 'w')`|JS `child_process`/`fs`/`net`/`http(s)`/`eval`/`Function`|Treffer → `error="Blocked pattern: ..."`, kein Execute, User bekommt nur die Datei
- Output-Limits: `max_output_chars=10000` (Default), Truncation setzt `truncated=True` + `\n…[truncated]`-Suffix|`SandboxResult{stdout, stderr, exit_code, execution_time_ms, truncated, error}`
- Timeout: Default 30s, bei Überschreitung `exit_code=-1` + `error="Timeout nach Ns"` + force-rm des Containers
- Code-Extraktion via [`utils/code_extractor.py`](zerberus/utils/code_extractor.py): `extract_code_blocks()` (Fenced ```lang Blöcke aus Markdown) + `first_executable_block(text, allowed_languages, fallback_language)`|Sprach-Aliase `py`→`python`, `js`/`node`/`nodejs`→`javascript`|Whitespace bleibt erhalten (Python-Indent), nur 1 trailing Newline weg
- Pipeline-Hook in [`telegram/router.py::_send_as_file`](zerberus/modules/telegram/router.py): nach erfolgreichem CODE-File-Versand → `_maybe_execute_in_sandbox()` → Result als Reply via `format_sandbox_result()`|Datei kommt ZUERST raus, Result als Reply auf Datei|`format_sandbox_result(result, filename, language)` baut `▶️ Ausgeführt in Nms` + optional `⚠️ Exit Code N` + stdout/stderr in Code-Fences
- Startup-Healthcheck in [`main.py`](zerberus/main.py) lifespan: `SandboxManager.healthcheck()` → `{ok, reason, docker, images}`|Boot-Banner `Sandbox: ok|skip|fail (...)`|Sandbox bleibt OPTIONAL — jeder Fehler ist WARNING, niemals fatal
- Singleton via `get_sandbox_manager()` (lazy, liest aktuelle `Settings`)|`reset_sandbox_manager()` für Tests
- Logging-Tag: `[SANDBOX-171]` (alle Execute-/Timeout-/Block-Events)
- Test-Pattern: Mock-basierte Unit-Tests via `unittest.mock.patch` auf `asyncio.create_subprocess_exec`/`asyncio.wait_for` ([`test_sandbox.py`](zerberus/tests/test_sandbox.py))|Live-Tests mit `@pytest.mark.docker` + `skipif(not _DOCKER_AVAILABLE)`|Marker in [`conftest.py::pytest_configure`](zerberus/tests/conftest.py) registriert
- HitL-Button-Flow für CODE-Intents folgt mit P172+ — aktuell P171 läuft die Sandbox automatisch nach LLM-Response, ohne Admin-Bestätigung. Härtung dafür ist nächster Patch.

## HitL-Hardening (P167)
- DB-Tabelle `hitl_tasks` ([`core/database.py::HitlTask`](zerberus/core/database.py))|UUID4-Hex-IDs (32 Zeichen)|Status: `pending`/`approved`/`rejected`/`expired`|`Base.metadata.create_all` in `init_db()` legt Tabelle an
- `HitlManager` ([`modules/telegram/hitl.py`](zerberus/modules/telegram/hitl.py)) async + DB-backed: `create_task` / `get_task` / `resolve_task(decision="approved"\|"rejected", is_admin_override=bool)` / `get_pending_tasks(chat_id=None)` / `expire_stale_tasks()` / `wait_for_decision`|In-Memory-Cache als Fast-Path + `asyncio.Event`-Notifizierung|`persistent=False` für Unit-Tests ohne DB
- Backward-Compat: Sync-Methoden (`create_request`/`approve`/`reject`/`get`) laufen rein in-memory|`HitlRequest = HitlTask`|alte Feld-Namen (`request_id`/`request_type`/`requester_chat_id`/`requester_user_id`) als `@property` lesbar
- Ownership-Layer im Router-Callback ([`router.py::process_update`](zerberus/modules/telegram/router.py)): `is_admin = clicker == admin_chat_id`, `is_requester = clicker == task.requester_id`|Klick erlaubt wenn admin OR requester|sonst spoofing-skip + Popup|Admin-Override (admin klickt für fremden requester) mit `is_admin_override=True` an `resolve_task` → `[HITL-167] Admin-Override` INFO-Log
- Auto-Reject-Sweep `hitl_sweep_loop(manager, interval, on_expired)`|Lifecycle in [`startup_huginn`](zerberus/modules/telegram/router.py) gestartet, in `shutdown_huginn` gecancelt|Default `timeout=300s`/`sweep_interval=30s` aus `HitlConfig` ([`core/config.py`](zerberus/core/config.py))|Defaults im Pydantic-Model (config.yaml gitignored)|abgelaufene Tasks: Telegram „⏰ Anfrage verworfen — zu langsam, Bro."
- Doppel-Bestätigung: `resolve_task` checkt `status == "pending"` vor Update → liefert `False` bei bereits aufgelöstem Task|Router antwortet „ℹ️ Schon entschieden."
- P8-Operationalisierung: Router-Callback-Pfad nimmt nur Inline-Button-Callbacks an, NIE Text|„Ja mach mal" ist kein gültiges GO für CODE/FILE/ADMIN
- Logging-Tags: `[HITL-167]` (alle Manager-/Sweep-Events)|`[HITL-POLICY-164]` bleibt aktiv für Policy-Decisions
- Test-Pattern: `tmp_db`-Fixture mit eigener SQLite ([`test_hitl_hardening.py`](zerberus/tests/test_hitl_hardening.py))|Stale-Tasks per `update().values(created_at=...)` direkt zurückdatieren|`_reset_telegram_singletons_for_tests()` für Router-Tests|Backward-Compat-Tests in `test_hitl_manager.py` mit `persistent=False`

## Guard-Kontext (P158 / P180)
- [`check_response()`](zerberus/hallucination_guard.py) akzeptiert optionale `caller_context: str` UND `rag_context: str`|`caller_context` beschreibt den Antwortenden (Persona, System-Zugehörigkeit)|`rag_context` ist das Referenz-Material das dem LLM zur Verfügung stand
- Im System-Prompt erscheinen beide als getrennte Blöcke: `[Kontext des Antwortenden]` (caller_context, ohne harten Cap) und `[Referenz-Wissen das dem Antwortenden zur Verfügung stand]` (rag_context, **Cap 1500 Zeichen** via `RAG_CONTEXT_MAX_CHARS`)
- `rag_context` lebt zusätzlich im User-Prompt als Faktenmaterial (Cap 2000, P120) — die zwei Stellen sind absichtlich redundant: System-Prompt = "treat as legitimate", User-Prompt = "Diskussions-Material"
- Huginn-Calls ([`telegram/router.py`](zerberus/modules/telegram/router.py)): Raben-Persona via `_build_huginn_guard_context(persona)` inkl. **800-Zeichen-Auszug** (P180, vorher 300)|RAG-Lookup-String (`_huginn_rag_lookup`) wird als `rag_context` an `_run_guard` durchgereicht
- Nala-Calls ([`legacy.py`](zerberus/app/routers/legacy.py)): Zerberus-Selbstreferenz („Referenzen auf Zerberus/Chris/Nala/Hel/Huginn keine Halluzinationen")|`rag_hits` werden formatiert als `rag_context` an `check_response()` gereicht (seit P120)
- WARNUNG = KEIN Block|Antwort geht IMMER an User|Admin (Chris) bekommt bei WARNUNG DM mit Chat-ID+Grund|BLOCK/TOXIC würden Antwort unterdrücken — gibt's aktuell nicht (nur OK/WARNUNG/SKIP/ERROR)
- Neue Frontends: eigene `caller_context` + `rag_context` definieren|beide leer = Pre-158-Verhalten

## Telegram-User-Allowlist (P181)
- **Nur Privat-Chats** — Gruppen sind Tailscale-intern, der Allowlist-Filter springt nur bei `chat_type == "private"`
- Drei Modi unter `modules.telegram.allowlist_mode`: `open` (Default, alle), `allowlist` (nur User in `allowed_users`), `admin_only` (nur `admin_chat_id`)
- **Admin ist im allowlist-Mode IMMER erlaubt**, auch wenn nicht in `allowed_users` — Lock-out-Schutz
- **Leere `allowed_users` im allowlist-Mode = alle erlaubt** (Safety-Fallback). Wer nur Admin will: `admin_only` setzen
- Position im Pipeline-Flow: VOR Sanitizer/Rate-Limit/RAG/LLM (kosten-/sicherheitskritisch)
- Absage-Rate-Limit: 1 Absage pro User pro Stunde (`_DENIED_NOTICE_INTERVAL_SECS`) — schützt gegen Voice-Spam → Outbound-Throttle
- Hel-UI: Huginn-Tab → Sektion „Zugriffskontrolle (Allowlist)" mit Mode-Dropdown + User-IDs-Feld (kommasepariert)
- Logging-Tag: `[ALLOWLIST-181]`

## ADMIN-Plausibilitäts-Heuristik (P182)
- `HitlPolicy.evaluate(parsed, user_message="")` nimmt jetzt optional den User-Text an|Wenn Intent=ADMIN aber `user_message` keine Admin-Marker hat (Slash-Prefix oder Keyword aus `_ADMIN_TOKENS`) → Downgrade auf CHAT
- Schutz vor Smalltalk-False-Positives: „Wie geht's dir?" wird vom LLM manchmal als ADMIN klassifiziert, aber ohne Slash und ohne Admin-Keyword bleibt das CHAT
- Token-Match via `re.findall(r"[a-zäöüß]+", text)` + Set-Intersection — kein Substring-Match (sonst würde "voraussetzung" auf "stat" hitten)
- Backward-Compat: Aufrufer ohne `user_message` (Default `""`) bekommen das Pre-182-Verhalten — alle bestehenden HitL-Policy-Tests bleiben grün
- Logging-Tag: `[HITL-POLICY-182]` für Downgrade-Events

## Unsupported-Media-Handler (P182)
- Voice/Audio/Video/Video-Note/Document/Sticker → kurze freundliche Absage + kein LLM-Call
- Photo bleibt UNTERSTÜTZT (Vision-Pfad) — nicht in `_UNSUPPORTED_MEDIA`
- Position: NACH Allowlist (denied User soll nicht erfahren dass der Bot existiert), VOR Rate-Limit (freundliche Erklärung statt „Sachte, Keule")
- Logging-Tag: `[HUGINN-182]` für Media-Skips
- Echte Voice→Whisper-Pipeline ist Backlog-Item B-072

## Settings-Cache (P156)
- `get_settings()` = Singleton in `core/config.py`|YAML-Write MUSS Cache invalidieren|sonst stale Wert + verzögerte UI-Werte nach Save
- Decorator: `@invalidates_settings` (sync+async)|Kontextmanager: `with settings_writer():` (granular)
- Migriert: 7 YAML-Writer in [`hel.py`](zerberus/app/routers/hel.py)+[`nala.py`](zerberus/app/routers/nala.py) (P156-Sweep)
- Neue YAML-Writer-Endpoints: IMMER `@invalidates_settings`|Test-Pattern: POST→GET im selben Test mit tmp-cwd-Fixture + `_settings = None`-Reset|→ [`test_huginn_config_endpoint.py`](zerberus/tests/test_huginn_config_endpoint.py)

## Datenbank-Migrationen (Alembic, seit P92)
- Config: `alembic.ini` im Root|Revisionen unter `alembic/versions/`
- Manueller Aufruf|KEIN Auto-Upgrade beim Serverstart
- Apply: `alembic upgrade head`|Neue Revision: `alembic revision -m "..."` + manuell upgrade/downgrade editieren
- IMMER idempotent (`PRAGMA table_info`-Check, `IF NOT EXISTS`)
- Vor Schema-Änderung: Backup `cp bunker_memory.db bunker_memory_backup_patch{N}.db`

## Tests (seit P93)
- Playwright in `zerberus/tests/`|Loki=E2E|Fenrir=Chaos|Vidar=Smoke
- Test-Accounts `loki`/`fenrir`/`vidar` Pflicht in `config.yaml` (P93, P153)
- Run: `pytest zerberus/tests/ -v --html=zerberus/tests/report/full_report.html --self-contained-html`
- Server muss laufen (`https://127.0.0.1:5000`)

### Test-Agenten
| Agent | Datei | Fokus | Passwort |
|-------|-------|-------|---------|
| Loki | `test_loki.py`, `test_loki_mega_patch.py` | E2E Happy-Path | `lokitest123` |
| Fenrir | `test_fenrir.py`, `test_fenrir_mega_patch.py` | Chaos/Edge/Stress | `fenrirtest123` |
| Vidar | `test_vidar.py` | Smoke (Go/No-Go) | `vidartest123` |

## Weiterführende Doku
- Projektspezifische Lessons: `lessons.md`
- Globale Lessons: https://github.com/Bmad82/Claude/lessons/
- Patch-Archiv: `docs/PROJEKTDOKUMENTATION.md`

## ⚠️ Dateinamen-Konvention
- Projektspezifisch: `CLAUDE_ZERBERUS.md` (diese Datei)
- Supervisor: `SUPERVISOR_ZERBERUS.md`
- Patch-Prompts: IMMER vollen Dateinamen mit Projektsuffix
- NIEMALS mit globaler CLAUDE.md verwechseln/zusammenführen

## Supervisor-Patch-Prompts
- Vom Supervisor (claude.ai Chat) als `.md`-Datei|nie inline Chat-Text|Claude Code via Copy-Paste

## Repo-Sync-Pflicht
- Nach jedem Patch: `git add -A; git commit -m "Patch XX – [Titel]"; git push` (nur Zerberus)
- Nach jedem Patch: PROJEKTDOKUMENTATION.md anhängen (am Ende, bestehende nicht ändern):
  - Patch-Nr+Titel|Datum (ISO)|Was geändert (1-3 Sätze)|Dateien neu/geändert|Teststand (X grün)
- PROJEKTDOKUMENTATION.md-Eintrag = Teil jedes Patches, von Claude Code erledigt|alte „liegt beim Supervisor"-Notizen ungültig
- **Nach jedem `git push`: `sync_repos.ps1` ausführen** (P164 — Sync ist LETZTER Schritt jedes Patches, Patch gilt erst als abgeschlossen wenn alle 3 Repos synchron)
- Falls `sync_repos.ps1` Fehler wirft: Chris informieren, NICHT stillschweigend überspringen
- Falls Umgebung kein PowerShell hat: explizit melden „⚠️ sync_repos.ps1 nicht ausgeführt — bitte manuell nachholen"|nicht „vergessen"
- Session-Ende ODER nach 5. Patch ist KEINE Ausrede mehr|sync nach JEDEM push (Coda-Setup pusht zuverlässig, vergisst aber Sync)
- `powershell -ExecutionPolicy Bypass -File sync_repos.ps1`|sync Ratatoskr (SUPERVISOR/CLAUDE/PROJEKTDOKU/lessons/backlog/README) + Claude-Repo (lessons.md→`lessons/zerberus_lessons.md`)|zieht Commit-Msg aus letztem Zerberus-Commit|pusht nur bei Änderungen
- NIEMALS Ratatoskr/Claude-Repo manuell editieren|nur via `sync_repos.ps1`|direkte Commits werden überschrieben
- Universelle Lessons können direkt in `C:\Users\chris\Python\Claude\lessons\` (z.B. `sqlite-db.md`)|Sync-Script fasst sie nicht an (schreibt nur `zerberus_lessons.md`)
