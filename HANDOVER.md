## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-03
**Letzter Patch:** P211 — GPU-Queue für VRAM-Konsumenten (Phase 5a Ziel #11 ABGESCHLOSSEN)
**Tests:** 2216 passed (+40 P211 aus 2176 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 1 skipped (existing)
**Manuelle Tests:** 1 / 93 ✅
**Commit:** 6283862 — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude — alle drei gepusht (Ratatoskr `1d94e95`, Claude `491c8f2`, 0 unpushed Commits in allen drei). `verify_sync.ps1` meldet Zerberus working-tree dirty: `system_prompt_chris.json` bleibt unangetastet (Mutzenbacher-Persona-Experiment, vom User am 2026-05-01 als "gedroppt" markiert — Schulden-Liste, Stand identisch zu allen Vorgaenger-HANDOVERS seit P205. Naechster Coda kann sie ggf. via `git checkout system_prompt_chris.json` zuruecksetzen oder via `git rm --cached` + `.gitignore` aus dem Tracking nehmen).
**Huginn-RAG-Sync:** beim Versuch fehlgeschlagen — Server nicht erreichbar (`RemoteProtocolError: Server disconnected` auf DELETE, `ReadError` auf POST). Erwartetes Verhalten wenn Zerberus-Server nicht läuft. Fix: nächster Coda mit laufendem Server `python -m tools.sync_huginn_rag` oder `scripts/sync_huginn_rag.ps1` aufrufen — Stand-Anker im Doku-Header ist auf P211 aktualisiert, sobald der Sync läuft kennt Huginn den neuen Stand.

---

## Zuletzt passiert (1 Patch in dieser Session)

**P211 — GPU-Queue fuer VRAM-Konsumenten (Phase 5a Ziel #11 ABGESCHLOSSEN).** Kooperatives Scheduling zwischen den vier VRAM-Konsumenten auf der RTX 3060 (12 GB physisch, ~11 GB nutzbar): Whisper (Docker :8002 — laeuft auf derselben GPU), Gemma E2B (Prosodie, CLI oder llama-server), Embedder (MiniLM/Multilingual via SentenceTransformer) und Reranker (CrossEncoder). Vorher konnten alle vier parallel ein Modell auf die GPU laden — bei einer typischen Voice-Eingabe (Whisper plus sofort danach Embedder/Reranker fuer das Projekt-RAG) reichten 12 GB nicht und der Server crashte mit `CUDA out of memory`.

**Was P211 baut:**

1. **Neues Modul** [`zerberus/core/gpu_queue.py`](zerberus/core/gpu_queue.py) mit drei Schichten:
   - **Pure-Function** — `compute_vram_budget(consumer_name) -> int` mit statischer Tabelle `VRAM_BUDGET_MB = {whisper:4000, gemma:2000, embedder:1000, reranker:512}`; unbekannte Konsumenten bekommen `DEFAULT_CONSUMER_BUDGET_MB=1500` mit Warning-Log (Tippfehler in der Verdrahtung sind so loud detektierbar). `should_queue(active_total_mb, requested_mb, *, total_mb=11000) -> bool` als Token-Bucket-Check.
   - **Datenklassen** — `GpuSlotInfo` mit `audit_id` (UUID4-hex), `consumer_name`, `requested_mb`, `queue_position` (0=sofort, >0=N-ter Waiter), `waited_at`/`acquired_at`/`released_at`, `timed_out`. Computed Properties `wait_ms` und `held_ms`.
   - **Async-Singleton** `GpuQueue` mit `_active_mb`/`_waiters`-FIFO/`_lock=asyncio.Lock`/`_active_slots`-Liste. Acquire unter Lock: passt → sofort `_active_mb += requested`, sonst Future in `_waiters`, ausserhalb des Locks `await asyncio.wait_for(future, timeout)`, Timeout-Cleanup filtert Waiter-Liste. Release: `_active_mb -= requested`, FIFO durch Waiter wecken bis nicht mehr passt (Head-of-Line-Block by design).
   - **Convenience** — `vram_slot(consumer_name, *, timeout=30.0)` als async Context-Manager: `async with vram_slot("whisper"): ...`. Identisch zu `get_gpu_queue().slot(...)`.

2. **Audit-Tabelle** `gpu_queue_audits` in [`database.py`](zerberus/core/database.py) mit `audit_id`/`consumer_name`/`requested_mb`/`queue_position`/`wait_ms`/`held_ms`/`timed_out`/`created_at`. Best-Effort-Insert via `store_gpu_queue_audit(info)`. Grundlage fuer Budget-Tuning (`SELECT consumer_name, AVG(wait_ms), MAX(wait_ms) FROM gpu_queue_audits GROUP BY consumer_name`).

3. **Verdrahtung in fuenf Stellen** (alle mit `async with vram_slot(...)`):
   - [`whisper_client.transcribe`](zerberus/utils/whisper_client.py) — `vram_slot("whisper", timeout=60.0)` um den HTTP-Call.
   - [`gemma_client.GemmaAudioClient.analyze_audio`](zerberus/modules/prosody/gemma_client.py) — `vram_slot("gemma", timeout=60.0)` um den Backend-Call. Stub-Modus skippt den Slot.
   - [`projects_rag.index_project_file`](zerberus/core/projects_rag.py) und `query_project_rag` — `vram_slot("embedder", timeout=30.0)` um den `_embed_text`-Aufruf. Bei Index: ein Slot fuer den ganzen Chunk-Loop einer Datei (vermeidet Per-Chunk-Overhead). Timeout-Reason `embed_timeout`.
   - [`rag/router.py`](zerberus/modules/rag/router.py) `query_documents` — `vram_slot("embedder")` um den `_encode`-Loop und `vram_slot("reranker")` um den `_rerank`-Call.
   - [`orchestrator.py`](zerberus/app/routers/orchestrator.py) — Embedder-Loop und Reranker-Call analog.

4. **Status-Endpoint** auth-frei `GET /v1/gpu/status` (analog `/v1/hitl/*`) liefert Snapshot `{total_mb, active_mb, free_mb, active_slots:[{consumer,requested_mb,held_ms}], waiters:[{consumer,requested_mb}]}`.

5. **Hel-Frontend-Toast** ([`hel.py`](zerberus/app/routers/hel.py)): CSS `.gpu-toast` (analog `.rag-toast` aus P205, fixed bottom-LEFT statt right damit beide Toasts nicht kollidieren), DOM `<div id="gpuToast" ...>`, JS-IIFE mit `_pollOnce()` (fetch `/v1/gpu/status` no-store, fail-quiet) und `_renderGpuToast(status)` (textContent statt innerHTML — Consumer-Namen aus Whitelist `whisper|gemma|embedder|reranker`). Toast erscheint sobald `waiters.length > 0`: `⏳ GPU wartet auf <Consumer>` plus Position bei mehreren Wartenden. Polling 4s via `setInterval`, Cleanup beim `beforeunload`. Klick = Soft-Dismiss.

**Tests:** 40 in [`test_p211_gpu_queue.py`](zerberus/tests/test_p211_gpu_queue.py).

`TestComputeVramBudget` (3) — known / unknown / case-insensitive.
`TestShouldQueue` (3) — fits / overflow / zero-or-negative.
`TestGpuQueueAcquireImmediate` (3) — when-empty / release-frees / parallel-3-consumers.
`TestGpuQueueFifo` (2) — overflow-waits / strict-FIFO.
`TestGpuQueueTimeout` (2) — raises-TimeoutError / cleans-waiter.
`TestGpuQueueStatus` (1) — Snapshot reportet Active+Waiters.
`TestStoreAudit` (1) — silent-when-no-DB.
Verdrahtungs-Source-Audits (10) — Whisper/Gemma/Embedder/Reranker je Imports + Aufruf-Stelle.
`TestGpuStatusEndpointSource` + `E2E` (4) — Endpoint-Registration + Snapshot-Schema.
`TestHelFrontendGpuToast` (6) — CSS/DOM/Endpoint-URL/textContent/Whitelist/44px.
`TestJsSyntaxIntegrity` (1) — `node --check` ueber alle inline `<script>`-Bloecke aus ADMIN_HTML, skipped wenn `node` nicht im PATH.
`TestSmoke` (3) — Module-Exports / DB-Tabelle-Schema / KNOWN_CONSUMERS-Konsistenz.

**Logging-Tag:** `[GPU-211]` mit `acquired immediate consumer=... requested_mb=... active_mb=.../...`, `queued consumer=... position=...`, `acquired after_wait wait_ms=...`, `timeout consumer=... wait_ms=...`, `released consumer=... held_ms=... woken=...`, `audit_written ...`.

**Kollateral-Fix:** Keine. Der neue Patch fuegt nur ein eigenstaendiges Modul + Tests + Verdrahtung + Frontend-Toast hinzu. Bestehende Tests laufen unveraendert (Pre-existing Failures bleiben pre-existing).

## Nächster Schritt — sofort starten

**P212: Secrets bleiben geheim (Phase 5a Ziel #12).** `.env`-Encrypt + Sandbox-Injection-Filter + Output-Maskierung. Schmaler Scope, fokussiert auf den ein-Variable-Fall (`OPENAI_API_KEY` in `.env` darf nie im Sandbox-Output landen).

**Konkret (Coda darf abweichen):**

1. **Pure-Function-Schicht** in neuem Modul `zerberus/core/secrets_filter.py`: `extract_secret_values(env_dict) -> Set[str]` (alle `*_KEY`/`*_SECRET`/`*_TOKEN`/`*_PASSWORD`-Werte sammeln); `mask_secrets_in_text(text, secrets, *, replacement="***REDACTED***") -> str` (alle Vorkommen substring-ersetzen, longest-first damit `OPENAI_API_KEY` nicht zu `***REDACTED***_KEY` wird).
2. **Sandbox-Side**: `SandboxResult.stdout`/`stderr` durch `mask_secrets_in_text` jagen, BEVOR sie an den Caller zurueckgehen. Verdrahtung in `manager.execute()`.
3. **Output-Synthese-Side**: `synthesize_code_output` muss vermeiden, dass der LLM die Secrets aus dem stdout/stderr in seine Synthese-Antwort baut. Pre-Filter auf den `payload`-Feldern bevor sie dem LLM uebergeben werden.
4. **`.env`-Encrypt** als Schuld-Punkt vermerken — vollstaendige Verschluesselung waere ein eigener Patch (P212b oder spaeter).
5. **Audit-Tabelle** `secret_redactions` mit `redaction_count`/`source` (sandbox|synthesis)/`session_id`/`created_at` — wenn jemand jemals einen Klartext-Secret im Output sieht, ist das ein Bug, der hier nachweisbar werden soll.
6. **Tests**: extract-Function Property-Test, mask-Function mit Edge-Cases (longest-first, leerer Secret, partielles Match), Verdrahtungs-Source-Audit, End-to-End mit Mock-Sandbox.
7. **Was P212 NICHT macht**: vollstaendige `.env`-Encryption (eigener Patch), Container-Isolation (P171/P203c reicht), Multi-Pass-LLM-Filter (eine Maskierung am Output reicht — der LLM sieht den Secret nie).

Alternativ falls P212 zu schmal: **P213 Reasoning-Schritte sichtbar im Chat (Ziel #13)** — Mobile-first Anzeige der Zwischenschritte des Agenten. SSE-Stream oder polling-basiert, Frontend-Card im Nala-Chat.

**Reihenfolge-Vorschlag** (Coda darf abweichen):
- **P212** Secrets bleiben geheim — Ziel #12
- **P213** Reasoning-Schritte sichtbar im Chat — Ziel #13
- **P214** Wiederkehrende Jobs / Scheduler — Ziel #14

Helper aus P211 die in P212/P213 direkt nutzbar sind:
- **`vram_slot`-Pattern** — Pure-Function-Trigger + Async-Wrapper-Acquire mit FIFO-Queue + Audit-Tabelle. Klont sich auf jede Resource-Lock-Anwendung (CPU-Slot, Disk-IO-Slot, API-Rate-Limit). Bei Reasoning-Schritten in P213 ggf. relevant fuer SSE-Buffer-Limits.
- **Status-Endpoint-Pattern** — auth-freier `GET /v1/<thing>/status` mit JSON-Snapshot, Frontend pollt mit `setInterval` + `cache: 'no-store'`. Klont sich auf jeden Live-Status-Bedarf (Reasoning-Steps, Cron-Job-Status, etc.).
- **Hel-Toast-Pattern** — CSS analog `.rag-toast`/`.gpu-toast` (fixed-positioned, fade-in via `.visible`-Klasse, 44px-Touch-Target, `pointer-events: none` im Hidden-State). DOM-Element + JS-IIFE mit Polling. Fuer jeden zukuenftigen Live-Status auf Hel-Seite recyclebar.
- **Verdrahtungs-Source-Audit-Test-Pattern** — `'vram_slot("whisper"' in src`-Heuristik aus P211, kombiniert mit `test_imports_vram_slot`. Klont sich fuer jeden Cross-Cutting-Concern (Audit, Lock, Log) — verhindert dass die naechste Coda-Instanz die Verdrahtung beim Refactor versehentlich rausschreibt.
- **`reset_global_queue_for_tests()`-Pattern** — Singleton + Reset-Helper + autouse-Fixture fuer Test-Isolation. Lesson aus P206/P208 (`ChatHitlGate`/`ChatSpecGate`) auf Resource-Pools uebertragen.

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL-Telegram (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), Projekte-Backend (P194), Projekte-UI (P195), Projekt-Datei-Upload (P196), Persona-Merge-Layer (P197), Projekt-Templates (P198), Projekt-RAG-Index (P199), PWA-Verdrahtung Nala + Hel (P200), Nala-Tab "Projekte" + Header-Setter (P201), PWA-Auth-Hotfix (P202), Project-Workspace-Layout (P203a), Hel-UI-Hotfix Event-Delegation (P203b), Sandbox-Workspace-Mount + execute_in_workspace (P203c), Prosodie-Kontext im LLM (P204), Code-Detection + Sandbox-Roundtrip im Chat (P203d-1), Output-Synthese fuer Sandbox-Code-Execution (P203d-2), UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (P203d-3), RAG-Toast in Hel-UI (P205), HitL-Gate vor Code-Execution + Audit-Tabelle code_executions + HANDOVER-Teststand-Konvention (P206), Workspace-Snapshots + Diff-View + Rollback + workspace_snapshots-Tabelle + sandbox_writable/snapshots_enabled-Flags + POST /v1/workspace/rollback (P207), Spec-Contract / Ambiguitäts-Check + spec_check.py-Modul + clarifications-Tabelle + spec_check_enabled/threshold/timeout-Flags + GET /v1/spec/poll + POST /v1/spec/resolve + Klarstellungs-Karte mit Textarea + drei Decision-Werten (P208), Zweite Meinung vor Ausführung / Sancho Panza + code_veto.py-Modul + code_vetoes-Tabelle + code_veto_enabled/temperature-Flags + Wandschlag-Banner mit roter Border + read-only Veto-Card + drei Verdict-Werten pass/veto/skipped/error (P209), Huginn-RAG-Auto-Sync + tools/sync_huginn_rag.py-Modul (Pure-Function build_sync_plan/validate_doc_header/extract_current_patch/parse_auth_string/load_auth_from_env/resolve_base_url + Async-Wrapper execute_sync_plan mit httpx + CLI mit dry-run + scripts/sync_huginn_rag.ps1-Wrapper + Stand-Anker-Pflicht-Header in huginn_kennt_zerberus.md + Doku-Pflicht-Tabellenzeile in WORKFLOW.md + Phase-5a-Ziel #18 + 53 Tests in test_p210_huginn_rag_sync.py — P210), **GPU-Queue fuer VRAM-Konsumenten + zerberus/core/gpu_queue.py-Modul (Pure-Function compute_vram_budget/should_queue + GpuSlotInfo-Dataclass + GpuQueue-Singleton mit asyncio.Lock + FIFO-Waiter-Liste + Token-Bucket + vram_slot-Convenience-Context-Manager) + gpu_queue_audits-Tabelle + Verdrahtung in whisper_client/gemma_client/projects_rag/rag-router/orchestrator + GET /v1/gpu/status-Endpoint + Hel-Frontend-Toast `.gpu-toast` mit 4s-Polling + 40 Tests in test_p211_gpu_queue.py — P211)**.

Helper aus P210/P209/P208/P207/P206 die in P211 schon recycelt wurden: Pure-Function-vs-Async-Wrapper-Trennung (analog `should_run_veto` + `run_veto` → `should_queue` + `_acquire`), Singleton-mit-Reset-Helper-Pattern (analog `ChatHitlGate`/`ChatSpecGate` → `GpuQueue` mit `reset_global_queue_for_tests`), Source-Audit-Test-Pattern (analog `TestDocSourceAudit` → `TestWhisperWiring`/`TestGemmaWiring`/etc.), Best-Effort-Audit-Insert-Pattern (analog `store_veto_audit` → `store_gpu_queue_audit`), Toast-Pattern fuer Hel-Frontend (analog `.rag-toast` aus P205 → `.gpu-toast` mit anderer Position).

Helper aus P205/P203d-3/P203d-2/P203d-1/P204: siehe vorherige HANDOVERs. `addMessage`-Wrapper-Return, `escapeHtml`, `renderCodeExecution`-Pattern, `.code-card`/`.output-card`-CSS, `code_execution`-Dict-Schema, Marker-Disjunktheit, RAG-Toast-Pattern, JS-Integrity-Test.

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#94-96 (P211: GPU-Queue-Stress mit parallel Whisper+Gemma+Embedder, Toast-Sichtbarkeit, Timeout-Reaktion bei kuenstlich blockiertem Slot)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 2216 passed (+40 P211 aus 2176 baseline)**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Zeile 1653 + 3096) — wie zuvor.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat einen `node --check`-Pass** seit P203d-3 — abgedeckt durch `TestJsSyntaxIntegrity`-Tests in P203d-3 + P206 + P207 + P208 + P209 + **P211** (skipped wenn `node` nicht im PATH).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **P207 Storage-GC fuer alte Snapshot-Tars** — wie zuvor.
- **P207 Hardlink-Snapshots statt Tar** — wie zuvor.
- **P207 Per-File-Rollback** — wie zuvor.
- **P207 Cross-Project-Diff** — bewusst weggelassen.
- **P207 Branch-Mechanik** — bewusst weggelassen. Linear forward/reverse only.
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
- **P210: Multi-Datei-Sync nicht implementiert.** Trivial erweiterbar (Liste statt einzelner Pfad).
- **P210: Server-Status-Check vorab fehlt.** Wenn der Server down ist, schlaegt der erste HTTP-Call eh fehl.
- **P210: Cron / Schedule-Integration fehlt.** Coda macht das am Session-Ende, nicht zeitgesteuert.
- **P210: Auto-Bumping der Patchnummer im Header fehlt.** Coda muss den Header bewusst aktualisieren (Doku-Pflicht).
- **P210: SUPERVISOR_ZERBERUS.md ist auf 928 Zeilen gewachsen** (Ziel <400) — bleibt als eigene Schuld auf dem Stack. Saubere Variante: alte Patches in `SUPERVISOR_ZERBERUS_archive.md` auslagern.
- **NEU P211: Audit-Tabelle ist da, aber Hel-UI-Auswertung fehlt.** `gpu_queue_audits` sammelt Daten, aber niemand zeigt sie. Ein Hel-Tab "GPU-Queue" mit `SELECT consumer_name, AVG(wait_ms), MAX(wait_ms), COUNT(*) FILTER (WHERE timed_out=1) FROM gpu_queue_audits GROUP BY consumer_name` waere ein eigener kleiner Patch (P211b).
- **NEU P211: Statisches Budget koennte sich aendern.** Wenn Embedder oder Reranker auf groessere Modelle umgezogen werden (z.B. E5-Mistral-7B-Instruct), reicht das 1-GB-Budget fuer den Embedder nicht mehr. Der Audit-Trail wird das zeigen — Anpassung dann in `VRAM_BUDGET_MB`.
- **NEU P211: Whisper-Slot bei Docker auf separater GPU.** Falls Whisper irgendwann auf eine zweite GPU (oder einen anderen Server) umgezogen wird, kollidiert er nicht mehr mit den Lokalen — der Whisper-Slot wuerde dann unnoetig blocken. Aktuell: einfach `KNOWN_CONSUMERS` updaten oder Whisper-Slot ganz weglassen.
- **NEU P211: Sync-Variante des Slots fehlt.** Falls in Zukunft sync-Code direkt VRAM braucht (z.B. ein script das ausserhalb des FastAPI-Loops laeuft), muss eine `with sync_vram_slot(...)`-Variante mit `threading.Lock` gebaut werden. Aktuell nicht noetig — alle Konsumenten sind in async-Pfaden.
- **NEU P211: Toast nur in Hel, nicht in Nala.** Bewusste Audience-Trennung. Wenn der User irgendwann auch im Nala-Chat sehen will warum die Antwort dauert, waere ein zweiter `.gpu-toast`-Block in `nala.py` ein eigener Patch.
- **NEU P211: Per-Consumer-Reservierung statt globaler Queue.** Aktuell teilen sich alle Konsumenten denselben Pool. Falls jemand Whisper-Calls priorisieren will (z.B. "Voice-Input geht immer vor"), waere ein `priority`-Feld auf `vram_slot` denkbar. Bewusst weggelassen — fairness > performance bei Single-User.
- **NEU P211: Cancel-Outstanding-Slots fehlt.** Wenn ein Caller den `await` abbricht (z.B. Client-Disconnect mit asyncio-CancelledError), bleibt der Future in `_waiters` haengen und wird nie geweckt. Tests zeigen das nicht — bei realer Last muss man pruefen, ob Cancel-Cleanup noetig ist.
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
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt (P203d-1) | Output-Synthese im Chat-Endpunkt (P203d-2) | UI-Render im Nala-Frontend (P203d-3) | RAG-Toast in Hel-UI (P205) | HitL-Gate vor Sandbox-Code-Execution: `ChatHitlGate`-Singleton, `/v1/hitl/poll`+`/v1/hitl/resolve`, Confirm-Karte, `code_executions`-Audit-Tabelle (P206) | Workspace-Snapshots + Diff + Rollback: `projects_snapshots.py` mit Pure-Function-`build_workspace_manifest`+`diff_snapshots`+`_is_safe_member`-Tar-Defense, neue `workspace_snapshots`-Tabelle, `[SNAPSHOT-207]`-Logging-Tag (P207) | Spec-Contract / Ambiguitäts-Check vor Haupt-LLM-Call: `spec_check.py` mit Pure-Function-Heuristik + `ChatSpecGate`-Singleton + drei Decision-Werten answered/bypassed/cancelled + `clarifications`-Tabelle + `[SPEC-208]`-Logging-Tag (P208) | Zweite Meinung vor Ausführung / Sancho Panza vor HitL-Gate: `code_veto.py` mit Pure-Function-`should_run_veto` + `build_veto_messages` + `parse_veto_verdict` + Async-Wrapper `run_veto` mit `temperature=0.1` + fail-open + `code_vetoes`-Tabelle + `[VETO-209]`-Logging-Tag (P209) | Huginn-RAG-Auto-Sync via `tools/sync_huginn_rag.py`-Modul mit Pure-Function `build_sync_plan` + Async-Wrapper `execute_sync_plan` (httpx) + CLI mit dry-run + Stand-Anker-Pflicht-Header in `docs/huginn_kennt_zerberus.md` + Idempotenz bei DELETE-404 + DELETE→UPLOAD-Reihenfolge-Invariante + `[SYNC-210]`-Logging-Tag + Marathon-Push-Zyklus eingebaut (P210) | **GPU-Queue fuer VRAM-Konsumenten via `zerberus/core/gpu_queue.py`-Modul mit Pure-Function `compute_vram_budget`/`should_queue` (statisches Budget Whisper=4 GB / Gemma=2 GB / Embedder=1 GB / Reranker=512 MB, `TOTAL_VRAM_MB=11000`) + `GpuSlotInfo`-Dataclass mit computed `wait_ms`/`held_ms` + globalem `GpuQueue`-Singleton mit asyncio.Lock + FIFO-Waiter-Liste + `vram_slot(consumer_name, *, timeout=30.0)`-Convenience-Context-Manager + `gpu_queue_audits`-Tabelle + Best-Effort-Audit-Insert + `[GPU-211]`-Logging-Tag + auth-freier `GET /v1/gpu/status`-Endpoint + Hel-Frontend-Toast `.gpu-toast` mit 4s-Polling und Whitelist `whisper|gemma|embedder|reranker` (P211)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`) | Slug ist immutable nach Anlage | Atomic Write für jeden Upload-Pfad | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge (P197) | Pure-Function vs. DB-Schicht trennen | Templates als reguläre `project_files`-Einträge (P198) | Idempotenz-Check VOR Schreiben für jeden Generator (P198) | Best-Effort-Verdrahtung im Endpoint (P198) | Per-Projekt-RAG-Index isoliert (P199) | Embedder-Wrapper als monkeypatchbare Funktion (P199) | Best-Effort-Indexing (P199) | RAG-Block-Marker `[PROJEKT-RAG]` (P199) | PWA-Endpoints auth-frei in separatem Router VOR auth-gated Hel-Router (P200) | SW-Scope folgt dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch (P200) | `persona_overlay` NIEMALS in User-sichtbarer Response (P201) | Header-Injektion fuer Cross-Cutting zentral in `profileHeaders()` (P201) | Zombie-ID-Schutz nach List-Refresh (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test (P201+P203d-3+P206+P207+P208+P209) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig (P203a) | Hardlink primär, Copy als Fallback (P203a) | Atomic-Write-Pattern auch fuer Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat ueber benutzergenerierte Daten ist verboten — IMMER `data-*` + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehoert in jede Test-Suite die HTML in Python-Source baut (P203b+P203d-3+P205+P206+P207+P208+P209+**P211**) | Prosodie-Bruecken-Block enthaelt NIEMALS numerische Werte (P204) | Mehrabian-Schwellen identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Bruecken-Bloecke MUESSEN substring-disjunkt sein (P204+P203d-2+P208 KLARSTELLUNG) | Sandbox-Workspace-Mount: Read-Only ist konservativer Default (P203c+P207) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte konservativ geblockt — `project_overlay is not None` als Diskriminator (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollstaendiges Sandbox-Result liegt vor (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open (P203d-1) | Two-stufige LLM-Pipelines: `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren (P203d-2) | Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (P203d-2+P209 should_run_veto) | Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding (P203d-2+P208 SPEC_ANSWER_MAX_BYTES+P209 VETO_CODE_MAX_BYTES/REASON_MAX_BYTES) | Synthese-LLM-Pipeline ist fail-open auf jeder Stufe (P203d-2) | Frontend-Renderer fuer additive Backend-Felder muss BACKWARDS-COMPAT bleiben (P203d-3+P205+P206+P207+P209 veto-Feld) | DOM-Insertion-Funktionen retournen Wrapper-Element (P203d-3) | Frontend-Renderer fuer User-/LLM-Strings: jedes innerHTML-Statement muss einen `escapeHtml(`-Aufruf enthalten (P203d-3+P206+P207) | Frontend-Renderer fuer User-Strings DARF auch `textContent` statt `innerHTML` nutzen (P208 .spec-card+P209 .veto-card+**P211 .gpu-toast**) | Mobile-first 44x44px Touch-Target gilt fuer JEDEN neuen Toggle/Button im Frontend (P203d-3+P206+P207+P208+P209+**P211 .gpu-toast min-height:44px**) | Status-Toasts: `textContent` statt `innerHTML` fuer Reason-Strings (P205+**P211**) | Toast-Replace-Pattern: ein einziger DOM-Container plus CSS-State-Toggle (P205+**P211**) | `pointer-events: none` im Hidden-State von fixed-positionierten Toasts/Overlays (P205+**P211**) | HitL-Pending-IDs sind UUID4-hex (32 chars), Cross-Session-Defense via Session-Match im Resolve, niemals Integer-IDs (P206+P208) | HitL-Pendings im Chat-Pfad sind transient (In-Memory), nicht persistiert (P206+P208 ChatSpecGate) | Synthese-LLM-Calls (P203d-2) DUERFEN NICHT auf `skipped=True`-Payloads laufen (P206+P209 vetoed) | Audit-Trail-Schreibungen sind Best-Effort: jeder Crash darf den Hauptpfad nicht blockieren (P206+P207+P208 store_clarification_audit+P209 store_veto_audit+**P211 store_gpu_queue_audit**) | HANDOVER-Header enthaelt die Zeile `**Manuelle Tests:** X / Y ✅` aus der WORKFLOW-Tabelle, ohne Reminder-Text oder Bewertung (P206-Konvention) | Sandbox-Writable-Mount: Default `projects.sandbox_writable=False` (P207) | Snapshot-Tars unter `<data>/projects/<slug>/_snapshots/<uuid4-hex>.tar`, atomar via Tempname + `os.replace` (P207) | Tar-Member-Validation `_is_safe_member` MUSS bei jedem Restore aufgerufen werden (P207) | Cross-Project-Defense: `rollback_snapshot_async` MUSS `expected_project_id` gegen Snapshot-Eigentuemer pruefen (P207) | JavaScript-String-Literale mit Newline (`\\n`) DUERFEN NICHT direkt aus Python-Source kommen — `String.fromCharCode(10)` als Workaround (P207) | Workspace-Snapshot-Pfad ist additiv und opt-in (P207) | Spec-Contract-Pfad ist additiv und opt-in (P208) | Spec-Probe-LLM-Call MUSS isoliert sein (Persona-frei, kein RAG, kein Prosody-Block) (P208+P209 Veto-Probe analog) | Drei Decision-Werte answered/bypassed/cancelled (P208) | Source-Detection ueber `X-Prosody-Context` + `X-Prosody-Consent: true` (P204-Konvention) — Voice-Bonus +0.20 im Score (P208) | `[KLARSTELLUNG]`-Marker substring-disjunkt zu allen anderen LLM-Markern (P208) | `enrich_user_message`-Pfad: bei `answered` MUSS sowohl `last_user_msg` als auch `req.messages[-letzter user-msg].content` aktualisiert werden (P208) | Veto-Pfad ist additiv und opt-in (P209) | Veto-Probe-LLM-Call MUSS isoliert sein (P209) | Bei VETO schreibt der Endpunkt KEINE Zeile in `code_executions`, Audit landet ausschliesslich in `code_vetoes` (P209) | Veto-Verdicts: `pass`/`veto`/`skipped`/`error` (P209) | Wandschlag-Banner ist read-only Audit-Spur — KEIN Approve-Button (P209) | Veto-LLM-Call ist deterministisch via `temperature_override=0.1` (P209) | Verdict-Parser MUSS robust gegen Markdown-Bold/Quotes/Doppelpunkte/Bindestriche/Lowercase/Multi-Line-Reasons sein (P209) | `docs/huginn_kennt_zerberus.md` MUSS einen `## Aktueller Stand`-Block ganz oben tragen mit `**Letzter Patch:** P###`-Zeile (P210) | Sync-Reihenfolge ist DELETE → UPLOAD invariant (P210) | Sync-Tool-DELETE muss 404 als Erfolg akzeptieren (P210) | Coda-Verantwortung: am Session-Ende `python -m tools.sync_huginn_rag` aufrufen VOR `sync_repos.ps1` (P210) | Spiegel-Kopie unter `docs/RAG Testdokumente/huginn_kennt_zerberus.md` ist NUR Test-Material, NICHT im Live-RAG (P210) | **GPU-Queue: VRAM-Konsumenten MUESSEN via `async with vram_slot("<name>"): ...` serialisiert werden — `whisper`/`gemma`/`embedder`/`reranker` sind die Whitelist, alles andere triggert `DEFAULT_CONSUMER_BUDGET_MB`-Warning (P211)** | **GPU-Queue: Statisches VRAM-Budget in `VRAM_BUDGET_MB`-Dict, NIE via `nvidia-smi`-Polling — robust gegen Treiber-Anomalien und unabhaengig von CUDA-Headern (P211)** | **GPU-Queue: FIFO-Reihenfolge mit Head-of-Line-Block by design — strikte Wartefolge verhindert Starvation, ein cleverer Best-Fit-Scheduler waere Overkill bei 4 Konsumenten (P211)** | **GPU-Queue: Audit-Tabelle `gpu_queue_audits` ist die Validierungs-Grundlage der Budget-Werte — `SELECT consumer_name, AVG(wait_ms), MAX(wait_ms) FROM gpu_queue_audits GROUP BY consumer_name` zeigt nach 1-2 Wochen wo die Annahmen daneben liegen (P211)** | **GPU-Queue: Hel-Toast statt Nala-Toast — System-Status gehoert ins Admin-Frontend, nicht ins User-Frontend (P211)**
