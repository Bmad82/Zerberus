## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P198 — Phase 5a #2: Template-Generierung beim Anlegen
**Tests:** 1487 passed (+23), 4 xfailed (pre-existing), 2 pre-existing Failures (unrelated)
**Commit:** d6889cb — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude ✅ (verify_sync.ps1: 3/3 clean, 0 unpushed)

---

## Zuletzt passiert
P198 ausgeliefert: Phase 5a Ziel #2 ("Projekte haben Struktur") abgeschlossen. Ein neu angelegtes Projekt startet nicht mehr leer, sondern bekommt zwei Skelett-Files: `ZERBERUS_<SLUG>.md` (Projekt-Bibel mit den fünf Standard-Sektionen "Ziel", "Stack", "Offene Entscheidungen", "Dateien", "Letzter Stand" — analog `ZERBERUS_MARATHON_WORKFLOW.md`) und `README.md` (kurze Prosa mit Name + Description). Inhalt rendert die Project-Daten ein.

Neuer Helper [`zerberus/core/projects_template.py`](zerberus/core/projects_template.py): `render_project_bible(project, *, now=None)` + `render_readme(project)` als Pure-Functions (synchron, deterministisch via `now`-Parameter — kein `datetime.utcnow()`-Drift in Tests). `template_files_for(project, *, now=None)` als Komposit liefert Liste `[{relative_path, content, mime_type}]`. `materialize_template(project, base_dir, *, dry_run=False, now=None)` als async DB+Storage-Schicht schreibt Bytes atomar via `_write_atomic` (lokale Kopie aus `hel._store_uploaded_bytes` — Helper soll auch ohne FastAPI-Stack laufen können, z.B. CLI-Migrations).

Templates landen im SHA-Storage (`<projects.data_dir>/projects/<slug>/<sha[:2]>/<sha>` — gleiche Konvention wie P196-Uploads), DB-Eintrag in `project_files` mit lesbarem `relative_path`. Damit erscheinen sie nahtlos in der Hel-Datei-Liste, im RAG-Index (P199) und in der Code-Execution-Pipeline (P200) — ohne Sonderpfad. Alternative wäre ein separater `_template/`-Pfad gewesen, hätte aber zwei Persistenz-Schichten und Drift-Risiko erzeugt.

**Idempotenz** via Existenz-Check vor Schreiben: `materialize_template` ruft `list_files(project_id)` ab und überspringt Pfade, die schon existieren. User-Inhalte bleiben unangetastet — wenn der User in einer früheren Session schon eine eigene README hochgeladen hat, kommt nur die fehlende Bibel neu dazu. Helper liefert nur die TATSÄCHLICH neu angelegten Files zurück (leer, wenn alles existiert). Test deckt das Szenario ab.

Verdrahtung in [`hel.py::create_project_endpoint`](zerberus/app/routers/hel.py): NACH `projects_repo.create_project()`, mit `await materialize_template(project, _projects_storage_base())`. Best-Effort: Fehler beim Materialisieren brechen das Anlegen NICHT ab (Projekt-Eintrag steht, Templates lassen sich notfalls nachgenerieren oder per Hand anlegen). Crash-Path mit Source-Audit-Test verifiziert (`monkeypatch.setattr(projects_template, "materialize_template", boom)` → `res["status"] == "ok"`). Response-Feld `template_files` listet die neu angelegten Einträge.

**Feature-Flag** `ProjectsConfig.auto_template: bool = True` (Default `True`, Default IM Pydantic-Modell weil `config.yaml` gitignored). Kann für Migrations-Tests/Bulk-Imports abgeschaltet werden.

