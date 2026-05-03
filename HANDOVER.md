## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-03
**Letzter Patch:** P209 — Zweite Meinung vor Ausführung / Sancho Panza (Phase 5a Ziel #7 ABGESCHLOSSEN)
**Tests:** 2123 passed (+88 P209 aus 2035 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 1 skipped (existing)
**Manuelle Tests:** 1 / 88 ✅
**Commit:** _wird nach Push nachgetragen_
**Repos synchron:** _wird nach sync_repos.ps1 + verify_sync.ps1 nachgetragen_

---

## Zuletzt passiert (1 Patch in dieser Session)

**P209 — Zweite Meinung vor Ausführung / Sancho Panza (Phase 5a Ziel #7 ABGESCHLOSSEN).** Veto-Logik VOR dem HitL-Gate aus P206. Ein zweites LLM (gleicher Provider, `temperature=0.1` deterministisch) bewertet den vom Haupt-Modell generierten Code-Vorschlag mit `PASS` oder `VETO` plus optional kurzer Begruendung. Bei VETO landet der Code im Wandschlag-Banner (rote Border, prominente Begruendung, kein Approve-Button) — kein HitL-Pending, kein Sandbox-Run, kein Snapshot. Bei PASS laeuft der bestehende P206/P207-Pfad weiter. Bei trivialen 1-Zeilern (print/return/var/pass/console.log) ohne Risk-Tokens skipt eine Pure-Function-Heuristik den Veto-LLM-Call (Token-Spar) und auditiert nur "skipped".

**Was P209 baut:**

1. **Neues Modul** [`zerberus/core/code_veto.py`](zerberus/core/code_veto.py) mit drei Schichten:
   - **Pure-Function** — `should_run_veto(code, language) -> bool` Trigger-Gate (leerer Code → False; trivialer 1-Zeiler ohne Risk-Token → False; Multiline ODER Risk-Token → True; Borderline-1-Zeiler ohne Trivial-Pattern → True). `_RISKY_TOKENS`-Liste mit Substring-Match (`subprocess`/`os.system`/`eval(`/`exec(`/`rm -rf`/`unlink`/`shutil.rmtree`/`open(`/`requests.post`/`urllib.request`/`httpx.post`/`fetch(`/`child_process`/`sudo`/`chmod`/`git push --force`/`--no-verify`/`pickle.load`/`yaml.load(`/`fs.unlink`). `_TRIVIAL_PATTERNS` mit kompilierten Regex fuer print/return/pass/console.log/var-assign. `build_veto_messages(code, language, user_prompt) -> List[dict]` baut zwei-Element-Liste mit `VETO_SYSTEM_PROMPT` der GENAU "PASS" oder "VETO" am Anfang verlangt + optional Begruendung im selben String, plus `user`-Message mit User-Wunsch + Code-Vorschlag (Sprache normalisiert lowercase, Code auf `VETO_CODE_MAX_BYTES=4000` truncated). `parse_veto_verdict(text) -> VetoVerdict` mit Regex `^[\s"'\`*_]*(PASS|VETO)\b\s*[:\-—]?\s*(.*)$` (case-insensitive), Multi-Line-Reason (erste Zeile + folgende bis Leerzeile), 64-char-Window-Fallback wenn First-Line nicht matchet, fail-open zu `PASS` bei unparseable Output (`error="parse_failed"`), `_truncate_reason` auf `VETO_REASON_MAX_BYTES=400`.
   - **Async-Wrapper** — `run_veto(code, language, user_prompt, llm_service, session_id, *, temperature=0.1) -> VetoVerdict` ruft `llm_service.call` mit `temperature_override=temperature`, fail-open auf jeder Stufe (LLM-Crash → `VetoVerdict(veto=False, error="llm_call_failed: ...")`, Non-Tuple → `unexpected_type`, leere/Non-String-Antwort → `empty_response`), `latency_ms` per `datetime.utcnow()`-Diff gemessen.
   - **VetoVerdict-Dataclass** mit `veto: bool`, `reason: str`, `raw: Optional[str]`, `latency_ms: Optional[int]`, `error: Optional[str]`, plus `to_payload_dict() -> {"vetoed": bool, "reason": str, "latency_ms": int|None}` als Frontend-Schema.

2. **Neue DB-Tabelle** `code_vetoes` in [`zerberus/core/database.py`](zerberus/core/database.py): Spalten `id` (PK), `audit_id` (UUID4 hex INDEX, eigene ID statt HitL-Pending-Korrelation), `session_id`, `project_id` (INDEX), `project_slug`, `language`, `code_text`, `user_prompt`, `verdict` (`pass|veto|skipped|error` INDEX), `reason`, `latency_ms`, `created_at`. Persistente Spur fuer System-Prompt-Tuning. `store_veto_audit(...)` Best-Effort, jeder Fehler wird geloggt + verschluckt, 8 KB Truncate.

3. **Verdrahtung in** [`zerberus/app/routers/legacy.py`](zerberus/app/routers/legacy.py)`::chat_completions` zwischen `first_executable_block` (P203d-1 Code-Detection) und HitL-Pending-Erzeugung (P206):
   - Bei `code_veto_enabled=True` UND `should_run_veto(code, language)=True`: `run_veto(...)` mit `settings.projects.code_veto_temperature` (Default 0.1).
   - **Drei Decision-Pfade**: `verdict.veto=True` → `_veto_skip_hitl_and_sandbox=True`, `code_execution_payload` mit `skipped=True`/`hitl_status="vetoed"`/`veto={vetoed, reason, latency_ms}`-Sub-Field; HitL/Sandbox/Snapshot werden uebersprungen. `verdict.veto=False` (PASS) → `veto_status_for_audit="pass"`, weiter zum HitL-Gate. `verdict.error` → `veto_status_for_audit="error"`, weiter zum HitL-Gate (fail-open). `should_run_veto=False` → `veto_status_for_audit="skipped"`, weiter zum HitL-Gate.
   - **Audit-Schreiben** am Ende des Hauptpfades. Bei `code_veto_enabled=False` bleibt `veto_status_for_audit=None` und kein Audit wird geschrieben — der Veto-Pfad existiert in dem Fall faktisch nicht.
   - **Bei VETO** schreibt der Endpunkt **keine** Zeile in `code_executions` (P206-Tabelle), weil HitL nicht lief — Audit landet ausschliesslich in `code_vetoes`. Korrelation zwischen den beiden Tabellen ueber `session_id`/`project_id`.

4. **Nala-Frontend** in [`zerberus/app/routers/nala.py`](zerberus/app/routers/nala.py):
   - **CSS-Block** (~85 Zeilen): `.veto-card` mit roter Border (`rgba(229,115,115,0.55)`), `.veto-card-header` (Icon `🛑 Veto vom zweiten Modell` + Sprach-Tag), `.veto-reason` prominent (`white-space: pre-wrap`), `.veto-meta` italic Latency, `.veto-code-toggle` mit `min-height: 44px` (Mobile-first **read-only** — kein Approve-Button), `.veto-code-block` collapsible mit `overflow-x: auto`/`max-height: 280px`, `.veto-card.veto-collapsed` Default-State faltet Code ein. Kein Post-Klick-State weil read-only Audit-Spur.
   - **JS-Funktion** `renderVetoCard(wrapperEl, codeExec, triptych)` analog `renderDiffCard` aus P207 — User-/LLM-Strings via `textContent` (XSS-safe by default), Code-Block via `<code>${escapeHtml(codeStr)}</code>` mit `<pre>`-Wrapping. Insertion vor dem Triptychon — Visual-Order: bubble → veto-card → triptych → export-row (OHNE Code-Card und Output-Card weil Veto-Pfad).
   - **`renderCodeExecution`-Erweiterung**: ganz frueh `if (vetoInfo && vetoInfo.vetoed === true) { renderVetoCard(...); return; }` (early-exit). Bei `vetoed=false` oder fehlendem Veto-Field laeuft der bestehende Code-Card/Output-Card/Diff-Card-Pfad unveraendert weiter — Backwards-Compat zu P203d-3/P206/P207.

5. **Neue Feature-Flags** in [`zerberus/core/config.py`](zerberus/core/config.py)`::ProjectsConfig`:
   - `code_veto_enabled: bool = True` — Master-Switch (off → kein Probe, kein Audit, kein Block in der Response)
   - `code_veto_temperature: float = 0.1` — deterministisch fuer wiederholbare Verdicts

**Tests:** 88 in [`test_p209_code_veto.py`](zerberus/tests/test_p209_code_veto.py).

`TestShouldRunVeto` (13) — leer/trivial-print/return/var/pass/multiline/subprocess/eval/rm-rf/open-write/requests-post/long-oneliner/none-language.

`TestRiskyTokens` (6) — subprocess/eval/no-risky/case-insensitive/force-push/no-verify.

`TestTrivialOneliner` (5) — print/multiline/long-line/pass/return.

`TestBuildVetoMessages` (7) — two-messages/system-pass-veto/user-code-lang/lang-lower/empty-lang/long-truncate/no-persona-leak.

`TestParseVetoVerdict` (13) — pass/veto/dash/lowercase/markdown-bold/quoted/pass-reason-ignored/unparseable-fail-open/empty/multiline-reason/64char-fallback/long-reason-truncate.

`TestVetoVerdict` (2) — payload-dict-pass/payload-dict-veto.

`TestRunVeto` (8) — happy-pass/happy-veto/temperature-passed/default-low/llm-crash/empty/non-tuple/non-string.

`TestStoreVetoAudit` (3) — happy/truncate/no-db.

`TestLegacySourceAudit` (10) — Logging-Tag, Imports, Audit-Aufruf, Feature-Flag, Temperature-Param, Reihenfolge-vor-HitL, Skip-Var, Veto-Field-im-Payload, hitl_status=vetoed, fail-open.

`TestNalaSourceAudit` (8) — renderVetoCard-Funktion, Aufruf-in-renderCodeExecution, early-Return, CSS-Klassen, rote Border, 44px-Touch, kein Approve-Button, textContent/escapeHtml, veto-collapsed-State.

`TestE2EVeto` (5) — veto-blockt-sandbox / pass-zur-sandbox / trivial-skipt-veto / disabled-skipt-audit / veto-keine-hitl-pending.

`TestJsSyntaxIntegrity` (1, skipped wenn node fehlt) — `node --check` ueber alle inline `<script>`-Bloecke aus NALA_HTML.

`TestSmoke` (4) — Config-Flags, default-temperature-low, `code_vetoes`-Tabelle, Module-Exports.

**Logging-Tag:** `[VETO-209]` mit `decision veto=... reason_len=... code_len=... lang=... session=... latency_ms=...` / `blocked session=... language=... reason_len=...` / `audit_written session=... project_id=... verdict=... language=... latency_ms=...` / `Pipeline-Fehler (fail-open): ...` / `llm_call_failed (fail-open): ...` / `llm_returned_unexpected_type type=...`. Worker-Protection-konform: kein Klartext aus Code/Begruendung/User-Prompt im Log, nur Längen-Metriken.

**Kollateral-Fix:** `test_p203d2_chat_synthesis.py::_setup` setzt jetzt zusätzlich `monkeypatch.setattr(get_settings().projects, "code_veto_enabled", False)` — sonst würde der neue Veto-Pfad (Default ON) bei nicht-trivialen Code-Blöcken einen dritten LLM-Call triggern und den 2-Step-Mock-LLM-Test brechen. Plus: `test_p207_workspace_snapshots.py::TestNalaSourceAudit::test_render_code_execution_calls_diff_renderer` und `test_diff_render_skipped_when_skipped_payload` haben ihr Source-Audit-Window von 6000 auf 7500 Zeichen erhöht — der `renderCodeExecution`-Body ist um den Veto-Early-Return + Kommentar gewachsen.

## Nächster Schritt — sofort starten

**P210: GPU-Queue für VRAM-Konsumenten (Phase 5a Ziel #11).** Kooperatives Scheduling zwischen Whisper, Gemma, Embeddings und Reranker. Pure-Token-Bucket-Pattern + asyncio.Lock pro VRAM-Slot, fail-fast bei Overrun. Ziel: keine GPU-Crashes mehr durch parallele Modell-Loads, klare Wartezeit-Anzeige im Frontend.

**Konkret (Coda darf abweichen):**

1. **Pure-Function-Schicht** in neuem Modul `zerberus/core/gpu_queue.py`: `compute_vram_budget(consumer_name) -> int` (statische Tabelle: Whisper=4 GB, Gemma=2 GB, Embeddings=1 GB, Reranker=512 MB). `should_queue(active_total, requested) -> bool` (wenn `active_total + requested > VRAM_TOTAL_MB`).
2. **Async-Wrapper** `acquire_vram_slot(consumer_name, *, timeout=30)` mit `asyncio.Lock`-Pool pro Slot + Token-Bucket. Fail-fast bei Timeout.
3. **Verdrahtung** in den Whisper-/Gemma-/Embedding-/Reranker-Pfaden — wrap each model call mit `async with vram_slot(consumer_name): ...`.
4. **Frontend-Toast** "GPU wartet auf Whisper, Position 2 in Queue..." (analog P205 RAG-Toast).
5. **Audit-Tabelle** `gpu_queue_audits` mit `consumer_name`/`wait_ms`/`queue_position`/`created_at`.
6. **Tests**: Token-Bucket-Boundary, Lock-Acquire-Release, Timeout, Source-Audit, JS-Integrity.
7. **Was P210 NICHT macht**: dynamische VRAM-Erkennung via `nvidia-smi` (statisches Budget reicht), Mehr-GPU-Verteilung (genug fuer eine RTX 3060), Cancel-Outstanding-Slots (FIFO).

Alternativ falls P210 zu viel: **P211 Secrets bleiben geheim (Ziel #12)** — `.env`-Encrypt + Sandbox-Injection-Filter + Output-Maskierung. Schmaler Scope, fokussiert auf den ein-Variable-Fall (`OPENAI_API_KEY` in `.env` darf nie im Sandbox-Output landen).

**Reihenfolge-Vorschlag** (Coda darf abweichen):
- **P210** GPU-Queue für VRAM-Konsumenten — Ziel #11
- **P211** Secrets bleiben geheim — Ziel #12
- **P212** Reasoning-Schritte sichtbar im Chat — Ziel #13

Helper aus P209 die in P210/P211/P212 direkt nutzbar sind:
- **`code_vetoes`-Tabelle** mit `audit_id`/`session_id`/`project_id`/`verdict`/`latency_ms`-Korrelation als Audit-Vorlage fuer `gpu_queue_audits` (P210) oder `secret_redactions` (P211).
- **`.veto-card`-CSS-Pattern** (rote Border + Read-Only + collapsible Code-Toggle mit 44x44px) klont sich trivial zu `.gpu-queue-card` (P210 Wartezeit-Anzeige) oder `.secret-mask-card` (P211 maskierte Outputs).
- **`renderVetoCard`-Pattern** (frueher `return` in `renderCodeExecution` + textContent-only-Render + kein Approve-Button) ist die Vorlage fuer jede neue **Read-only Audit-Karte**.
- **`parse_veto_verdict`-Pattern** (First-Line-Match + Multi-Line-Reason + Fail-open-Fallback + Bytes-Truncate) ist universell fuer jeden LLM-Verdict-Parser.
- **`should_run_veto`-Heuristik-Set** ist erweiterbar (neue Risk-Tokens einbauen, Tests parametrisieren) ohne API-Bruch — P211 kann fuer `should_redact_secret` denselben Stil uebernehmen.

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL-Telegram (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), Projekte-Backend (P194), Projekte-UI (P195), Projekt-Datei-Upload (P196), Persona-Merge-Layer (P197), Projekt-Templates (P198), Projekt-RAG-Index (P199), PWA-Verdrahtung Nala + Hel (P200), Nala-Tab "Projekte" + Header-Setter (P201), PWA-Auth-Hotfix (P202), Project-Workspace-Layout (P203a), Hel-UI-Hotfix Event-Delegation (P203b), Sandbox-Workspace-Mount + execute_in_workspace (P203c), Prosodie-Kontext im LLM (P204), Code-Detection + Sandbox-Roundtrip im Chat (P203d-1), Output-Synthese fuer Sandbox-Code-Execution (P203d-2), UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (P203d-3), RAG-Toast in Hel-UI (P205), HitL-Gate vor Code-Execution + Audit-Tabelle code_executions + HANDOVER-Teststand-Konvention (P206), Workspace-Snapshots + Diff-View + Rollback + workspace_snapshots-Tabelle + sandbox_writable/snapshots_enabled-Flags + POST /v1/workspace/rollback (P207), Spec-Contract / Ambiguitäts-Check + spec_check.py-Modul + clarifications-Tabelle + spec_check_enabled/threshold/timeout-Flags + GET /v1/spec/poll + POST /v1/spec/resolve + Klarstellungs-Karte mit Textarea + drei Decision-Werten (P208), **Zweite Meinung vor Ausführung / Sancho Panza + code_veto.py-Modul + code_vetoes-Tabelle + code_veto_enabled/temperature-Flags + Wandschlag-Banner mit roter Border + read-only Veto-Card + drei Verdict-Werten pass/veto/skipped/error (P209)**.

Helper aus P208/P207/P206 die in P209 schon recycelt wurden: `audit_id`-Korrelationsschluessel-Pattern (in `code_vetoes`, eigene UUID4 statt HitL-Pending-Korrelation), `.diff-card`-CSS-Vokabular (Border, Action-Footer, Post-Klick-State — kopiert zu `.veto-card` mit roter Border statt gold), `renderDiffCard`-Pattern (Render mit textContent + escapeHtml + Insertion vor Triptychon — kopiert zu `renderVetoCard`), `node --check`-JS-Integrity-Test-Pattern, `escapeHtml(`-Audit-Test (kopiert), Bytes-genau-Truncate-Pattern aus P208 (`SPEC_PROBE_MAX_BYTES`/`SPEC_ANSWER_MAX_BYTES` → `VETO_CODE_MAX_BYTES`/`VETO_REASON_MAX_BYTES`/`AUDIT_MAX_TEXT_BYTES`).

Helper aus P205/P203d-3/P203d-2/P203d-1/P204: siehe vorherigen HANDOVER. `addMessage`-Wrapper-Return, `escapeHtml`, `renderCodeExecution`-Pattern, `.code-card`/`.output-card`-CSS, `code_execution`-Dict-Schema, Marker-Disjunktheit `[CODE-EXECUTION]` vs. `[PROJEKT-RAG]` / `[PROJEKT-KONTEXT]` / `[PROSODIE]` / `[KLARSTELLUNG]`, RAG-Toast-Pattern, JS-Integrity-Test.

## Offenes
- **NEU 2026-05-03 (Chris-Feature-Request, ausserhalb Patch-Zaehlung umgesetzt):** [`docs/huginn_kennt_zerberus.md`](docs/huginn_kennt_zerberus.md) ist Doku-Pflicht. Wurde inhaltlich auf P209-Stand gehoben (Phase-5a-Ziel #7 erledigt, Sancho-Panza-Veto-Layer als Pre-Filter zum HitL-Gate; Schicht 3 der Sicherheitsarchitektur erweitert um Veto-Probe; `[VETO-209]`-Logging-Tag in Audit-Liste; Tests-Zahl 2123 statt 2035). Spiegel-Kopie unter [`docs/RAG Testdokumente/huginn_kennt_zerberus.md`](docs/RAG%20Testdokumente/huginn_kennt_zerberus.md) ebenfalls aktualisiert (Sancho-Panza-Eintrag von "noch nicht implementiert" auf "implementiert in Phase 5a", neuer Absatz zwischen Spec-Contract und HitL-Gate, Test-Zahl > 2100). Coda hat NICHT committed — Chris reviewed die neue Doku zuerst und uploaded selbst per `curl -u Chris:... -F file=@docs/huginn_kennt_zerberus.md -F category=system http://localhost:5000/hel/admin/rag/upload`.
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#86-89 (P209: Push, Veto-Pfad Happy gefaehrlicher Code, Veto-Pfad PASS harmloser Code, Veto-Skip bei trivialem Code + Disabled-Flag)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 2123 passed (+88 P209 aus 2035 baseline)**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Zeile 1653 + 3096) — wie zuvor.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat einen `node --check`-Pass** seit P203d-3 — abgedeckt durch `TestJsSyntaxIntegrity`-Tests in P203d-3 + P206 + P207 + P208 + **P209** (skipped wenn `node` nicht im PATH).
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
- **NEU P209: Token-Cost-Tracking fuer Veto-Call** — der Veto-LLM-Call (~30-100 Tokens, einmal pro nicht-trivialem Code-Block) wird aktuell nicht in `interactions.cost` aufaggregiert. Schuld analog P203d-1/P203d-2/P208 (alle Probe-Calls sind nicht in `interactions.cost`).
- **NEU P209: Mehr-Modell-Voting** — bewusst weggelassen. Genau ein Veto-Call pro Run. Mehrere Modelle wuerden teurer und das Verhaeltnis Kosten:Robustheit-Gewinn ist fragwuerdig.
- **NEU P209: Lern-Loop ueber `code_vetoes`-History** — keine automatische Threshold-/Prompt-Adjustierung. Manuelle Auswertung via SQL (`SELECT verdict, COUNT(*) FROM code_vetoes GROUP BY verdict`). Wenn Veto-Rate > 50%, ist der System-Prompt zu streng; wenn < 5%, zu lax.
- **NEU P209: Sprach-spezifische Heuristiken** — `should_run_veto`-Signatur akzeptiert `language` als Parameter, nutzt ihn aber nicht. Vorbereitung: spaeter koennte `bash`/`sh` aggressiver triggern als `python`.
- **NEU P209: Veto-Override durch User** — bewusst weggelassen. Bei false-positives muss der User die Frage neu formulieren oder den Code anders schreiben. Ein "ich weiss was ich tue, fuehre trotzdem aus"-Button wuerde den Sicherheits-Layer aufweichen.
- **NEU P209: Persistenz der Verdicts ueber Server-Restart** — `VetoVerdict` ist transient. Nur der Audit landet in der DB.
- **NEU P209: Telegram-Pfad** — bewusst weggelassen. Huginn hat eigenen P167-HitL ohne Veto.
- **Pre-existing Files ohne Workspace** — wie zuvor.
- **Pre-existing Files ohne RAG-Index** — wie zuvor.
- **Lazy-Embedder-Latenz** — wie zuvor.
- **PWA-Cache-Bust nach Patches** — wie zuvor: nach dem ersten Open der PWA muss der Service-Worker den Cache invalidieren — Cache-Name-Bump auf `nala-shell-v3` waere sauber. Bei P209 nicht zwingend (network-first faengt das auf), aber empfohlen weil die JS-Surface noch weiter gewachsen ist.
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
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt (P203d-1) | Output-Synthese im Chat-Endpunkt (P203d-2) | UI-Render im Nala-Frontend (P203d-3) | RAG-Toast in Hel-UI (P205) | HitL-Gate vor Sandbox-Code-Execution: `ChatHitlGate`-Singleton, `/v1/hitl/poll`+`/v1/hitl/resolve`, Confirm-Karte, `code_executions`-Audit-Tabelle (P206) | Workspace-Snapshots + Diff + Rollback: `projects_snapshots.py` mit Pure-Function-`build_workspace_manifest`+`diff_snapshots`+`_is_safe_member`-Tar-Defense, neue `workspace_snapshots`-Tabelle, `[SNAPSHOT-207]`-Logging-Tag (P207) | Spec-Contract / Ambiguitäts-Check vor Haupt-LLM-Call: `spec_check.py` mit Pure-Function-Heuristik + `ChatSpecGate`-Singleton + drei Decision-Werten answered/bypassed/cancelled + `clarifications`-Tabelle + `[SPEC-208]`-Logging-Tag (P208) | **Zweite Meinung vor Ausführung / Sancho Panza vor HitL-Gate: `code_veto.py` mit Pure-Function-`should_run_veto` (Trigger-Gate mit Risk-Token-Liste + Trivial-1-Zeiler-Skip) + `build_veto_messages` (System-Prompt verlangt PASS oder VETO am Anfang) + `parse_veto_verdict` (robust gegen Markdown/Quotes/Multiline-Reasons) + Async-Wrapper `run_veto` mit `temperature=0.1` + fail-open. `VetoVerdict`-Dataclass mit `to_payload_dict() -> {vetoed, reason, latency_ms}`. Neue `code_vetoes`-Tabelle mit `audit_id`/`session_id`/`project_id`/`verdict` (pass|veto|skipped|error)/`reason`/`latency_ms`. Chat-Endpunkt schaltet Veto zwischen `first_executable_block` und HitL-Pending: bei VETO → Wandschlag-Payload mit `skipped=True`/`hitl_status="vetoed"`/`veto`-Sub-Field, kein HitL, kein Sandbox; bei PASS → weiter zum HitL-Gate. Frontend rendert `.veto-card` mit roter Border, prominenter Begruendung, italic Latency-Meta, collapsible Code-Toggle (44x44 Touch, **kein Approve-Button** — read-only Audit-Spur). `renderCodeExecution`-Erweiterung: bei `vetoed=true` → `renderVetoCard` + early return. Defaults `code_veto_enabled=True` + `code_veto_temperature=0.1`. `[VETO-209]`-Logging-Tag (P209)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`) | Slug ist immutable nach Anlage | Atomic Write für jeden Upload-Pfad | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge (P197) | Pure-Function vs. DB-Schicht trennen | Templates als reguläre `project_files`-Einträge (P198) | Idempotenz-Check VOR Schreiben für jeden Generator (P198) | Best-Effort-Verdrahtung im Endpoint (P198) | Per-Projekt-RAG-Index isoliert (P199) | Embedder-Wrapper als monkeypatchbare Funktion (P199) | Best-Effort-Indexing (P199) | RAG-Block-Marker `[PROJEKT-RAG]` (P199) | PWA-Endpoints auth-frei in separatem Router VOR auth-gated Hel-Router (P200) | SW-Scope folgt dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch (P200) | `persona_overlay` NIEMALS in User-sichtbarer Response (P201) | Header-Injektion fuer Cross-Cutting zentral in `profileHeaders()` (P201) | Zombie-ID-Schutz nach List-Refresh (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test (P201+P203d-3+P206+P207+P208+**P209**) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig (P203a) | Hardlink primär, Copy als Fallback (P203a) | Atomic-Write-Pattern auch fuer Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat ueber benutzergenerierte Daten ist verboten — IMMER `data-*` + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehoert in jede Test-Suite die HTML in Python-Source baut (P203b+P203d-3+P205+P206+P207+P208+**P209**) | Prosodie-Bruecken-Block enthaelt NIEMALS numerische Werte (P204) | Mehrabian-Schwellen identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Bruecken-Bloecke MUESSEN substring-disjunkt sein (P204+P203d-2+P208 KLARSTELLUNG) | Sandbox-Workspace-Mount: Read-Only ist konservativer Default (P203c+P207) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte konservativ geblockt — `project_overlay is not None` als Diskriminator (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollstaendiges Sandbox-Result liegt vor (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open (P203d-1) | Two-stufige LLM-Pipelines: `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren (P203d-2) | Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (P203d-2+**P209 should_run_veto**) | Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding (P203d-2+P208 SPEC_ANSWER_MAX_BYTES+**P209 VETO_CODE_MAX_BYTES/REASON_MAX_BYTES**) | Synthese-LLM-Pipeline ist fail-open auf jeder Stufe (P203d-2) | Frontend-Renderer fuer additive Backend-Felder muss BACKWARDS-COMPAT bleiben (P203d-3+P205+P206+P207+**P209 veto-Feld**) | DOM-Insertion-Funktionen retournen Wrapper-Element (P203d-3) | Frontend-Renderer fuer User-/LLM-Strings: jedes innerHTML-Statement muss einen `escapeHtml(`-Aufruf enthalten — Audit-Test prueft das per Regex (P203d-3+P206+P207) | Frontend-Renderer fuer User-Strings DARF auch `textContent` statt `innerHTML` nutzen — Audit-Test akzeptiert beide (P208 .spec-card+**P209 .veto-card**) | Mobile-first 44x44px Touch-Target gilt fuer JEDEN neuen Toggle/Button im Frontend (P203d-3+P206+P207+P208+**P209 .veto-code-toggle**) | Status-Toasts: `textContent` statt `innerHTML` fuer Reason-Strings (P205) | Toast-Replace-Pattern: ein einziger DOM-Container plus CSS-State-Toggle (P205) | `pointer-events: none` im Hidden-State von fixed-positionierten Toasts/Overlays (P205) | HitL-Pending-IDs sind UUID4-hex (32 chars), Cross-Session-Defense via Session-Match im Resolve, niemals Integer-IDs (P206+P208) | HitL-Pendings im Chat-Pfad sind transient (In-Memory), nicht persistiert (P206+P208 ChatSpecGate) | Synthese-LLM-Calls (P203d-2) DUERFEN NICHT auf `skipped=True`-Payloads laufen (P206+**P209 vetoed**) | Audit-Trail-Schreibungen sind Best-Effort: jeder Crash darf den Hauptpfad nicht blockieren (P206+P207+P208 store_clarification_audit+**P209 store_veto_audit**) | HANDOVER-Header enthaelt die Zeile `**Manuelle Tests:** X / Y ✅` aus der WORKFLOW-Tabelle, ohne Reminder-Text oder Bewertung (P206-Konvention) | Sandbox-Writable-Mount: Default `projects.sandbox_writable=False` — RO-Konvention bleibt Standard, opt-in via Settings-Flag (P207) | Snapshot-Tars unter `<data>/projects/<slug>/_snapshots/<uuid4-hex>.tar`, atomar via Tempname + `os.replace` (P207) | Tar-Member-Validation `_is_safe_member` MUSS bei jedem Restore aufgerufen werden — kein Symlink/Hardlink, kein absoluter Pfad, kein `..`, Member-Resolve innerhalb dest_root (P207) | Cross-Project-Defense: `rollback_snapshot_async` MUSS `expected_project_id` gegen Snapshot-Eigentuemer pruefen, sonst Reject (P207) | JavaScript-String-Literale mit Newline (`\\n`) DUERFEN NICHT direkt aus Python-Source kommen — `String.fromCharCode(10)` als Workaround (P207) | Workspace-Snapshot-Pfad ist additiv und opt-in: ohne `sandbox_writable=True`+`snapshots_enabled=True` bleibt das `code_execution`-Feld P206-kompatibel (P207) | Spec-Contract-Pfad ist additiv und opt-in: ohne `spec_check_enabled=True` faehrt das Chat-Verhalten unveraendert wie pre-P208 (P208) | Spec-Probe-LLM-Call MUSS isoliert sein (Persona-frei, kein RAG, kein Prosody-Block) — Werkzeug, kein Gespraech (P208+**P209 Veto-Probe analog**) | Drei Decision-Werte answered/bypassed/cancelled — `answered` braucht non-empty `answer_text`, sonst False; `cancelled` early-return mit `model="spec-cancelled"` ohne Haupt-LLM-Call; `timeout` = bypass (defensiver UX) (P208) | Source-Detection ueber `X-Prosody-Context` + `X-Prosody-Consent: true` (P204-Konvention) — Voice-Bonus +0.20 im Score (P208) | `[KLARSTELLUNG]`-Marker substring-disjunkt zu allen anderen LLM-Markern (PROJEKT-RAG/PROJEKT-KONTEXT/PROSODIE/CODE-EXECUTION/AKTIVE-PERSONA) (P208) | `enrich_user_message`-Pfad: bei `answered` MUSS sowohl `last_user_msg` als auch `req.messages[-letzter user-msg].content` aktualisiert werden, sonst sieht der Haupt-LLM-Call die Original-Message (P208) | **Veto-Pfad ist additiv und opt-in: ohne `code_veto_enabled=True` bleibt das Chat-Verhalten unveraendert wie pre-P209 (P209)** | **Veto-Probe-LLM-Call MUSS isoliert sein (Persona-frei, kein RAG, kein Prosody-Block) — Werkzeug, kein Gespraech (P209)** | **Bei VETO schreibt der Endpunkt KEINE Zeile in `code_executions` (P206-Tabelle), weil HitL nicht lief — Audit landet ausschliesslich in `code_vetoes`. Korrelation ueber `session_id`/`project_id` (P209)** | **Veto-Verdicts: `pass`/`veto`/`skipped`/`error` — `veto` triggert Wandschlag-Banner mit `skipped=True`+`hitl_status="vetoed"`; `pass` weiter zum HitL-Gate; `skipped` bei trivialem Code (kein LLM-Call); `error` fail-open weiter zum HitL-Gate (P209)** | **Wandschlag-Banner ist read-only Audit-Spur — KEIN Approve-Button (im Gegensatz zu HitL-Card aus P206), nur Code-Toggle. Source-Audit-Test prueft das per Regex (P209)** | **Veto-LLM-Call ist deterministisch via `temperature_override=0.1` (Default) — Veto soll wiederholbare Entscheidungen liefern, nicht zwischen PASS und VETO schwanken (P209)** | **Verdict-Parser MUSS robust gegen Markdown-Bold (`**VETO**`), Quotes (`"PASS"`), Doppelpunkte/Bindestriche, Lowercase, Multi-Line-Reasons sein, plus First-Line-Match + 64-char-Window-Fallback. Fail-open zu PASS bei unparseable Output (P209)**
