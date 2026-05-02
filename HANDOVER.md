## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P203a — Project-Workspace-Layout (Vorbereitung Phase 5a #5)
**Tests:** 1635 passed (+36), 4 xfailed (pre-existing), 2 failed (pre-existing aus Schuldenliste: edge-tts + dual-rag)
**Commit:** 2cab625 — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude ✅ (verify_sync.ps1: 3/3 clean, 0 unpushed)

---

## Zuletzt passiert (1 Patch in dieser Session)

**P203a — Project-Workspace-Layout (Vorbereitung Phase 5a #5).** Codas eigenständige Aufteilung von P203 (Code-Execution-Pipeline): Phase-5a-Ziel #5 ist groß (Workspace + Sandbox-Mount + Tool-Use-LLM + UI-Synthese), wird in drei Sub-Patches zerlegt. P203a legt das Workspace-Layout — die Sandbox kann Dateien später nur dann sinnvoll mounten, wenn sie an ihrem `relative_path` (statt unter den SHA-Pfaden) im Filesystem stehen. Heute also: per Projekt ein `<data_dir>/projects/<slug>/_workspace/`-Ordner, in den die `project_files`-Einträge per Hardlink (Copy-Fallback) gespiegelt werden. Verdrahtet als Best-Effort-Side-Effect bei Upload, Delete-File, Delete-Project, Template-Materialize — analog RAG-Trigger-Pattern (P199).

**Architektur-Entscheidungen P203a:**

- **Hardlink primär, Copy als Fallback.** `os.link` versucht zuerst — gleiche Inode wie SHA-Storage, kein Plattenplatz-Verbrauch, instantan. Bei `OSError` (cross-FS, NTFS-without-dev-mode, FAT32, Permission) → `shutil.copy2`. Methode wird im Log + Test-Return ausgewiesen (`"hardlink"` / `"copy"` / `None`-bei-noop). Hat den Nebeneffekt, dass auf Windows-Test-Maschinen ohne dev-mode der Copy-Pfad live getestet wird.
- **Atomic via Tempfile + os.replace.** Auch im Workspace, nicht nur im SHA-Storage. Grund: parallele Sandbox-Reads dürfen nie ein halb-geschriebenes Workspace-File sehen. Pattern dupliziert (statt Import aus hel.py), weil das Workspace-Modul auch ohne FastAPI-Stack importierbar bleiben muss (Tests, künftige CLI).
- **Pfad-Sicherheit zwei-stufig.** `is_inside_workspace(target, root)` resolved beide Pfade und prüft `relative_to`. Schützt gegen `../../etc/passwd`-style relative_paths aus alten Datenbanken oder Migrations. Plus: `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet — verhindert ein versehentliches `wipe_workspace(Path("/"))` bei Slug-Manipulation.
- **Best-Effort-Verdrahtung in vier Trigger-Punkten.** Upload-Endpoint (nach `register_file` + nach RAG-Index), Delete-File-Endpoint (nach `delete_file`, mit Slug aus extra `get_project`-Call vor dem DB-Delete), Delete-Project-Endpoint (`wipe_workspace` nach `delete_project`), `materialize_template` (nach jedem `register_file`). Alle vier wickeln den Workspace-Call in try/except — Hauptpfad bleibt grün. Lazy-Import (`from zerberus.core import projects_workspace` im try-Block) wie bei RAG, damit der Helper nicht beim Import-Time geladen wird.
- **`sync_workspace` als Komplett-Resync.** Materialisiert alle DB-Files, entfernt Orphans (Files im Workspace, die nicht mehr in der DB sind). Idempotent — zweiter Aufruf liefert `{materialized:0, removed:0, skipped:N}`. Nicht in den Endpoints verdrahtet (Single-File-Trigger reichen), aber als Recovery-API für künftige CLI/Reindex-Endpoint vorhanden.
- **Feature-Flag `ProjectsConfig.workspace_enabled`.** Default `True` — der Workspace ist ab P203a Standardverhalten. Tests können es abschalten (siehe `TestWorkspaceDisabled`-Klasse), nützlich falls die CI auf einer FS-Sandbox läuft, die weder Hardlinks noch Copy zulässt.

**Tests P203a (36 neu):** `TestPureFunctions` (4: layout, no-io, traversal, root-itself), `TestMaterializeFile` (6: create, nested, idempotent, traversal, missing-source, copy-fallback via monkeypatched `os.link`), `TestRemoveFile` (5: removes, cleans-empty-parents, keeps-non-empty, missing, traversal), `TestWipeWorkspace` (3: removes, idempotent-missing, rejects-wrong-dirname), `TestSyncWorkspace` (4: unknown-project, materializes-all, idempotent, removes-orphans), `TestAsyncWrappers` (3: materialize_file_async, unknown-file, remove_file_async), `TestEndpointIntegration` (4: upload, delete-file, delete-project, template-materialize), `TestWorkspaceDisabled` (1), `TestSourceAudit` (5).

**Welcher Pfad existiert noch nicht (kommt mit P203b/c):**

- Sandbox-Mount auf den Workspace-Ordner — der bestehende `SandboxManager` (P171) verbietet Volume-Mount explizit ("Kein Volume-Mount vom Host"). P203b muss entweder eine Erweiterung (`workspace_mount: Optional[Path]`) oder eine Schwester-Klasse (`ProjectSandboxManager`) bauen. Empfehlung: Erweiterung mit Read-Only-Mount per Default, Read-Write nur explizit.
- LLM-Tool-Use-Pfad für Code-Generation — kommt mit P203c. Bis dahin steht der Workspace bereit, aber niemand schreibt was rein außer dem Upload/Template-Pfad.
- Frontend-Render von Code+Output-Blöcken — kommt mit P203c.

## Nächster Schritt — sofort starten

**P203b: Sandbox-Workspace-Mount.** `SandboxManager` um optionalen `workspace_mount: Optional[Path]` erweitern (Read-Only-Default, Read-Write per separatem Flag). Bestehender Pfad ohne Mount bleibt unverändert. Neue Helper-Funktion `execute_in_workspace(project_id, code, language, base_dir)` die intern `workspace_root_for(slug, base_dir)` zieht und an `SandboxManager.execute(workspace_mount=...)` durchreicht. Tests: existing-Pfad unverändert, Workspace-Mount sichtbar im docker-args, Read-Write-Flag, Sicherheit (Mount-Pfad muss innerhalb `data_dir` liegen).

Konkret (Coda darf abweichen):
1. **`SandboxManager.execute` Signatur erweitern.** Optional `workspace_mount: Optional[Path] = None`, optional `mount_writable: bool = False`. Wenn gesetzt, in `_run_in_container` einen `-v <abs>:/workspace[:ro]` an die docker-args anhängen + `--workdir /workspace`. Wenn `mount_writable=False` → `:ro`-Suffix.
2. **Neue Convenience-Funktion `execute_in_workspace(project_id, code, language, base_dir, *, writable=False)`** in `projects_workspace.py` (oder neuem Modul `projects_sandbox.py`): zieht Slug aus DB, ruft `workspace_root_for`, reicht an `SandboxManager.execute` durch.
3. **Tests:** Pure-Function (docker-args-Audit für `:ro` vs ohne), Sicherheits-Check (Mount-Pfad muss innerhalb `data_dir`-Tree liegen — verhindert `/etc/passwd`-Mount bei manipuliertem Slug), Integration mit Mock-Sandbox.
4. **HitL-Gate kommt erst P205.** P203b läuft direkt durch — sicher, weil Workspace-Mount Read-Only-default und Sandbox bereits hart-isoliert ist.

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P203b** Sandbox-Workspace-Mount — wie oben
- **P203c** Chat-Pipeline-Verdrahtung (Tool-Use-LLM, Output-Synthese, UI) — schließt Ziel #5 ab
- **P204** RAG-Toast in Hel-UI — Upload-Response enthält `rag.{chunks, skipped, reason}`, Frontend ignoriert es, kleiner Füller
- **P205** HitL-Gate vor Code-Execution — Ziel #6
- **P206** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P207** Spec-Contract / Ambiguitäts-Check — Ziel #8

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200)**, **Nala-Tab "Projekte" + Header-Setter (P201)**, **PWA-Auth-Hotfix (P202)**, **Project-Workspace-Layout (P203a: Hardlink+Copy-Fallback, atomic, sync_workspace, vier Best-Effort-Trigger, Pfad-Sicherheit)**.

Helper aus P203a die in P203b+ direkt nutzbar sind:
- `projects_workspace.workspace_root_for(slug, base_dir)` — Pfad-Konvention `<base>/projects/<slug>/_workspace/`. Wird von P203b als Sandbox-Mount-Source genommen
- `projects_workspace.materialize_file(workspace_root, relative_path, source_path)` — Sync FS-Operation, idempotent. Falls P203c nach Code-Execution Files in den Workspace zurückrollt: hier ist die Pure-Schicht
- `projects_workspace.sync_workspace(project_id, base_dir)` — Komplett-Resync. Empfohlen als Implementation für künftiges `POST /hel/admin/projects/{id}/resync-workspace`
- `projects_workspace.is_inside_workspace(target, root)` — Pfad-Sicherheits-Check, Pure. Verwendbar in P203b für Mount-Source-Validation
- `projects_workspace.wipe_workspace(workspace_root)` — Complete-Removal mit Sicherheits-Check (Pfad muss auf `_workspace` enden). Wird in `delete_project_endpoint` aufgerufen — P203b/c müssen das nicht nochmal triggern

Helper aus P201 die in P203+ direkt nutzbar sind:
- `nala.nala_projects_list(request)` — JWT-authenticated Read-Endpoint
- JS-`profileHeaders(extra)` — wirkt für alle Nala-Calls. Wenn P203c weitere Header durchschleifen muss (z.B. `X-Sandbox-Run-Id`), hier ergänzen statt am Call-Site
- JS-`getActiveProjectId()` / `getActiveProjectMeta()` — falls ein Frontend-UI-Element den aktuellen Projekt-Slug rendern soll
- JS-`escapeProjectText(s)` — generischer XSS-Helper, in P203c wiederverwendbar für User-eingegebene Code-Filenames/-Outputs

Helper aus P200 die in P203+ direkt nutzbar sind:
- `pwa.render_service_worker(cache_name, shell)` — Pure-Function-SW-Renderer. Achtung: Nach P202 cached er KEINE Navigation mehr — das ist Absicht und muss so bleiben
- `scripts/generate_pwa_icons.py` — deterministischer PIL-Renderer

Helper aus P199 die in P203+ direkt nutzbar sind:
- `projects_rag.query_project_rag(project_id, query, base_dir, *, k=5)` — Top-K-Hits für eine Query. Nutzt P203c für den Code-Generation-Prompt
- `projects_rag.format_rag_block(hits, project_slug=None)` — markdown-formatierter Block für System-Prompt-Anhang
- `projects_rag.chunk_file_content(text, relative_path)` — Pure-Function-Chunker für ein einzelnes File
- `projects_rag.index_project_file(project_id, file_id, base_dir)` / `remove_file_from_index(...)` — bequeme async-API für Trigger-Punkte

Projekt-Bausteine (P194-P199):
- `projects_repo.create_project/get_project/update_project/archive_project/delete_project` — CRUD
- `projects_repo.register_file/list_files/get_file/delete_file` — Datei-Metadaten
- `projects_repo.compute_sha256/storage_path_for/sanitize_relative_path/is_extension_blocked/count_sha_references` — Helper
- `persona_merge.merge_persona/read_active_project_id/resolve_project_overlay` — Persona-Layer (P197)
- `projects_template.template_files_for/materialize_template` — Skelett-Files (P198)
- `projects_rag.index_project_file/remove_file_from_index/query_project_rag/format_rag_block` — RAG-Layer (P199)
- `projects_workspace.workspace_root_for/materialize_file/remove_file/wipe_workspace/sync_workspace` — Workspace-Layer (P203a)
- `hel._projects_storage_base()` — Storage-Wurzel-Indirektion (Tests können umbiegen)

Persona-Pipeline-Bausteine (P184/P185/P197/P199/P201):
- `legacy.load_system_prompt(profile_name)` — User-Persona-Resolver
- `legacy._wrap_persona(prompt)` — AKTIVE-PERSONA-Marker davor
- `persona_merge.merge_persona(base, overlay, slug)` — Projekt-Overlay-Block hinten dran (P197)
- `persona_merge.read_active_project_id(headers)` — liest `X-Active-Project-Id` aus dem Request (P197)
- `runtime_info.append_runtime_info(prompt, settings)` — Live-Modell/Sandbox/RAG-Block
- `prompt_features.append_decision_box_hint(prompt, settings)` — Decision-Box-Syntax-Anweisung
- `projects_rag.query_project_rag + format_rag_block` — Projekt-RAG-Block hinten dran (P199)

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), #11-16 (P196), #17-18 (P197), #19-21 (P198), #22-25 (P199), #26-32 (P200), #33-37 (P201), #38-41 (P202), **#42-45 (P203a: Push, Workspace-Materialize live, Hardlink-vs-Copy auf Chris' FS, Wipe-bei-Delete-Project verifizieren)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (`edge-tts` + `test_rag_dual_switch.test_fallback_logic`) — **lokaler Stand 1599 baseline + 36 neu = 1635 passed, 2 failed (beide pre-existing aus Schuldenliste)**, 4 xfailed sichtbar, 1 skipped — nicht blockierend.
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete (P194/P199 räumen DB+RAG-Ordner; **P203a räumt auch den Workspace-Ordner**), aber NICHT die `<sha[:2]>/<sha>`-Bytes — separater Cleanup-Job.
- **Hel-UI zeigt RAG-Status noch nicht prominent an** — P204 vorgemerkt.
- **Pre-existing Files ohne Workspace** — Files, die VOR P203a hochgeladen oder via P198 materialisiert wurden, sind nicht im Workspace. Recovery-Pfad steht (`projects_workspace.sync_workspace(project_id, base_dir)`). Mini-CLI: `for p in await list_projects(): await sync_workspace(p["id"], base_dir)`. Oder Reindex-Endpoint `POST /hel/admin/projects/{id}/sync-workspace` — kleiner Patch, gehört in P204 mit rein.
- **Pre-existing Files ohne RAG-Index** — separat (P199-Kontext). Beide Reindex-Pfade könnten in einem Endpoint zusammengefasst werden.
- **Lazy-Embedder-Latenz** — beim ersten `index_project_file`-Call lädt MiniLM-L6-v2. Während Tests via Monkeypatch abgefangen, in Produktion einmal pro Server-Start. Falls das stört: Eager-Init in `main.py` Startup-Hook.
- **Embedder-Modell hardcoded auf MiniLM-L6-v2** — wenn auf dual umsteigen gewünscht, `_embed_text` umbauen.
- **PWA-Cache-Bust nach Patches** — Cache-Namen `nala-shell-v2`/`hel-shell-v2`. Falls nochmal harter Cache-Bust nötig: auf `-v3` setzen + redeployen.
- **PWA-Icons nur Initial-Buchstabe** — `scripts/generate_pwa_icons.py` Theme-Konstanten anpassen falls echtes Logo.
- **PWA hat keinen Offline-Modus für die Hauptseite** — P202 hat den HTML-Cache aus dem SW-Pfad genommen. Akzeptabel für Heimserver.
- **P201 Nala-Tab zeigt nur Auswählen, kein Anlegen** — Quick-Create-Button später möglich.
- **P203a Workspace-Mount im Sandbox** — kommt mit P203b. Bis dahin steht der Workspace bereit, wird aber von keinem Sandbox-Pfad genutzt. Der existierende `_run_pipeline` (orchestrator.py) ruft den ALTEN Sandbox-Pfad (`executor.py`, P52) ohne Mount — nicht ändern in P203a, das ist P203b's Job.
- **Hardlink vs. Copy auf Windows** — `os.link` funktioniert auf NTFS, schlägt aber bei FAT32/exFAT/cross-device fehl. Der Copy-Fallback ist live-getestet via Monkeypatch. Wenn Chris' Workstation auf NTFS läuft, sollten 99% der Materialisierungen Hardlinks sein. Bei Volumen-relevanten Workspaces später: `df -h` prüfen, ob Hardlinks tatsächlich greifen (Workspace-Größe sollte ~0 sein).

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | **Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) — Vorbereitung für Code-Execution-Pipeline**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt toleriert RAG-Fehler (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig (P199) | PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden (P200) | Service-Worker-Scope folgt aus dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen (P200) | `persona_overlay` darf NIEMALS in einer User-sichtbaren Response auftauchen (P201) | Header-Injektion für Cross-Cutting-Concerns MUSS zentral in `profileHeaders()` passieren (P201) | Zombie-ID-Schutz: nach jedem List-Refresh prüfen, ob die im localStorage gemerkte Auswahl noch existiert; sonst räumen (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test für alle User-eingegebenen Felder im DOM-Renderer (P201) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen, wenn die Page WWW-Authenticate-Mechanismen nutzt (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | **Workspace-Pfad-Sicherheit zwei-stufig: `is_inside_workspace` für jeden Schreib-/Löschvorgang im Workspace, plus `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet — verhindert Slug-Manipulations-Angriffe (P203a)** | **Hardlink primär, Copy als Fallback bei `OSError` — die Methode wird im Return ausgewiesen damit Tests/Logs sehen welcher Pfad griff (P203a)** | **Atomic-Write-Pattern (tempfile + os.replace) gilt auch für Workspace-Spiegelung — parallele Sandbox-Reads dürfen nie ein halb-geschriebenes File sehen (P203a)**