**Git-Init bewusst weggelassen.** Im P198-Plan stand "Optional Git-Init", aber: SHA-Storage ist kein Working-Tree (Bytes liegen unter Hash-Pfaden, nicht unter `relative_path`). `git init` ergibt erst Sinn mit einem echten `_workspace/`-Layout, das mit der Code-Execution-Pipeline (P200, Phase 5a #5) kommt — dort wird ein Workspace-Mount gebraucht, in dem Code laufen kann. Bis dahin: kein halbgares Git-Init.

23 neue Tests in [`test_projects_template.py`](zerberus/tests/test_projects_template.py): 6 Pure-Function-Edge-Cases (Slug-Uppercase im Filename, Anlegedatum eingerendert, alle 5 Sektionen, Description in Ziel-Block, leere Description → Placeholder, fehlende Project-Keys → Defaults), 2 README-Tests, 3 `template_files_for`-Komposit-Tests, 6 Materialize-Tests via `tmp_db` + `tmp_path` (Two-Files, SHA-Storage-Pfad, Idempotenz, User-Content-Schutz, Dry-Run-no-side-effect, Content-Render), 3 End-to-End über `create_project_endpoint` (Flag-on, Flag-off, Crash-Resilienz), 3 Source-Audit (Imports, Flag-Honor, Konstanten). `disable_auto_template`-Autouse-Fixture in `test_projects_endpoints.py` + `test_projects_files_upload.py` hält deren File-Counts stabil. Teststand 1464 → **1487 passed**.

Logging-Tag `[TEMPLATE-198]` zeigt `slug`/`path`/`size`/`sha[:8]` pro neu angelegter Datei + `skip slug=... path=... (already exists)` bei Idempotenz-Skip. Bei Crash WARNING via `logger.exception` mit Slug.

**Phase 5a Ziel #2 ist damit abgeschlossen.** Ziele #1 (Backend P194 + UI P195 + Datei-Upload P196) und #2 (Templates) sind durch. Decision 3 (Persona-Merge-Layer) seit P197 aktiv.

## Nächster Schritt — sofort starten
**P199: Projekt-RAG-Index** — Phase 5a Ziel #3 ("Projekte haben eigenes Wissen"). Jedes Projekt soll einen isolierten FAISS-Index bekommen, der die in P196 hochgeladenen + die in P198 generierten Files indexiert. Code-LLM braucht Projektkontext, ohne dass der globale RAG-Index mit projekt-spezifischen Inhalten verschmutzt wird.

Konkret (Coda darf abweichen):
1. **Index-Schema definieren.** Empfehlung: pro Projekt-Slug ein eigener FAISS-Index unter `data/projects/<slug>/_rag/index.faiss` + `_rag/meta.json`. Dual-Embedder-Setup (P187) übernehmen — DE/EN sprach-spezifisch, oder erstmal nur DE für den Anfang.
2. **Index-Helper.** Neuer `zerberus/core/projects_rag.py` mit `index_project_file(project_id, file_id)` (chunkt + embedded + persistiert), `query_project_rag(project_id, query, *, k=5)` (lädt Index, Top-K Chunks). Bestehende `chunkers.py` + Embedder-Setup aus `modules/rag/` weiterverwenden.
3. **Trigger-Punkte.** (a) Beim Upload (`upload_project_file_endpoint` in `hel.py` NACH `register_file`), (b) Beim Materialisieren (P198 — `materialize_template` ruft am Ende `index_project_file` für jede neue Datei), (c) Beim Delete (Index-Eintrag entfernen, sonst stale).
4. **Wirkung im Chat.** Wenn `X-Active-Project-Id` gesetzt ist und das Projekt einen RAG-Index hat: NACH dem System-Prompt-Build (P197), aber VOR `messages.insert(0, system)`, einen Block "[PROJEKT-RAG]" mit den Top-K Chunks zur User-Frage anhängen. Oder als Tool-Use-Pattern, wenn Code-LLM später aktiviert wird.
5. **Feature-Flag.** `ProjectsConfig.rag_enabled: bool = True` (oder `auto_index: bool`) — Tests können den Pfad abschalten, lokale Setups ohne FAISS-Dependency auch.
6. **Tests.** Pure-Function-Tests für Chunking + Embedding-Mock, async DB-Tests für Index-Persistierung, End-to-End über Upload-Endpoint (Datei rein → Index hat Eintrag), Edge-Cases (leere Datei, binäre Datei → skip, Index existiert nicht → on-the-fly bauen).

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P199** Projekt-RAG-Index — Phase 5a Ziel #3 (oben beschrieben)
- **P200** Code-Execution-Pipeline — Ziel #5 (Intent `PROJECT_CODE` → LLM → Sandbox, mit Workspace-Layout für Git)
- **P201** Nala-Tab "Projekte" + Header-Setter — verdrahtet das Nala-Frontend mit P197 (User wählt aktives Projekt im Chat-UI)
- **P202** HitL-Gate vor Code-Execution — Ziel #6

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198: `projects_template.py` + Endpoint-Verdrahtung + `auto_template`-Flag)**.

