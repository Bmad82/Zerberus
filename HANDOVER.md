## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-03
**Letzter Patch:** P210 — Huginn-RAG-Auto-Sync (Phase 5a Ziel #18 ABGESCHLOSSEN, ausserhalb der ursprünglichen 17 Ziele eingeschoben)
**Tests:** 2176 passed (+53 P210 aus 2123 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 1 skipped (existing)
**Manuelle Tests:** 1 / 93 ✅
**Commit:** d2fc5de — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude — alle drei gepusht (Ratatoskr `10f45cd`, Claude `3e46b6e`, 0 unpushed Commits in allen drei). `verify_sync.ps1` meldet Zerberus working-tree dirty: `system_prompt_chris.json` bleibt unangetastet (Mutzenbacher-Persona-Experiment, vom User am 2026-05-01 als "gedroppt" markiert — Schulden-Liste, Stand identisch zu allen Vorgaenger-HANDOVERS seit P205. Naechster Coda kann sie ggf. via `git checkout system_prompt_chris.json` zuruecksetzen oder via `git rm --cached` + `.gitignore` aus dem Tracking nehmen).

---

## Zuletzt passiert (1 Patch in dieser Session)

**P210 — Huginn-RAG-Auto-Sync / Selbstwissen-Aktualität (Phase 5a Ziel #18 ABGESCHLOSSEN).** User-Pain-Patch ausserhalb der ursprünglichen 17 Phase-5a-Ziele eingeschoben. Huginn antwortete konsistent "Patch 178" auf "bei welchem Patch sind wir?", obwohl die Doku längst auf P209 stand. Diagnose ergab zwei voneinander unabhängige Ursachen: (1) `docs/huginn_kennt_zerberus.md` hatte keinen expliziten Stand-Anker — Huginn rät auf prominente Logging-Tags, der häufigste war `[HUGINN-178]` (Patch 178 = Huginn-RAG-Lookup-Feature selbst), (2) Index-Mtime-Drift — Coda updated die Doku, aber Chris musste manuell hochladen und vergaß es regelmässig. P210 fixt beide: Doku-Header bekommt `## Aktueller Stand`-Block (immer als erster Chunk indexiert, rankt bei Stand-Fragen oben), Auto-Sync-Skript löscht alte Chunks + lädt neue automatisch, eingebaut in den Marathon-Push-Zyklus.

**Was P210 baut:**

1. **Neues Modul** [`tools/sync_huginn_rag.py`](tools/sync_huginn_rag.py) mit drei Schichten:
   - **Pure-Function** — `build_sync_plan(source_path, *, source_name, category, run_reindex)` plant 2 oder 3 Steps (DELETE → POST [→ REINDEX]), validiert die Doku vorab via `validate_doc_header`. `validate_doc_header(text) -> (bool, str)` prüft `## Aktueller Stand`-Block + `**Letzter Patch:** P###`-Zeile (Diagnose-Helper). `extract_current_patch(text) -> Optional[str]` liest die Patchnummer für Logging. `parse_auth_string(raw)` / `load_auth_from_env(env, env_file_path)` / `resolve_base_url(env)` injectable für Tests. `.env`-Parser ist trivial (KEY=VALUE, # für Kommentare, optional Quotes) — KEINE python-dotenv-Dependency.
   - **Async-Wrapper** — `execute_sync_plan(plan, base_url, *, auth, http_client, timeout)` mit `httpx.AsyncClient`, fail-soft bei DELETE-404 (Erst-Upload-Idempotenz), fail-fast bei UPLOAD-Fehler. Exceptions werden als `SyncResult.errors`-Liste eingesammelt statt zu propagieren — der ganze Plan läuft auch bei Einzel-Step-Crashes durch. `httpx` wird lazy importiert (im Test-Pfad nicht nötig wenn `http_client` injiziert wird).
   - **SyncStep / SyncResult-Dataclasses** — `SyncStep(method, path, params, files, data, success_codes, description)`, `SyncResult(success, steps_executed, steps_failed, errors, response_payloads)`.
   - **CLI** `python -m tools.sync_huginn_rag` mit `--source/--source-name/--category/--base-url/--reindex/--env-file/--dry-run`. Exit-Codes: `0`=ok, `1`=Sync-Fehler, `2`=Plan-Fehler.

2. **PowerShell-Wrapper** [`scripts/sync_huginn_rag.ps1`](scripts/sync_huginn_rag.ps1) — analog `verify_sync.ps1`. Setzt CWD auf Repo-Root, ruft Python-Modul, reicht Exit-Code durch. Switches `-Reindex`, `-DryRun`, `-Source`, `-BaseUrl` als Pass-Through.

3. **Doku-Header-Pflicht** — neuer `## Aktueller Stand`-Block in [`docs/huginn_kennt_zerberus.md`](docs/huginn_kennt_zerberus.md) (Zeilen 3-12) und Spiegel-Kopie [`docs/RAG Testdokumente/huginn_kennt_zerberus.md`](docs/RAG%20Testdokumente/huginn_kennt_zerberus.md) (Zeilen 3-12). Vier Pflicht-Bullets: Letzter Patch, Phase, Tests, Datum. Source-Audit-Tests prüfen Existenz UND P##-Mindestnummer (`>= 210`).

4. **WORKFLOW.md erweitert** — neue Doku-Pflicht-Tabellenzeile `RAG-Sync für huginn_kennt_zerberus.md` (Skript-Aufruf, Endpoints, Idempotenz). Neuer ausführlicher Regel-Block im Abschnitt nach `## Doku-Pflicht`: nennt Pflicht-Header, Sync-Skript-Aufruf (PowerShell + Python), Auth via Env-Var (`HUGINN_RAG_AUTH=User:Pass`), Server-URL via `ZERBERUS_URL`, Spiegel-Kopie wird NICHT mitsynct (nur Test-Material). Neues Phase-5a-Ziel #18 "Huginn kennt sich selbst zuverlässig" (✅ markiert).

5. **Reihenfolge-Invariante DELETE → UPLOAD.** Würde man umkehren, würde DELETE die gerade hochgeladenen neuen Chunks soft-löschen (selber `source`-String). Test `test_delete_before_upload` schützt explizit gegen diese Refactoring-Falle.

6. **Endpoints wiederverwendet** aus Hel-Admin-Router (kein Backend-Touch):
   - `DELETE /hel/admin/rag/document?source=<name>` (P116 Soft-Delete) — markiert Chunks als `deleted: true` in `metadata.json`. 404 wenn keine Chunks zur Source existieren — fuer den Sync OK (Erst-Upload-Fall).
   - `POST /hel/admin/rag/upload` (P108 + P111 mit Auto-Detect — explizit `category=system` gesetzt fuer Huginn-RAG-Filter aus P178).
   - `POST /hel/admin/rag/reindex` (Default OFF — Soft-Delete reicht für Lookup, Reindex spart eigentlich nur Disk-Space).

**Tests:** 53 in [`test_p210_huginn_rag_sync.py`](zerberus/tests/test_p210_huginn_rag_sync.py).

`TestBuildSyncPlan` (11) — default-2-steps / delete-source-param / delete-accepts-404 / upload-only-200 / upload-category / upload-file / reindex-optional / missing-file / missing-header / missing-patch / delete-before-upload.

`TestValidateDocHeader` (5) — valid / empty / missing-header / missing-patch / header-mid-file.

`TestExtractCurrentPatch` (4) — p210 / uppercase / no-match / 3-or-4-digit.

`TestParseAuthString` (5) — basic / colon-in-password / empty / no-colon / empty-user.

`TestLoadAuthFromEnv` (5) — env-var / no-source / file / env-beats-file / quoted-value.

`TestResolveBaseUrl` (3) — default / from-env / strip-slash.

`TestExecuteSyncPlan` (10) — happy / 404-ok / 500-fail / 403-fail / exception / method-carry / multipart / payload-recorded / with-reindex.

`TestCli` (3) — dry-run / missing-file / invalid-doc.

`TestDocSourceAudit` (3) — main-stand-anker / main-recent-patch (`>= 210`) / mirror-stand-anker.

`TestWorkflowSourceAudit` (2) — sync-in-doku-pflicht / goal-18.

`TestSmoke` (3) — module-exports / constants / ps1-wrapper-exists.

**Logging-Tag:** `[SYNC-210]` mit "Quelle:", "Server:", "Source-Name:", "Kategorie:", "Reindex:", "Auth: gesetzt|NICHT gesetzt", "Dry-Run — N geplante Schritte:", "Sync erfolgreich.", "Sync fehlgeschlagen (N Step(s)).", "Plan-Fehler: ...". Pure-User-CLI-Logs (kein Server-Side-Logging — Server schreibt seine eigenen `[RAG-108/111/116/169]`-Tags).

**Kollateral-Fix:** Keine Test-Fixes nötig. Der neue Patch fügt nur ein eigenständiges Modul + Tests + Doku hinzu, modifiziert keinen bestehenden Code-Pfad. Der HANDOVER-OFFENES-Eintrag von 2026-05-03 ("Chris reviewed die neue Doku zuerst und uploaded selbst") ist durch P210 obsolet — Chris muss die Datei nicht mehr manuell hochladen.

## Nächster Schritt — sofort starten

**P211: GPU-Queue für VRAM-Konsumenten (Phase 5a Ziel #11).** Kooperatives Scheduling zwischen Whisper, Gemma, Embeddings und Reranker. Pure-Token-Bucket-Pattern + asyncio.Lock pro VRAM-Slot, fail-fast bei Overrun. Ziel: keine GPU-Crashes mehr durch parallele Modell-Loads, klare Wartezeit-Anzeige im Frontend.

**Konkret (Coda darf abweichen):**

1. **Pure-Function-Schicht** in neuem Modul `zerberus/core/gpu_queue.py`: `compute_vram_budget(consumer_name) -> int` (statische Tabelle: Whisper=4 GB, Gemma=2 GB, Embeddings=1 GB, Reranker=512 MB). `should_queue(active_total, requested) -> bool` (wenn `active_total + requested > VRAM_TOTAL_MB`).
2. **Async-Wrapper** `acquire_vram_slot(consumer_name, *, timeout=30)` mit `asyncio.Lock`-Pool pro Slot + Token-Bucket. Fail-fast bei Timeout.
3. **Verdrahtung** in den Whisper-/Gemma-/Embedding-/Reranker-Pfaden — wrap each model call mit `async with vram_slot(consumer_name): ...`.
4. **Frontend-Toast** "GPU wartet auf Whisper, Position 2 in Queue..." (analog P205 RAG-Toast).
5. **Audit-Tabelle** `gpu_queue_audits` mit `consumer_name`/`wait_ms`/`queue_position`/`created_at`.
6. **Tests**: Token-Bucket-Boundary, Lock-Acquire-Release, Timeout, Source-Audit, JS-Integrity.
7. **Was P211 NICHT macht**: dynamische VRAM-Erkennung via `nvidia-smi` (statisches Budget reicht), Mehr-GPU-Verteilung (genug fuer eine RTX 3060), Cancel-Outstanding-Slots (FIFO).

Alternativ falls P211 zu viel: **P212 Secrets bleiben geheim (Ziel #12)** — `.env`-Encrypt + Sandbox-Injection-Filter + Output-Maskierung. Schmaler Scope, fokussiert auf den ein-Variable-Fall (`OPENAI_API_KEY` in `.env` darf nie im Sandbox-Output landen).

**Reihenfolge-Vorschlag** (Coda darf abweichen):
- **P211** GPU-Queue für VRAM-Konsumenten — Ziel #11
- **P212** Secrets bleiben geheim — Ziel #12
- **P213** Reasoning-Schritte sichtbar im Chat — Ziel #13

Helper aus P210 die in P211/P212/P213 direkt nutzbar sind:
- **`build_sync_plan` + `execute_sync_plan`-Pattern** — Pure-Function-Plan + Async-IO-Execute mit injectable `http_client`. Anwendbar auf jeden HTTP-Bulk-Workflow (RAG-Reindex, Multi-File-Upload, etc.).
- **`MockClient`-Pattern** — 30-Zeilen-Test-Helper fuer jeden httpx-basierten Code, klont sich trivial auf andere HTTP-Tests.
- **`validate_doc_header`-Pattern** — Pflicht-Check fuer strukturierte Markdown-Files. Klont sich fuer andere RAG-Dokus, falls die je in den RAG kommen.
- **Source-Audit-Test-Pattern** mit `TestDocSourceAudit` + `TestWorkflowSourceAudit` — verhindert dass die naechste Coda-Instanz die Pflege-Konvention vergisst.
- **Reihenfolge-Invariante-Test-Pattern** (`test_delete_before_upload`) — fuer jede Pure-Function die eine Plan-Liste erzeugt mit kritischer Reihenfolge.

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL-Telegram (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), Projekte-Backend (P194), Projekte-UI (P195), Projekt-Datei-Upload (P196), Persona-Merge-Layer (P197), Projekt-Templates (P198), Projekt-RAG-Index (P199), PWA-Verdrahtung Nala + Hel (P200), Nala-Tab "Projekte" + Header-Setter (P201), PWA-Auth-Hotfix (P202), Project-Workspace-Layout (P203a), Hel-UI-Hotfix Event-Delegation (P203b), Sandbox-Workspace-Mount + execute_in_workspace (P203c), Prosodie-Kontext im LLM (P204), Code-Detection + Sandbox-Roundtrip im Chat (P203d-1), Output-Synthese fuer Sandbox-Code-Execution (P203d-2), UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (P203d-3), RAG-Toast in Hel-UI (P205), HitL-Gate vor Code-Execution + Audit-Tabelle code_executions + HANDOVER-Teststand-Konvention (P206), Workspace-Snapshots + Diff-View + Rollback + workspace_snapshots-Tabelle + sandbox_writable/snapshots_enabled-Flags + POST /v1/workspace/rollback (P207), Spec-Contract / Ambiguitäts-Check + spec_check.py-Modul + clarifications-Tabelle + spec_check_enabled/threshold/timeout-Flags + GET /v1/spec/poll + POST /v1/spec/resolve + Klarstellungs-Karte mit Textarea + drei Decision-Werten (P208), Zweite Meinung vor Ausführung / Sancho Panza + code_veto.py-Modul + code_vetoes-Tabelle + code_veto_enabled/temperature-Flags + Wandschlag-Banner mit roter Border + read-only Veto-Card + drei Verdict-Werten pass/veto/skipped/error (P209), **Huginn-RAG-Auto-Sync + tools/sync_huginn_rag.py-Modul (Pure-Function build_sync_plan/validate_doc_header/extract_current_patch/parse_auth_string/load_auth_from_env/resolve_base_url + Async-Wrapper execute_sync_plan mit httpx + CLI mit dry-run + scripts/sync_huginn_rag.ps1-Wrapper + Stand-Anker-Pflicht-Header in huginn_kennt_zerberus.md + Doku-Pflicht-Tabellenzeile in WORKFLOW.md + Phase-5a-Ziel #18 + 53 Tests in test_p210_huginn_rag_sync.py — P210)**.

Helper aus P209/P208/P207/P206 die in P210 schon recycelt wurden: Pure-Function-vs-Async-Wrapper-Trennung (analog `should_run_veto` + `run_veto`), Mock-basiertes Test-Pattern (analog `TestRunVeto` mit Mock-LLM → `TestExecuteSyncPlan` mit MockClient), Source-Audit-Test-Pattern (analog `TestNalaSourceAudit` → `TestDocSourceAudit`+`TestWorkflowSourceAudit`), Bytes-genau-Truncate-Pattern (P209 `VETO_CODE_MAX_BYTES` → P210 `.env`-Parser-Quote-Stripping), Default-Konstanten-Smoke-Test-Pattern (analog P209 `TestSmoke::test_constants_match_protocol`).

Helper aus P205/P203d-3/P203d-2/P203d-1/P204: siehe vorherigen HANDOVER. `addMessage`-Wrapper-Return, `escapeHtml`, `renderCodeExecution`-Pattern, `.code-card`/`.output-card`-CSS, `code_execution`-Dict-Schema, Marker-Disjunktheit `[CODE-EXECUTION]` vs. `[PROJEKT-RAG]` / `[PROJEKT-KONTEXT]` / `[PROSODIE]` / `[KLARSTELLUNG]`, RAG-Toast-Pattern, JS-Integrity-Test.

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#90-93 (P210: Push, Sync-Tool Dry-Run + Live-Sync, Stand-Anker im Telegram, Auth-Fehler-Pfad)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 2176 passed (+53 P210 aus 2123 baseline)**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Zeile 1653 + 3096) — wie zuvor.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat einen `node --check`-Pass** seit P203d-3 — abgedeckt durch `TestJsSyntaxIntegrity`-Tests in P203d-3 + P206 + P207 + P208 + P209 (skipped wenn `node` nicht im PATH).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **P207 Storage-GC fuer alte Snapshot-Tars** — bei jedem writable-Run entstehen zwei neue `.tar`-Files. Es gibt aktuell keinen Cleanup-Job. Ein "behalte die letzten N Snapshots pro Projekt"-Sweep waere ein eigener Patch.
- **P207 Hardlink-Snapshots statt Tar** — Tar ist Tests-tauglich aber bei grossen Workspaces (>100 MB) wird das Kopieren teuer. Variante: pro Datei einen Hardlink in `_snapshots/<id>/<rel_path>`. Erst bei messbaren Disk-Problemen anfassen.
- **P207 Per-File-Rollback** — aktuell ist Snapshot atomare Einheit. User koennte "nur diese eine Datei zurueckdrehen" wuenschen. Geht via separaten `restore_single_file(snapshot_id, rel_path)`-Aufruf, nicht hochpriorisiert.
- **P207 Cross-Project-Diff** — bewusst weggelassen.
- **P207 Branch-Mechanik** — bewusst weggelassen. Linear forward/reverse only.
- **P207 Automatischer Rollback bei `exit_code != 0`** — bewusst nicht.
- **P207 Token-Cost-Tracking fuer Snapshot-Pfad** — Snapshots erzeugen keine LLM-Kosten.
- **P208 Token-Cost-Tracking fuer Spec-Probe-Call** — der Probe-LLM-Call (~50 Tokens, einmal pro ambiger Message) wird aktuell nicht in `interactions.cost` aufaggregiert. Schuld analog P203d-1/P203d-2.
- **P208 Multi-Turn-Klarstellung** — bewusst weggelassen. Eine Frage pro Turn.
- **P208 Edit-Vor-Run** — User kann Original-Prompt nicht editieren in der Card, nur antworten oder bypassen.
- **P208 Telegram-Pfad** — bewusst weggelassen.
- **P208 Persistierung der Pendings** — In-Memory only.
- **P208 Threshold-Tuning ueber `clarifications`-Tabelle** — manueller SQL-Query auf `SELECT status, COUNT(*) FROM clarifications GROUP BY status`.
- **P209 Token-Cost-Tracking fuer Veto-Call** — der Veto-LLM-Call (~30-100 Tokens, einmal pro nicht-trivialem Code-Block) wird aktuell nicht in `interactions.cost` aufaggregiert. Schuld analog P203d-1/P203d-2/P208.
- **P209 Mehr-Modell-Voting** — bewusst weggelassen. Genau ein Veto-Call pro Run.
- **P209 Lern-Loop ueber `code_vetoes`-History** — keine automatische Threshold-/Prompt-Adjustierung.
- **P209 Sprach-spezifische Heuristiken** — `should_run_veto`-Signatur akzeptiert `language` als Parameter, nutzt ihn aber nicht.
- **P209 Veto-Override durch User** — bewusst weggelassen.
- **P209 Persistenz der Verdicts ueber Server-Restart** — `VetoVerdict` ist transient.
- **P209 Telegram-Pfad** — bewusst weggelassen.
- **NEU P210: Spiegel-Kopie wird nicht mitsynct.** Sie ist Test-Material, kein Live-RAG-Inhalt. Wenn sich der RAG-Test-Set vom Live-RAG entkoppelt, kann das stillen Drift erzeugen. Der Stand-Anker-Block muss bei jedem Patch in beiden Files parallel mitgepflegt werden (Doku-Pflicht).
- **NEU P210: Auto-Trigger nach git commit fehlt.** Stattdessen explizit im Marathon-Push-Zyklus dokumentiert (vor `sync_repos.ps1`). Wenn Coda das vergisst, bleibt der Index alt — fail-soft, aber Lessons-Wert.
- **NEU P210: Multi-Datei-Sync nicht implementiert.** Wenn weitere Doku-Files in den RAG sollen (z.B. `lessons.md`, `PROJEKTDOKUMENTATION.md` Auszuege), muss der Plan-Builder erweitert werden — trivial: Liste statt einzelner Pfad.
- **NEU P210: Server-Status-Check vorab fehlt.** Wenn der Server down ist, schlägt der erste HTTP-Call eh fehl. Kein separater Health-Probe nötig.
- **NEU P210: Cron / Schedule-Integration fehlt.** Coda macht das am Session-Ende, nicht zeitgesteuert. Wenn der Server gerade nicht läuft, bleibt der Index alt — der nächste manuelle Sync-Aufruf fixt es.
- **NEU P210: Auto-Bumping der Patchnummer im Header fehlt.** Coda muss den Header bewusst aktualisieren (Doku-Pflicht). Sonst würde der Stand-Anker mechanisch jede gewünschte Nummer kriegen.
- **NEU P210: SUPERVISOR_ZERBERUS.md ist auf 928 Zeilen gewachsen** (Ziel <400). Nicht in P210 gekürzt — bleibt als eigene Schuld auf dem Stack. Saubere Variante: alte Patches (P150-P200) in `SUPERVISOR_ZERBERUS_archive.md` auslagern.
- **Pre-existing Files ohne Workspace** — wie zuvor.
- **Pre-existing Files ohne RAG-Index** — wie zuvor.
- **Lazy-Embedder-Latenz** — wie zuvor.
- **PWA-Cache-Bust nach Patches** — wie zuvor.
- **PWA-Icons nur Initial-Buchstabe** — wie zuvor.
- **PWA hat keinen Offline-Modus für die Hauptseite** — wie zuvor.
- **P201 Nala-Tab zeigt nur Auswählen, kein Anlegen** — wie zuvor.
- **Hardlink vs. Copy auf Windows** — wie zuvor.
- **P204 BERT-Header-Reuse aus P193** — wie zuvor.
- **P203c Mount-Spec auf Windows** — wie zuvor.
- **P203c keine Sync-After-Write-Verdrahtung** — wie zuvor (TEILWEISE GELOEST mit P207).
- **P203d-1: Token-Cost-Tracking für `code_execution`-Pfad** — wie zuvor.
- **P203d-1: `code_execution` ist nicht in der DB** — GELOEST mit P206.
- **P203d-2: Synthese-LLM nutzt aktuell den Default-Cloud-Modus** — wie zuvor.
- **P203d-2: Synthese kann den Code-Block aus dem `answer` nicht entfernen** — wie zuvor.
- **P203d-3: Keine Syntax-Highlighting-Library im Frontend** — wie zuvor.
- **P203d-3: Code-Card hat keinen Copy-to-Clipboard-Button** — wie zuvor.
- **P205: Sammel-Toast bei Multi-Upload bewusst nicht implementiert** — wie zuvor.
- **P206: HitL-Pendings nicht persistiert ueber Server-Restart** — wie zuvor (Absicht).
- **P206: Keine Edit-Vor-Run-Funktion in der Confirm-Card** — wie zuvor.
- **P206: Telegram hat keinen Chat-HitL-Pfad** — wie zuvor.
- **P206: HANDOVER-Teststand-Konvention** — wie zuvor.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt (P203d-1) | Output-Synthese im Chat-Endpunkt (P203d-2) | UI-Render im Nala-Frontend (P203d-3) | RAG-Toast in Hel-UI (P205) | HitL-Gate vor Sandbox-Code-Execution: `ChatHitlGate`-Singleton, `/v1/hitl/poll`+`/v1/hitl/resolve`, Confirm-Karte, `code_executions`-Audit-Tabelle (P206) | Workspace-Snapshots + Diff + Rollback: `projects_snapshots.py` mit Pure-Function-`build_workspace_manifest`+`diff_snapshots`+`_is_safe_member`-Tar-Defense, neue `workspace_snapshots`-Tabelle, `[SNAPSHOT-207]`-Logging-Tag (P207) | Spec-Contract / Ambiguitäts-Check vor Haupt-LLM-Call: `spec_check.py` mit Pure-Function-Heuristik + `ChatSpecGate`-Singleton + drei Decision-Werten answered/bypassed/cancelled + `clarifications`-Tabelle + `[SPEC-208]`-Logging-Tag (P208) | Zweite Meinung vor Ausführung / Sancho Panza vor HitL-Gate: `code_veto.py` mit Pure-Function-`should_run_veto` + `build_veto_messages` + `parse_veto_verdict` + Async-Wrapper `run_veto` mit `temperature=0.1` + fail-open + `code_vetoes`-Tabelle + `[VETO-209]`-Logging-Tag (P209) | **Huginn-RAG-Auto-Sync via `tools/sync_huginn_rag.py`-Modul mit Pure-Function `build_sync_plan` + Async-Wrapper `execute_sync_plan` (httpx) + CLI mit dry-run + Stand-Anker-Pflicht-Header in `docs/huginn_kennt_zerberus.md` + Idempotenz bei DELETE-404 + DELETE→UPLOAD-Reihenfolge-Invariante + `[SYNC-210]`-Logging-Tag + Marathon-Push-Zyklus eingebaut (vor `sync_repos.ps1`). Auth via `HUGINN_RAG_AUTH=User:Pass` Env-Var oder `.env`-Datei. Server-URL via `ZERBERUS_URL` (Default `http://localhost:5000`) (P210)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`) | Slug ist immutable nach Anlage | Atomic Write für jeden Upload-Pfad | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge (P197) | Pure-Function vs. DB-Schicht trennen | Templates als reguläre `project_files`-Einträge (P198) | Idempotenz-Check VOR Schreiben für jeden Generator (P198) | Best-Effort-Verdrahtung im Endpoint (P198) | Per-Projekt-RAG-Index isoliert (P199) | Embedder-Wrapper als monkeypatchbare Funktion (P199) | Best-Effort-Indexing (P199) | RAG-Block-Marker `[PROJEKT-RAG]` (P199) | PWA-Endpoints auth-frei in separatem Router VOR auth-gated Hel-Router (P200) | SW-Scope folgt dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch (P200) | `persona_overlay` NIEMALS in User-sichtbarer Response (P201) | Header-Injektion fuer Cross-Cutting zentral in `profileHeaders()` (P201) | Zombie-ID-Schutz nach List-Refresh (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test (P201+P203d-3+P206+P207+P208+P209) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig (P203a) | Hardlink primär, Copy als Fallback (P203a) | Atomic-Write-Pattern auch fuer Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat ueber benutzergenerierte Daten ist verboten — IMMER `data-*` + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehoert in jede Test-Suite die HTML in Python-Source baut (P203b+P203d-3+P205+P206+P207+P208+P209) | Prosodie-Bruecken-Block enthaelt NIEMALS numerische Werte (P204) | Mehrabian-Schwellen identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Bruecken-Bloecke MUESSEN substring-disjunkt sein (P204+P203d-2+P208 KLARSTELLUNG) | Sandbox-Workspace-Mount: Read-Only ist konservativer Default (P203c+P207) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte konservativ geblockt — `project_overlay is not None` als Diskriminator (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollstaendiges Sandbox-Result liegt vor (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open (P203d-1) | Two-stufige LLM-Pipelines: `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren (P203d-2) | Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (P203d-2+P209 should_run_veto) | Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding (P203d-2+P208 SPEC_ANSWER_MAX_BYTES+P209 VETO_CODE_MAX_BYTES/REASON_MAX_BYTES) | Synthese-LLM-Pipeline ist fail-open auf jeder Stufe (P203d-2) | Frontend-Renderer fuer additive Backend-Felder muss BACKWARDS-COMPAT bleiben (P203d-3+P205+P206+P207+P209 veto-Feld) | DOM-Insertion-Funktionen retournen Wrapper-Element (P203d-3) | Frontend-Renderer fuer User-/LLM-Strings: jedes innerHTML-Statement muss einen `escapeHtml(`-Aufruf enthalten — Audit-Test prueft das per Regex (P203d-3+P206+P207) | Frontend-Renderer fuer User-Strings DARF auch `textContent` statt `innerHTML` nutzen — Audit-Test akzeptiert beide (P208 .spec-card+P209 .veto-card) | Mobile-first 44x44px Touch-Target gilt fuer JEDEN neuen Toggle/Button im Frontend (P203d-3+P206+P207+P208+P209 .veto-code-toggle) | Status-Toasts: `textContent` statt `innerHTML` fuer Reason-Strings (P205) | Toast-Replace-Pattern: ein einziger DOM-Container plus CSS-State-Toggle (P205) | `pointer-events: none` im Hidden-State von fixed-positionierten Toasts/Overlays (P205) | HitL-Pending-IDs sind UUID4-hex (32 chars), Cross-Session-Defense via Session-Match im Resolve, niemals Integer-IDs (P206+P208) | HitL-Pendings im Chat-Pfad sind transient (In-Memory), nicht persistiert (P206+P208 ChatSpecGate) | Synthese-LLM-Calls (P203d-2) DUERFEN NICHT auf `skipped=True`-Payloads laufen (P206+P209 vetoed) | Audit-Trail-Schreibungen sind Best-Effort: jeder Crash darf den Hauptpfad nicht blockieren (P206+P207+P208 store_clarification_audit+P209 store_veto_audit) | HANDOVER-Header enthaelt die Zeile `**Manuelle Tests:** X / Y ✅` aus der WORKFLOW-Tabelle, ohne Reminder-Text oder Bewertung (P206-Konvention) | Sandbox-Writable-Mount: Default `projects.sandbox_writable=False` — RO-Konvention bleibt Standard, opt-in via Settings-Flag (P207) | Snapshot-Tars unter `<data>/projects/<slug>/_snapshots/<uuid4-hex>.tar`, atomar via Tempname + `os.replace` (P207) | Tar-Member-Validation `_is_safe_member` MUSS bei jedem Restore aufgerufen werden — kein Symlink/Hardlink, kein absoluter Pfad, kein `..`, Member-Resolve innerhalb dest_root (P207) | Cross-Project-Defense: `rollback_snapshot_async` MUSS `expected_project_id` gegen Snapshot-Eigentuemer pruefen, sonst Reject (P207) | JavaScript-String-Literale mit Newline (`\\n`) DUERFEN NICHT direkt aus Python-Source kommen — `String.fromCharCode(10)` als Workaround (P207) | Workspace-Snapshot-Pfad ist additiv und opt-in: ohne `sandbox_writable=True`+`snapshots_enabled=True` bleibt das `code_execution`-Feld P206-kompatibel (P207) | Spec-Contract-Pfad ist additiv und opt-in: ohne `spec_check_enabled=True` faehrt das Chat-Verhalten unveraendert wie pre-P208 (P208) | Spec-Probe-LLM-Call MUSS isoliert sein (Persona-frei, kein RAG, kein Prosody-Block) — Werkzeug, kein Gespraech (P208+P209 Veto-Probe analog) | Drei Decision-Werte answered/bypassed/cancelled — `answered` braucht non-empty `answer_text`, sonst False; `cancelled` early-return mit `model="spec-cancelled"` ohne Haupt-LLM-Call; `timeout` = bypass (defensiver UX) (P208) | Source-Detection ueber `X-Prosody-Context` + `X-Prosody-Consent: true` (P204-Konvention) — Voice-Bonus +0.20 im Score (P208) | `[KLARSTELLUNG]`-Marker substring-disjunkt zu allen anderen LLM-Markern (PROJEKT-RAG/PROJEKT-KONTEXT/PROSODIE/CODE-EXECUTION/AKTIVE-PERSONA) (P208) | `enrich_user_message`-Pfad: bei `answered` MUSS sowohl `last_user_msg` als auch `req.messages[-letzter user-msg].content` aktualisiert werden, sonst sieht der Haupt-LLM-Call die Original-Message (P208) | Veto-Pfad ist additiv und opt-in: ohne `code_veto_enabled=True` bleibt das Chat-Verhalten unveraendert wie pre-P209 (P209) | Veto-Probe-LLM-Call MUSS isoliert sein (Persona-frei, kein RAG, kein Prosody-Block) — Werkzeug, kein Gespraech (P209) | Bei VETO schreibt der Endpunkt KEINE Zeile in `code_executions` (P206-Tabelle), weil HitL nicht lief — Audit landet ausschliesslich in `code_vetoes`. Korrelation ueber `session_id`/`project_id` (P209) | Veto-Verdicts: `pass`/`veto`/`skipped`/`error` — `veto` triggert Wandschlag-Banner mit `skipped=True`+`hitl_status="vetoed"`; `pass` weiter zum HitL-Gate; `skipped` bei trivialem Code (kein LLM-Call); `error` fail-open weiter zum HitL-Gate (P209) | Wandschlag-Banner ist read-only Audit-Spur — KEIN Approve-Button (im Gegensatz zu HitL-Card aus P206), nur Code-Toggle. Source-Audit-Test prueft das per Regex (P209) | Veto-LLM-Call ist deterministisch via `temperature_override=0.1` (Default) — Veto soll wiederholbare Entscheidungen liefern, nicht zwischen PASS und VETO schwanken (P209) | Verdict-Parser MUSS robust gegen Markdown-Bold (`**VETO**`), Quotes (`"PASS"`), Doppelpunkte/Bindestriche, Lowercase, Multi-Line-Reasons sein, plus First-Line-Match + 64-char-Window-Fallback. Fail-open zu PASS bei unparseable Output (P209) | **`docs/huginn_kennt_zerberus.md` MUSS einen `## Aktueller Stand`-Block ganz oben tragen mit `**Letzter Patch:** P###`-Zeile — sonst wuerde Huginn bei Stand-Fragen wieder auf prominente Logging-Tags raten. Source-Audit-Test `TestDocSourceAudit::test_main_doc_patch_is_recent` prueft `>= 210` (P210)** | **Sync-Reihenfolge ist DELETE → UPLOAD invariant — wuerde man umkehren, wuerde DELETE die gerade hochgeladenen neuen Chunks soft-loeschen. Test `test_delete_before_upload` schuetzt explizit (P210)** | **Sync-Tool-DELETE muss 404 als Erfolg akzeptieren (Erst-Upload-Idempotenz). Sync-Tool-UPLOAD muss 200 fordern. Sync-Tool-Exceptions werden als `errors`-Liste eingesammelt statt zu propagieren — der Plan laeuft auch bei Einzel-Step-Crashes durch (P210)** | **Coda-Verantwortung: am Session-Ende `python -m tools.sync_huginn_rag` (oder `scripts/sync_huginn_rag.ps1`) aufrufen VOR `sync_repos.ps1` — Chris muss die Datei NIE manuell hochladen. In Doku-Pflicht-Tabelle dokumentiert (P210)** | **Spiegel-Kopie unter `docs/RAG Testdokumente/huginn_kennt_zerberus.md` ist NUR Test-Material, NICHT im Live-RAG. Stand-Anker-Block parallel mitpflegen (Doku-Pflicht), aber Sync-Skript synct sie NICHT (P210)**
