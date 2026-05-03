## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-03
**Letzter Patch:** P206 — HitL-Gate vor Code-Execution + HANDOVER-Teststand-Konvention (Phase 5a Ziel #6 ABGESCHLOSSEN)
**Tests:** 1872 passed (+55 P206 aus 1817 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 1 skipped (existing)
**Manuelle Tests:** 1 / 75 ✅
**Commit:** 3ddcee4 — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude — alle drei gepusht (Ratatoskr `97f7438`, Claude `537bd1f`, 0 unpushed Commits in allen drei). `verify_sync.ps1` meldet Zerberus working-tree dirty: `system_prompt_chris.json` bleibt unangetastet (Mutzenbacher-Persona-Experiment, vom User am 2026-05-01 als "gedroppt" markiert — Schulden-Liste, Stand identisch zum Vorgaenger-HANDOVER nach P205). Naechster Coda kann sie ggf. via `git checkout system_prompt_chris.json` zuruecksetzen oder via `git rm --cached` + `.gitignore` aus dem Tracking nehmen.

---

## Zuletzt passiert (1 Patch in dieser Session)

**P206 — HitL-Gate vor Code-Execution (Phase 5a Ziel #6 ABGESCHLOSSEN).** Das letzte Sicherheitsnetz vor `execute_in_workspace`. Bisher (P203d-1) lief jeder erkannte Code-Block direkt in die Sandbox — RO-Mount machte das vergleichsweise sicher, aber Phase-5a-Ziel #6 fordert explizite User-Confirmation. Plus: integriert die kleine Teststand-Reminder-Konvention aus dem Feature-Request (HANDOVER-Header zeigt `**Manuelle Tests:** X / Y ✅`).

**Was P206 baut:**

1. **Neues Modul** [`zerberus/core/hitl_chat.py`](zerberus/core/hitl_chat.py) mit `ChatHitlGate`-Singleton (~80 Zeilen) — In-Memory-only, keine DB-Persistenz weil Long-Polls beim Server-Restart sowieso sterben:
   - `create_pending(session_id, project_id, project_slug, code, language) -> ChatHitlPending` — UUID4-hex als ID, `asyncio.Event` als Notification-Shortcut
   - `wait_for_decision(pending_id, timeout) -> str` — blockt bis Resolve oder TimeoutError, status flippt auf `approved`/`rejected`/`timeout`
   - `resolve(pending_id, decision, *, session_id=None) -> bool` — idempotent (Doppel-Resolve liefert False), Cross-Session-Defense (UUID raten reicht nicht: session_id muss matchen)
   - `list_for_session(session_id)` — liefert nur eigene Pendings, niemals fremde
   - `cleanup(pending_id)` — entfernt nach Resolve, sonst Memory-Leak bei jedem Code-Block
   - Plus `store_code_execution_audit(...)`-Helper schreibt Audit-Trail-Zeile

2. **Neue DB-Tabelle** `code_executions` in [`zerberus/core/database.py`](zerberus/core/database.py) — schliesst die HANDOVER-Schuld P203d-1 ("code_execution ist nicht in der DB"). Spalten: `pending_id` (UUID-Korrelation), `session_id`, `project_id`, `project_slug`, `language`, `exit_code`, `execution_time_ms`, `truncated`/`skipped` (0/1 SQLite-friendly), `hitl_status` (approved/rejected/timeout/bypassed/error), `code_text`/`stdout_text`/`stderr_text`/`error_text` (8 KB Truncate via `_truncate_for_audit`), `created_at`/`resolved_at`. `init_db`-Bootstrap legt die Tabelle automatisch an — keine Alembic-Migration noetig.

3. **Verdrahtung in** [`zerberus/app/routers/legacy.py`](zerberus/app/routers/legacy.py)`::chat_completions` zwischen `first_executable_block` und `execute_in_workspace`:
   - `settings.projects.hitl_enabled=True` (neuer Default) → Pending erzeugen, `wait_for_decision(pending_id, timeout=hitl_timeout_seconds)` blockt long-poll-style
   - Bei `approved`/`bypassed` → Sandbox laeuft normal, `code_execution_payload` mit `skipped=False`, `hitl_status` gefuellt
   - Bei `rejected`/`timeout` → Sandbox uebersprungen, `code_execution_payload` mit `skipped=True`, `exit_code=-1`, `error="Vom User abgebrochen"` oder `"Keine User-Bestaetigung (Timeout)"`
   - Synthese-Gate erweitert: `if code_execution_payload is not None and not code_execution_payload.get("skipped"):` — kein Synthese-Call bei Skip (es gibt nichts auszuwerten)
   - Audit-Schreibung am Ende: `store_code_execution_audit(...)` mit `pending_id`/`session_id`/`hitl_status`/`payload`

4. **Zwei neue auth-freie Endpoints** in `legacy.py` (per `/v1/`-Invariante kein JWT):
   - `GET /v1/hitl/poll` — Frontend-Long-Poll, liefert das aelteste Pending der Session (sortiert nach `created_at`) oder `null`. Header `X-Session-ID` als Owner-Diskriminator.
   - `POST /v1/hitl/resolve` — Body `{pending_id, decision, session_id}`, idempotent + Cross-Session-Block. Antwort `{ok, decision}`.

5. **Nala-Frontend** in [`zerberus/app/routers/nala.py`](zerberus/app/routers/nala.py):
   - **CSS-Block** `.hitl-card`/`.hitl-actions`/`.hitl-approve`/`.hitl-reject`/`.hitl-resolved` (~70 Zeilen) — Kintsugi-Gold-Border `rgba(240,180,41,0.55)`, gruene/rote Action-Buttons mit `min-height: 44px` und `min-width: 44px` (Mobile-first). Plus `.exit-badge.exit-skipped` fuer Code-Card im Skip-State.
   - **JS-Funktionen** (~120 Zeilen direkt nach `renderCodeExecution`): `startHitlPolling(abortSignal, snapshotSessionId)` (1-Sekunden-Intervall, sofortige erste Runde), `stopHitlPolling()`, `renderHitlCard(pending)` mit `escapeHtml(String(pending.code))` als XSS-Schutz, `resolveHitlPending(pendingId, decision)` (Buttons sperren, POST `/v1/hitl/resolve`, Card-State-Update auf hitl-approved/hitl-rejected), `clearHitlState()`.
   - **`sendMessage`-Verdrahtung:** vor dem Chat-Fetch wird `clearHitlState()` + `startHitlPolling(myAbort.signal, reqSessionId)` aufgerufen, im `finally`-Block `stopHitlPolling()`. Polling laeuft nur waehrend der Chat-Fetch offen ist. Card bleibt nach Klick als Audit-Spur im DOM stehen.
   - **`renderCodeExecution`-Erweiterung:** liest `codeExec.skipped` und `codeExec.hitl_status`, ersetzt Exit-Badge bei Skip durch `⏸ uebersprungen` (rejected) oder `⏱ timeout` (timeout) — Banner mit Reason kommt aus dem `error`-Feld.

6. **Feature-Flag** `projects.hitl_enabled: bool = True` plus `hitl_timeout_seconds: int = 60` in [`zerberus/core/config.py`](zerberus/core/config.py)`::ProjectsConfig`. Bei `false` laeuft P203d-1-Verhalten ohne Gate (Audit-Status `bypassed`).

7. **Doku-Pflicht-Erweiterung** in [`ZERBERUS_MARATHON_WORKFLOW.md`](ZERBERUS_MARATHON_WORKFLOW.md): HANDOVER-Header bekommt die Zeile `**Manuelle Tests:** X / Y ✅`. Implementierung der Feature-Request-Konvention aus dem Chris-Brief vom 2026-05-03 — kein Reminder-Text, keine Eskalation, nur die Zahl. Die Psychologie macht den Rest.

**Tests:** 55 in [`test_p206_hitl_chat_gate.py`](zerberus/tests/test_p206_hitl_chat_gate.py).

`TestChatHitlGate` (13) — Pure-async Pending/Resolve/Wait-Mechanik: UUID4 + status pending, `to_public_dict`-Schluessel ohne `status`, list_for_session-Filter, approve/reject flippt status, invalid decision/unknown id/already-resolved/cross-session blockiert, wait sofort wenn schon resolvt, wait blockt + resolvt nach Delay, wait timeout setzt status, cleanup raeumt Registry.

`TestStoreCodeExecutionAudit` (3) — DB-Insert mit allen Feldern, Truncate bei langem stdout (>8 KB), silent skip wenn DB nicht initialisiert.

`TestHitlEndpoints` (7) — `hitl_poll` empty/own/cross-session-Filter, `hitl_resolve` approved/unknown/invalid/cross-session blocked.

`TestLegacySourceAudit` (8) — `[HITL-206]`-Tag, `get_chat_hitl_gate`-Import, `create_pending`/`wait_for_decision`/`cleanup` im Quelltext, Synthese-Skip-Gate (`not code_execution_payload.get("skipped")`), Audit-Aufruf nach `store_interaction("assistant"`, `code_execution`-Field im Response-Schema, `/v1/hitl/poll` + `/v1/hitl/resolve` als Routen registriert.

`TestNalaSourceAudit` (12) — JS-Funktionen `startHitlPolling`/`renderHitlCard`/`resolveHitlPending`/`clearHitlState` definiert, `sendMessage` startet + stoppt Polling, `/v1/hitl/poll` + `/v1/hitl/resolve` referenziert, `pending_id`/`decision`/`session_id` im Resolve-Body, `escapeHtml(String(pending.code` im Renderer (XSS), CSS-Klassen `.hitl-card`/`.hitl-actions`/`.hitl-approve`/`.hitl-reject`/`.hitl-resolved` vorhanden, 44x44px Touch-Target im `.hitl-actions button`, `renderCodeExecution` erkennt `codeExec.skipped` + `exit-skipped`-Klasse, Defense-in-Depth-Test: `pending.code` nur via `escapeHtml(`.

`TestE2EHitlGateInChat` (6) — Mock-LLM + Mock-Sandbox + monkeypatched `wait_for_decision`: approved laesst Sandbox laufen + `skipped=False` + `hitl_status=approved`, rejected skippt Sandbox + `skipped=True` + `error` enthaelt "abgebrochen", timeout skippt + `error` enthaelt "timeout", bypassed (hitl_enabled=False) ruft `wait_for_decision` NICHT auf + `hitl_status=bypassed`, Audit-Row geschrieben fuer approved + rejected.

`TestJsSyntaxIntegrity` (1, skipped wenn node fehlt) — `node --check` ueber alle inline `<script>`-Bloecke aus NALA_HTML. Lesson aus P203b/P203d-3.

`TestSmoke` (3) — Config-Flags vorhanden, DB-Tabelle hat alle HitL-Spalten, `/nala/`-Endpoint enthaelt Renderer + CSS + Endpoints.

**Kollateral-Fix:** `test_p203d_chat_sandbox.py::_setup_common` und `test_p203d2_chat_synthesis.py::_setup` plus `test_synthesis_failure_keeps_original_answer` setzen jetzt `monkeypatch.setattr(get_settings().projects, "hitl_enabled", False)` — sonst wuerde das neue HitL-Gate (Default ON) im Test 60 Sekunden auf eine Decision warten, die nie kommt. P203d-Tests sind nicht ueber HitL — sauberer Bypass.

**Logging-Tag:** `[HITL-206]` mit `pending_create id=... session=... project_id=... language=... code_len=N` / `decision id=... session=... status=approved|rejected|timeout` / `bypassed session=... (hitl_enabled=False)` / `skipped session=... status=... language=...` / `audit_written session=... project_id=... hitl_status=... skipped=... exit_code=...` / `audit_failed (non-fatal)` / Log-Tag bleibt Worker-Protection-konform (KEINE Code-Inhalte oder Output-Inhalte im Log).

## Nächster Schritt — sofort starten

**P207: Diff-View / Snapshots / Rollback (Phase 5a Ziel #9 + #10).** Bevor Sandbox-Code in einem `writable=True`-Mount Files schreibt, soll der User vor und nach dem Run eine atomare Snapshots-Spur sehen — Plus die Moeglichkeit, das Workspace auf den Vor-Stand zurueckzudrehen. P206 (HitL) gibt das Yay/Nay vor der Ausfuehrung; P207 gibt die Sicht UND die Reverse-Option NACH der Ausfuehrung.

**Konkret (Coda darf abweichen):**

1. **Snapshot-Schicht** in `zerberus/core/projects_workspace.py` (oder neues Modul `projects_snapshots.py`): Pure-Function `snapshot_workspace(workspace_root) -> dict[str, str]` (Pfad → Content-Hash). Async-Wrapper `materialize_snapshot_async(project_id, label)` schreibt einen Snapshot-Eintrag plus optional einen Tar-Archive-Dump unter `data/projects/<slug>/_snapshots/<timestamp>_<label>.tar.gz` (oder pure SHA-Storage-Refs, je nach Disk-Budget).
2. **DB-Tabelle** `workspace_snapshots`: `id`, `project_id`, `label` (z.B. "before_p206_run" / "after_p206_run"), `created_at`, `file_count`, `total_bytes`, `archive_path` (oder JSON-Blob mit Pfad-Hash-Liste).
3. **Diff-Engine**: `diff_snapshots(snapshot_a, snapshot_b) -> list[FileDiff]` mit Status `added` / `modified` / `deleted`, plus Inline-Text-Diff (`difflib.unified_diff`) fuer Text-Files.
4. **Verdrahtung im Chat-Endpunkt**: nach P206-Approve UND wenn `writable=True` (P207-neu, kein Default!) → snapshot vorher, sandbox lauft writable, snapshot nachher. Diff-Result in `code_execution`-Response (additives Feld `diff`).
5. **UI in Nala-PWA**: nach Code-Execution mit `code_execution.diff` → neue Diff-Card unter Output-Card. Inline-Diff mit gruenen/roten Linien, Klick-Toggle pro Datei expandiert. **Rollback-Button** "↩️ Aenderungen zurueckdrehen" → POST `/v1/workspace/rollback` mit `pending_id`/`snapshot_id` → Server schreibt `before`-Snapshot ueber `after`-Stand.
6. **Tests**: Snapshot-Idempotenz, Diff-Algorithm (added/modified/deleted/binary-skip), Rollback restoriert exakt, UI-Render-Audit, Mobile-44px-Touch.
7. **Was P207 NICHT macht**: Cross-Project-Diff (per-Projekt isoliert), Branch-Mechanik wie Git (linear forward/reverse only), automatischer Rollback bei exit_code != 0 (User-Choice).

Alternativ falls P207 zu viel: **P208 Spec-Contract / Ambiguitaets-Check (Ziel #8)** — vor dem ersten LLM-Call eine Domain-Probe ("Was genau soll der Code tun? In welcher Sprache? Welche Inputs?"), bei Whisper-Input mit hoher Ambiguitaet (kurzer Satz, mehrere Interpretationen) eine Rueckfrage statt direkt Code generieren. Schmaler Scope, kein Workspace-Touch.

**Reihenfolge-Vorschlag** (Coda darf abweichen):
- **P207** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P208** Spec-Contract / Ambiguitäts-Check — Ziel #8
- **P209** Zweite Meinung vor Ausführung — Ziel #7
- **P210** GPU-Queue für VRAM-Konsumenten — Ziel #11

Helper aus P206 die in P207 direkt nutzbar sind:
- `code_executions`-Tabelle hat schon `pending_id`/`session_id`/`project_id` als Korrelationsschluessel — Snapshot-Eintraege koennen via `pending_id` ueberkreuzt werden ohne neue Foreign-Key-Schicht.
- `[HITL-206]`-Pattern (long-poll innerhalb Request + separate Resolve-Endpoint + In-Memory-Singleton) ist die Vorlage fuer jeden weiteren User-Bestaetigungs-Pfad in der Pipeline.
- `.hitl-card`-CSS klont sich trivial zu `.diff-card` / `.rollback-card` (gleiches Vokabular: Border, Aktions-Buttons, 44x44 Touch-Target, Post-Klick-State-Erhalt als Audit-Spur).
- `clearHitlState`/`startHitlPolling`/`stopHitlPolling`-Pattern ist eine Mini-State-Machine die fuer Diff-Render-Polling (falls noetig) recycled werden kann.

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL-Telegram (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), Projekte-Backend (P194), Projekte-UI (P195), Projekt-Datei-Upload (P196), Persona-Merge-Layer (P197), Projekt-Templates (P198), Projekt-RAG-Index (P199), PWA-Verdrahtung Nala + Hel (P200), Nala-Tab "Projekte" + Header-Setter (P201), PWA-Auth-Hotfix (P202), Project-Workspace-Layout (P203a), Hel-UI-Hotfix Event-Delegation (P203b), Sandbox-Workspace-Mount + execute_in_workspace (P203c), Prosodie-Kontext im LLM (P204), Code-Detection + Sandbox-Roundtrip im Chat (P203d-1), Output-Synthese fuer Sandbox-Code-Execution (P203d-2), UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (P203d-3), RAG-Toast in Hel-UI (P205), **HitL-Gate vor Code-Execution + Audit-Tabelle code_executions + HANDOVER-Teststand-Konvention (P206)**.

Helper aus P206 die in P207 direkt nutzbar sind: siehe oben.

Helper aus P205 die direkt nutzbar sind: `_RAG_REASON_LABELS`/`_showRagToast`-Pattern (statisches Reason-Mapping mit Default-Fallback + textContent-Renderer + Singleton-Timeout-Slot) als Vorlage fuer weitere Status-Toasts in Hel/Nala. `.rag-toast`-CSS klont sich trivial zu `.workspace-toast` / `.snapshot-toast`. Replace-Pattern via `id="*Toast"` + `.visible`-Toggle haelt UI ruhig.

Helper aus P203d-3/P203d-2/P203d-1/P204: siehe vorherigen HANDOVER. `addMessage`-Wrapper-Return, `escapeHtml`, `renderCodeExecution`-Pattern, `.code-card`/`.output-card`-CSS, `code_execution`-Dict-Schema, Marker-Disjunktheit `[CODE-EXECUTION]` vs. `[PROJEKT-RAG]` / `[PROJEKT-KONTEXT]` / `[PROSODIE]`.

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#71-75 (P206: Push, Approve-Pfad, Reject-Pfad, Timeout-Pfad, Bypass-Flag)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 1872 passed (+55 P206 aus 1817 baseline)**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Zeile 1653 + 3096) — wie zuvor.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat einen `node --check`-Pass** seit P203d-3 — abgedeckt durch `TestJsSyntaxIntegrity`-Tests in P203d-3 + P206 (skipped wenn `node` nicht im PATH).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **Pre-existing Files ohne Workspace** — wie zuvor.
- **Pre-existing Files ohne RAG-Index** — wie zuvor.
- **Lazy-Embedder-Latenz** — wie zuvor.
- **PWA-Cache-Bust nach Patches** — wie zuvor: nach dem ersten Open der PWA muss der Service-Worker den Cache invalidieren — Cache-Name-Bump auf `nala-shell-v3` waere sauber. Bei P206 nicht zwingend (network-first faengt das auf), aber empfohlen weil JS-Surface deutlich gewachsen ist.
- **PWA-Icons nur Initial-Buchstabe** — wie zuvor.
- **PWA hat keinen Offline-Modus für die Hauptseite** — wie zuvor.
- **P201 Nala-Tab zeigt nur Auswählen, kein Anlegen** — wie zuvor.
- **Hardlink vs. Copy auf Windows** — wie zuvor.
- **P204 BERT-Header-Reuse aus P193** — wie zuvor.
- **P203c Mount-Spec auf Windows** — wie zuvor.
- **P203c keine Sync-After-Write-Verdrahtung** — bleibt offen, P207 (Diff/Snapshots) ist der natuerliche Ort.
- **P203d-1: Token-Cost-Tracking für `code_execution`-Pfad** — wie zuvor.
- **P203d-1: `code_execution` ist nicht in der DB** — **GELOEST mit P206**: neue `code_executions`-Tabelle mit Audit-Trail pro Roundtrip (incl. HitL-Status).
- **P203d-2: Synthese-LLM nutzt aktuell den Default-Cloud-Modus** — wie zuvor.
- **P203d-2: Synthese kann den Code-Block aus dem `answer` nicht entfernen** — wie zuvor.
- **P203d-3: Keine Syntax-Highlighting-Library im Frontend** — wie zuvor.
- **P203d-3: Code-Card hat keinen Copy-to-Clipboard-Button** — wie zuvor.
- **P205: Sammel-Toast bei Multi-Upload bewusst nicht implementiert** — wie zuvor.
- **NEU P206: HitL-Pendings nicht persistiert ueber Server-Restart** — bewusste Entscheidung. Long-Poll-Requests sterben beim Restart sowieso (Connection-Bruch); persistente Pendings wuerden zu "Geister-Confirm-Karten" fuehren, die nach Hours-spaeterem Wiederconnect ploetzlich auftauchen. Bei einem zukuenftigen "Background-Code-Run-Mode" (LLM-getriggertes Cron-Job, kein interaktiver Chat) muss man das nochmal anschauen — dann ist das Telegram-HitL-Pattern (P167 mit DB-Persist) das richtige Vorbild.
- **NEU P206: Keine Edit-Vor-Run-Funktion in der Confirm-Card** — User sieht den Code, kann aber nicht editieren bevor er zustimmt. Falls UX-Feedback "Ich will den Code anpassen": Card mit `<textarea>`-Inplace-Edit + `_resolveHitlPending(id, decision, edited_code)` waere die natuerliche Erweiterung — Backend muesste dann den `code` aus dem Resolve-Body uebernehmen statt aus dem Pending.
- **NEU P206: Telegram hat keinen Chat-HitL-Pfad** — Huginn nutzt sein eigenes P167-System (`HitlManager` mit DB-Persist). Die zwei Welten sollen separat bleiben — Telegram-Callbacks kommen Stunden spaeter, der Chat-Long-Poll ist binnen 60s. Architektur-Trennung ist Absicht.
- **NEU P206: HANDOVER-Teststand-Konvention** — am Session-Ende ist das Coda's Job, die Zeile `**Manuelle Tests:** X / Y ✅` aus der Manuelle-Tests-Tabelle in WORKFLOW.md zu zaehlen und in den HANDOVER-Header zu schreiben. Spezifiziert in der Doku-Pflicht-Sektion von WORKFLOW.md.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt (P203d-1) | Output-Synthese im Chat-Endpunkt (P203d-2) | UI-Render im Nala-Frontend (P203d-3) | RAG-Toast in Hel-UI (P205) | **HitL-Gate vor Sandbox-Code-Execution: `ChatHitlGate`-Singleton (In-Memory) erzeugt Pending nach Code-Detection, blockt `wait_for_decision(timeout=hitl_timeout_seconds=60)` long-poll-style, Frontend pollt parallel `GET /v1/hitl/poll` und rendert Confirm-Card mit ✅/❌ 44x44 Touch-Buttons, `POST /v1/hitl/resolve` setzt asyncio.Event, Endpunkt wacht auf und fuehrt aus oder skippt mit `code_execution.skipped=True` + `hitl_status` in der Response. Audit-Trail in neuer `code_executions`-Tabelle (`pending_id`/`session_id`/`project_id`/`language`/`exit_code`/`hitl_status`/`code_text`/`stdout_text`/`stderr_text` mit 8 KB Truncate). Cross-Session-Defense: `session_id`-Match im Resolve. Bypass-Flag `hitl_enabled: bool = True` (Default ON, audit-status `bypassed` bei OFF). `[HITL-206]`-Logging-Tag. Synthese (P203d-2) skippt skipped-Payloads (P206)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`) | Slug ist immutable nach Anlage | Atomic Write für jeden Upload-Pfad | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge (P197) | Pure-Function vs. DB-Schicht trennen | Templates als reguläre `project_files`-Einträge (P198) | Idempotenz-Check VOR Schreiben für jeden Generator (P198) | Best-Effort-Verdrahtung im Endpoint (P198) | Per-Projekt-RAG-Index isoliert (P199) | Embedder-Wrapper als monkeypatchbare Funktion (P199) | Best-Effort-Indexing (P199) | RAG-Block-Marker `[PROJEKT-RAG]` (P199) | PWA-Endpoints auth-frei in separatem Router VOR auth-gated Hel-Router (P200) | SW-Scope folgt dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch (P200) | `persona_overlay` NIEMALS in User-sichtbarer Response (P201) | Header-Injektion fuer Cross-Cutting zentral in `profileHeaders()` (P201) | Zombie-ID-Schutz nach List-Refresh (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test (P201+P203d-3+P206) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig (P203a) | Hardlink primär, Copy als Fallback (P203a) | Atomic-Write-Pattern auch fuer Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat ueber benutzergenerierte Daten ist verboten — IMMER `data-*` + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehoert in jede Test-Suite die HTML in Python-Source baut (P203b+P203d-3+P205+P206) | Prosodie-Bruecken-Block enthaelt NIEMALS numerische Werte (P204) | Mehrabian-Schwellen identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Bruecken-Bloecke MUESSEN substring-disjunkt sein (P204+P203d-2) | Sandbox-Workspace-Mount: Read-Only ist konservativer Default (P203c) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte konservativ geblockt — `project_overlay is not None` als Diskriminator (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollstaendiges Sandbox-Result liegt vor (P203d-1) | `writable=False` ist hardcoded in P203d-1 (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open (P203d-1) | Two-stufige LLM-Pipelines: `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren (P203d-2) | Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (P203d-2) | Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding (P203d-2) | Synthese-LLM-Pipeline ist fail-open auf jeder Stufe (P203d-2) | Frontend-Renderer fuer additive Backend-Felder muss BACKWARDS-COMPAT bleiben (P203d-3+P205+P206) | DOM-Insertion-Funktionen retournen Wrapper-Element (P203d-3) | Frontend-Renderer fuer User-/LLM-Strings: jedes innerHTML-Statement muss einen `escapeHtml(`-Aufruf enthalten — Audit-Test prueft das per Regex (P203d-3+P206) | Mobile-first 44x44px Touch-Target gilt fuer JEDEN neuen Toggle/Button im Frontend (P203d-3+P206) | Status-Toasts: `textContent` statt `innerHTML` fuer Reason-Strings (P205) | Toast-Replace-Pattern: ein einziger DOM-Container plus CSS-State-Toggle (P205) | `pointer-events: none` im Hidden-State von fixed-positionierten Toasts/Overlays (P205) | **HitL-Pending-IDs sind UUID4-hex (32 chars), Cross-Session-Defense via Session-Match im Resolve, niemals Integer-IDs (P206)** | **HitL-Pendings im Chat-Pfad sind transient (In-Memory), nicht persistiert — Long-Poll-Requests sterben beim Restart sowieso, persistente Pendings wuerden zu Geister-Karten fuehren (P206)** | **Synthese-LLM-Calls (P203d-2) DUERFEN NICHT auf `skipped=True`-Payloads laufen — Trigger-Gate haengt zusaetzlich von `not payload.get("skipped")` ab (P206)** | **Audit-Trail-Schreibungen sind Best-Effort: jeder Crash darf den Hauptpfad nicht blockieren — `try/except` um den DB-Insert (P206)** | **HANDOVER-Header enthaelt die Zeile `**Manuelle Tests:** X / Y ✅` aus der WORKFLOW-Tabelle, ohne Reminder-Text oder Bewertung (P206-Konvention)**