Helper aus P198 die in P199+ direkt nutzbar sind:
- `projects_template.template_files_for(project)` — wenn ein anderer Code-Pfad die Skelett-Files in-memory braucht (z.B. Migrations-Tool, das alle alten Projekte nachträglich materialisiert), Pure-Function ohne I/O
- `projects_template.materialize_template(project, base_dir, *, dry_run=False)` — bequeme async-Schnittstelle, idempotent. Mit `dry_run=True` als Inspektions-Modus für CLI-Tools.
- `projects_template.PROJECT_BIBLE_FILENAME_TEMPLATE` + `README_FILENAME` — Konstanten für andere Module, die Template-Files erkennen müssen (z.B. RAG-Filter, der Template-Inhalte separat gewichten will)
- `projects_template._write_atomic(target, data)` — atomar schreiben, falls noch andere Helper Bytes ins Storage legen müssen, die nicht den Upload-Endpoint nutzen

Projekt-Bausteine (P194-P198):
- `projects_repo.create_project/get_project/update_project/archive_project/delete_project` — CRUD
- `projects_repo.register_file/list_files/get_file/delete_file` — Datei-Metadaten
- `projects_repo.compute_sha256/storage_path_for/sanitize_relative_path/is_extension_blocked/count_sha_references` — Helper
- `persona_merge.merge_persona/read_active_project_id/resolve_project_overlay` — Persona-Layer (P197)
- `hel._projects_storage_base()` — Storage-Wurzel-Indirektion (Tests können umbiegen)

Persona-Pipeline-Bausteine (P184/P185/P197):
- `legacy.load_system_prompt(profile_name)` — User-Persona-Resolver
- `legacy._wrap_persona(prompt)` — AKTIVE-PERSONA-Marker davor
- `persona_merge.merge_persona(base, overlay, slug)` — Projekt-Overlay-Block hinten dran (P197)
- `runtime_info.append_runtime_info(prompt, settings)` — Live-Modell/Sandbox/RAG-Block
- `prompt_features.append_decision_box_hint(prompt, settings)` — Decision-Box-Syntax-Anweisung

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), #11-16 (P196), #17-18 (P197: Header-Test mit curl, Hel-UI Persona-Overlay-Edit-→-LLM-Wirkung), **#19-21 (P198: Anlegen erzeugt Files, Idempotenz, Edit-Bibel-überlebt-Re-Anlegen)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (4 xfailed sichtbar — nicht blockierend).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete (P194 `delete_project` löscht aktuell nur DB-Cascade, Bytes können verwaisen wenn der SHA nirgends sonst referenziert ist) — low-prio bis Storage-Volumen relevant.
- **Nala-Frontend setzt `X-Active-Project-Id` noch nicht** — kommt mit dem Nala-Tab "Projekte" (P201-Vorschlag). Bis dahin ist P197 nur über externe Clients (`curl`, SillyTavern, eigene Skripte) nutzbar.
- **Hel-UI zeigt `template_files` aus der Endpoint-Response noch nicht prominent an** — erscheint nach dem Anlegen ja sowieso in der Datei-Liste, aber ein "Erstellt: 2 Skelett-Files" als Toast wäre nett. Low-prio.
- **Pre-existing Projekte ohne Templates** — Projekte, die VOR P198 angelegt wurden, haben keine Skelett-Files. Wenn Chris die nachrüsten will: `materialize_template` ist idempotent → einmal pro Projekt aufrufen, fügt nur die fehlenden Files hinzu. Bei Bedarf einen kleinen CLI-Migrations-Befehl bauen, kein eigener Patch nötig.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | **Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198)** | **Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198)** | **Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (Crash beim Materialisieren bricht Anlegen nicht ab — P198)**
