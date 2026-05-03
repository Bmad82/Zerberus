## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-03
**Letzter Patch:** P207 — Workspace-Snapshots, Diff-View, Rollback (Phase 5a Ziele #9 + #10 ABGESCHLOSSEN)
**Tests:** 1946 passed (+74 P207 aus 1872 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 1 skipped (existing)
**Manuelle Tests:** 1 / 80 ✅
**Commit:** TBD — wird nach Push nachgetragen
**Repos synchron:** TBD — wird nach `sync_repos.ps1` + `verify_sync.ps1` nachgetragen. `system_prompt_chris.json` bleibt working-tree dirty (Mutzenbacher-Persona-Experiment, vom User am 2026-05-01 als "gedroppt" markiert — Schulden-Liste, Stand identisch zu vorher).

---

## Zuletzt passiert (1 Patch in dieser Session)

**P207 — Workspace-Snapshots, Diff-View, Rollback (Phase 5a Ziele #9 + #10 ABGESCHLOSSEN).** Sicherheitsnetz NACH der Sandbox-Ausfuehrung. P206 gibt das Yay/Nay VOR der Ausfuehrung; P207 gibt die Sicht UND die Reverse-Option DANACH. Schliesst die letzten beiden offenen Phase-5a-Ziele rund um den Code-Execution-Pfad.

**Was P207 baut:**

1. **Neues Modul** [`zerberus/core/projects_snapshots.py`](zerberus/core/projects_snapshots.py) mit drei Schichten:
   - **Pure-Function** — `snapshot_dir_for(slug, base_dir)` → `<base>/projects/<slug>/_snapshots/`, `_looks_text` (Heuristik: kein Null-Byte in den ersten 8 KB → text), `build_workspace_manifest(workspace_root, include_content=True)` baut `{rel_path: {hash, size, binary, content?}}`-Dict (Content nur fuer Text-Files <64 KB), `diff_snapshots(before, after) -> List[DiffEntry]` vergleicht zwei Manifeste (added/modified/deleted, sortiert nach Pfad, optional `unified_diff` via `difflib.unified_diff` mit `a/path`/`b/path`-Headers), `_is_safe_member` Tar-Member-Validation (kein Symlink/Hardlink, kein absoluter Pfad, kein `..`-Traversal, Member-Resolve muss innerhalb dest_root liegen).
   - **Sync-FS** — `materialize_snapshot(workspace_root, snapshot_root, *, label, snapshot_id=None)` schreibt ein Tar (`ustar`-Format) atomar via Tempname + `os.replace`, liefert `{id, label, archive_path, file_count, total_bytes, manifest}`. `restore_snapshot(workspace_root, archive_path)` raeumt Workspace-Inhalt komplett (Root bleibt stehen — keine Watcher-Konfusion), validiert Tar-Members und extrahiert nur sichere Files. Liefert `{file_count, total_bytes}` oder None.
   - **Async DB** — `store_snapshot_row(...)` schreibt in `workspace_snapshots`-Tabelle (Best-Effort, jeder Fehler verschluckt + geloggt). `load_snapshot_row(snapshot_id)` liefert Metadaten als Dict. High-Level: `snapshot_workspace_async(project_id, base_dir, *, label, pending_id=None, parent_snapshot_id=None)` zieht Slug aus DB, materialisiert Tar, schreibt DB-Row. `rollback_snapshot_async(snapshot_id, base_dir, *, expected_project_id=None)` macht Project-Owner-Check (Cross-Project-Defense) und ruft `restore_snapshot`.

2. **Neue DB-Tabelle** `workspace_snapshots` in [`zerberus/core/database.py`](zerberus/core/database.py): Spalten `id` (PK), `snapshot_id` (UUID4-hex, UNIQUE+INDEX), `project_id` (INDEX), `project_slug`, `label` (`before_run`/`after_run`/`manual`), `archive_path`, `file_count`, `total_bytes`, `pending_id` (Korrelation zu `hitl_chat`/`code_executions` aus P206), `parent_snapshot_id` (zeigt vom `after`-Snapshot auf den `before`-Snapshot derselben Ausfuehrung), `created_at`. Bewusst KEINE Foreign-Keys — Models bleiben dependency-frei wie der Rest.

3. **Verdrahtung in** [`zerberus/app/routers/legacy.py`](zerberus/app/routers/legacy.py)`::chat_completions` zwischen P206-Approve und `execute_in_workspace`:
   - `_writable = bool(getattr(settings.projects, "sandbox_writable", False))` — Default False, RO bleibt P203c-Standard
   - `_snapshots_active = _writable and bool(getattr(settings.projects, "snapshots_enabled", True))`
   - Bei `_snapshots_active`: `_before_snap = await snapshot_workspace_async(..., label="before_run", pending_id=hitl_pending_id)`
   - `execute_in_workspace(..., writable=_writable)` — der hardcoded `writable=False` aus P203d-1 wurde durch den Settings-Lookup ersetzt
   - Nach erfolgreichem Run: `_after_snap = await snapshot_workspace_async(..., label="after_run", parent_snapshot_id=_before_snap["id"])`, dann `diff = diff_snapshots(_before_snap["manifest"], _after_snap["manifest"])`. Drei additive Felder ins `code_execution_payload`: `diff` (Liste von `DiffEntry.to_public_dict()`), `before_snapshot_id`, `after_snapshot_id`.
   - Fail-Open auf jeder Stufe — wenn `before_snap` None liefert, kein after-Snapshot, kein Diff (Hauptpfad bleibt gruen). HitL-Skip (rejected/timeout) → kein Snapshot-Pfad (es gab keinen Run).

4. **Neuer Endpoint** `POST /v1/workspace/rollback` in `legacy.py` (auth-frei wie `/v1/hitl/*`, Dictate-Lane-Invariante): Body `WorkspaceRollbackRequest{snapshot_id, project_id}`, Response `WorkspaceRollbackResponse{ok, snapshot_id?, project_id?, project_slug?, file_count?, total_bytes?, error?}`. Reject-Pfade: `snapshots_disabled` (Master-Switch off → 200 mit `ok=False`), `restore_failed` (unbekannte snapshot_id ODER project_mismatch — beides liefert None aus `rollback_snapshot_async`), `pipeline_error` (uncaughter Crash). Defense-in-Depth: `expected_project_id` muss zum Snapshot-Eigentuemer passen — ein Snapshot aus Projekt A kann nicht ueber Projekt B angewendet werden.

5. **Nala-Frontend** in [`zerberus/app/routers/nala.py`](zerberus/app/routers/nala.py):
   - **CSS-Block** `.diff-card`/`.diff-card-header`/`.diff-summary`/`.diff-list`/`.diff-entry`/`.diff-entry-head`/`.diff-status.diff-{added,modified,deleted}`/`.diff-path`/`.diff-size`/`.diff-entry-body`/`.diff-content`/`.diff-content .diff-line-{add,del,meta}`/`.diff-binary-note`/`.diff-actions`/`.diff-rollback`/`.diff-resolved`/`.diff-card.diff-{rolled-back,rollback-failed}` (~125 Zeilen). Kintsugi-Gold-Border, `.diff-rollback` mit `min-height: 44px`/`min-width: 44px` (Mobile-first), Inline-Diff `.diff-content` mit `overflow-x: auto` + `max-height: 240px`.
   - **JS-Funktionen** (~250 Zeilen direkt nach `renderCodeExecution`):
     - `renderDiffCard(wrapperEl, codeExec, triptych)` — liest `codeExec.diff` (Array), `codeExec.before_snapshot_id`, `codeExec.after_snapshot_id`. Baut Header mit `📋 Workspace-Aenderungen` plus Summary `N neu, M geaendert, K geloescht`. Fuer jeden DiffEntry eine `<li class="diff-entry diff-collapsed">` mit Status-Badge, Pfad (XSS-escaped via `escapeHtml`), Size-Label (`+5B` / `-7B` / `5B → 11B`); Klick auf Entry-Head toggled `.diff-collapsed`. Body: bei `binary=true` Hinweis "Binaerdatei — kein Inline-Diff", bei vorhandenem `unified_diff` ein `<pre class="diff-content">` mit `colorizeUnifiedDiff(unified)`, sonst ein Hinweis je nach Status (added/deleted/no inline diff).
     - `colorizeUnifiedDiff(text)` — splittet auf `String.fromCharCode(10)` (Lesson aus P203b: Newline-Literale in Python-Source werden frueh interpretiert), normalisiert CR via Split/Join, escapeHtml pro Zeile, wickelt Plus/Minus/Header in `<span class="diff-line-{add,del,meta}">`. Joined wieder mit `String.fromCharCode(10)`.
     - `rollbackWorkspace(cardEl, snapshotId, projectId)` — sperrt den Button sofort, `POST /v1/workspace/rollback` mit `{snapshot_id, project_id}`, setzt Card-State auf `diff-rolled-back` (gruen, "✅ Workspace zurueckgesetzt") oder `diff-rollback-failed` (rot, mit error-Reason). Karte bleibt sichtbar als Audit-Spur.
   - **`renderCodeExecution`-Erweiterung:** nach Code-Card + Output-Card-Insert, falls `!skipped && Array.isArray(codeExec.diff) && codeExec.before_snapshot_id` → `renderDiffCard(...)` aufrufen. Backwards-compat zu P206-only-Backends (kein `before_snapshot_id` → kein Diff-Render). Bei HitL-Skip (`skipped=true`) wird der Diff-Pfad explizit uebersprungen — es gab gar keinen Run.

6. **Neue Feature-Flags** in [`zerberus/core/config.py`](zerberus/core/config.py)`::ProjectsConfig`:
   - `sandbox_writable: bool = False` — Default RO bleibt P203c-Konvention; nur wenn explizit auf True gesetzt, mountet die Sandbox writable.
   - `snapshots_enabled: bool = True` — Master-Switch fuer den Snapshot-Pfad. Bei False laeuft die Sandbox writable, aber kein before/after-Snapshot, kein Diff in der Response.

7. **Tar-Sicherheits-Spec:** Wir nutzen `tarfile` ohne den 3.12+ `data_filter`, weil 3.10/3.11 unterstuetzt werden sollen. `_is_safe_member` prueft pro Member: kein dev/symlink/hardlink, kein absoluter Pfad, kein `..` in Path-Parts, resolved-Path muss innerhalb des dest_root liegen. Boese Members werden geskippt + geloggt (`[SNAPSHOT-207] restore: unsafe member skipped: ...`), legitime werden extrahiert.

8. **Konvention-Update:** der hardcoded `writable=False` aus P203d-1 wurde durch `getattr(settings.projects, "sandbox_writable", False)` ersetzt. Der zugehoerige P203d-1-Source-Audit-Test wurde entsprechend nachgezogen — er pruefe jetzt den Settings-Lookup plus den Pydantic-Default `False`. Default-Verhalten unveraendert.

**Tests:** 74 in [`test_p207_workspace_snapshots.py`](zerberus/tests/test_p207_workspace_snapshots.py).

`TestSnapshotDirFor` (2) — Pfad-Layout + Pure-Function-Garantie (kein FS-Zugriff).

`TestLooksText` (4) — Empty/Pure-Text/Null-Byte-Binary/UTF-8-Umlauts.

`TestBuildWorkspaceManifest` (5) — Empty-Workspace, basic, binary-no-content, include_content=False, large-text-skipped-for-content.

`TestDiffSnapshots` (8) — Identical, added, deleted, modified mit unified_diff, modified-binary-no-diff, modified-text-without-content, sorted-by-path, to_public_dict-keys.

`TestUnifiedDiff` (1) — Format mit `a/path`/`b/path`-Headers + Plus/Minus-Zeilen.

`TestIsSafeMember` (5) — Normal-File OK, absolute-path-blocked, dotdot-blocked, symlink-blocked, hardlink-blocked.

`TestMaterializeSnapshot` (4) — None-fuer-fehlenden-Workspace, Tar-mit-Files, explicit snapshot_id, Manifest-im-Result.

`TestRestoreSnapshot` (3) — None-bei-fehlendem-Archive, Restore-recreates-Files (incl. Cleanup von Files die nicht im Snapshot waren), Path-Traversal-Member-skipped.

`TestStoreLoadSnapshotRow` (3) — Roundtrip mit allen Spalten, Load-unknown returns None, Silent-Skip ohne DB.

`TestSnapshotWorkspaceAsync` (2) — Happy-Path mit DB-Row, None-fuer-missing-project.

`TestRollbackSnapshotAsync` (3) — Happy-Path, Project-Mismatch-Reject, Unknown-Snapshot-Reject.

`TestWorkspaceRollbackEndpoint` (4) — OK-Pfad, unknown_snapshot → restore_failed, project_mismatch → restore_failed, snapshots_disabled-Block.

`TestLegacySourceAudit` (9) — Logging-Tag, Imports, writable-from-settings, snapshots_enabled-flag, writable-passed-to-execute, diff/before/after im Payload, Endpoint registriert, Pydantic-Models exportiert.

`TestNalaSourceAudit` (10) — JS-Funktionen `renderDiffCard`/`colorizeUnifiedDiff`/`rollbackWorkspace` definiert, Rollback-POST zum richtigen Endpoint, renderCodeExecution-Trigger-Gate auf `!skipped && before_snapshot_id`, CSS-Klassen vorhanden, 44x44 Touch-Target, escapeHtml fuer Pfad und Diff-Content, Rollback-Button-disabled-Logik, Diff-Render-Skip bei `skipped=true`.

`TestE2EWritableSandboxAndDiff` (4) — writable=True triggert Snapshots+Diff (Mock-Sandbox mutiert Workspace), writable=False keine Snapshots, snapshots_enabled=False keine Diff-Felder, HitL-rejected kein Snapshot.

`TestJsSyntaxIntegrity` (1, skipped wenn node fehlt) — `node --check` ueber alle inline `<script>`-Bloecke aus NALA_HTML. Lesson aus P203b/P203d-3/P206 wieder relevant geworden — beim ersten Wurf hatte `colorizeUnifiedDiff` echte Newline-Chars im Source, weil Python die `\n`-Escapes interpretiert. Fix: `String.fromCharCode(10)` statt `'\n'`-Literal.

`TestSmoke` (4) — Config-Flags vorhanden + bool, DB-Tabelle hat alle Spalten, /nala/-Endpoint enthaelt Diff-Renderer + CSS + Endpoint, legacy.py exportiert Rollback-Models.

**Kollateral-Fix:** [`test_p203d_chat_sandbox.py`](zerberus/tests/test_p203d_chat_sandbox.py)`::TestP203d1SourceAudit::test_writable_false_default_in_call_site` wurde nachgezogen — die Konvention "writable=False hardcoded" gilt seit P207 nicht mehr; der Test prueft jetzt den Settings-Lookup `getattr(settings.projects, "sandbox_writable", False)` plus den Pydantic-Default. Backward-compat fuer den alten Verhaltenswunsch (RO im Default-Pfad) ist garantiert ueber den Default-Wert.

**Logging-Tag:** `[SNAPSHOT-207]` mit `materialized id=... label=... file_count=... total_bytes=... archive=...` / `db_row_written id=... snapshot_id=... label=... project_id=...` / `diff project_id=... before=... after=... changes=...` / `restored archive=... file_count=... total_bytes=...` / `rollback_done snapshot_id=... project_id=... slug=... file_count=...` / `rollback_endpoint snapshot_id=... project_id=... slug=... file_count=...` / `restore: unsafe member skipped: ...` / `before_run fehlgeschlagen (fail-open): ...` / `after_run/diff fehlgeschlagen (fail-open): ...` / `rollback_endpoint Pipeline-Fehler: ...`.

## Nächster Schritt — sofort starten

**P208: Spec-Contract / Ambiguitaets-Check (Phase 5a Ziel #8).** Vor dem ersten LLM-Call eine Domain-Probe ("Was genau soll der Code tun? In welcher Sprache? Welche Inputs?"), bei Whisper-Input mit hoher Ambiguitaet (kurzer Satz, mehrere Interpretationen) eine Rueckfrage statt direkt Code generieren. Schmaler Scope, kein Workspace-Touch, keine neuen Snapshots.

**Konkret (Coda darf abweichen):**

1. **Pure-Function-Schicht** in neuem Modul `zerberus/core/spec_check.py`: `compute_ambiguity_score(user_message, *, source="text"|"voice") -> float` (0.0-1.0), Heuristiken: Satzlaenge, Pronomen-Dichte ohne Antezedens, fehlende Sprachangabe, fehlende Input-/Output-Spec, generische Verben ("mach", "tu", "schreib") ohne Substantiv-Anker. Voice-Input bekommt Score-Bonus +0.2 (Whisper-Ambiguitaet).
2. **Trigger-Gate**: `should_ask_clarification(score, *, threshold=0.65) -> bool`. Threshold per `projects.spec_check_threshold` konfigurierbar.
3. **LLM-Schicht**: Wenn ambig → kurzer Spec-Probe-Call (eine Frage, kein Code). Antwort wird als `clarification`-Field in der Chat-Response zurueckgegeben (additiv), Frontend rendert eine "Verstehe ich das richtig?"-Karte, User antwortet, das wird als Erweiterung des urspruenglichen Prompts an den eigentlichen Code-Call gehaengt.
4. **UI**: Clarification-Card analog HitL-Card (P206) — aber statt ✅/❌ ein Text-Input fuer die Antwort, plus "Trotzdem versuchen"-Button als Override.
5. **Tests**: Score-Heuristik-Unit-Tests, Trigger-Gate-Boundary, LLM-Mock fuer den Probe-Call, UI-Source-Audit, JS-Integrity.
6. **Was P208 NICHT macht**: keine Sprachen-Erkennung im Code-Block (das ist P203d-1), kein Refactoring der existierenden HitL-Card (Clarification ist eine separate Karte die VOR HitL kommt).

Alternativ falls P208 zu viel: **P209 Zweite Meinung vor Ausfuehrung (Ziel #7)** — Veto-Logik: ein zweites Modell (z.B. Mistral-Small als Guard) bewertet den vom ersten Modell generierten Code auf "macht das wirklich was der User will + ist es sicher". Bei Veto landet der Code nicht im HitL-Gate sondern in einem "Wandschlag-Banner" mit Veto-Begruendung.

**Reihenfolge-Vorschlag** (Coda darf abweichen):
- **P208** Spec-Contract / Ambiguitäts-Check — Ziel #8
- **P209** Zweite Meinung vor Ausführung — Ziel #7
- **P210** GPU-Queue für VRAM-Konsumenten — Ziel #11
- **P211** Secrets bleiben geheim — Ziel #12

Helper aus P207 die in P208/P209 direkt nutzbar sind:
- **`workspace_snapshots`-Tabelle plus `pending_id`/`parent_snapshot_id`-Korrelationsschluessel** sind universell genug, dass P208/P209 sie fuer Audit-Spuren wiederverwenden koennen, falls eine Spec-Probe oder ein Veto-Pfad ein "vor diesem Run hat das Modell so gedacht"-Snapshot braeuchte.
- **`.diff-card`/`.diff-list`/`.diff-entry`-CSS** klont sich trivial zu `.spec-card`/`.veto-card` (gleiches Vokabular: Border, collapsible Body, 44x44 Touch-Target, Post-Klick-State-Erhalt als Audit-Spur).
- **`renderDiffCard`/`rollbackWorkspace`-Pattern** (Render-Funktion + Async-Resolve-Funktion mit Card-State-Update) ist die Vorlage fuer jede weitere User-Interaktions-Karte im Chat-Flow.
- **`String.fromCharCode(10)`-Trick** fuer Newline-Splits in JS, der aus Python-Source generiert wird — Lesson dokumentiert in lessons.md, vermeidet ein Wiederaufflammen von P203b-Bugs.
- **`_is_safe_member`-Tar-Defense** ist universell genug, dass jeder weitere Restore-/Extract-Pfad (z.B. Backup-Restore, Template-Import) sie nutzen kann.

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL-Telegram (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), Projekte-Backend (P194), Projekte-UI (P195), Projekt-Datei-Upload (P196), Persona-Merge-Layer (P197), Projekt-Templates (P198), Projekt-RAG-Index (P199), PWA-Verdrahtung Nala + Hel (P200), Nala-Tab "Projekte" + Header-Setter (P201), PWA-Auth-Hotfix (P202), Project-Workspace-Layout (P203a), Hel-UI-Hotfix Event-Delegation (P203b), Sandbox-Workspace-Mount + execute_in_workspace (P203c), Prosodie-Kontext im LLM (P204), Code-Detection + Sandbox-Roundtrip im Chat (P203d-1), Output-Synthese fuer Sandbox-Code-Execution (P203d-2), UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (P203d-3), RAG-Toast in Hel-UI (P205), HitL-Gate vor Code-Execution + Audit-Tabelle code_executions + HANDOVER-Teststand-Konvention (P206), **Workspace-Snapshots + Diff-View + Rollback + workspace_snapshots-Tabelle + sandbox_writable/snapshots_enabled-Flags + POST /v1/workspace/rollback (P207)**.

Helper aus P207 die in P208/P209 direkt nutzbar sind: siehe oben.

Helper aus P206 die in P207 schon recycelt wurden: `pending_id`-Korrelation in der neuen `workspace_snapshots`-Tabelle, `[HITL-206]`-Long-Poll-Pattern als Vorlage fuer kuenftige User-Confirm-Pfade, `.hitl-card`-CSS-Vokabular fuer `.diff-card`-Border + Action-Footer + Post-Klick-State.

Helper aus P205/P203d-3/P203d-2/P203d-1/P204: siehe vorherigen HANDOVER. `addMessage`-Wrapper-Return, `escapeHtml`, `renderCodeExecution`-Pattern, `.code-card`/`.output-card`-CSS, `code_execution`-Dict-Schema, Marker-Disjunktheit `[CODE-EXECUTION]` vs. `[PROJEKT-RAG]` / `[PROJEKT-KONTEXT]` / `[PROSODIE]`, RAG-Toast-Pattern, JS-Integrity-Test.

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#76-80 (P207: Push, Default-RO, Writable+Diff+Rollback live, Tar-Path-Traversal+Cross-Project-Reject, Snapshots-Master-Switch off)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 1946 passed (+74 P207 aus 1872 baseline)**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Zeile 1653 + 3096) — wie zuvor.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat einen `node --check`-Pass** seit P203d-3 — abgedeckt durch `TestJsSyntaxIntegrity`-Tests in P203d-3 + P206 + P207 (skipped wenn `node` nicht im PATH).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **NEU P207: Storage-GC fuer alte Snapshot-Tars** — bei jedem writable-Run entstehen zwei neue `.tar`-Files unter `data/projects/<slug>/_snapshots/`. Es gibt aktuell keinen Cleanup-Job. Ein "behalte die letzten N Snapshots pro Projekt"-Sweep waere ein eigener Patch (analog dem Storage-GC-Job aus den allgemeinen Schulden). Bis dahin manuelles `rm` per Hand wenn Disk knapp.
- **NEU P207: Hardlink-Snapshots statt Tar** — Tar ist Tests-tauglich und bietet eine atomare Restore-Einheit, aber bei grossen Workspaces (>100 MB) wird das Kopieren teuer. Variante: pro Datei einen Hardlink in `_snapshots/<id>/<rel_path>` legen (Same-Inode wie Workspace + SHA-Storage). Wuerde Disk-Kosten auf ~0 bringen, kostet aber komplexere Restore-Logik (atomar Workspace flippen). Erst bei messbaren Disk-Problemen anfassen.
- **NEU P207: Per-File-Rollback** — aktuell ist Snapshot eine atomare Einheit (alles oder nichts). Ein User koennte wuenschen "nur diese eine Datei zurueckdrehen". Geht via separaten `restore_single_file(snapshot_id, rel_path)`-Aufruf, nicht hochpriorisiert (User kann "rollback all", manuell editieren, neuer Run).
- **NEU P207: Cross-Project-Diff** — bewusst weggelassen. Snapshots sind per Projekt isoliert; ein Diff zwischen zwei Projekten ergibt selten Sinn (verschiedene Slug-Kontexte).
- **NEU P207: Branch-Mechanik** — bewusst weggelassen. Linear forward/reverse only (analog Git mit `reset --hard`, kein `branch`/`merge`). Bei Bedarf koennte `parent_snapshot_id` zu einem Tree-Konzept ausgebaut werden, aktuell wird er nur fuer die `before→after`-Korrelation genutzt.
- **NEU P207: Automatischer Rollback bei exit_code != 0** — bewusst nicht. User-Choice; der Diff zeigt was passiert ist, der User entscheidet. Manche Crashes hinterlassen wertvolle Teil-Outputs (z.B. Logfiles), die nicht automatisch weggeworfen werden sollen.
- **NEU P207: Token-Cost-Tracking fuer Snapshot-Pfad** — Snapshots erzeugen keine LLM-Kosten (nur Disk + DB-Insert), insofern entfaellt der entsprechende Tracker. Aber wenn P208 (Spec-Contract) Probe-Calls macht, brauchts dort einen separaten Cost-Aggregator.
- **Pre-existing Files ohne Workspace** — wie zuvor.
- **Pre-existing Files ohne RAG-Index** — wie zuvor.
- **Lazy-Embedder-Latenz** — wie zuvor.
- **PWA-Cache-Bust nach Patches** — wie zuvor: nach dem ersten Open der PWA muss der Service-Worker den Cache invalidieren — Cache-Name-Bump auf `nala-shell-v3` waere sauber. Bei P207 nicht zwingend (network-first faengt das auf), aber empfohlen weil die JS-Surface noch weiter gewachsen ist.
- **PWA-Icons nur Initial-Buchstabe** — wie zuvor.
- **PWA hat keinen Offline-Modus für die Hauptseite** — wie zuvor.
- **P201 Nala-Tab zeigt nur Auswählen, kein Anlegen** — wie zuvor.
- **Hardlink vs. Copy auf Windows** — wie zuvor.
- **P204 BERT-Header-Reuse aus P193** — wie zuvor.
- **P203c Mount-Spec auf Windows** — wie zuvor.
- **P203c keine Sync-After-Write-Verdrahtung** — **TEILWEISE GELOEST mit P207**: writable-Mount macht jetzt before/after-Snapshots, der User sieht den Diff und kann rollbacken. Echte "Sync zurueck in den SHA-Storage" macht P207 nicht — die geaenderten Files leben nur im Workspace, nicht im SHA-Storage. Bei Bedarf koennte der after-Run zusaetzlich `register_file` fuer geaenderte Dateien aufrufen, das bleibt Schuld.
- **P203d-1: Token-Cost-Tracking für `code_execution`-Pfad** — wie zuvor.
- **P203d-1: `code_execution` ist nicht in der DB** — GELOEST mit P206.
- **P203d-2: Synthese-LLM nutzt aktuell den Default-Cloud-Modus** — wie zuvor.
- **P203d-2: Synthese kann den Code-Block aus dem `answer` nicht entfernen** — wie zuvor.
- **P203d-3: Keine Syntax-Highlighting-Library im Frontend** — wie zuvor. **NEU P207:** Inline-Diff hat eigene Plus/Minus/Header-Faerbung via `colorizeUnifiedDiff` (gruen/rot/grau) — keine syntax-highlighting fuer den Code selbst, aber visuelle Diff-Trennung.
- **P203d-3: Code-Card hat keinen Copy-to-Clipboard-Button** — wie zuvor.
- **P205: Sammel-Toast bei Multi-Upload bewusst nicht implementiert** — wie zuvor.
- **P206: HitL-Pendings nicht persistiert ueber Server-Restart** — wie zuvor (Absicht).
- **P206: Keine Edit-Vor-Run-Funktion in der Confirm-Card** — wie zuvor.
- **P206: Telegram hat keinen Chat-HitL-Pfad** — wie zuvor.
- **P206: HANDOVER-Teststand-Konvention** — wie zuvor.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt (P203d-1) | Output-Synthese im Chat-Endpunkt (P203d-2) | UI-Render im Nala-Frontend (P203d-3) | RAG-Toast in Hel-UI (P205) | HitL-Gate vor Sandbox-Code-Execution: `ChatHitlGate`-Singleton, `/v1/hitl/poll`+`/v1/hitl/resolve`, Confirm-Karte, `code_executions`-Audit-Tabelle (P206) | **Workspace-Snapshots + Diff + Rollback: `projects_snapshots.py` mit Pure-Function-`build_workspace_manifest`+`diff_snapshots`+`_is_safe_member`-Tar-Defense, Sync-FS-`materialize_snapshot`+`restore_snapshot` mit ustar-Tars unter `data/projects/<slug>/_snapshots/<id>.tar`, neue `workspace_snapshots`-Tabelle mit `pending_id`/`parent_snapshot_id`-Korrelation zu P206. Chat-Endpunkt schiesst before/after-Snapshots wenn `projects.sandbox_writable=True` UND `projects.snapshots_enabled=True` UND HitL approved, Diff-Liste plus zwei Snapshot-IDs im additiven `code_execution.diff`/`before_snapshot_id`/`after_snapshot_id`-Feld. Frontend rendert `.diff-card` unter Output-Card mit Status-Badges (added/modified/deleted), collapsible Inline-Diff (gruen/rot/grau via `colorizeUnifiedDiff`+`String.fromCharCode(10)`), `↩️ Aenderungen zurueckdrehen`-Button (44x44 Touch) ruft `POST /v1/workspace/rollback` mit `{snapshot_id, project_id}` → Project-Owner-Check (Cross-Project-Defense) → `restore_snapshot` extrahiert Tar mit Member-Validation. Defaults `sandbox_writable=False`+`snapshots_enabled=True` lassen P206-Verhalten unveraendert. `[SNAPSHOT-207]`-Logging-Tag (P207)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`) | Slug ist immutable nach Anlage | Atomic Write für jeden Upload-Pfad | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge (P197) | Pure-Function vs. DB-Schicht trennen | Templates als reguläre `project_files`-Einträge (P198) | Idempotenz-Check VOR Schreiben für jeden Generator (P198) | Best-Effort-Verdrahtung im Endpoint (P198) | Per-Projekt-RAG-Index isoliert (P199) | Embedder-Wrapper als monkeypatchbare Funktion (P199) | Best-Effort-Indexing (P199) | RAG-Block-Marker `[PROJEKT-RAG]` (P199) | PWA-Endpoints auth-frei in separatem Router VOR auth-gated Hel-Router (P200) | SW-Scope folgt dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch (P200) | `persona_overlay` NIEMALS in User-sichtbarer Response (P201) | Header-Injektion fuer Cross-Cutting zentral in `profileHeaders()` (P201) | Zombie-ID-Schutz nach List-Refresh (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test (P201+P203d-3+P206+P207) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig (P203a) | Hardlink primär, Copy als Fallback (P203a) | Atomic-Write-Pattern auch fuer Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat ueber benutzergenerierte Daten ist verboten — IMMER `data-*` + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehoert in jede Test-Suite die HTML in Python-Source baut (P203b+P203d-3+P205+P206+P207) | Prosodie-Bruecken-Block enthaelt NIEMALS numerische Werte (P204) | Mehrabian-Schwellen identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Bruecken-Bloecke MUESSEN substring-disjunkt sein (P204+P203d-2) | Sandbox-Workspace-Mount: Read-Only ist konservativer Default (P203c+P207) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte konservativ geblockt — `project_overlay is not None` als Diskriminator (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollstaendiges Sandbox-Result liegt vor (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open (P203d-1) | Two-stufige LLM-Pipelines: `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren (P203d-2) | Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (P203d-2) | Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding (P203d-2) | Synthese-LLM-Pipeline ist fail-open auf jeder Stufe (P203d-2) | Frontend-Renderer fuer additive Backend-Felder muss BACKWARDS-COMPAT bleiben (P203d-3+P205+P206+P207) | DOM-Insertion-Funktionen retournen Wrapper-Element (P203d-3) | Frontend-Renderer fuer User-/LLM-Strings: jedes innerHTML-Statement muss einen `escapeHtml(`-Aufruf enthalten — Audit-Test prueft das per Regex (P203d-3+P206+P207) | Mobile-first 44x44px Touch-Target gilt fuer JEDEN neuen Toggle/Button im Frontend (P203d-3+P206+P207) | Status-Toasts: `textContent` statt `innerHTML` fuer Reason-Strings (P205) | Toast-Replace-Pattern: ein einziger DOM-Container plus CSS-State-Toggle (P205) | `pointer-events: none` im Hidden-State von fixed-positionierten Toasts/Overlays (P205) | HitL-Pending-IDs sind UUID4-hex (32 chars), Cross-Session-Defense via Session-Match im Resolve, niemals Integer-IDs (P206) | HitL-Pendings im Chat-Pfad sind transient (In-Memory), nicht persistiert (P206) | Synthese-LLM-Calls (P203d-2) DUERFEN NICHT auf `skipped=True`-Payloads laufen (P206) | Audit-Trail-Schreibungen sind Best-Effort: jeder Crash darf den Hauptpfad nicht blockieren (P206+P207) | HANDOVER-Header enthaelt die Zeile `**Manuelle Tests:** X / Y ✅` aus der WORKFLOW-Tabelle, ohne Reminder-Text oder Bewertung (P206-Konvention) | **Sandbox-Writable-Mount: Default `projects.sandbox_writable=False` — RO-Konvention bleibt Standard, opt-in via Settings-Flag (P207)** | **Snapshot-Tars unter `<data>/projects/<slug>/_snapshots/<uuid4-hex>.tar`, atomar via Tempname + `os.replace` (P207)** | **Tar-Member-Validation `_is_safe_member` MUSS bei jedem Restore aufgerufen werden — kein Symlink/Hardlink, kein absoluter Pfad, kein `..`, Member-Resolve innerhalb dest_root (P207)** | **Cross-Project-Defense: `rollback_snapshot_async` MUSS `expected_project_id` gegen Snapshot-Eigentuemer pruefen, sonst Reject (P207)** | **JavaScript-String-Literale mit Newline (`\\n`) DUERFEN NICHT direkt aus Python-Source kommen — Python interpretiert die Escape-Sequenz frueh und der JS-Quelltext landet mit echtem Newline drin, was `node --check` killt. `String.fromCharCode(10)` als Workaround. Lesson aus P203b wieder relevant geworden (P207)** | **Workspace-Snapshot-Pfad ist additiv und opt-in: ohne `sandbox_writable=True`+`snapshots_enabled=True` bleibt das `code_execution`-Feld P206-kompatibel (kein `diff`/`before_snapshot_id`/`after_snapshot_id`) (P207)**
