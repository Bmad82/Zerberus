## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-04
**Letzter Patch:** P213 — Reasoning-Schritte sichtbar im Chat (Phase 5a Ziel #13 ABGESCHLOSSEN)
**Tests:** 2313 passed (+57 P213 aus 2256 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 1 skipped (existing)
**Manuelle Tests:** 1 / 102 ✅
**Commit:** wird beim Push gesetzt
**Repos synchron:** wird beim Push aktualisiert
**Huginn-RAG-Sync:** wird beim Push aktualisiert

---

## Zuletzt passiert (1 Patch in dieser Session)

**P213 — Reasoning-Schritte sichtbar im Chat (Phase 5a Ziel #13 ABGESCHLOSSEN).** Vor P213 sah der User waehrend einer Chat-Turn nur die finale Antwort. Wenn die Pipeline arbeitete (Spec-Probe → RAG → Veto → HitL-Wartezeit → Sandbox-Run → Synthese-Call), war das auf Mobile oft mehrere Sekunden Stille — der User wusste nicht, ob das System haengt oder arbeitet. P213 macht die Zwischenschritte sichtbar als kollabierte Karte unter der Bot-Bubble (Default eingeklappt, ein Klick zeigt die Liste).

**Was P213 baut:**

1. **Neues Modul** [`zerberus/core/reasoning_steps.py`](zerberus/core/reasoning_steps.py) mit drei Schichten:
   - **Pure-Function** — `compute_step_duration_ms(started, finished) -> int|None` (None bei laufendem Step, sonst Millisekunden, clamped >= 0). `should_emit(kind, *, enabled, disabled_kinds)` als Trigger-Gate (globaler Kill-Switch + per-kind Opt-out + Whitelist `KNOWN_STEP_KINDS={spec_check, rag_query, veto_probe, hitl_wait, sandbox_run, synthesis, embedder, reranker, guard, llm_call}`). `truncate_text(text, *, max_bytes)` Bytes-genau mit Ellipsis-Marker `…` und Unicode-safe-Cut.
   - **Datenklasse** `ReasoningStep` mit `step_id` (UUID4-hex), `session_id`, `kind`, `summary`, `started_at`, `status` (Default `running`, Set `KNOWN_STATUSES={running, done, error, skipped}`), `finished_at`, optional `detail`. `duration_ms` als computed property. `to_public_dict()` schmal: KEIN `session_id`, KEIN `detail` (Audit-only) — nur `step_id`/`kind`/`summary`/`status`/`duration_ms`/`started_at`/`finished_at` fuer Frontend.
   - **`ReasoningStreamGate`-Singleton** mit Per-Session-FIFO (`DEFAULT_BUFFER_PER_SESSION=32`, `DEFAULT_SESSION_TTL_SECONDS=600`). `emit(*, session_id, kind, summary, detail)` ist sync (kein await im Hot-Path). `mark_done(step_id, *, status, detail)` idempotent. `cleanup_session` + `cleanup_stale_sessions` als TTL-Sweep. `consume_steps(session_id, *, wait_seconds)` async, blockt long-poll-style.
   - **Convenience** `emit_step(session_id, kind, summary, *, detail)` und `mark_step_done(step, *, status, detail)`. `mark_step_done` akzeptiert sowohl `ReasoningStep`-Objekt als auch `None` — damit der Aufrufer unbedingt `mark_step_done(emit_step(...))` schreiben kann ohne Null-Check.
   - **Sync-emit + async-audit-Asymmetrie** — `mark_step_done` checkt `asyncio.get_running_loop()`: wenn Loop verfuegbar → `loop.create_task(_audit_step(result))` als Hintergrund-Task; wenn kein Loop (Sync-Test) → Coroutine wird GAR NICHT erst erzeugt (sonst RuntimeWarning "coroutine was never awaited"). Fail-open: jeder Audit-Crash wird geloggt + verschluckt.

2. **Neue DB-Tabelle** `reasoning_audits` in [`database.py`](zerberus/core/database.py) mit `step_id`/`session_id`/`kind`/`status`/`duration_ms`/`summary`/`detail`/`created_at`. Schreibt nur Endzustaende (`done`/`error`/`skipped`) — der `running`-Zwischenstand wandert nie in die DB. Auswertung: `SELECT kind, AVG(duration_ms), MAX(duration_ms), COUNT(*) FROM reasoning_audits WHERE status='done' GROUP BY kind` zeigt Latenz-Hotspots als Tuning-Grundlage.

3. **Zwei neue auth-freie Endpoints** in [`legacy.py`](zerberus/app/routers/legacy.py):
   - `GET /v1/reasoning/poll?wait=N` — auth-frei (Dictate-Lane-Invariante). Liefert `{"steps": [...]}` als JSON-Array fuer die Session aus `X-Session-ID`-Header. `wait` auf `[0, DEFAULT_POLL_TIMEOUT_SECONDS=10]` geclamped, um abusive Long-Polls zu verhindern. Best-Effort-Sweep aelterer Sessions bei jedem Poll.
   - `POST /v1/reasoning/clear` — wirft die Steps der Session weg, idempotent. Frontend ruft das beim Beginn einer neuen Chat-Turn auf.

4. **Verdrahtung in 7 Pipeline-Stellen** in `chat_completions` (legacy.py):
   1. **Turn-Reset** beim Eintritt: `get_reasoning_gate().cleanup_session(session_id)` als zusaetzliche Defensive (zusaetzlich zum Frontend-`POST /clear`).
   2. **Projekt-RAG**: `emit_step("rag_query", "Projekt-RAG durchsucht (<slug>)")` vor `query_project_rag`, `mark_done` mit `chunks=N`-Detail.
   3. **Spec-Check**: `emit_step("spec_check", "Frage prueft auf Mehrdeutigkeit")` nur wenn `should_ask_clarification` greift, `mark_done` `done`/`skipped`.
   4. **LLM-Call**: `emit_step("llm_call", "Modell formuliert Antwort")` mit try/except, `mark_done` `error` falls die Call-Coroutine wirft. Auch im Fallback-Pfad und Direct-LLM-Pfad.
   5. **Veto-Probe**: `emit_step("veto_probe", "Zweites Modell prueft Code")` vor `run_veto`, `mark_done` abhaengig vom Verdict (`pass`/`error`).
   6. **HitL-Wartezeit**: `emit_step("hitl_wait", "Wartet auf Bestaetigung")` vor `wait_for_decision`, Status-Mapping `approved`/`bypassed` → `done`, `rejected`/`timeout` → `skipped`, sonst `error`.
   7. **Sandbox-Run**: `emit_step("sandbox_run", "Sandbox laeuft (<lang>)")` vor `execute_in_workspace`, `mark_done` `done`/`error` (exit_code) oder `skipped` (None-Result).
   8. **Synthese**: `emit_step("synthesis", "Verstaendliche Antwort wird formuliert")` vor `synthesize_code_output`, `mark_done` `done`/`skipped` (leerer Output)/`error`.

5. **Nala-Frontend** in [`nala.py`](zerberus/app/routers/nala.py):
   - **CSS** `.reasoning-card` mit Default-collapsed-State, `.reasoning-toggle` als 44px-Touch-Target (Mobile-first), `.reasoning-list` als ungeordnete Liste mit `.reasoning-step`-Eintraegen. Status-Klassen `.reasoning-running` (animiertes Spin-Icon via `@keyframes reasonSpin`), `.reasoning-done` (Gruen #6cd4a1), `.reasoning-error` (Rot #e57373), `.reasoning-skipped` (Grau #8aa0c0).
   - **Polling-Loop** `startReasoningPolling(abortSignal, snapshotSessionId)` — 4s-Intervall, erster Tick nach 800ms, fail-quiet bei Netzfehler, Auto-Stop wenn die Session wechselt.
   - **Renderer** `renderReasoningSteps(steps)` — Karte beim ersten Step erzeugt, danach in-place Update via `_reasoningStepIndex`-Map (step_id → li-Element). Status-Icons via `escapeHtml(_reasonIcon(...))`. Summary via `textContent` (XSS-safe). Per-Step `data-step-id` + `data-step-kind` als stabile DOM-Keys.
   - **Toggle** als globale Event-Delegation auf `[data-reasoning-toggle]` (kein onclick-Concat im innerHTML, P203b-Invariante).
   - **Default-Labels** `_REASON_KIND_LABELS` als Frontend-Whitelist — Defense-in-Depth zur Backend-Whitelist `KNOWN_STEP_KINDS`. Falls das Backend einen unbekannten Kind liefert, faellt der Renderer auf den Roh-Namen zurueck.
   - **Hookup an `sendMessage`** — `clearReasoningState()` + `startReasoningPolling(myAbort.signal, reqSessionId)` parallel zu HitL/Spec-Polling. `stopReasoningPolling()` im `finally`-Block (Karte bleibt aber als Audit-Spur stehen).

**Tests:** 57 in [`test_p213_reasoning_steps.py`](zerberus/tests/test_p213_reasoning_steps.py).

`TestComputeStepDurationMs` (4) — running / finished / zero / negative-clamped.
`TestShouldEmit` (4) — known-kinds / unknown / disabled-globally / disabled-kinds.
`TestTruncateText` (4) — none / short / truncate-with-ellipsis / unicode-safe.
`TestReasoningStep` (3) — running-no-duration / finished-duration / public-dict-omits-internal.
`TestStreamGateEmit` (4) — running-step / unknown-kind / no-session / truncated.
`TestStreamGateMarkDone` (4) — sets-finish / idempotent / invalid-status / unknown-id.
`TestStreamGateBufferCap` (1) — fifo-cap-drops-oldest.
`TestStreamGateCleanup` (2) — cleanup-session / cleanup-stale.
`TestStreamGateConsume` (4) — immediate / empty / long-poll-emit / long-poll-timeout.
`TestConvenience` (3) — singleton-emit / mark-none-no-crash / mark-step-object.
`TestStoreAudit` (1) — silent-when-DB-not-initialized.
`TestLegacyWiring` (4) — imports / kinds-emitted / turn-reset / endpoints-registered.
`TestReasoningPollEndpoint` (4) — empty / steps-for-session / session-isolation / wait-clamped.
`TestReasoningClearEndpoint` (2) — clear-removes / idempotent.
`TestNalaFrontendReasoningCard` (7) — css / 44px / poll-endpoint / clear-endpoint / hookup / event-delegation / xss-textContent.
`TestJsSyntaxIntegrity` (1) — node --check ueber inline scripts (skipped wenn node fehlt).
`TestSmoke` (4) — module-exports / db-schema / kinds-statuses-disjoint / constants-sane.

**Logging-Tag:** `[REASON-213]` mit `emit step=… session=… kind=… status=…`/`done step=… session=… kind=… status=… duration_ms=…`/`audit_written step=… session=… kind=… status=… duration_ms=…`/`audit_failed (non-fatal): …`/`audit_import_failed: …`/`buffer_overflow session=… removed_kind=…`/`sweep removed_sessions=…`.

**Kollateral-Fix:** `test_p203d_chat_sandbox.py::TestP203d1SourceAudit::test_writable_false_default_in_call_site` Window-Erweiterung von ±2500 auf ±3500 Bytes — der Sandbox-Block in legacy.py wuchs durch P213-emit_step-Aufrufe um ~700 Bytes, das alte Fenster traf nicht mehr alle relevanten Zeilen. Semantisch identisch (prueft weiterhin "writable kommt aus settings, hardcoded ist es nicht").

## Nächster Schritt — sofort starten

**P214: Wiederkehrende Jobs / Scheduler (Phase 5a Ziel #14).** Cron-Style-Tasks fuer das System (z.B. "tagliches Backup", "stuendlicher RAG-Reindex", "wochentliches Workspace-GC"). Hat keine UI-Komponente (zumindest nicht primaer), nur Backend.

**Konkret (Coda darf abweichen):**

1. **Pure-Function-Schicht** in neuem Modul `zerberus/core/scheduler.py`: `ScheduledJob`-Dataclass mit `job_id` (UUID4-hex), `name` (User-lesbar, z.B. "rag_reindex_nightly"), `cron_expression` (Standard-Crontab, z.B. `"0 3 * * *"` fuer 3am taeglich), `task_module`/`task_function` (dotted-path zur auszufuehrenden Funktion, z.B. `"zerberus.tasks.reindex_rag"`), `enabled` (bool), `last_run_at`/`next_run_at`, `last_status` (`success|error|skipped`).
2. **Cron-Parser** als Pure-Function `parse_cron(expr) -> CronSpec` und `compute_next_run(spec, *, now)` — wir bauen das selbst statt python-crontab/croniter zu importieren (Cross-Platform, dependency-frei).
3. **Async-Wrapper-Schicht**: `SchedulerLoop`-Singleton mit asyncio-Hintergrund-Task. Beim Server-Start aktiviert, prueft alle 30s ob ein Job fällig ist, importiert die `task_function` dynamisch, ruft sie auf, schreibt Audit. Idempotenz: ein Job darf nicht parallel zu sich selbst laufen (Mutex pro `job_id`).
4. **Audit-Tabelle**: `scheduled_job_runs` mit `run_id` (UUID4)/`job_id`/`started_at`/`finished_at`/`status`/`duration_ms`/`error_text` — analog `code_executions`/`code_vetoes`. Grundlage fuer "welcher Job staut?"-Auswertungen.
5. **Job-Definitionen** als Python-Dict in `zerberus/tasks/__init__.py` (statisch, nicht DB-driven — die Liste der Jobs ist Code-Versionierung, nicht User-konfigurierbar). Initial: `rag_reindex_nightly`, `workspace_snapshot_gc_weekly`, `secret_redaction_audit_summary_weekly`.
6. **HTTP-Endpunkt** `GET /hel/admin/scheduler/jobs` (admin-only via Basic Auth) liefert die Liste mit Status, `POST /hel/admin/scheduler/jobs/<id>/trigger` startet einen Job manuell, `POST /hel/admin/scheduler/jobs/<id>/disable` deaktiviert ihn.
7. **Tests**: ScheduledJob-Dataclass-Properties, Cron-Parser-Pure-Function (auch obscure Cron-Patterns wie `*/15`, `1-5`, `0,30`), SchedulerLoop-Hintergrund-Task-Tests (mit Mock-Time), Audit-Best-Effort, Source-Audits, End-to-End mit echtem Job-Aufruf.
8. **Was P214 NICHT macht**: User-konfigurierbare Jobs (Code-Versionierung sicherer), Distributed Scheduling (Single-Node only, Zerberus laeuft auf einer Maschine), UI-basierte Cron-Eingabe (Hel zeigt nur Status, keine Bearbeitung), Webhook-Trigger (eigener spaeter Patch), Job-Dependency-Ketten (jeder Job ist standalone).

Alternativ falls P214 zu breit oder ohne klaren Use-Case: **P215 Bessere RAG-Quellen-Karten / Source-UI (Ziel #15)** — Frontend-Karte unter der Bot-Antwort, die zeigt aus welchen Doku-Chunks die Antwort kommt. Aktuell sieht der User nur die Antwort, nicht die Source-Trace.

**Reihenfolge-Vorschlag** (Coda darf abweichen):
- **P214** Wiederkehrende Jobs / Scheduler — Ziel #14
- **P215** Bessere RAG-Quellen-Karten / Source-UI — Ziel #15
- **P216** Billige Fehler billig fangen / Validierung vor teuren LLM-Calls — Ziel #15 alt → eigentlich Ziel #15 / "Token sparen"

Helper aus P213 die in P214/P215 direkt nutzbar sind:
- **`emit_step`/`mark_step_done`-Pattern** — wenn P214 lange Jobs hat (z.B. RAG-Reindex 30s+), kann der Scheduler-Loop selbst ein `emit_step("scheduled_job", ...)` schreiben, damit die Hel-UI live sieht welcher Job laeuft.
- **`KNOWN_STEP_KINDS`-Whitelist mit Backend+Frontend-Doppelung** — bei P215 (Source-Karten) als Vorlage: beide Seiten haben einen Default-Label-Mapping fuer unbekannte Kinds.
- **Sync-emit + async-audit-Asymmetrie** — fuer Telemetrie im Hot-Path: niemals `await` auf einen DB-Insert in einem User-blockierenden Pfad. `loop = asyncio.get_running_loop()` mit None-Fallback ist das Pattern, wenn kein Loop verfuegbar ist.
- **Per-Session-FIFO mit TTL-Sweep beim Read** — fuer P214: pro `job_id` ein Buffer der letzten N Runs, Stale-Sweep beim `GET /hel/admin/scheduler/jobs/<id>/runs` statt Hintergrund-Task.
- **`.reasoning-card`-CSS-Pattern** (Default-collapsed + Toggle + 44px-Touch-Target + Status-Klassen) klont sich trivial fuer P214 (Hel-Karte fuer Job-Run-Status), P215 (Source-Karte unter Bot-Bubble), oder P216 (Validation-Pre-Card).
- **`mark_step_done(emit_step(...))` mit None-tolerantem Caller-Pattern** — universell fuer jede Optional-Telemetrie-Pipeline.
- **Window-basierte Source-Audit-Tests brauchen ±5000-Reserve, nicht ±2500** — Lehre aus dem P213-Sandbox-Window-Fix; bei zukuenftigen Verdrahtungs-Patches IMMER konservativ dimensionieren.

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL-Telegram (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), Projekte-Backend (P194), Projekte-UI (P195), Projekt-Datei-Upload (P196), Persona-Merge-Layer (P197), Projekt-Templates (P198), Projekt-RAG-Index (P199), PWA-Verdrahtung Nala + Hel (P200), Nala-Tab "Projekte" + Header-Setter (P201), PWA-Auth-Hotfix (P202), Project-Workspace-Layout (P203a), Hel-UI-Hotfix Event-Delegation (P203b), Sandbox-Workspace-Mount + execute_in_workspace (P203c), Prosodie-Kontext im LLM (P204), Code-Detection + Sandbox-Roundtrip im Chat (P203d-1), Output-Synthese fuer Sandbox-Code-Execution (P203d-2), UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (P203d-3), RAG-Toast in Hel-UI (P205), HitL-Gate vor Code-Execution + Audit-Tabelle code_executions + HANDOVER-Teststand-Konvention (P206), Workspace-Snapshots + Diff-View + Rollback + workspace_snapshots-Tabelle + sandbox_writable/snapshots_enabled-Flags + POST /v1/workspace/rollback (P207), Spec-Contract / Ambiguitäts-Check + spec_check.py-Modul + clarifications-Tabelle + spec_check_enabled/threshold/timeout-Flags + GET /v1/spec/poll + POST /v1/spec/resolve + Klarstellungs-Karte mit Textarea + drei Decision-Werten (P208), Zweite Meinung vor Ausführung / Sancho Panza + code_veto.py-Modul + code_vetoes-Tabelle + code_veto_enabled/temperature-Flags + Wandschlag-Banner mit roter Border + read-only Veto-Card + drei Verdict-Werten pass/veto/skipped/error (P209), Huginn-RAG-Auto-Sync + tools/sync_huginn_rag.py-Modul + scripts/sync_huginn_rag.ps1-Wrapper + Stand-Anker-Pflicht-Header in huginn_kennt_zerberus.md + Doku-Pflicht-Tabellenzeile in WORKFLOW.md + Phase-5a-Ziel #18 + 53 Tests (P210), GPU-Queue fuer VRAM-Konsumenten + zerberus/core/gpu_queue.py-Modul + gpu_queue_audits-Tabelle + Verdrahtung in whisper_client/gemma_client/projects_rag/rag-router/orchestrator + GET /v1/gpu/status-Endpoint + Hel-Frontend-Toast `.gpu-toast` mit 4s-Polling + 40 Tests (P211), Secrets-Filter fuer Sandbox+Synthese-Output + zerberus/core/secrets_filter.py-Modul + secret_redactions-Audit-Tabelle + Verdrahtung in sandbox/manager.py._run_in_container und sandbox/synthesis.py.synthesize_code_output als Defense-in-Depth + 40 Tests (P212), **Reasoning-Schritte sichtbar im Chat + zerberus/core/reasoning_steps.py-Modul (Pure-Function compute_step_duration_ms/should_emit/truncate_text + ReasoningStep-Dataclass + ReasoningStreamGate-Singleton mit Per-Session-FIFO + sync-emit + async-audit-Asymmetrie + Convenience emit_step/mark_step_done(step|None)) + reasoning_audits-Audit-Tabelle (nur Endzustaende geschrieben) + zwei auth-freie Endpoints GET /v1/reasoning/poll + POST /v1/reasoning/clear + Verdrahtung in 7 Pipeline-Stellen in chat_completions (Turn-Reset/Projekt-RAG/Spec-Check/LLM-Call/Veto-Probe/HitL-Wartezeit/Sandbox-Run/Synthese) + Nala-Frontend `.reasoning-card` mit 44px-Touch-Target + Default-collapsed + Event-Delegation auf data-reasoning-toggle + Status-Klassen running/done/error/skipped + 4s-Polling mit erstem Tick nach 800ms + in-place Update via _reasoningStepIndex-Map + Frontend-Whitelist _REASON_KIND_LABELS + 57 Tests in test_p213_reasoning_steps.py — P213)**.

Helper aus P212/P211/P210/P209/P208/P207/P206 die in P213 schon recycelt wurden: Pure-Function-vs-Async-Wrapper-Trennung (analog `should_queue` + `_acquire` → `should_emit`/`compute_step_duration_ms` + `ReasoningStreamGate.emit`), Singleton-mit-Reset-Helper-Pattern (analog `GpuQueue` mit `reset_global_queue_for_tests` → `ReasoningStreamGate` mit `reset_reasoning_gate_for_tests`), Source-Audit-Test-Pattern (analog `TestSandboxWiring`/`TestSynthesisWiring` aus P212 → `TestLegacyWiring` aus P213), Best-Effort-Audit-Insert-Pattern (analog `store_secret_redaction` → `_audit_step`), Per-Session-FIFO-Buffer-Pattern (analog `ChatHitlGate` aus P206 + `ChatSpecGate` aus P208 → `ReasoningStreamGate`).

Helper aus P205/P203d-3/P203d-2/P203d-1/P204: siehe vorherige HANDOVERs. `addMessage`-Wrapper-Return, `escapeHtml`, `renderCodeExecution`-Pattern, `.code-card`/`.output-card`-CSS, `code_execution`-Dict-Schema, Marker-Disjunktheit, RAG-Toast-Pattern, JS-Integrity-Test.

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#100-102 (P213: Reasoning-Karte live, Polling-Endpoint, Audit-Tabelle Latenz-Analyse)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 2313 passed (+57 P213 aus 2256 baseline)**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Zeile 1653 + 3096) — wie zuvor.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat einen `node --check`-Pass** seit P203d-3 — abgedeckt durch `TestJsSyntaxIntegrity`-Tests in P203d-3 + P206 + P207 + P208 + P209 + P211 + **P213**.
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **P207 Storage-GC fuer alte Snapshot-Tars** — wie zuvor.
- **P207 Hardlink-Snapshots statt Tar** — wie zuvor.
- **P207 Per-File-Rollback** — wie zuvor.
- **P207 Cross-Project-Diff** — bewusst weggelassen.
- **P207 Branch-Mechanik** — bewusst weggelassen.
- **P207 Automatischer Rollback bei `exit_code != 0`** — bewusst nicht.
- **P207 Token-Cost-Tracking fuer Snapshot-Pfad** — Snapshots erzeugen keine LLM-Kosten.
- **P208 Token-Cost-Tracking fuer Spec-Probe-Call** — wie zuvor.
- **P208 Multi-Turn-Klarstellung** — bewusst weggelassen.
- **P208 Edit-Vor-Run** — User kann Original-Prompt nicht editieren in der Card.
- **P208 Telegram-Pfad** — bewusst weggelassen.
- **P208 Persistierung der Pendings** — In-Memory only.
- **P208 Threshold-Tuning ueber `clarifications`-Tabelle** — manueller SQL-Query.
- **P209 Token-Cost-Tracking fuer Veto-Call** — wie zuvor.
- **P209 Mehr-Modell-Voting** — bewusst weggelassen.
- **P209 Lern-Loop ueber `code_vetoes`-History** — wie zuvor.
- **P209 Sprach-spezifische Heuristiken** — wie zuvor.
- **P209 Veto-Override durch User** — bewusst weggelassen.
- **P209 Persistenz der Verdicts ueber Server-Restart** — `VetoVerdict` ist transient.
- **P209 Telegram-Pfad** — bewusst weggelassen.
- **P210: Spiegel-Kopie wird nicht mitsynct.** Stand-Anker-Block muss bei jedem Patch in beiden Files parallel mitgepflegt werden (Doku-Pflicht).
- **P210: Auto-Trigger nach git commit fehlt.** Stattdessen explizit im Marathon-Push-Zyklus dokumentiert.
- **P210: Multi-Datei-Sync nicht implementiert.**
- **P210: Server-Status-Check vorab fehlt.**
- **P210: Cron / Schedule-Integration fehlt.** Coda macht das am Session-Ende.
- **P210: Auto-Bumping der Patchnummer im Header fehlt.**
- **P210: SUPERVISOR_ZERBERUS.md ist auf 1100+ Zeilen gewachsen** (Ziel <400) — bleibt als eigene Schuld auf dem Stack. Saubere Variante: alte Patches in `SUPERVISOR_ZERBERUS_archive.md` auslagern.
- **P211: Audit-Tabelle ist da, aber Hel-UI-Auswertung fehlt.** Eigener Patch P211b mit Hel-Tab "GPU-Queue".
- **P211: Statisches Budget koennte sich aendern.**
- **P211: Whisper-Slot bei Docker auf separater GPU.**
- **P211: Sync-Variante des Slots fehlt.**
- **P211: Toast nur in Hel, nicht in Nala.** Bewusste Audience-Trennung.
- **P211: Per-Consumer-Reservierung statt globaler Queue.** Bewusst weggelassen.
- **P211: Cancel-Outstanding-Slots fehlt.**
- **P212: Vollstaendige `.env`-Verschluesselung fehlt.**
- **P212: Pattern-basiertes Matching ohne env-Lookup fehlt.** Bewusst weggelassen.
- **P212: Multi-Pass-LLM-Filter fehlt.** Sehr unwahrscheinlich + nicht behebbar.
- **P212: Hel-UI-Auswertung der `secret_redactions`-Tabelle fehlt.** Eigener Patch P212b.
- **P212: Dynamische `.env`-Reloads fehlen.** Bewusst weggelassen.
- **P212: Maskierung im Veto-Pfad fehlt.** Optional, aktuell nicht noetig (Veto-Pfad sehr eng).
- **P212: Sandbox-source hat keine session_id.** Bewusst weggelassen, sonst muesste die Sandbox-API geaendert werden.
- **NEU P213: Hel-UI-Auswertung der `reasoning_audits`-Tabelle fehlt.** Analog zu P211/P212-Schulden — die Audit-Tabelle ist da, aber niemand zeigt sie. Ein Hel-Tab "Reasoning-Latenz" mit `SELECT kind, AVG(duration_ms), MAX(duration_ms), COUNT(*) FROM reasoning_audits WHERE status='done' GROUP BY kind ORDER BY 2 DESC` waere ein eigener kleiner Patch (P213b).
- **NEU P213: SSE/WebSocket-Streaming statt Polling.** Aktuell pollt das Frontend alle 4s — bei Pipelines mit Sub-Sekunden-Schritten wuerden Steps "verschluckt" (running → done bevor der Poll greift). Bewusst weggelassen weil 4s-Granularitaet fuer den User-Use-Case ausreicht (Steps sind nie kuerzer als 200ms in der Praxis), aber bei zukuenftigen sehr schnellen Pipelines (z.B. lokales Modell mit 50ms-Inferenz) waere SSE der naechste Schritt.
- **NEU P213: Cross-Session-Visibility / Multi-User-Karte fehlt.** Jeder User sieht nur seine eigenen Steps via `X-Session-ID`-Header. Eine Hel-UI-Karte die alle aktiven Reasoning-Streams zeigt (Operations-View) waere ein eigener Patch P213c.
- **NEU P213: Detail-Drill-Down pro Step fehlt.** Der `detail`-String steht in der Audit-Tabelle, nicht in der Karten-UI. Wer wissen will warum ein Step `error` war, schaut in `reasoning_audits.detail` per SQL — kein UI-Klick. Bewusst weggelassen weil der Gebrauchswert klein ist (User schaut nicht in DB-Tabellen).
- **NEU P213: Replay-Modus fuer alte Sessions fehlt.** Steps sind transient (In-Memory + TTL-Sweep). Wer historische Reasoning-Spuren rekonstruieren will, fragt die Audit-Tabelle ab.
- **NEU P213: Embedder/Reranker/Guard-Kinds sind in der Whitelist aber nicht verdrahtet.** Wer sie verdrahten will, klont das `emit_step`/`mark_step_done`-Pattern aus den HitL/Spec/Sandbox-Stellen (z.B. in `rag/router.py` um die Embedder-Schleife herum).
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
- **P203c keine Sync-After-Write-Verdrahtung** — TEILWEISE GELOEST mit P207.
- **P203d-1: Token-Cost-Tracking für `code_execution`-Pfad** — wie zuvor.
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
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt (P203d-1) | Output-Synthese im Chat-Endpunkt (P203d-2) | UI-Render im Nala-Frontend (P203d-3) | RAG-Toast in Hel-UI (P205) | HitL-Gate vor Sandbox-Code-Execution: `ChatHitlGate`-Singleton, `/v1/hitl/poll`+`/v1/hitl/resolve`, Confirm-Karte, `code_executions`-Audit-Tabelle (P206) | Workspace-Snapshots + Diff + Rollback: `projects_snapshots.py` (P207) | Spec-Contract / Ambiguitäts-Check vor Haupt-LLM-Call: `spec_check.py` (P208) | Zweite Meinung vor Ausführung / Sancho Panza vor HitL-Gate: `code_veto.py` (P209) | Huginn-RAG-Auto-Sync via `tools/sync_huginn_rag.py`-Modul + `scripts/sync_huginn_rag.ps1` + Stand-Anker-Pflicht-Header in `docs/huginn_kennt_zerberus.md` + Marathon-Push-Zyklus eingebaut (P210) | GPU-Queue fuer VRAM-Konsumenten via `zerberus/core/gpu_queue.py`-Modul + `gpu_queue_audits`-Tabelle + auth-freier `GET /v1/gpu/status`-Endpoint + Hel-Frontend-Toast `.gpu-toast` (P211) | Secrets-Filter via `zerberus/core/secrets_filter.py`-Modul + `secret_redactions`-Audit-Tabelle + Verdrahtung in `sandbox/manager.py._run_in_container` und `sandbox/synthesis.py.synthesize_code_output` als Defense-in-Depth (P212) | **Reasoning-Schritte sichtbar via `zerberus/core/reasoning_steps.py`-Modul mit Pure-Function `compute_step_duration_ms`/`should_emit`/`truncate_text` + `ReasoningStep`-Dataclass mit `to_public_dict`-Schmalfilter + `ReasoningStreamGate`-Singleton mit Per-Session-FIFO (Default 32 Steps, TTL 600s) + sync-emit + async-audit-Asymmetrie + Convenience `emit_step` und `mark_step_done(step|None)` no-op-tolerant + Whitelist `KNOWN_STEP_KINDS={spec_check, rag_query, veto_probe, hitl_wait, sandbox_run, synthesis, embedder, reranker, guard, llm_call}` + Status-Set `{running, done, error, skipped}` + DB-Tabelle `reasoning_audits` (nur Endzustaende geschrieben) + zwei auth-freie Endpoints `GET /v1/reasoning/poll?wait=N` und `POST /v1/reasoning/clear` + Verdrahtung in 7 Pipeline-Stellen in `chat_completions` (Turn-Reset/Projekt-RAG/Spec-Check/LLM-Call/Veto-Probe/HitL-Wartezeit/Sandbox-Run/Synthese) + Nala-Frontend `.reasoning-card` mit 44px-Touch-Target + Default-collapsed + Event-Delegation auf `data-reasoning-toggle` + Status-Klassen `.running` (Spin-Icon)/`done`/`error`/`skipped` + 4s-Polling mit erstem Tick nach 800ms + in-place Update via `_reasoningStepIndex`-Map + Frontend-Whitelist `_REASON_KIND_LABELS` als Default-Fallback + `[REASON-213]`-Logging-Tag (P213)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`) | Slug ist immutable nach Anlage | Atomic Write für jeden Upload-Pfad | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge (P197) | Pure-Function vs. DB-Schicht trennen | Templates als reguläre `project_files`-Einträge (P198) | Idempotenz-Check VOR Schreiben für jeden Generator (P198) | Best-Effort-Verdrahtung im Endpoint (P198) | Per-Projekt-RAG-Index isoliert (P199) | Embedder-Wrapper als monkeypatchbare Funktion (P199) | Best-Effort-Indexing (P199) | RAG-Block-Marker `[PROJEKT-RAG]` (P199) | PWA-Endpoints auth-frei in separatem Router VOR auth-gated Hel-Router (P200) | SW-Scope folgt dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch (P200) | `persona_overlay` NIEMALS in User-sichtbarer Response (P201) | Header-Injektion fuer Cross-Cutting zentral in `profileHeaders()` (P201) | Zombie-ID-Schutz nach List-Refresh (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test (P201+P203d-3+P206+P207+P208+P209+**P213**) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig (P203a) | Hardlink primär, Copy als Fallback (P203a) | Atomic-Write-Pattern auch fuer Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat ueber benutzergenerierte Daten ist verboten — IMMER `data-*` + Event-Delegation (P203b+**P213** `data-reasoning-toggle`) | JS-Integrity-Test (`node --check`) gehoert in jede Test-Suite die HTML in Python-Source baut (P203b+P203d-3+P205+P206+P207+P208+P209+P211+**P213**) | Prosodie-Bruecken-Block enthaelt NIEMALS numerische Werte (P204) | Mehrabian-Schwellen identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Bruecken-Bloecke MUESSEN substring-disjunkt sein (P204+P203d-2+P208 KLARSTELLUNG) | Sandbox-Workspace-Mount: Read-Only ist konservativer Default (P203c+P207) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte konservativ geblockt — `project_overlay is not None` als Diskriminator (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollstaendiges Sandbox-Result liegt vor (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open (P203d-1) | Two-stufige LLM-Pipelines: `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren (P203d-2) | Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (P203d-2+P209 should_run_veto+**P213 should_emit**) | Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding (P203d-2+P208 SPEC_ANSWER_MAX_BYTES+P209 VETO_CODE_MAX_BYTES/REASON_MAX_BYTES+**P213 SUMMARY_MAX_BYTES/DETAIL_MAX_BYTES**) | Synthese-LLM-Pipeline ist fail-open auf jeder Stufe (P203d-2) | Frontend-Renderer fuer additive Backend-Felder muss BACKWARDS-COMPAT bleiben (P203d-3+P205+P206+P207+P209+**P213** reasoning-Karte erscheint nur bei nicht-leerer Step-Liste) | DOM-Insertion-Funktionen retournen Wrapper-Element (P203d-3) | Frontend-Renderer fuer User-/LLM-Strings: jedes innerHTML-Statement muss einen `escapeHtml(`-Aufruf enthalten (P203d-3+P206+P207) | Frontend-Renderer fuer User-Strings DARF auch `textContent` statt `innerHTML` nutzen (P208 .spec-card+P209 .veto-card+P211 .gpu-toast+**P213 .reasoning-step.summary**) | Mobile-first 44x44px Touch-Target gilt fuer JEDEN neuen Toggle/Button im Frontend (P203d-3+P206+P207+P208+P209+P211+**P213** .reasoning-toggle min-height:44px) | Status-Toasts: `textContent` statt `innerHTML` fuer Reason-Strings (P205+P211) | Toast-Replace-Pattern: ein einziger DOM-Container plus CSS-State-Toggle (P205+P211) | `pointer-events: none` im Hidden-State von fixed-positionierten Toasts/Overlays (P205+P211) | HitL-Pending-IDs sind UUID4-hex (32 chars), Cross-Session-Defense via Session-Match im Resolve, niemals Integer-IDs (P206+P208+**P213 step_id auch UUID4-hex**) | HitL-Pendings im Chat-Pfad sind transient (In-Memory), nicht persistiert (P206+P208 ChatSpecGate+**P213 ReasoningStreamGate**) | Synthese-LLM-Calls (P203d-2) DUERFEN NICHT auf `skipped=True`-Payloads laufen (P206+P209 vetoed) | Audit-Trail-Schreibungen sind Best-Effort: jeder Crash darf den Hauptpfad nicht blockieren (P206+P207+P208+P209+P211+P212+**P213 _audit_step**) | HANDOVER-Header enthaelt die Zeile `**Manuelle Tests:** X / Y ✅` aus der WORKFLOW-Tabelle, ohne Reminder-Text oder Bewertung (P206-Konvention) | Sandbox-Writable-Mount: Default `projects.sandbox_writable=False` (P207) | Snapshot-Tars unter `<data>/projects/<slug>/_snapshots/<uuid4-hex>.tar`, atomar via Tempname + `os.replace` (P207) | Tar-Member-Validation `_is_safe_member` MUSS bei jedem Restore aufgerufen werden (P207) | Cross-Project-Defense: `rollback_snapshot_async` MUSS `expected_project_id` gegen Snapshot-Eigentuemer pruefen (P207) | JavaScript-String-Literale mit Newline (`\\n`) DUERFEN NICHT direkt aus Python-Source kommen — `String.fromCharCode(10)` als Workaround (P207) | Workspace-Snapshot-Pfad ist additiv und opt-in (P207) | Spec-Contract-Pfad ist additiv und opt-in (P208) | Spec-Probe-LLM-Call MUSS isoliert sein (Persona-frei, kein RAG, kein Prosody-Block) (P208+P209 Veto-Probe analog) | Drei Decision-Werte answered/bypassed/cancelled (P208) | Source-Detection ueber `X-Prosody-Context` + `X-Prosody-Consent: true` (P204-Konvention) — Voice-Bonus +0.20 im Score (P208) | `[KLARSTELLUNG]`-Marker substring-disjunkt zu allen anderen LLM-Markern (P208) | `enrich_user_message`-Pfad: bei `answered` MUSS sowohl `last_user_msg` als auch `req.messages[-letzter user-msg].content` aktualisiert werden (P208) | Veto-Pfad ist additiv und opt-in (P209) | Veto-Probe-LLM-Call MUSS isoliert sein (P209) | Bei VETO schreibt der Endpunkt KEINE Zeile in `code_executions`, Audit landet ausschliesslich in `code_vetoes` (P209) | Veto-Verdicts: `pass`/`veto`/`skipped`/`error` (P209) | Wandschlag-Banner ist read-only Audit-Spur — KEIN Approve-Button (P209) | Veto-LLM-Call ist deterministisch via `temperature_override=0.1` (P209) | Verdict-Parser MUSS robust gegen Markdown-Bold/Quotes/Doppelpunkte/Bindestriche/Lowercase/Multi-Line-Reasons sein (P209) | `docs/huginn_kennt_zerberus.md` MUSS einen `## Aktueller Stand`-Block ganz oben tragen mit `**Letzter Patch:** P###`-Zeile (P210) | Sync-Reihenfolge ist DELETE → UPLOAD invariant (P210) | Sync-Tool-DELETE muss 404 als Erfolg akzeptieren (P210) | Coda-Verantwortung: am Session-Ende `python -m tools.sync_huginn_rag` aufrufen VOR `sync_repos.ps1` (P210) | Spiegel-Kopie unter `docs/RAG Testdokumente/huginn_kennt_zerberus.md` ist NUR Test-Material, NICHT im Live-RAG (P210) | GPU-Queue: VRAM-Konsumenten MUESSEN via `async with vram_slot("<name>"): ...` serialisiert werden (P211) | GPU-Queue: Statisches VRAM-Budget in `VRAM_BUDGET_MB`-Dict, NIE via `nvidia-smi`-Polling (P211) | GPU-Queue: FIFO-Reihenfolge mit Head-of-Line-Block by design (P211) | GPU-Queue: Audit-Tabelle `gpu_queue_audits` ist die Validierungs-Grundlage der Budget-Werte (P211) | GPU-Queue: Hel-Toast statt Nala-Toast (P211) | Secrets-Filter: Sandbox + Synthese maskieren BEIDE den Output (Defense-in-Depth) (P212) | Secrets-Filter: longest-first-Invariante beim Mask-Loop (P212) | Secrets-Filter: Audit-Tabelle `secret_redactions` schreibt NUR bei `redaction_count > 0` (P212) | Secrets-Filter: `MIN_SECRET_LENGTH=8` ist die Falsch-Positiv-Heuristik (P212) | Secrets-Filter: Replacement-Skip wenn ein Secret zufaellig dem Replacement-Text gleicht (P212) | Secrets-Filter: Cache `load_secret_values` mit Lazy-`frozenset`-Snapshot (P212) | Secrets-Filter: `mask_and_audit` ist fail-open auf jeder Stufe (P212) | **Reasoning-Pfad ist additiv und opt-in via Trigger-Gate `should_emit` (P213)** | **Reasoning-Steps sind transient — nur In-Memory + TTL-Sweep, persistente Spur nur in `reasoning_audits`-Tabelle (P213)** | **Reasoning-Audit schreibt NUR Endzustaende (`done`/`error`/`skipped`), niemals `running` — sonst wird die Tabelle leeres Rauschen (P213)** | **Reasoning-Convenience: `mark_step_done(step|None)` MUSS None-tolerant sein, damit der Aufrufer unbedingt `mark_step_done(emit_step(...))` ohne Null-Check schreiben kann (P213)** | **Reasoning-Frontend: `_REASON_KIND_LABELS` als Frontend-Whitelist ist Defense-in-Depth zur Backend-Whitelist `KNOWN_STEP_KINDS` — bei jedem Kind/Type-Feld mit JSON-Transport BEIDE Seiten Whitelist + Default-Fallback (P213)** | **Sync-emit + async-audit-Asymmetrie: `emit_step` ist sync (kein await im Hot-Path), Audit-Insert ist asynchron als Hintergrund-Task auf dem laufenden Event-Loop — wenn kein Loop verfuegbar ist (Sync-Test), wird die Coroutine GAR NICHT erst erzeugt (sonst RuntimeWarning "coroutine was never awaited") (P213)** | **Reasoning-Endpoint `wait`-Param MUSS auf `[0, DEFAULT_POLL_TIMEOUT_SECONDS]` geclamped werden, sonst kann ein boeswilliger Caller einen Long-Poll mit 9999 Sekunden absetzen (P213)** | **Window-basierte Source-Audit-Tests brauchen ±5000-Reserve, nicht ±2500 — bei jedem Verdrahtungs-Patch kann der File um hunderte Bytes wachsen (P213-Lehre aus Sandbox-Test-Window-Fix)**
