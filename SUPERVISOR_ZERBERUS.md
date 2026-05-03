# SUPERVISOR_ZERBERUS.md – Zerberus Pro 4.0
*Strategischer Stand für die Supervisor-Instanz (claude.ai Chat)*
*Letzte Aktualisierung: Patch 208 (2026-05-03) – Spec-Contract / Ambiguitäts-Check (Phase 5a Ziel #8 ABGESCHLOSSEN)*

---

## Aktueller Patch

**Patch 208** — Spec-Contract / Ambiguitäts-Check (Phase 5a Ziel #8 ABGESCHLOSSEN) (2026-05-03)

Erst verstehen, dann coden. Vor dem ersten Haupt-LLM-Call schaetzt eine Pure-Function-Heuristik die Ambiguitaet der User-Eingabe; bei Score >= Threshold (Default 0.65) faehrt ein schmaler "Spec-Probe"-LLM-Call (eine Frage, keine Vorrede), das Frontend rendert eine Klarstellungs-Karte mit Original-Message + Frage + Textarea + drei Buttons. User antwortet, klickt "Trotzdem versuchen" oder bricht ab. Erst danach laeuft der eigentliche Code-/Antwort-Pfad mit ggf. angereichertem Prompt weiter. Whisper-Input bekommt einen Score-Bonus (`+0.20`) — Voice-Transkripte sind systematisch ambiger als getippter Text.

**Architektur: drei Schichten plus Feature-Flags plus Endpoints plus Frontend-Card.**

- **Spec-Modul** [`zerberus/core/spec_check.py`](zerberus/core/spec_check.py) mit Pure-Function-Schicht (`compute_ambiguity_score(message, *, source="text"|"voice")` heuristik 0.0-1.0; `should_ask_clarification(score, *, threshold=0.65)` Trigger-Gate; `build_spec_probe_messages(message)` baut die `messages`-Liste fuer den Probe-Call; `build_clarification_block(question, answer)` plus `enrich_user_message(original, question, answer)` haengen `[KLARSTELLUNG]…[/KLARSTELLUNG]` an; Marker substring-disjunkt zu `[PROJEKT-RAG]`/`[PROJEKT-KONTEXT]`/`[PROSODIE]`/`[CODE-EXECUTION]`/`[AKTIVE-PERSONA]`), Async-Wrapper (`run_spec_probe(message, llm_service, session_id)` ruft den Probe-LLM mit `temperature=0.3` und liefert die Frage oder `None`), Pending-Registry (`ChatSpecGate`-Singleton analog `ChatHitlGate` aus P206 — In-Memory only, `asyncio.Event` pro Pending, Cross-Session-Defense ueber `session_id`-Match), Audit-Helper (`store_clarification_audit(...)` schreibt in `clarifications`-Tabelle, Best-Effort, 4 KB Truncate).
- **Heuristik-Score** addiert pro Treffer einen Penalty: kurze Saetze (<4 Woerter +0.40, <8 +0.20, <14 +0.05), Pronomen-Dichte (max +0.30), Code-Verb ohne Sprache (+0.20), generisches Verb ohne Substantiv-Anker (+0.15), Code-Verb ohne IO-Spec (+0.10), Voice-Bonus (+0.20). Clamped auf [0, 1]. Ein klarer Prompt mit Sprachangabe + IO-Spec landet meist <0.4, "bau das" landet >0.8.
- **Neue Tabelle** `clarifications` in [`zerberus/core/database.py`](zerberus/core/database.py): `pending_id`, `session_id`, `project_id`, `project_slug`, `original_message`, `question`, `answer_text`, `score` (Float), `source` (text|voice), `status` (answered|bypassed|cancelled|timeout|error), `created_at`, `resolved_at`. Persistente Spur fuer Threshold-Tuning.
- **Verdrahtung in [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py)** zwischen `last_user_msg`-Cleanup (Cloud/Local-Suffix entfernt) und Intent-Detection. Drei Decision-Pfade:
  - `answered` mit `answer_text` → `last_user_msg` wird via `enrich_user_message` mit `[KLARSTELLUNG]`-Block angereichert, `req.messages` mitgespiegelt → Haupt-LLM-Call sieht Frage + User-Antwort
  - `bypassed` → Original durch (User akzeptiert Risiko)
  - `cancelled` → Chat endet mit Hinweis-Antwort, **kein** Haupt-LLM-Call (early-return mit `model="spec-cancelled"`)
  - `timeout` → wie `bypassed` (defensiver: User schaut nicht hin → eher durchlassen statt frustrieren)
- **Source-Detection**: Voice wenn `X-Prosody-Context` + `X-Prosody-Consent: true` Header gesetzt sind (P204-Konvention). Sonst Text.
- **Neue Endpoints** in `legacy.py` (auth-frei wie `/v1/hitl/*`, Dictate-Lane-Invariante):
  - `GET /v1/spec/poll` → liefert aeltestes pending Spec-Pending der Session
  - `POST /v1/spec/resolve` mit `{pending_id, decision, session_id, answer_text?}` → idempotent + Cross-Session-Block. `answered` braucht non-empty `answer_text`, sonst `ok=False`.
- **Nala-Frontend** in [`nala.py`](zerberus/app/routers/nala.py):
  - **CSS** (~135 Zeilen): `.spec-card`/`.spec-card-header`/`.spec-source-tag`/`.spec-original`/`.spec-question`/`.spec-answer-row`/`.spec-answer-input` (textarea, focus-Border in Gold)/`.spec-actions`/`.spec-answer-btn` (gold)/`.spec-bypass-btn` (gruen)/`.spec-cancel-btn` (rot) plus Post-Klick-States `.spec-card.spec-{answered,bypassed,cancelled}`. Mobile-first 44x44 Touch-Targets fuer alle drei Buttons.
  - **JS-Funktionen** (~150 Zeilen): `startSpecPolling(abortSignal, snapshotSessionId)` pollt `/v1/spec/poll` im Sekunden-Takt waehrend der Chat-Long-Poll laeuft. `renderSpecCard(pending)` baut Karte mit Original-Message (textContent — XSS-safe by default), Frage (textContent), Textarea, drei Buttons (`✉️ Antwort senden` / `→ Trotzdem versuchen` / `✗ Abbrechen`). `resolveSpecPending(pendingId, decision, answerText)` macht POST und setzt Card-State (`spec-answered` gruen, `spec-bypassed` gold, `spec-cancelled` rot). Karte bleibt nach Klick sichtbar als Audit-Spur. `clearSpecState()`/`stopSpecPolling()` analog HitL.
  - **Verdrahtung in `sendMessage`**: nach `clearHitlState() + startHitlPolling(...)` analog `clearSpecState() + startSpecPolling(myAbort.signal, reqSessionId)`. Beide laufen unabhaengig — Spec kommt VOR dem LLM-Call, HitL kommt NACH dem Code-Block.
- **Feature-Flags** in [`config.py::ProjectsConfig`](zerberus/core/config.py): `spec_check_enabled: bool = True`, `spec_check_threshold: float = 0.65`, `spec_check_timeout_seconds: int = 60`. Master-Switch off → kein Probe, kein Pending, kein Block in der Response.

**Was P208 bewusst NICHT macht:**

- **Sprachen-Erkennung im Code-Block** — das macht P203d-1 (auf der LLM-Output-Seite, nicht der User-Seite).
- **Refactoring der HitL-Card (P206)** — Spec ist eine separate Karte, die VOR HitL kommt. Beide bleiben unabhaengig.
- **Multi-Turn-Klarstellung** — eine Frage pro Turn, dann zurueck in den Hauptpfad. Folge-Klarstellungen sind ein eigener Patch.
- **Telegram-Pfad** — Huginn hat eigenen HitL aus P167; Spec-Probe waere dort ein Overkill (Telegram-Threads sind asynchroner als Chat).
- **Persistierung der Pendings** — In-Memory only, Long-Poll-Requests sterben beim Restart sowieso (analog P206-Konvention).
- **Edit-Vor-Run** — User kann den Original-Prompt nicht editieren in der Card, nur antworten oder bypassen. Bei Bedarf neue Message schicken.
- **Token-Cost-Tracking fuer den Probe-Call** — Schuld analog P203d-1 (interactions.cost erfasst nur den Haupt-Call).

**Tests:** 89 in [`test_p208_spec_contract.py`](zerberus/tests/test_p208_spec_contract.py). Strukturiert in 14 Klassen: `TestComputeAmbiguityScore` (9 — leer/range/length/voice/code-verb/pronoun/clear/clamp/io), `TestShouldAskClarification` (5 — above/below/exact/invalid/custom-threshold), `TestBuildSpecProbeMessages` (5 — list-shape/system-content/user-content/empty/system-prompt-constrains), `TestEnrichUserMessage` (6 — marker/preserve/q+a/empty/answer-only/build), `TestMarkerUniqueness` (2 — disjunkt/format), `TestRunSpecProbe` (7 — happy/strip/empty/whitespace/crash/non-tuple/truncate), `TestChatSpecGate` (13 — uuid/dict-keys/list-filter/answered+text/answered-empty-rejected/bypassed/cancelled/invalid-decision/session-mismatch/wait-immediate/wait-timeout/cleanup/answer-truncated), `TestStoreClarificationAudit` (3 — happy/truncate/no-db), `TestSpecPollResolveEndpoints` (6 — empty/by-session/no-leak/answered/bypassed/unknown), `TestLegacySourceAudit` (13 — Logging-Tag, Imports, Endpoints, Pydantic-Models, source-Kwarg, Threshold/Timeout/Enabled-Flags, cancelled-early-return, enrich, voice-detection, audit-call), `TestNalaSourceAudit` (10 — JS-Funktionen, Endpoints, sendMessage-Verdrahtung, CSS-Klassen, 44px-Touch, escapeHtml/textContent, textarea, drei Decisions, Audit-Trail-States), `TestE2ESpecCheck` (5 — non-ambig skip / ambig+bypassed / ambig+answered+enrichment / ambig+cancelled+early-return / disabled-flag-skip), `TestJsSyntaxIntegrity` (1 — `node --check` ueber NALA_HTML, skipped wenn node fehlt), `TestSmoke` (4 — config-flags / clarifications-tabelle / endpoints / module-exports).

**Lokal:** 1946 baseline → **2035 passed** (+89 P208), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info` durch `config.yaml`-Drift `deepseek-v4-pro`), 0 NEUE Failures aus P208.

**Logging-Tag:** `[SPEC-208]` mit `pending_create id=... session=... score=... source=... question_len=...`, `decision id=... session=... status=... answer_len=...`, `ambig score=... threshold=... source=... session=...`, `not_ambig score=... threshold=... source=... session=...`, `probe_returned_empty session=... (fail-open)`, `audit_written session=... project_id=... status=... source=... score=...`, `Pipeline-Fehler (fail-open): ...`.

---

**Patch 207** — Workspace-Snapshots + Diff-View + Rollback (Phase 5a Ziele #9 + #10 ABGESCHLOSSEN) (2026-05-03)

Sicherheitsnetz NACH der Sandbox-Ausfuehrung. P206 gibt das Yay/Nay VOR dem Run; P207 gibt die Sicht UND die Reverse-Option DANACH. Wenn `projects.sandbox_writable=True` (Default False — RO bleibt P203c-Standard) UND `projects.snapshots_enabled=True` (Default True) UND HitL approved hat, schiesst der Chat-Endpunkt einen `before_run`-Tar-Snapshot, faehrt die Sandbox writable und schiesst danach einen `after_run`-Snapshot. Aus dem Paar entsteht ein Diff (added/modified/deleted plus optional `unified_diff` fuer Text-Files <64 KB), der additiv im `code_execution.diff`-Feld der Response landet — zusammen mit `before_snapshot_id` und `after_snapshot_id`. Der User sieht im Frontend eine Diff-Card und kann via `↩️ Aenderungen zurueckdrehen` den Workspace auf den `before`-Stand zuruecksetzen.

**Architektur: drei Schichten plus Feature-Flag plus Endpoint plus Frontend-Card.**

- **Snapshot-Modul** [`zerberus/core/projects_snapshots.py`](zerberus/core/projects_snapshots.py) mit Pure-Function-Schicht (`build_workspace_manifest`, `diff_snapshots`, `_looks_text`, `_is_safe_member` als Tar-Member-Validation, `_build_unified_diff`), Sync-FS-Schicht (`materialize_snapshot` schreibt atomar via Tempname + `os.replace`, `restore_snapshot` raeumt Workspace-Inhalt komplett und extrahiert Tar mit Member-Validation), Async-DB-Schicht (`store_snapshot_row`/`load_snapshot_row` auf neue Tabelle `workspace_snapshots`) und High-Level-Convenience (`snapshot_workspace_async`/`rollback_snapshot_async` mit `expected_project_id`-Check).
- **Neue Tabelle** `workspace_snapshots` in [`zerberus/core/database.py`](zerberus/core/database.py): `snapshot_id` (UUID4-hex UNIQUE), `project_id`, `project_slug`, `label` (`before_run`/`after_run`/`manual`), `archive_path`, `file_count`, `total_bytes`, `pending_id` (Korrelation zu `hitl_chat`/`code_executions` aus P206), `parent_snapshot_id` (zeigt vom `after`- auf den `before`-Snapshot derselben Ausfuehrung), `created_at`. Snapshot-Tars liegen unter `data/projects/<slug>/_snapshots/<id>.tar`.
- **Verdrahtung in [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py)** zwischen P206-Approve und `execute_in_workspace`:

```python
_writable = bool(getattr(settings.projects, "sandbox_writable", False))
_snapshots_active = _writable and bool(getattr(settings.projects, "snapshots_enabled", True))

if _snapshots_active:
    _before_snap = await snapshot_workspace_async(project_id=..., label="before_run", pending_id=...)

_result = await execute_in_workspace(..., writable=_writable)  # ehemals hardcoded False

if _snapshots_active and _before_snap is not None:
    _after_snap = await snapshot_workspace_async(label="after_run", parent_snapshot_id=_before_snap["id"])
    _diff = diff_snapshots(_before_snap["manifest"], _after_snap["manifest"])
    code_execution_payload["diff"] = [d.to_public_dict() for d in _diff]
    code_execution_payload["before_snapshot_id"] = _before_snap["id"]
    code_execution_payload["after_snapshot_id"] = _after_snap["id"]
```

Fail-Open auf jeder Stufe; HitL-Skip-Pfad (rejected/timeout) triggert keinen Snapshot.

- **Neuer Endpoint** `POST /v1/workspace/rollback` in `legacy.py` (auth-frei wie `/v1/hitl/*`): `WorkspaceRollbackRequest{snapshot_id, project_id}` → `WorkspaceRollbackResponse{ok, snapshot_id?, project_id?, project_slug?, file_count?, total_bytes?, error?}`. Reject-Pfade: `snapshots_disabled`, `restore_failed` (unbekannter snapshot ODER project_mismatch), `pipeline_error`. Cross-Project-Defense: `expected_project_id` muss zum Snapshot-Eigentuemer passen.
- **Nala-Frontend** in [`nala.py`](zerberus/app/routers/nala.py):
  - **CSS** (~125 Zeilen): `.diff-card`/`.diff-card-header`/`.diff-summary`/`.diff-list`/`.diff-entry`/`.diff-entry-head`/`.diff-status.diff-{added,modified,deleted}`/`.diff-path`/`.diff-size`/`.diff-content`/`.diff-content .diff-line-{add,del,meta}`/`.diff-binary-note`/`.diff-actions`/`.diff-rollback`/`.diff-resolved` plus Post-Klick-States `.diff-card.diff-{rolled-back,rollback-failed}`. Kintsugi-Gold-Border, `.diff-rollback` mit `min-height: 44px`/`min-width: 44px`.
  - **JS-Funktionen** (~250 Zeilen): `renderDiffCard(wrapperEl, codeExec, triptych)` baut Header mit Summary `N neu, M geaendert, K geloescht`, dann pro DiffEntry eine `<li>` mit Status-Badge + Pfad (escapeHtml) + Size-Label; Klick toggled Inline-Diff. `colorizeUnifiedDiff(text)` faerbt Plus/Minus/Header (gruen/rot/grau) — nutzt `String.fromCharCode(10)` statt `'\n'`-Literal (Lesson aus P203b: Newline-Escapes werden in Python-Source frueh interpretiert). `rollbackWorkspace(cardEl, snapshotId, projectId)` macht POST und setzt Card-State.
  - **`renderCodeExecution`-Erweiterung:** nach Code-Card + Output-Card-Insert, falls `!skipped && Array.isArray(codeExec.diff) && codeExec.before_snapshot_id` → `renderDiffCard(...)`. Backwards-compat zu P206-only-Backends.
- **Feature-Flags** in [`config.py::ProjectsConfig`](zerberus/core/config.py): `sandbox_writable: bool = False` (RO-Default bleibt) + `snapshots_enabled: bool = True` (Master-Switch).
- **Konvention-Update:** der hardcoded `writable=False` aus P203d-1 wurde durch `getattr(settings.projects, "sandbox_writable", False)` ersetzt. Der zugehoerige P203d-1-Source-Audit-Test wurde entsprechend nachgezogen.

**Was P207 bewusst NICHT macht:**

- **Cross-Project-Diff** — Snapshots sind per Projekt isoliert, ein Cross-Diff macht selten Sinn.
- **Branch-Mechanik** — linear forward/reverse only. `parent_snapshot_id` koennte zu einem Tree ausgebaut werden, aktuell nur fuer `before→after`-Korrelation.
- **Automatischer Rollback bei `exit_code != 0`** — User-Choice. Manche Crashes hinterlassen wertvolle Teil-Outputs.
- **Per-File-Rollback** — alles oder nichts pro Snapshot. Inline-Diff-Anzeige ist additiv, Rollback wirkt aufs Ganze.
- **Hardlink-Snapshots** — Tar ist Tests-tauglich + atomar. Bei messbaren Disk-Problemen umstellen.
- **Storage-GC fuer alte Snapshots** — alte `.tar`-Files bleiben liegen. "Behalte die letzten N pro Projekt"-Sweep ist eigener Patch (HANDOVER-Schuld).
- **Sync-After-Write zurueck in den SHA-Storage** — geaenderte Files leben nur im Workspace, nicht im SHA-Storage. Schuld bleibt offen.
- **Cost-Tracking** — Snapshots erzeugen keine LLM-Kosten, nur Disk + DB.

**Tests:** 74 in [`test_p207_workspace_snapshots.py`](zerberus/tests/test_p207_workspace_snapshots.py). Strukturiert in 9 Klassen analog P206: Pure-Function-Schicht (Manifest, Diff, Looks-Text, Is-Safe-Member, Unified-Diff), Sync-FS (Materialize, Restore mit Path-Traversal-Defense), DB (Store/Load + Convenience), Endpoint (OK/restore_failed/project_mismatch/snapshots_disabled), Source-Audit legacy.py + nala.py, End-to-End mit Mock-Sandbox die den Workspace mutiert, JS-Integrity (`node --check`), Smoke. Plus 1 nachgezogener Test in `test_p203d_chat_sandbox.py` (`writable=False` ist nicht mehr hardcoded, sondern Settings-Lookup mit Pydantic-Default).

**Lokal:** 1872 baseline → **1946 passed** (+74 P207), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info` durch `config.yaml`-Drift `deepseek-v4-pro`), 0 NEUE Failures aus P207.

**Logging-Tag:** `[SNAPSHOT-207]` mit `materialized id=... label=... file_count=... total_bytes=...`, `db_row_written ...`, `diff project_id=... before=... after=... changes=...`, `restored archive=... file_count=...`, `rollback_done snapshot_id=... project_id=... slug=...`, `rollback_endpoint snapshot_id=... project_id=...`, plus Defense-Logs (`restore: unsafe member skipped: ...`, `before_run fehlgeschlagen (fail-open): ...`).

---

**Patch 206** — HitL-Gate vor Code-Execution + HANDOVER-Teststand-Konvention (Phase 5a Ziel #6 ABGESCHLOSSEN) (2026-05-03)

Bisher (P203d-1) lief jeder erkannte Code-Block direkt in die Sandbox — RO-Mount machte das vergleichsweise sicher, aber Phase-5a-Ziel #6 fordert explizite User-Confirmation. P206 schliesst das Ziel mit einem In-Memory-Long-Poll-Gate und einer Confirm-Karte im Nala-Frontend. Plus integriert die kleine Teststand-Reminder-Konvention aus dem Feature-Request (HANDOVER-Header zeigt `**Manuelle Tests:** X / Y ✅`).

**Architektur: drei Schichten plus Feature-Flag plus Audit-Tabelle.**

- **Pure-Mechanik** in [`zerberus/core/hitl_chat.py`](zerberus/core/hitl_chat.py): `ChatHitlGate`-Singleton mit dict-Registry + `asyncio.Event` pro Pending. `create_pending(session_id, project_id, project_slug, code, language)` legt UUID4-hex-IDs an, `wait_for_decision(pending_id, timeout)` blockt via `asyncio.wait_for(event.wait(), timeout=N)`, `resolve(pending_id, decision, *, session_id)` flippt status + setzt Event mit Cross-Session-Defense, `cleanup(pending_id)` raeumt nach Resolve. In-Memory-only — Long-Poll-Requests sterben beim Server-Restart sowieso, persistente Pendings wuerden zu "Geister-Karten" fuehren. Plus `store_code_execution_audit(...)`-Helper schreibt eine Audit-Zeile mit 8 KB Truncate fuer code/stdout/stderr.
- **Audit-Tabelle** `code_executions` in [`zerberus/core/database.py`](zerberus/core/database.py) — schliesst die HANDOVER-Schuld P203d-1 ("code_execution ist nicht in der DB"). Spalten: `pending_id`/`session_id`/`project_id`/`project_slug`/`language`/`exit_code`/`execution_time_ms`/`truncated`/`skipped`/`hitl_status`/`code_text`/`stdout_text`/`stderr_text`/`error_text`/`created_at`/`resolved_at`. SQLite-friendly mit `Integer` 0/1 fuer Boolean. `init_db`-Bootstrap legt sie automatisch an.
- **Verdrahtung in [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py)** zwischen `first_executable_block` und `execute_in_workspace`:

```python
if getattr(settings.projects, "hitl_enabled", True):
    _gate = get_chat_hitl_gate()
    _pending = await _gate.create_pending(...)
    _hitl_decision = await _gate.wait_for_decision(_pending.id, _timeout)
    _gate.cleanup(_pending.id)
else:
    _hitl_decision = "bypassed"

if _hitl_decision in ("approved", "bypassed"):
    _result = await execute_in_workspace(..., writable=False)
    code_execution_payload = {..., "skipped": False, "hitl_status": _hitl_decision}
else:
    code_execution_payload = {..., "skipped": True, "hitl_status": _hitl_decision,
                              "exit_code": -1, "error": "Vom User abgebrochen"}
```

Synthese-Gate (P203d-2) erweitert: `if code_execution_payload is not None and not code_execution_payload.get("skipped"):` — kein zweiter LLM-Call bei Skip.

- **Zwei neue auth-freie Endpoints** in `legacy.py` (per `/v1/`-Invariante kein JWT):
  - `GET /v1/hitl/poll` — liefert das aelteste Pending der Session als JSON (oder `{"pending": null}`). Header `X-Session-ID` als Owner-Diskriminator.
  - `POST /v1/hitl/resolve` — Body `{pending_id, decision, session_id}`, idempotent + Cross-Session-Block. Antwort `{ok, decision}`.
- **Nala-Frontend** in [`nala.py`](zerberus/app/routers/nala.py):
  - **CSS** (~70 Zeilen): `.hitl-card` mit Kintsugi-Gold-Border `rgba(240,180,41,0.55)` + Inset-Shadow, `.hitl-actions` Flex-Row mit `gap: 8px`, `.hitl-approve`/`.hitl-reject` mit `min-height: 44px`/`min-width: 44px` (Mobile-first Touch). Post-Klick-States `.hitl-approved`/`.hitl-rejected` schimmern gruen/rot. `.hitl-resolved` als zentrierter italic-Text. Plus `.exit-badge.exit-skipped` fuer Code-Card im Skip-State.
  - **JS-Funktionen** (~120 Zeilen): `startHitlPolling(abortSignal, snapshotSessionId)` (1-Sekunden-Intervall, sofortige erste Runde, stoppt bei AbortSignal oder Session-Wechsel), `stopHitlPolling()`, `renderHitlCard(pending)` mit `escapeHtml(String(pending.code))` als XSS-Schutz, `resolveHitlPending(pendingId, decision)` (Buttons sperren, POST `/v1/hitl/resolve` mit `{pending_id, decision, session_id: sessionId}`, Card-State-Update), `clearHitlState()` als State-Reset.
  - **`sendMessage`-Verdrahtung:** vor dem Chat-Fetch wird `clearHitlState()` + `startHitlPolling(myAbort.signal, reqSessionId)` aufgerufen, im `finally`-Block `stopHitlPolling()`. Card bleibt nach Klick als Audit-Spur im DOM stehen.
  - **`renderCodeExecution`-Erweiterung:** liest `codeExec.skipped` und `codeExec.hitl_status`, ersetzt Exit-Badge bei Skip durch `⏸ uebersprungen` (rejected) oder `⏱ timeout` — Reason-Text kommt aus dem `error`-Feld.
- **Feature-Flag** `projects.hitl_enabled: bool = True` plus `hitl_timeout_seconds: int = 60` in `ProjectsConfig`. Bei `false` laeuft P203d-1-Verhalten ohne Gate (Audit-Status `bypassed`).
- **Doku-Pflicht-Erweiterung** in [`ZERBERUS_MARATHON_WORKFLOW.md`](ZERBERUS_MARATHON_WORKFLOW.md): HANDOVER-Header bekommt die Zeile `**Manuelle Tests:** X / Y ✅`. Implementierung der Feature-Request-Konvention aus dem Chris-Brief vom 2026-05-03 — kein Reminder-Text, keine Eskalation, nur die Zahl.

**Was P206 bewusst NICHT macht:**

- **Persistenz ueber Server-Restart** — bewusste Entscheidung, siehe oben. Bei zukuenftigen "Background-Code-Run-Modes" (LLM-getriggertes Cron-Job) muesste man das nochmal anschauen.
- **Edit-Vor-Run-Funktion** — User sieht den Code, kann aber nicht editieren. Falls UX-Feedback "Ich will den Code anpassen": Card mit Inplace-Textarea waere eine eigene UX-Schicht.
- **Telegram-Pfad** — Huginn nutzt sein eigenes P167-System (`HitlManager` mit DB-Persist). Architektur-Trennung ist Absicht (transient vs. delayed-Callback).
- **HitL fuer Output-Synthese** — zweites Gate waere Overkill. Die Synthese liest nur stdout/stderr und macht eine Zusammenfassung — kein Sicherheitsrisiko.

**Logging-Tag:** `[HITL-206]` mit `pending_create id=... session=... project_id=... language=... code_len=N` / `decision id=... session=... status=approved|rejected|timeout` / `bypassed session=... (hitl_enabled=False)` / `skipped session=... status=... language=...` / `audit_written session=... project_id=... hitl_status=... skipped=... exit_code=...`. Worker-Protection-konform: keine Code-Inhalte oder Output-Inhalte im Log.

**Tests:** 55 in [`test_p206_hitl_chat_gate.py`](zerberus/tests/test_p206_hitl_chat_gate.py). Acht Klassen: Pure-Gate-Mechanik (13), Audit-Trail-DB-Insert (3), Endpoint-Direktaufrufe (7), Source-Audit `legacy.py` (8), Source-Audit `nala.py` inkl. XSS + 44x44px (12), End-to-End mit gemocktem `wait_for_decision` (6), JS-Integrity per `node --check` (1), Smoke (3).

**Kollateral-Fix:** `test_p203d_chat_sandbox.py::_setup_common` und `test_p203d2_chat_synthesis.py::_setup` plus `test_synthesis_failure_keeps_original_answer` setzen jetzt `monkeypatch.setattr(get_settings().projects, "hitl_enabled", False)` — sonst wuerde das neue HitL-Gate (Default ON) im Test 60s auf eine Decision warten, die nie kommt.

**Teststand:** 1817 baseline (P205) → **1872 passed** (+55 P206-Tests), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info` durch `config.yaml`-Drift), 0 neue Failures aus P206. 1 skipped (existing).

**Effekt fuer den User:** Nala-Chat mit aktivem Projekt + Code-erzeugendem Prompt (z.B. "Lies /workspace/data.txt"). Statt direkter Sandbox-Ausfuehrung erscheint eine Gold-umrandete Confirm-Karte mit Code-Vorschau und zwei 44x44px-Buttons. ✅ Ausfuehren → Karte wird gruen ("Code laeuft..."), Sandbox lauft, Output-Card erscheint. ❌ Abbrechen → Karte wird rot ("Abgebrochen"), Sandbox bleibt aus, Code-Card zeigt Skip-Badge `⏸ uebersprungen`. Audit-Trail in `code_executions`-Tabelle erlaubt spaeter Hel-Admin-Reports ueber Approve-Rate, Reject-Reasons, Timeout-Haeufigkeit pro Projekt.

---

## Vorheriger Patch

**Patch 205** — RAG-Toast in Hel-UI nach Datei-Upload (Phase 5a Schuld aus P199) (2026-05-03)

`POST /hel/admin/projects/{id}/files` retourniert seit P199 ein `rag`-Dict im Response-Body (`{chunks, skipped, reason}`). Das Hel-Frontend hat das Feld bisher ignoriert — der User sah nicht, ob die hochgeladene Datei indiziert wurde, wieviel Chunks gebaut wurden oder warum sie geskippt wurde (zu gross / Binärdatei / leer / kein Inhalt / Embed-Crash). P205 schliesst die Schuld mit einem dezenten Toast unten rechts.

**Architektur: Frontend-only, drei Bausteine** in [`zerberus/app/routers/hel.py`](zerberus/app/routers/hel.py):

- **CSS-Block** im `<style>`-Bereich (~30 Zeilen): `.rag-toast` mit `position: fixed`, `bottom: 20px`, `right: 20px`, `min-height: 44px` (Mobile-first Touch-Dismiss), `transition: opacity/transform 0.2s` plus `.rag-toast.visible`-State (opacity 1 + translateY 0). Zwei Border-Color-Varianten: `.success` (cyan `#4ecdc4`) und `.warn` (rot `#ff6b6b`). Erbt das Kintsugi-Gold-Border `#c8941f` als Default. `pointer-events: none` im Hidden-State, damit der Toast niemals einen Klick im Hintergrund abfaengt.
- **Reason-Mapping** als JS-Konstante `_RAG_REASON_LABELS` direkt vor `_uploadProjectFiles`: kurze de-DE-Strings fuer alle Codes aus `zerberus/core/projects_rag.py::index_project_file` (`rag_disabled` → "RAG aus", `too_large` → "zu gross", `binary` → "Binärdatei", `empty` → "leere Datei", `no_chunks` → "kein Inhalt", `embed_failed` → "Embed-Fehler", `file_not_found`/`project_not_found`/`bytes_missing` → entsprechend, `exception` → "Indizierungs-Fehler"). Unbekannte Codes fallen auf "übersprungen" zurueck.
- **`_showRagToast(rag)`** als JS-Renderer (~25 Zeilen): liest `rag.skipped` und `rag.chunks` und `rag.reason`, baut den Text via Mapping-Lookup (`📚 N Chunks indiziert` bei Erfolg, `⚠ Datei nicht indiziert: <Label>` bei Skip), setzt `el.textContent` (XSS-immun — Reason-Strings stammen NIEMALS direkt aus dem Server-Pfad, nur aus dem statischen Mapping), toggelt die `success`/`warn`-Klassen, fuegt `.visible` hinzu. Auto-Timeout 3500 ms via `setTimeout`, der frueh canceled wird wenn ein neuer Toast kommt (`_showRagToast._t` als Singleton-Slot). Klick auf den Toast cancelt den Timeout und versteckt sofort. Replace-Pattern: bei Multi-File-Upload bleibt der letzte Toast sichtbar (Progress-Liste zeigt pro File den Status — Toast ist additiv).

**DOM-Element** direkt vor `</body>` (vor dem SW-Reg-Script): `<div id="ragToast" class="rag-toast" role="status" aria-live="polite"></div>`. Ein einziger Toast-Container, kein Stacking — Reuse durch CSS-State-Toggle.

**Verdrahtung** im bestehenden Drop-Zone-Upload `_uploadProjectFiles` → `xhr.onload` Success-Branch direkt nach dem `'fertig'`-Render der Progress-Zeile:

```js
if (xhr.status >= 200 && xhr.status < 300) {
    row.innerHTML = '&#10004; ' + _escapeHtml(f.name) + ' — fertig';
    row.style.color = '#4ecdc4';
    // Patch 205: RAG-Status nach Erfolgs-Render als Toast zeigen.
    try {
        const body = JSON.parse(xhr.responseText);
        if (body && body.rag) _showRagToast(body.rag);
    } catch (_) {}
}
```

Fail-quiet auf jeder Stufe: kaputter JSON-Body bricht den Upload-Loop nicht ab; fehlendes `body.rag` (Backwards-Compat zu Backends ohne P199) → kein Toast.

**Was P205 bewusst NICHT macht:**
- **Kein neuer Backend-Endpoint, keine Schema-Aenderung.** Der `rag`-Block lag schon seit P199 in der Response. P205 ist reine Lesefehler-Behebung im Frontend.
- **Keine Sammel-Aggregation.** Bei Multi-File-Upload gewinnt der letzte Toast — die Progress-Liste oben zeigt pro File den Erfolgs-Status. Sammel-Toast (`📚 47 Chunks aus 3 Dateien indiziert`) waere komplexer Code fuer einen Edge-Case.
- **Kein Stacking.** Pattern aus dem HANDOVER-Spec: neuer Toast ersetzt alten via gleichem `#ragToast`-Container und CSS-State-Toggle.
- **Kein Nala-Pfad.** Nala hat aktuell keinen Datei-Upload (Projekt-Anlage ist Hel-only). Wenn P201/P196 spaeter Nala-seitige Uploads bekommt, muss der Toast dort separat verdrahtet werden — Quelltext-Lift-und-Klon, kein Module-Refactor.
- **Keine i18n.** de-DE hardgecodet, analog zur restlichen Hel-UI.
- **Kein Telegram-Pfad.** Huginn (Telegram-Adapter) hat keine Datei-Uploads dieser Art.
- **`escapeHtml`-Doppelung in hel.py** (Zeile 1653 + 3096) bleibt als bestehende Schuld stehen; P205 nutzt sowieso `textContent` und braucht keinen Helper.

**Logging-Tag:** keiner (Frontend-only, alle Backend-Logs `[RAG-199]` aus P199 bleiben).

**Tests:** 20 in [`test_p205_hel_rag_toast.py`](zerberus/tests/test_p205_hel_rag_toast.py):

- `TestToastFunctionExists` (2) — Funktion definiert + Signatur `_showRagToast(rag)`.
- `TestReasonMapping` (6) — alle 5 Hauptcodes (`too_large`, `binary`, `empty`, `no_chunks`, `embed_failed`) im Mapping plus expliziter `'too_large' → 'zu gross'`-Check.
- `TestRagToastCss` (4) — `.rag-toast` definiert, `min-height: 44px` (Mobile-first), `position: fixed`, Toggle-Klasse (`.visible`).
- `TestToastDom` (1) — `<div id="ragToast">` im HTML.
- `TestUploadWiring` (3) — `body.rag` im Upload-Block, `_showRagToast(...)`-Aufruf im Block, Reihenfolge `'fertig'` < `_showRagToast` (Toast NACH Render).
- `TestToastXss` (1) — `_showRagToast`-Body nutzt `textContent` ODER `_escapeHtml` (kein nacker `innerHTML` mit Reason-String).
- `TestJsSyntaxIntegrity` (1) — `node --check` ueber alle inline `<script>`-Bloecke aus `ADMIN_HTML` (skipped wenn `node` fehlt). Lesson aus P203b: ein einzelner SyntaxError invalidiert den gesamten Block.
- `TestHelHtmlSmoke` (2) — `ADMIN_HTML` enthaelt alle Toast-Pieces, genau ein `id="ragToast"` (kein Doppel-Render).

**Kollateral-Fix:** `test_projects_ui::TestP196JsFunctions::test_uploads_are_sequential` hat einen `[:3000]`-Slice auf `_uploadProjectFiles`-Body — durch das neue `_showRagToast` und die Toast-Verdrahtung wuchs der Body um ~600 Zeichen, der `for (let i = 0; i < files.length`-Marker rutschte ueber die 3000-Zeichen-Grenze. Slice auf 4500 erhoeht (gleiche Datei).

**Teststand:** 1797 baseline (P203d-3) → **1817 passed** (+20 P205-Tests), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 0 neue Failures aus P205. 1 skipped (existing).

**Effekt fuer den User:** Beim Datei-Upload in Hel taucht unten rechts kurz ein Toast auf:
- `📚 14 Chunks indiziert` (cyan-Border, Erfolg)
- `⚠ Datei nicht indiziert: zu gross` (rot-Border, Skip mit Grund)

3.5 Sekunden sichtbar oder bis Tap. Bei Multi-Upload sequenziell: jeder File ersetzt den vorigen Toast. Die Progress-Zeilen oberhalb des Drop-Bereichs zeigen pro File den Roh-Status (✓ fertig / ✗ Fehler) wie bisher — RAG-Status ist die Zusatzinfo, die nur im Toast erscheint.

---

## Aeltere Patches

**Patch 203d-3** — UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (Phase 5a Ziel #5 ABGESCHLOSSEN) (2026-05-03)

Dritter und letzter Sub-Patch der P203d-Aufteilung (P203d-1 Backend-Pfad / P203d-2 Output-Synthese / P203d-3 UI-Render). Schliesst Phase-5a-Ziel #5 endgueltig: nach P203d-1 reichte der Chat-Endpunkt das `code_execution`-Feld in der HTTP-Response durch, P203d-2 ersetzte den `answer` durch eine LLM-Synthese — das Nala-Frontend ignorierte das `code_execution`-Feld trotzdem komplett. Der User las nur die Synthese-Antwort, sah aber nicht den ausgefuehrten Code, die Roh-Ausgabe oder die Laufzeit. P203d-3 baut den UI-Render: nach `addMessage(reply, 'bot')` rendert `renderCodeExecution(wrapperEl, data.code_execution)` zwei Karten unter dem Bot-Bubble.

**Architektur: Frontend-only, drei Bausteine.** Alles in [`zerberus/app/routers/nala.py`](zerberus/app/routers/nala.py) — kein neues Modul, kein Backend-Touch:

- **CSS-Block** im `<style>`-Bereich (~120 Zeilen): neue Klassen `.code-card`/`.code-card-header`/`.lang-tag`/`.exit-badge` (mit `.exit-ok` gruen / `.exit-fail` rot)/`.exec-meta`/`.code-content` (`overflow-x: auto`, `max-height: 380px`)/`.exec-error-banner`/`.output-card` (default `.collapsed`)/`.output-card-header`/`.code-toggle` (44×44px Touch-Target)/`.output-content` (mit `.output-stderr`-Variant in rot)/`.truncated-marker`. Der Toggle erbt das Kintsugi-Gold-Pattern von `.expand-toggle` (P124).
- **`escapeHtml(s)`** als 3-Zeilen-Helper neben `escapeProjectText` (P201). Delegiert an `escapeProjectText` — gleiche Semantik, eigener Name fuer den XSS-Audit-Test.
- **`renderCodeExecution(wrapperEl, codeExec)`** — neuer JS-Renderer (~80 Zeilen). Liest `codeExec.{language, code, exit_code, stdout, stderr, execution_time_ms, truncated, error}`. Baut Code-Card (Header mit `lang-tag` + `exit-badge` + optional Laufzeit-Meta, Body als `<pre><code>` mit `escapeHtml`-escapeden Code, optional `exec-error-banner` wenn `error != null`). Baut Output-Card nur wenn stdout oder stderr da: Header mit Label (`📤 Ausgabe` bei exit=0, `⚠️ Ausgabe (Fehler)` sonst) + 44×44px Toggle, Body collapsed-by-default, expandiert auf Klick, enthaelt `<pre>`-Bloecke fuer stdout/stderr (escaped) plus optional `truncated-marker` wenn `truncated: true`. Insertion-Punkt: vor dem `.sentiment-triptych`-Element. Visual-Order: bubble → toolbar → code-card → output-card → triptych → export-row.

**`addMessage` retourniert wrapper.** Die Funktion gibt jetzt das DOM-Wrapper-Element zurueck, damit der Caller (sendMessage) den Renderer nachtraeglich einhaengen kann. Backwards-Compat: alle bisherigen Caller (Voice-Input, History-Replay, Late-Fallback) ignorieren den Return-Value, ihr Verhalten bleibt identisch.

**Verdrahtung in `sendMessage`** direkt nach dem Bot-Bubble-Render:

```js
const botWrapper = addMessage(reply, 'bot');
// Patch 203d-3: Code-Card + Output-Card unter Bot-Bubble (fail-quiet).
if (data.code_execution) {
    try { renderCodeExecution(botWrapper, data.code_execution); } catch (_e) {}
}
loadSessions();
```

Fail-quiet: Renderer-Crash darf den Chat-Loop nicht unterbrechen.

**Was P203d-3 bewusst NICHT macht** (kommt mit P206/P207 oder bewusste Leerstelle):

- **Keine Syntax-Highlighting-Library.** Code wird als Plain-Text in `<pre><code>` gerendert — keine Prism.js, keine highlight.js. Bewusst: PWA-Bundle bleibt leichtgewichtig, kein zusaetzlicher Asset-Pfad.
- **Kein Edit-Knopf am Code.** Der Code ist read-only. Nala bleibt Chat-Interface — wer den Code anpassen will, kopiert ihn aus dem Bubble (Toolbar hat `📋`-Button) und schickt eine neue Frage.
- **Keine Re-Run-Funktion.** Code laeuft im Backend ein Mal pro LLM-Call. Wer den gleichen Block neu pruefen will, schickt die Frage erneut (Retry-Button an User-Bubble).
- **Keine Output-Card im Skip-Fall.** Wenn `code_execution.code` leer/fehlend ist (alter Backend, kein aktives Projekt, kein Block, Sandbox disabled), rendert der Renderer NICHTS — Bot-Bubble erscheint normal. Backwards-Compat zu Backends die `code_execution` nicht kennen.
- **Keine SSE-Stream-Frames.** Code-Card und Output-Card erscheinen synchron mit dem Bot-Bubble (Chat ist non-streaming bis P203d-2).
- **Kein Telegram-Pfad.** Huginn (Telegram-Adapter) bleibt auf dem Text-Sandbox-Output via `format_sandbox_result` (P171). Nur `/v1/chat/completions` → Nala-PWA-Renderer bekommt das neue UI.
- **Kein Copy-to-Clipboard-Button am Code.** User kopiert via Browser-Long-Press oder ueber den `📋`-Toolbar-Button (kopiert aber den Synthese-Text, nicht den Card-Code).

**Tests:** 30 in [`test_p203d3_nala_code_render.py`](zerberus/tests/test_p203d3_nala_code_render.py):

- `TestRendererExists` (2) — Funktion definiert + Signatur `(wrapperEl, codeExec)`.
- `TestRendererLiestSchemaFelder` (8) — Renderer liest `code`/`language`/`exit_code`/`stdout`/`stderr`/`truncated`/`error`/`execution_time_ms` aus dem P203d-1-Schema.
- `TestRendererFallbacks` (2) — Null-Check im Eingang, Skip bei leerem Code.
- `TestRendererInsertionPoint` (1) — `insertBefore(.sentiment-triptych)` haelt die Visual-Order.
- `TestSendMessageVerdrahtung` (5) — `addMessage` retournt wrapper, Caller bindet ihn (`= addMessage(reply, 'bot')`), Renderer-Aufruf in `sendMessage` mit `data.code_execution`, Reihenfolge addMessage-vor-Renderer, try/catch.
- `TestXssEscape` (4) — `escapeHtml`-Helper definiert, `escapeProjectText` nicht geloescht (P201-Audit darf nicht brechen), Min-Count 4 `escapeHtml(`-Aufrufe im Renderer (lang+code+stdout+stderr), keine `innerHTML`-Assigns ohne `escapeHtml`.
- `TestCss` (5) — `.code-card`/`.output-card`-Klassen, `.code-toggle` mit `min-height: 44px` UND `min-width: 44px`, `.code-content` mit `overflow-x: auto`, `.output-card.collapsed`-State, `.exit-badge.exit-ok`/`.exit-fail`-Farbcodes.
- `TestJsSyntaxIntegrity` (1, skipped wenn node fehlt) — `node --check` ueber alle inline `<script>`-Bloecke aus `NALA_HTML`. Lesson aus P203b: ein einzelner SyntaxError invalidiert den gesamten Block.
- `TestNalaEndpointSmoke` (1) — `GET /nala/` liefert HTML mit `function renderCodeExecution`, `data.code_execution`, `.code-card`, `.output-card`.

**Logging-Tag:** keiner. Frontend-only-Patch, alle Backend-Logs (`[SANDBOX-203d]`/`[SYNTH-203d-2]`) bleiben aus P203d-1/2.

**Teststand:** 1767 baseline (P203d-2) → **1797 passed** (+30 P203d-3-Tests), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 0 neue Failures.

**Effekt fuer den User:** Bei aktivem Projekt + Code-Block in der Antwort + erfolgreicher Sandbox-Execution sieht Nala jetzt nicht nur die menschenlesbare Synthese (`Der Inhalt der Datei ist 42.`), sondern darunter ZWEI Karten: (a) eine Code-Card mit Sprach-Tag, exit-Status (gruen/rot) und dem ausgefuehrten Code-Block; (b) eine Output-Card mit collapsible stdout/stderr — eingeklappt by default, expandiert auf Tap. Der gesamte Code-Execution-Pfad ist damit von der LLM-Antwort bis ins UI sichtbar.

**Effekt fuer die naechste Coda-Session:** Phase-5a-Ziel #5 ist abgeschlossen. Die naechsten Patches widmen sich anderen Zielen: P205 (RAG-Toast in Hel — Phase-5a-Schuld aus P199), P206 (HitL-Gate vor Code-Execution — Ziel #6 baut auf P203d auf), P207 (Diff-View / Snapshots / Rollback — Ziel #9 + #10), P208 (Spec-Contract / Ambiguitäts-Check — Ziel #8). Helper aus P203d-3 die direkt nutzbar sind: `addMessage` retourniert das Wrapper-Element (P206 HitL-Confirm-Card kann sich nachtraeglich einhaengen), `escapeHtml`-Helper im Frontend (XSS-Schutz fuer alle neuen Karten), `.code-card`/`.output-card`-CSS-Pattern als Vorlage fuer neue Karten.

---

**Patch 203d-2** — Output-Synthese fuer Sandbox-Code-Execution im Chat (Phase 5a #5, Backend-Loop schliesst) (2026-05-03)

Zweiter Sub-Patch der P203d-Aufteilung (P203d-1 Backend-Pfad / P203d-2 Output-Synthese / P203d-3 UI). Schliesst den Backend-Loop von Phase-5a-Ziel #5: nach P203d-1 reichte der Chat-Endpunkt den rohen `SandboxResult` (stdout/stderr/exit_code) als additives `code_execution`-Feld durch — der `answer`-String enthielt aber weiter den Original-Code-Block, ohne menschenlesbare Erklaerung des Outputs. P203d-2 fuegt einen zweiten LLM-Call ein, der Original-Frage + Code + stdout/stderr in eine zusammenfassende Antwort verwandelt und damit den `answer` ersetzt.

**Architektur: Pure-Function-Schicht plus Async-Wrapper.** Neues Modul [`zerberus/modules/sandbox/synthesis.py`](zerberus/modules/sandbox/synthesis.py) — Pattern analog zu `prosody/injector.py` (P204) und `persona_merge.py` (P197):

- `should_synthesize(payload) -> bool` — Trigger-Gate. True wenn `exit_code != 0` (Crash → Erklaerung noetig) ODER `exit_code == 0` UND `stdout` nicht leer (Output → Aufbereitung). False bei `payload is None`, fehlendem `exit_code` oder `exit_code == 0` mit leerem stdout (nichts zu sagen).
- `_truncate(text, limit=5000)` — Bytes-genau truncaten (UTF-8-encoded), ASCII-Marker `\n…[gekuerzt]` am Ende. Multi-Byte-sicher via `decode(errors='ignore')`.
- `build_synthesis_messages(user_prompt, payload) -> list[dict]` — Pure-Function: System-Prompt ("Fasse menschenlesbar zusammen, wiederhole den Code nicht stumpf, erklaere Fehler") plus User-Message mit Original-Frage, fenced Code-Block, fenced stdout, fenced stderr. Marker `[CODE-EXECUTION — Sprache: ... | exit_code: ...]` und `[/CODE-EXECUTION]` substring-disjunkt zu `[PROJEKT-RAG]` (P199), `[PROJEKT-KONTEXT]` (P197), `[PROSODIE]` (P204).
- `synthesize_code_output(user_prompt, payload, llm_service, session_id)` — Async-Wrapper. Trigger-Check, dann LLM-Call via `LLMService.call(messages, session_id)`, Result-Validation (Tuple, non-empty string), Logging-Tag `[SYNTH-203d-2]`. Fail-Open: jeder Crash, leere LLM-Antwort, falsches Result-Format → Returns `None` → Caller behaelt Original-Answer.

**Verdrahtung in `legacy.py::chat_completions`.** Direkt nach dem P203d-1-Block (vor dem Sentiment-Triptychon):

```python
if code_execution_payload is not None:
    try:
        synthesized = await synthesize_code_output(
            user_prompt=last_user_msg,
            payload=code_execution_payload,
            llm_service=llm_service,
            session_id=session_id,
        )
        if synthesized:
            answer = synthesized
    except Exception as _synth_err:
        logger.warning(f"[SYNTH-203d-2] Pipeline-Fehler (fail-open): {_synth_err}")
```

**Reihenfolge-Aenderung — `store_interaction` aufgeteilt.** Vorher (P203d-1): User-Insert + Assistant-Insert + `update_interaction()` als Block VOR der Sandbox-Stelle. Nachher (P203d-2): User-Insert frueh (Eingabe ist endgueltig), Assistant-Insert + `update_interaction()` NACH der Synthese, damit der gespeicherte `answer` der finale Output ist und nicht der Roh-Output mit Code-Block. Falls Synthese skipte oder fehlschlug, ist es der Original-LLM-Output (Backwards-Compat zu P203d-1).

**Was P203d-2 bewusst NICHT macht** (kommt mit P203d-3/P207):

- **Kein UI-Render.** Code-Block + Output-Block in Nala-Frontend ist P203d-3 — separate Endpunkt-Erweiterung mit ggf. SSE-Stream-Frame.
- **Kein zweiter `store_interaction`-Eintrag fuer den Original-Output.** Die `interactions`-Tabelle bekommt nur den finalen `answer`. Wer den Roh-Output braucht, liest das `code_execution`-Feld in der HTTP-Response.
- **Keine Cost-Aggregation in `interactions.cost`.** Der Synthese-Call addiert eigene Tokens, wird aber nicht aufsummiert (Schuld vermerkt; HitL-Token-Tracking-Patch wird das adressieren).
- **Kein Streaming.** `chat_completions` bleibt synchron. SSE `code_execution`/`synth`-Frames sind P203d-3-Thema.
- **Keine writable-Mount-Aenderung.** P203d-1 forciert weiter `writable=False`. Sync-After-Write kommt mit P207.
- **Kein eigener Fehler-Markierer im `answer`.** Bei Synthese-Fail bleibt der Roh-Output mit Code-Block — kein Hinweis "Synthese fehlgeschlagen". Frontend sieht das implizit am `code_execution`-Feld.

**Tests:** 47 in [`test_p203d2_chat_synthesis.py`](zerberus/tests/test_p203d2_chat_synthesis.py):

- `TestShouldSynthesize` (8) — Trigger-Gate: None/Non-Dict, exit_code-Varianten, exit=0+leerer stdout vs. exit=0+stdout-da, exit!=0 mit/ohne stderr.
- `TestTruncate` (5) — Short/empty/at-limit/over-limit, Multi-Byte-UTF-8-safe.
- `TestBuildSynthesisMessages` (9) — Format der zwei Messages, Original-Prompt im User-Msg, Code-Fence, stdout/stderr nur wenn vorhanden, exit_code im Marker, Marker-Disjunktheit, System-Prompt-Inhalte, Truncate bei Mega-Stdout.
- `TestSynthesizeCodeOutput` (8) — Async-Wrapper: Skip-bei-None-payload, Skip-bei-exit0-leer, Happy-Path, exit!=0-Pfad, Fail-Open bei LLM-Crash/leerer-Antwort/Whitespace/Non-Tuple.
- `TestP203d2SourceAudit` (7) — Synthese-Modul existiert, Logging-Tag, Imports in legacy.py, korrekte Args-Reihenfolge im Aufruf, Reihenfolge-Garantie (Synthese VOR Assistant-Store), User-Store FRUEH, Truncate-Limit-Konstante.
- `TestE2ESynthesis` (10) — End-to-End ueber `chat_completions` mit Two-Step-Mock-LLM (Erst-Call: Code-Block, Zweit-Call: Synthese): answer ersetzt bei Happy-Path, Fehler-Erklaerung bei exit!=0, User-Prompt im Synthese-Call, kein Synthese-Call bei Plain-Text/leerem-Output/inaktivem-Projekt/disabled-Sandbox, Original-Answer bleibt bei Synthese-Crash/leerer-Synthese, OpenAI-Schema unangetastet.

**Logging-Tag:** `[SYNTH-203d-2]` (separat von `[SANDBOX-203d]` aus P203d-1, damit Operations-Logs den Synthese-Pfad isoliert beobachten koennen).

**Teststand:** 1720 baseline (P203d-1) → **1767 passed** (+47 P203d-2-Tests), 4 xfailed pre-existing, 3 failed pre-existing aus Schuldenliste (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 0 neue Failures.

**Effekt fuer den User:** Bei aktivem Projekt + Code-Block in der Antwort + erfolgreicher Sandbox-Execution liest Nala statt `\`\`\`python\nprint(2+2)\n\`\`\`` jetzt eine Antwort wie `Das Ergebnis ist 4.` Bei einem Fehler (`exit_code=1`, stderr=`ZeroDivisionError`) liefert Nala eine Fix-Empfehlung anstatt nur den Crash-Output. Das `code_execution`-Feld bleibt zusaetzlich in der HTTP-Response — Frontends koennen den Roh-Output und die Synthese parallel rendern (P203d-3 wird das Layout bauen).

**Effekt fuer die naechste Coda-Session:** P203d-3 kann sofort starten — Synthese-Output kommt im normalen `choices[0].message.content`-Pfad, das `code_execution`-Feld liefert separat den Code-Block + stdout/stderr fuers UI. Frontend-Patch ist orthogonal: Nala-Renderer baut Code-Card + Synthese-Card unter dem Chat-Bubble-Layout. Nach P203d-3 ist Phase-5a-Ziel #5 vollstaendig abgeschlossen.

---

**Patch 203c** — Sandbox-Workspace-Mount + execute_in_workspace (Phase 5a #5, Zwischenschritt) (2026-05-02)

Dritter Sub-Patch der P203-Aufteilung (P203a Workspace-Layout + P203b Hel-Hotfix + P203c Sandbox-Mount). P203a hatte den Working-Tree gelegt (`<data_dir>/projects/<slug>/_workspace/` mit Hardlink+Copy-Fallback) — die Sandbox aus P171 hatte aber explizit "Kein Volume-Mount vom Host" als Sicherheitsregel. P203c erweitert genau diese Stelle um einen kontrollierten Mount-Pfad: `SandboxManager.execute(...)` nimmt jetzt zwei keyword-only Parameter `workspace_mount: Optional[Path] = None` und `mount_writable: bool = False`. Default-Pfad ohne Mount bleibt unverändert (Backwards-Compat für den Huginn-Pipeline-Flow).

**Architektur: Read-Only-Default, Defense-in-Depth zwei-stufig.** Wenn `workspace_mount` gesetzt ist, ergänzt `_run_in_container` die docker-args um `-v <abs>:/workspace[:ro]` plus `--workdir /workspace` — `:ro`-Suffix hängt nur weg, wenn der Caller ausdrücklich `mount_writable=True` setzt. Damit kann der Sandbox-Code Files lesen (Source-Trees, Config, Daten), aber das Workspace nicht von innen verändern, ohne dass die ausführende Schicht das explizit zulässt. P203d wird Read-Write erst bei Code-Generation-Pfaden mit anschließendem `sync_workspace`-Re-Sync nutzen.

**Mount-Validation als Early-Reject.** Vor dem `docker run`-Aufruf prüft `execute()`, dass der Mount-Pfad existiert UND ein Verzeichnis ist — sonst SandboxResult mit `error=...` und Exit-Code -1. Verhindert obskure docker-Fehlermeldungen und macht das Failure-Mode deterministisch testbar (kein Docker-Daemon nötig).

**Convenience-Wrapper `execute_in_workspace` in `projects_workspace.py`.** Der Caller (P203d, künftige CLI) ruft nicht direkt `SandboxManager.execute(workspace_mount=..., ...)`, sondern `execute_in_workspace(project_id, code, language, base_dir, *, writable=False, timeout=None)`. Der Wrapper zieht den Slug aus der DB via `projects_repo.get_project`, baut den Workspace-Pfad via `workspace_root_for(slug, base_dir)`, validiert per `is_inside_workspace(workspace_root, base_dir)` (Defense-in-Depth gegen Slug-Manipulation — falls ein entartetes `slug=../etc` jemals durch den Sanitizer rutscht), legt den Workspace-Ordner an wenn er fehlt (Projekt existiert noch ohne Files), und reicht an `get_sandbox_manager().execute(...)` durch. Returns `None` bei unbekanntem Projekt, deaktivierter Sandbox oder Sicherheits-Reject — damit kann der Caller einheitlich auf `None`-Pfade reagieren (Datei-Fallback wie in P171).

**Was P203c bewusst NICHT macht** (kommt mit P203d/P206):
- **Kein HitL-Gate.** Die HitL-Bestätigung (Phase-5a #6) hängt davor, kommt mit P206. P203c läuft direkt durch, weil RO-Mount + hart-isolierte Sandbox (no-network, read-only-rootfs, no-new-privileges, pids/cpu/memory-limits, kein Volume-Mount sonst) den blast-radius klein halten.
- **Kein Tool-Use-LLM-Pfad.** P203d verdrahtet die Chat-Pipeline (Code-Detection im Output, Sandbox-Roundtrip, Output-Synthese, UI-Render).
- **Kein Sync-After-Write.** Falls jemand `writable=True` aufruft und der Sandbox-Code Files erzeugt: P203d muss `sync_workspace(project_id, base_dir)` hinterher rufen, damit die DB + RAG die Änderungen sehen. P203c liefert nur die Brücke.
- **Keine Image-Pull-Logik.** Healthcheck (P171) bleibt unverändert — Caller muss vorher prüfen.

- **Pure-Function-Schicht:** [`zerberus/modules/sandbox/manager.py::SandboxManager._run_in_container`](zerberus/modules/sandbox/manager.py) — Mount-Block setzt `-v <abs>:/workspace[:ro]` + `--workdir /workspace`, vor `image, *run_args`. Pfad-Resolution via `workspace_mount.resolve(strict=False)` damit Symlinks/8.3-Windows-Namen Docker nicht verwirren. Logging-Tag `[SANDBOX-203c]` für Mount-Stelle.
- **Validation-Schicht:** `execute()` checkt `workspace_mount.exists()` + `.is_dir()` vor `_image_and_command`. Fail-Mode: `SandboxResult(exit_code=-1, error=...)` — kein docker-Aufruf.
- **Convenience:** [`zerberus/core/projects_workspace.py::execute_in_workspace`](zerberus/core/projects_workspace.py) — async, zieht Slug aus DB, validiert workspace_root inside base_dir per `is_inside_workspace`, legt Ordner an, reicht an `get_sandbox_manager().execute(...)` durch. Logging-Tag `[WORKSPACE-203c]`.
- **Tests:** 17 in [`test_p203c_sandbox_workspace.py`](zerberus/tests/test_p203c_sandbox_workspace.py): docker-args-Audit ohne Mount unverändert (Backwards-Compat), Mount-RO-Default, Mount-Writable, Mount-nonexistent → Error, Mount-is-File → Error, Disabled-Sandbox → None auch mit Mount, Blocked-Pattern-Vorrang, execute_in_workspace mit fehlendem Projekt → None, korrekter Mount-Pfad-Passthrough, writable-Passthrough, Workspace-Anlage on-demand, Slug-Traversal-Reject (Defense-in-Depth), Source-Audit Mount-Stelle, Source-Audit execute_in_workspace, Disabled-Sandbox-Passthrough via Convenience, Timeout-Passthrough, Mount-Pfad-resolve()-stable.
- **Logging-Tag:** `[SANDBOX-203c]` (Mount-Setup) und `[WORKSPACE-203c]` (Convenience-Reject).
- **Teststand:** 1685 baseline (P204) → **1705 passed** (+20: 17 neue P203c-Tests + 3 die durch geänderten Default-Pfad jetzt mit gezählt werden), 4 xfailed pre-existing, 0 neue Failures.
- **Effekt für die nächste Coda-Session:** P203d kann sofort starten — `execute_in_workspace(project_id, code, language, base_dir, *, writable=...)` ist die einzige öffentliche API für Workspace-gebundene Code-Execution. Bei Code-Generation reicht: `result = await execute_in_workspace(...)` mit Projekt-ID aus dem aktiven Chat-Header (P201), `result.stdout`/`result.stderr` für die UI-Synthese, ggf. `await sync_workspace(...)` wenn `writable=True`. HitL-Gate wickelt sich später per P206 davor.

---

**Patch 204** — Prosodie-Kontext im LLM (Phase 5a #17, unabhängig einschiebbar) (2026-05-02)

Schließt Phase-5a-Ziel #17: Die Prosodie-Pipeline (P189-193) lieferte ihre Daten bisher nur ans UI-Triptychon (P192) — DeepSeek bekam beim Voice-Input keinen Kontext, das LLM "hörte" die Stimme nicht. P190 hatte zwar einen rudimentären `[Prosodie-Hinweis ...]`-Block hinzugefügt, aber nur Gemma (kein BERT, kein Konsens-Label) und in einem ad-hoc-Format. P204 baut die Brücke richtig: ein markierter `[PROSODIE]...[/PROSODIE]`-Block analog `[PROJEKT-RAG]` (P199), mit BERT-Sentiment und Mehrabian-Konsens.

**Format-Entscheidung: qualitative Labels, keine Zahlen (Worker-Protection P191).** Confidence/Score/Valence/Arousal werden im Konsens-Label verkocht — das LLM bekommt nur menschenlesbare Beschreibungen wie "leicht positiv", "ruhig", "deutlich negativ", "inkongruent — Text positiv, Stimme negativ". Damit kann das Modell die Daten nicht zu Performance-Bewertungen aus Stimmungsdaten missbrauchen. Tests verifizieren die Number-Free-Property mit einem Regex (`\d+\.\d+`, `%`, `\b\d+\b`).

**Architektur: Pure-Function `build_prosody_block` plus Wrapper `inject_prosody_context`.** Pure-Schicht baut den Block-String (lookup-table-based mood/tempo-Übersetzung de, Mehrabian-Konsens-Logik aus `utils.sentiment_display` repliziert), Wrapper hängt am System-Prompt an mit Idempotenz-Check (Marker-Substring im Prompt → kein zweiter Block). BERT-Parameter sind keyword-only und additiv — bestehende P190-Aufrufer ohne BERT bekommen einen Block ohne Sentiment-Text-Zeile (Stimm-only-Pfad).

**Verdrahtung in `legacy.py /v1/chat/completions`.** Der `X-Prosody-Context`-Header (vom Whisper-Endpoint übers Frontend durchgereicht) plus `X-Prosody-Consent: true` schaltet den Block frei. Server-seitig wird BERT auf der letzten User-Message berechnet (fail-open: BERT-Fehler → kein Sentiment-Text-Zeile, Block läuft ohne) und an `inject_prosody_context` durchgereicht. Voice-only-Garantie: der Header existiert nur nach Whisper-Roundtrip (Frontend setzt ihn nicht bei getipptem Text) — defense-in-depth über Stub-Source-Check filtert versehentliche Pseudo-Contexts.

**Mehrabian-Konsens (Pure):** BERT positiv + Prosody-Valenz negativ → `"inkongruent — ..."`. Sonst: Confidence > 0.5 → Stimme dominiert (Stimm-Mood gewinnt), Confidence ≤ 0.5 → BERT-Fallback (`"deutlich/leicht positiv/negativ"` oder `"neutral"`). Schwellen identisch zu `utils/sentiment_display.py` (P192) — UI-Konsens und LLM-Konsens dürfen nicht voneinander abweichen.

- **Pure-Schicht:** [`zerberus/modules/prosody/injector.py::build_prosody_block`](zerberus/modules/prosody/injector.py) plus Helper `_consensus_label`, `_bert_qualitative` und Marker-Konstanten `PROSODY_BLOCK_MARKER` / `PROSODY_BLOCK_CLOSE`. Lookup-Tables `_BERT_LABEL_DE`, `_PROSODY_MOOD_DE`, `_PROSODY_TEMPO_DE`. Schwellen `_BERT_HIGH=0.7`, `_PROSODY_DOMINATES_CONFIDENCE=0.5`, `_MIN_CONFIDENCE_FOR_BLOCK=0.3`.
- **Wrapper:** `inject_prosody_context(system_prompt, prosody_result, *, bert_label=None, bert_score=None)` — Backward-Compat-Signatur (keyword-only-Parameter additiv). Idempotent. Logging `[PROSODY-204]` wenn BERT mitgegeben, `[PROSODY-190]` ohne.
- **Verdrahtung:** [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py) — JSON-Parse + Type-Guard (nur `dict`), BERT-Call in try/except (fail-open), Aufruf mit Keyword-Args.
- **Tests:** 33 in [`test_p204_prosody_context.py`](zerberus/tests/test_p204_prosody_context.py) (`TestBuildProsodyBlock` 9: Marker, Stimme/Tempo, Konsens-mit-BERT, Konsens-ohne-BERT, Stub-Reject, Low-Conf-Reject, None/Wrong-Type, Invalid-Conf, Unknown-Mood-Fallback; `TestWorkerProtectionNoNumbers` 3 parametrisiert: Regex-Check `%`/`\d+\.\d+`/`\b\d+\b`; `TestConsensusLabel` 6: Inkongruenz, Voice-dominiert, BERT-Fallback, Neutral, ohne-BERT, Invalid-Inputs; `TestBertQualitative` 6: positive/negative high/low, neutral, invalid; `TestInjectWithBert` 5: append-mit-BERT, Stub-skip, Low-Conf-skip, leerer-Base, Idempotenz; `TestP204LegacyVerdrahtung` 6 Source-Audit; `TestMarkerUniqueness` 3: Format, Distinct-vom-PROJEKT-RAG/PROJEKT-KONTEXT). Plus 6 nachgeschärfte Tests in [`test_prosody_pipeline.py::TestInjectProsodyContext`](zerberus/tests/test_prosody_pipeline.py) — Format-Assertions auf neue Marker (`PROSODY_BLOCK_MARKER`, `PROSODY_BLOCK_CLOSE`, qualitative Labels statt Zahlen, Konsens-Zeile, Inkongruenz-via-BERT, neuer Idempotenz-Test).
- **Logging-Tag:** `[PROSODY-204]` für BERT-erweiterten Block, `[PROSODY-190]` bleibt für Stimm-only-Pfad.
- **Teststand:** 1645 baseline (P203b) → **1685 passed** (+40), 4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste (`edge-tts` + `test_rag_dual_switch.test_fallback_logic`, beide nicht blockierend).
- **Effekt für den User:** Bei Voice-Input liest DeepSeek im System-Prompt jetzt einen Block wie `[PROSODIE — Stimmungs-Kontext aus Voice-Input]\nStimme: muede\nTempo: langsam\nSentiment-Text: leicht positiv (BERT)\nSentiment-Stimme: muede (Gemma)\nKonsens: muede\n[/PROSODIE]`. Nala kann den Ton subtil anpassen (zurücknehmen wenn jemand müde klingt, nachfragen bei Stress) ohne plakative "Du klingst traurig!"-Reaktionen. Bei getipptem Text: kein Block, Chat unverändert. Bei deaktiviertem Consent: kein Block. Bei Stub-Pipeline (kein Modell geladen): kein Block.
- **Was P204 bewusst NICHT macht:** Persistierung der Prosodie-Daten in der DB (Worker-Protection — Daten sind one-shot pro Request). Kein neues UI (Triptychon P192 zeigt schon). Keine Pipeline-Änderung (Brücke zum LLM, nicht Refactor von Whisper/Gemma/BERT). Kein Voice-Indicator-Header über `X-Voice-Input` o.ä. — der bestehende `X-Prosody-Context`-Header IST der Voice-Indikator (Frontend setzt ihn nur nach Whisper-Roundtrip), Stub-Source-Check + Consent-Header sind defense-in-depth.

---

**Patch 203b** — Hel-UI-Hotfix (BLOCKER, Chris-Bugmeldung) (2026-05-02)

Behebt einen Blocker in Hel: Die UI rendert, aber NICHTS ist anklickbar — Tabs wechseln nicht, Buttons reagieren nicht, Formulare nicht bedienbar. Nala lief unauffällig. Auffällig erst nach P200/P202 (PWA-Roll-Out + Cache-Wipe), Symptom verschleiert vorher durch Browser-Cache.

Root-Cause: Im `loadProjectFiles`-Renderer (eingeführt mit P196) stand fehlerhaftes Python-Quote-Escaping. Im Python-Source: `+ ',\'' + _escapeHtml(f.relative_path).replace(/'/g, "\\'") + '\')" '` — Python evaluiert die Escape-Sequenzen im Triple-Quoted-String und produziert in der ausgelieferten HTML/JS-Zeile: `+ ',''  + _escapeHtml(...).replace(/'/g, "\'") + '')" '`. JavaScript parst das als `+ ',' '' +` (zwei adjacent String-Literale ohne Operator) und wirft `SyntaxError: Unexpected string`. Ein einziger Syntax-Fehler in einem `<script>`-Block invalidiert den **gesamten** Block — alle Funktionen darin werden nicht definiert, inkl. `activateTab`, `toggleHelSettings`, `loadProjects`, `loadMetrics`. Damit: keine Klicks, keine Tabs, kein nichts. Nala unbetroffen, weil eigener Renderer.

Fix: Inline `onclick="deleteProjectFile(...)"` durch `data-*`-Attribute (`data-project-id`, `data-file-id`, `data-relative-path`) plus Event-Delegation per `addEventListener` ersetzt. Pattern ist immun gegen Quote-Escape-Probleme (Filename geht durch `_escapeHtml` direkt ins Attribut, statt durch eine fragile JS-String-Concat-Kette mit `replace(/'/g, ...)`) und gleichzeitig XSS-sicher.

Warum erst nach P200/P202 sichtbar: Bug existiert seit P196. Bis P200 hatte der Browser eine ältere Hel-Version aus dem HTTP-Cache. Mit P200 (SW-Roll-Out + Cache-v1) wechselte der Cache, mit P202 (SW-v2-Activate + Wipe) wurde alles geräumt — der Browser holte die echte aktuelle Hel-Seite mit dem P196-Bug. Chris' manuelles Unregister + Cache-Wipe + Hard-Refresh hat das Symptom dann deutlich gemacht.

- **Fix:** [`hel.py::loadProjectFiles`](zerberus/app/routers/hel.py) — `<button class="proj-file-delete-btn" data-project-id="..." data-file-id="..." data-relative-path="...">` plus `list.querySelectorAll('.proj-file-delete-btn').forEach(btn => btn.addEventListener('click', () => deleteProjectFile(...)))`. Kein `onclick`-Attribut, kein `replace(/'/g, "\\'")` mehr.
- **Tests:** 10 neue in [`test_p203b_hel_js_integrity.py`](zerberus/tests/test_p203b_hel_js_integrity.py) — drei Source-Audit-Tests gegen das alte Bug-Pattern (`+ ',''`, `onclick="deleteProjectFile(`, `replace(/'/g, "\\'")`), fünf Source-Audit-Tests für die neue Event-Delegation (Klassen-Name + drei data-Attribute + addEventListener-Nähe), ein Smoke-Test gegen den Endpunkt-Output, und ein **JS-Integrity-Test** der ALLE inline `<script>`-Blöcke aus `ADMIN_HTML` extrahiert und mit `node --check` validiert (skipped wenn `node` nicht im PATH). Letzterer hätte den Bug bei P196 sofort gefangen — nachträglich eingebaut als Schutz vor Wiederholung. Plus 1 angepasster bestehender Test (`test_projects_ui::test_file_list_has_delete_button` — Block-Range erweitert + Klassen-Name-Check, weil event-delegation den Block länger macht).
- **Logging:** Kein neuer Tag — Hotfix bleibt unter `[PWA-200]`/`[PWA-202]`-Doku-Klammer im Quelltext.
- **Teststand:** lokal 1635 baseline (P203a) → **1645 passed** (+10), 4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste.
- **Effekt für den User:** Hel wieder voll bedienbar — Tabs wechseln, Buttons funktionieren, Forms gehen. Beim Laden der Projekte-Tab werden Files mit Lösch-Button korrekt gerendert, Klick auf Lösch-Button feuert die Delete-Bestätigung. Kein Browser-Cache-Reset mehr nötig (Bug-Pattern aus dem HTML raus).
- **Lessons:** (a) JS-Syntax-Errors in inline `<script>`-Blöcken sind silent-killers für die ganze Page — ein Test der `node --check` über alle Inline-Scripts laufen lässt fängt das früh. (b) Inline `onclick` mit String-Concat über benutzergenerierte Daten ist immer fragil — Event-Delegation mit `data-*`-Attributen ist robust und XSS-sicher. (c) Browser-/SW-Caches können Bugs monatelang verschleiern — bei "neuen" Symptomen nach Cache-Wipe immer auch ältere Patches auf rendering-Probleme prüfen.

---

**Patch 203a** — Project-Workspace-Layout (Phase 5a #5, Vorbereitung) (2026-05-02)

Eigenständige Aufteilung von P203 (Code-Execution-Pipeline, Phase-5a-Ziel #5) durch Coda: Das Original-Ziel ist groß (Workspace + Sandbox-Mount + Tool-Use-LLM + UI-Synthese), wird in drei Sub-Patches zerlegt. P203a legt heute das Workspace-Layout — die Sandbox kann Dateien später nur dann sinnvoll mounten, wenn sie an ihrem `relative_path` (statt unter den SHA-Hash-Pfaden) im Filesystem stehen. Pro Projekt entsteht beim Upload/Template-Materialize ein echter Working-Tree unter `<data_dir>/projects/<slug>/_workspace/`.

Architektur-Entscheidung: **Hardlink primär (`os.link`), Copy als Fallback (`shutil.copy2`).** Hardlinks haben gleiche Inode wie der SHA-Storage — kein Plattenplatz-Verbrauch, instantan, atomar. Bei `OSError` (cross-FS, NTFS-without-dev-mode, FAT32, Permission) fällt der Helper auf `shutil.copy2`. Die gewählte Methode wird im Return ausgewiesen (`"hardlink"` / `"copy"` / `None`-bei-noop) und geloggt — auf Windows-Test-Maschinen ohne dev-mode wird damit auch der Copy-Pfad live mitgetestet (Monkeypatch-Test simuliert `os.link`-Failure).

**Atomic via Tempfile + os.replace.** Auch im Workspace, nicht nur im SHA-Storage. Grund: parallele Sandbox-Reads (P203b) dürfen nie ein halb-geschriebenes Workspace-File sehen. Pattern dupliziert (statt Import aus hel.py), weil das Workspace-Modul auch ohne FastAPI-Stack importierbar bleiben muss (Tests, künftige CLI).

**Pfad-Sicherheit zwei-stufig.** `is_inside_workspace(target, root)` resolved beide Pfade und prüft `relative_to` — schützt gegen `../../etc/passwd`-style relative_paths aus alten Datenbanken oder Migrations. Plus: `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet — verhindert ein versehentliches `wipe_workspace(Path("/"))` bei Slug-Manipulation.

**Best-Effort-Verdrahtung in vier Trigger-Punkten.** Upload-Endpoint (nach `register_file` + nach RAG-Index), Delete-File-Endpoint (nach `delete_file`, mit Slug aus extra `get_project`-Call vor dem DB-Delete), Delete-Project-Endpoint (`wipe_workspace` nach `delete_project`), `materialize_template` (nach jedem `register_file`). Alle vier wickeln den Workspace-Call in try/except — Hauptpfad bleibt grün auch wenn Hardlink/Copy scheitert. Lazy-Import (`from zerberus.core import projects_workspace` im try-Block) wie bei RAG, damit der Helper nicht beim Import-Time geladen wird.

**`sync_workspace` als Komplett-Resync.** Materialisiert alle DB-Files, entfernt Orphans (Files im Workspace, die nicht mehr in der DB sind). Idempotent. Nicht in den Endpoints verdrahtet (Single-File-Trigger reichen), aber als Recovery-API für künftige CLI/Reindex-Endpoint vorhanden — Pre-P203a-Files können damit in den Workspace nachgezogen werden.

**Was P203a bewusst NICHT macht** (kommt mit P203b/c):

- Sandbox-Mount auf den Workspace-Ordner — der bestehende `SandboxManager` (P171) verbietet Volume-Mount explizit ("Kein Volume-Mount vom Host"). P203b muss entweder eine Erweiterung (`workspace_mount: Optional[Path]`) oder eine Schwester-Klasse bauen. Empfehlung: Erweiterung mit Read-Only-Mount per Default, Read-Write nur explizit.
- LLM-Tool-Use-Pfad für Code-Generation — kommt mit P203c.
- Frontend-Render von Code+Output-Blöcken — kommt mit P203c.

- **Pure-Function-Schicht:** [`projects_workspace.py::workspace_root_for`](zerberus/core/projects_workspace.py) + `is_inside_workspace` — keine I/O, deterministisch, Pfad-Sicherheits-Check. Verwendbar in P203b für Mount-Source-Validation.
- **Sync-FS-Schicht:** `materialize_file` (mit Hardlink-primary + Copy-Fallback + Idempotenz via Inode/Size-Check), `remove_file` (räumt leere Eltern bis Workspace-Root), `wipe_workspace` (Sicherheits-Reject auf Wrong-Dirname). Pure-Aber-Mit-FS-I/O.
- **Async DB-Schicht:** `materialize_file_async`, `remove_file_async`, `sync_workspace` — DB-Lookup + Schicht-Wrapper.
- **Verdrahtung:** [`hel.py::upload_project_file_endpoint`](zerberus/app/routers/hel.py), [`hel.py::delete_project_file_endpoint`](zerberus/app/routers/hel.py), [`hel.py::delete_project_endpoint`](zerberus/app/routers/hel.py), [`projects_template.py::materialize_template`](zerberus/core/projects_template.py) — alle Best-Effort mit Lazy-Import.
- **Config-Flag:** [`config.py::ProjectsConfig.workspace_enabled`](zerberus/core/config.py) — Default `True`. Tests können abschalten (siehe `TestWorkspaceDisabled`-Klasse).
- **Logging-Tag:** `[WORKSPACE-203]` — Materialize, Wipe, Sync.
- **Tests:** 36 in [`test_projects_workspace.py`](zerberus/tests/test_projects_workspace.py) (4 Pure-Function inkl. Traversal-Reject + No-IO; 6 materialize_file inkl. nested-dirs + idempotent + missing-source + Copy-Fallback via monkeypatched `os.link`; 5 remove_file inkl. cleans-empty-parents + keeps-non-empty + traversal-reject; 3 wipe_workspace inkl. rejects-wrong-dirname; 4 sync_workspace inkl. removes-orphans; 3 async-Wrapper; 4 Endpoint-Integration mit echten Hel-Endpoints; 1 workspace_disabled-Pfad; 5 Source-Audit-Tests für die vier Verdrahtungs-Stellen).
- **Teststand:** lokal 1599 baseline → **1635 passed** (+36), 4 xfailed pre-existing, 2 failed pre-existing aus Schuldenliste (`edge-tts` + `test_rag_dual_switch.test_fallback_logic`, beide nicht blockierend).
- **Effekt für die nächste Coda-Session:** P203b kann sofort starten — `workspace_root_for(slug, base_dir)` liefert den Mount-Pfad, `is_inside_workspace` validiert User-Inputs, `sync_workspace` ist Recovery-API. P203b's Job ist nur noch: `SandboxManager.execute` um `workspace_mount` erweitern + `execute_in_workspace`-Convenience + Tests.

---

**Patch 202** — PWA-Auth-Hotfix: SW-Navigation-Skip + Cache-v2 (2026-05-02)

Behebt einen kritischen Bug aus P200: Hel war im Browser nur noch als JSON-Antwort `{"detail":"Not authenticated"}` sichtbar — kein Basic-Auth-Prompt mehr. Nala und Huginn unauffällig. Ursache: Der von P200 eingeführte Service-Worker fängt im Scope `/hel/` ALLE GET-Requests ab und macht `event.respondWith(fetch(event.request))`. Bei navigierenden Top-Level-Anfragen liefert der SW damit die 401-Response unverändert an die Page zurück — und der Browser ignoriert in diesem Fall den `WWW-Authenticate: Basic`-Header, der normalerweise den nativen Auth-Prompt triggert. Ergebnis: User sah JSON statt Login-Dialog.

Architektur-Entscheidung: **Navigation-Requests gar nicht erst abfangen.** Statt nachträglich auf 401 zu reagieren oder Auth-Header zu injecten, returnt der SW jetzt früh wenn `event.request.mode === 'navigate'`. Die Navigation läuft durch den nativen Browser-Stack — inklusive Auth-Prompt, HTTPS-Indikator, Mixed-Content-Warnung, etc. Das ist ohnehin sauberere PWA-Hygiene: SWs cachen statische Assets, nicht HTML-Pages.

**Cache-Versions-Bump auf -v2.** Damit der activate-Hook der neuen SW-Version den verseuchten v1-Cache räumt, der noch /hel/-Navigations-Antworten enthalten könnte. Da der SW selbst per `Cache-Control: no-cache` ausgeliefert wird, bekommt jeder Browser den neuen SW beim nächsten Reload, ohne manuelles Eingreifen — der activate räumt dann automatisch.

**APP_SHELL ohne Root-Pfad.** Vorher enthielt `HEL_SHELL` den Eintrag `"/hel/"` — beim install-Hook versuchte der SW per `cache.addAll(APP_SHELL)` den Hel-Root zu cachen, was wegen Basic-Auth mit 401 fehlschlug. `Promise.all` rejected dann komplett, die `.catch(() => null)`-Klausel im Code swallow den Fehler still, aber semantisch falsch. Jetzt sind die SHELL-Listen rein statische Assets, die alle 200 liefern — der precache läuft sauber durch.

**Side-Effect:** HTML-Reload geht jetzt immer übers Netz (kein Offline-Modus für die Hauptseite). Für den Heimserver-Use-Case akzeptabel — ohne laufenden Server gibt es ohnehin keinen sinnvollen Betrieb (Chat-Endpoints, Whisper, Sandbox sind alle online-only). Wenn später ein echter Offline-Modus gewünscht ist, müsste man den Approach umkrempeln (cache-first für Navigation, mit eigenem Auth-Handling) — aktuell nicht relevant.

- **SW-Template:** [`pwa.py::SW_TEMPLATE`](zerberus/app/routers/pwa.py) — drei Zeilen früh-return für `event.request.mode === 'navigate'`, vor dem `respondWith`-Block.
- **Cache-Names:** `nala-shell-v1` → `nala-shell-v2`, `hel-shell-v1` → `hel-shell-v2` in den Endpoint-Funktionen `nala_service_worker` + `hel_service_worker`.
- **APP_SHELL:** `NALA_SHELL` und `HEL_SHELL` ohne Root-Pfad-Eintrag — nur statische Assets (`/static/css/...`, `/static/favicon.ico`, `/static/pwa/*.png`).
- **Tests:** 5 neue in [`test_pwa.py`](zerberus/tests/test_pwa.py) — Pure-Function-Test für navigation-skip im SW-Body, drei Shell-Lists-Tests (kein Root-Pfad in Nala/Hel), und ein End-to-End-Test mit `TestClient` der `verify_admin` ohne Credentials anpingt und den `WWW-Authenticate: Basic`-Header verifiziert. Plus 3 angepasste bestehende Tests (Cache-Name v2 statt v1, Asset-Audit auf Static-Assets statt Root-Pfad).
- **Logging:** Kein neuer Tag — die SW-Korrektur ist klein-genug für `[PWA-200]`/`[PWA-202]` als Doku-Hinweis im Quelltext-Header. Kein Server-seitiger Log-Output.
- **Teststand:** 1594 → **1602 passed** (+8), 4 xfailed pre-existing.
- **Effekt für den User:** `/hel/` zeigt wieder den nativen Browser-Auth-Prompt. Nach Login funktioniert Hel-UI komplett wie vor P200. Bestehende SW-v1-Installationen werden automatisch auf v2 hochgezogen, sobald der User `/hel/` neu lädt.

---

**Patch 201** — Phase 5a #4: Nala-Tab "Projekte" + Header-Setter (2026-05-02)

Schließt Phase-5a-Ziel #4 ("Dateien kommen ins Projekt") komplett ab — der Backend-Teil war seit P196 da, der Indexer seit P199, jetzt verdrahtet P201 das letzte Stück: Nala-User können vom Chat aus ein aktives Projekt auswählen, und ab da fließt der Persona-Overlay (P197) und das Datei-Wissen (P199-RAG) automatisch in jede Antwort. Vorher war diese Kombination nur über externe Clients (curl, SillyTavern, eigene Skripte) erreichbar — die Hel-CRUD-UI (P195) konnte zwar Projekte anlegen und Dateien hochladen, aber kein User in Nala konnte sie aktivieren.

Architektur-Entscheidung: **Eigener `/nala/projects`-Endpoint statt Wiederverwendung von `/hel/admin/projects`.** Drei Gründe: (a) Hel-CRUD ist Basic-Auth-gated, Nala-User haben aber JWT (zwei Auth-Welten, kein Bridge nötig). (b) Nala-User darf NIEMALS `persona_overlay` sehen — das ist Admin-Geheimnis, kann Prompt-Engineering-Spuren oder interne Tonfall-Hints enthalten, die der User nicht beeinflussen können soll. Der Endpoint slimmed das Response-Dict auf `{id, slug, name, description, updated_at}`. (c) Archivierte Projekte werden hier per Default ausgeblendet — der User soll keine "alten" Projekte sehen, die er nicht mehr nutzen soll.

**Header-Injektion zentral in `profileHeaders()`.** Statt nur den Chat-Fetch zu modifizieren, hängt P201 den `X-Active-Project-Id`-Header in die zentrale Helper-Funktion, die ALLE Nala-Calls verwenden (Chat, Voice, Whisper, Profile-Endpoints). Damit ist garantiert, dass der Projekt-Kontext konsistent gegated ist — keine Möglichkeit, dass ein neuer Endpoint den Header "vergisst".

**State in zwei localStorage-Keys.** `nala_active_project_id` (numerisch) für die Header-Injektion, `nala_active_project_meta` (JSON: id+slug+name) für den Header-Chip-Renderer ohne Re-Fetch. Beim Logout (handle401) wird beides bewusst NICHT gelöscht — der nächste Login bekommt das Projekt automatisch zurück, was für den typischen Use-Case (gleicher User, gleicher Browser) genau richtig ist.

**Zombie-ID-Schutz.** Wenn `loadNalaProjects` ein Projekt nicht mehr in der Liste findet (gelöscht oder archiviert in der Zwischenzeit), wird die aktive Auswahl automatisch geräumt. Sonst hängt der Header-Chip an einer Zombie-ID und das Backend würde einen non-existing-project-Header bekommen (was P197 zwar gracefully ignoriert, aber sauberer ist client-seitig zu räumen).

**UI-Verdrahtung minimal-invasiv.** Neuer Settings-Tab "📁 Projekte" zwischen "Ausdruck" und "System" — bestehende Tab-Mechanik wird wiederverwendet, kein neues Modal, kein Sidebar-Tab. Lazy-Loading: `loadNalaProjects()` läuft erst, wenn der User auf den Tab klickt, nicht beim Modal-Öffnen — spart Roundtrip wenn der User nur Theme ändern will. Active-Project-Chip im Chat-Header, klick öffnet Settings + springt direkt auf den Projekte-Tab. Chip nur sichtbar wenn ein Projekt aktiv ist.

**XSS-Schutz im Renderer.** Der Listen-Renderer rendert User-eingegebene Felder (name, slug, description) — alle drei laufen durch `escapeProjectText()` (wandelt `&`, `<`, `>`, `"`, `'` in HTML-Entities). Source-Audit-Test zählt mindestens drei `escapeProjectText`-Aufrufe in `renderNalaProjectsList`, damit ein vergessener Aufruf in zukünftigen Refactorings sofort auffällt.

- **Backend-Endpoint:** [`nala.py::nala_projects_list`](zerberus/app/routers/nala.py) — `GET /nala/projects`, JWT-pflichtig via `request.state.profile_name`, ruft `projects_repo.list_projects(include_archived=False)` und slimmed das Response-Dict.
- **UI-Tab:** Settings-Modal um vierten Tab "📁 Projekte" erweitert. Panel mit Aktiv-Anzeige, Aktualisieren-Button, Auswahl-löschen-Button, Listen-Container.
- **Header-Chip:** [`nala.py`](zerberus/app/routers/nala.py) — `<span id="active-project-chip" class="active-project-chip">` neben dem Profile-Badge im main-header. Goldener Pill-Border, klick öffnet Settings + Projects-Tab.
- **JS-Funktionen:** `getActiveProjectId`, `getActiveProjectMeta`, `setActiveProject`, `clearActiveProject`, `selectActiveProjectById`, `renderActiveProjectChip`, `renderNalaProjectsActive`, `renderNalaProjectsList`, `escapeProjectText`, `loadNalaProjects` — kompletter Lifecycle.
- **Header-Injektion:** [`nala.py::profileHeaders`](zerberus/app/routers/nala.py) — drei zusätzliche Zeilen, die `X-Active-Project-Id` setzen wenn aktiv. Damit wirkt es auf ALLE Nala-Calls, nicht nur Chat.
- **Lazy-Load:** [`switchSettingsTab`](zerberus/app/routers/nala.py) ruft `loadNalaProjects()` wenn der Projects-Tab aktiviert wird.
- **CSS:** `.active-project-chip` mit gold-border, transparent-Hintergrund, hover-Glow, max-width + ellipsis für lange Slugs.
- **Tests:** 21 neue in [`test_nala_projects_tab.py`](zerberus/tests/test_nala_projects_tab.py) (6 Endpoint inkl. 401, archived-versteckt, persona_overlay-NICHT-im-Response, minimal-Felder; 11 Source-Audit für Tab/Panel/Chip/JS/Header-Injektion/Lazy-Load; 2 XSS-Schutz inkl. Min-Count escapeProjectText; 1 Zombie-ID-Schutz; 1 Tab-Lazy-Load). Plus 1 nachgeschärfter Test in `test_settings_umbau.py` (alter `openSettingsModal()`-Proxy-Test → spezifischer 🔧-Emoji + icon-btn-Pattern-Check, weil P201 erlaubt openSettingsModal im Header NUR via Project-Chip).
- **Teststand:** 1572 → **1594 passed** (+22), 4 xfailed pre-existing, 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Ziel #4:** ✅ Dateien kommen ins Projekt (komplett: P196 Upload + P199 Index + P201 Nala-Auswahl). Damit sind die Ziele #1, #2, #3, #4, #16 durch. Nächste sinnvolle Patches: #5 (Code-Execution P202).

**Patch 200** — Phase 5a #16: PWA für Nala + Hel (2026-05-02)

Schließt Phase-5a-Ziel #16 ("Nala + Hel als PWA") ab. Beide Web-UIs lassen sich auf iPhone (Safari → "Zum Home-Bildschirm") und Android (Chrome → "App installieren") als eigenständige Apps installieren — Browser-Chrome verschwindet, Splash-Screen + Theme-Color stimmen, Icon im Kintsugi-Stil (Gold auf Blau für Nala, Rot auf Anthrazit für Hel). Kein Offline-Modus, keine Push-Notifications, kein Background-Sync — alles bewusst weggelassen, weil der Heimserver eh laufen muss und Huginn die Push-Schiene besetzt.

Architektur-Entscheidung: **Eigener Router `pwa.py` statt Erweiterung des Hel/Nala-Routers.** Hintergrund: `hel.router` hat router-weite Basic-Auth-Dependency (`dependencies=[Depends(verify_admin)]`), die jeden Endpoint dort gated. Würde man `/hel/manifest.json` und `/hel/sw.js` in `hel.router` definieren, würde der Browser beim Manifest-Fetch eine Auth-Challenge bekommen und der Install-Prompt nie erscheinen. Lösung: separater `pwa.router` ohne Dependencies, in `main.py` VOR `hel.router` eingehängt — gleiche URL-Pfade, aber andere Auth-Policy. FastAPI matcht die URL-Pfade global: weil `hel.router` keine Route für `/manifest.json` oder `/sw.js` definiert, gibt es keinen Konflikt; `pwa.router` gewinnt durch frühere Registrierung trotzdem die Race-Condition für eventuell hinzukommende Routes.

**Service-Worker-Scope-Trick:** Der Browser begrenzt den SW-Scope per Default auf den Pfad, von dem die SW-Datei ausgeliefert wird. `/nala/sw.js` → scope `/nala/`, `/hel/sw.js` → scope `/hel/`. Damit braucht es KEINEN `Service-Worker-Allowed`-Header und keine Root-Position für die SW-Datei. Genau die richtige Granularität: die Nala-PWA cacht nur Nala-URLs, die Hel-PWA nur Hel-URLs.

**Manifeste pro App, nicht ein gemeinsames:** Beide Apps brauchen separate Icons, Theme-Colors, Namen — zwei `manifest.json`-Endpoints sind sauberer als Conditional-Logic in einem Manifest. Konstanten `NALA_MANIFEST` und `HEL_MANIFEST` in `pwa.py` sind Pure-Python-Dicts, direkt als Tests ohne Server konsumierbar.

**Icon-Generierung deterministisch via PIL.** Skript `scripts/generate_pwa_icons.py` zeichnet 4 PNGs (Nala 192/512, Hel 192/512) — dunkler Hintergrund, großer goldener/roter Initial-Buchstabe, drei dünne Kintsugi-Adern in der Akzentfarbe als Bruchnaht-Anspielung. Deterministisch (kein RNG), damit Re-Runs bytes-identische PNGs erzeugen und der Git-Diff sauber bleibt. Fonts werden aus systemweiten Serif-Kandidaten (Georgia/Times/DejaVuSerif/Arial) gesucht, mit PIL-Default-Font als Fallback.

**Service-Worker-Logik minimal:** Install precacht App-Shell-Liste (HTML-Page + shared-design.css + favicon + Icons), Activate räumt alte Caches, Fetch macht network-first mit cache-fallback (damit Updates direkt durchgehen). Non-GET-Requests passen unverändert durch. Kein Background-Sync, kein Push-Listener.

**Wirkung im HTML:** `<head>` beider Templates erweitert um `<link rel="manifest">`, `<meta name="theme-color">`, vier Apple-Mobile-Web-App-Meta-Tags, zwei Apple-Touch-Icons (192/512). Service-Worker-Registrierung als kleines `<script>` vor `</body>`: Feature-Detection, `load`-Listener, `register('/nala/sw.js', { scope: '/nala/' })`, Catch loggt nach Console (kein UI-Block beim SW-Fail).

- **Neuer Router:** [`zerberus/app/routers/pwa.py`](zerberus/app/routers/pwa.py) — vier Endpoints (`/nala/manifest.json`, `/nala/sw.js`, `/hel/manifest.json`, `/hel/sw.js`), keine Auth-Dependencies. Pure-Function-Schicht: `render_service_worker(cache_name, shell)` rendert SW-JS aus Template + Cache-Name + Shell-URL-Liste. Konstanten `NALA_MANIFEST`, `HEL_MANIFEST`, `NALA_SHELL`, `HEL_SHELL`.
- **Verdrahtung in [`main.py`](zerberus/main.py):** `from zerberus.app.routers import legacy, nala, orchestrator, hel, archive, pwa`, dann `app.include_router(pwa.router)` als ERSTER `include_router`-Call (vor allen anderen). Kommentar erklärt warum die Reihenfolge zwingend ist.
- **HTML-Verdrahtung Nala:** [`nala.py::NALA_HTML`](zerberus/app/routers/nala.py) `<head>` um sieben Tags erweitert (Manifest-Link, Theme-Color #0a1628, Apple-Capable yes, Apple-Status-Bar black-translucent, Apple-Title "Nala", zwei Apple-Touch-Icons). SW-Registrierung als 8-Zeilen-Script vor `</body>`.
- **HTML-Verdrahtung Hel:** [`hel.py::ADMIN_HTML`](zerberus/app/routers/hel.py) analog mit Theme-Color #1a1a1a, Apple-Title "Hel", Hel-Icons. SW-Registrierung mit Scope `/hel/`.
- **Icons:** vier PNGs unter `zerberus/static/pwa/{nala,hel}-{192,512}.png`, generiert via `scripts/generate_pwa_icons.py`. Skript ist Repo-Bestandteil, damit Icons reproduzierbar sind und das Theme später zentral angepasst werden kann.
- **Logging:** Kein eigener Tag — SW-Fehler landen browser-seitig in `console.warn` mit Tag `[PWA-200]`. Server-seitig sind die Endpoints stumm (Standard-Access-Log reicht).
- **Tests:** 39 neue Tests in [`test_pwa.py`](zerberus/tests/test_pwa.py) — 5 Pure-Function-Tests für `render_service_worker` (Cache-Name, Shell-URLs, alle drei Event-Listener, skipWaiting/clients.claim, GET-only-Caching), 6 Manifest-Dict-Tests (Pflichtfelder, 192+512-Icons pro App, Themes unterscheiden sich, JSON-serialisierbar), 4 Endpoint-Tests (Status 200, korrekte Media-Types, Body-Inhalte, Cache-Control), 8 Source-Audit-Tests pro HTML (Manifest-Link, Theme-Color, alle Apple-Tags, SW-Registrierung mit korrektem Scope), 4 Icon-Existenz-Tests (PNG-Magic-Bytes), 3 Routing-Order-Tests (pwa-Import, pwa-Include, pwa-VOR-hel), 2 Generator-Skript-Tests.
- **Teststand:** 1533 → **1572 passed** (+39), 4 xfailed pre-existing, 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Ziel #16:** ✅ Nala + Hel als PWA. Unabhängiges Ziel, blockiert nichts. Damit sind die Ziele #1, #2, #3 und #16 durch. Nächste sinnvolle Patches: #5 (Code-Execution P201) oder #4 abschließen via Nala-Tab "Projekte" (P202).

**Patch 199** — Phase 5a #3: Projekt-RAG-Index (2026-05-02)

Schließt Phase-5a-Ziel #3 ("Projekte haben eigenes Wissen") ab. Jedes Projekt bekommt einen eigenen, isolierten Vektor-Index unter `data/projects/<slug>/_rag/{vectors.npy, meta.json}` — der globale RAG-Index in `modules/rag/router.py` bleibt unberührt. Damit kann das LLM beim aktiven Projekt (P197 `X-Active-Project-Id`-Header) auf Inhalte aus den Projektdateien zugreifen, ohne dass projekt-spezifische Chunks den globalen Memory-Index verschmutzen.

Architektur-Entscheidung: **Pure-Numpy-Linearscan statt FAISS.** Per-Projekt-Indizes sind klein (typisch 10–2000 Chunks). Ein `argpartition` auf einem `(N, 384)`-Array ist auf der Größenordnung schneller als FAISS-Setup-Overhead und macht Tests dependency-frei (kein faiss-Mock nötig). Persistenz als `vectors.npy` (float32) + `meta.json` (Liste, gleiche Reihenfolge). Atomare Writes via `tempfile.mkstemp` + `os.replace`. Eine FAISS-Migration ist trivial nachrüstbar, falls Projekte signifikant >10k Chunks bekommen — aber das ist heute nicht der Bottleneck.

Embedder: **MiniLM-L6-v2 (384 dim)** als Default, lazy-loaded — kompatibel mit dem Legacy-Globalpfad und ohne sprach-spezifisches Setup. Tests monkeypatchen `_embed_text` mit einem Hash-basierten 8-dim-Pseudo-Embedder; der echte SentenceTransformer wird in Unit-Tests nie geladen. Wenn der globale Index irgendwann komplett auf Dual umsteigt, kann man den Per-Projekt-Pfad mit demselben Modell betreiben — das ist eine reine Konfig-Änderung im Wrapper.

Chunker-Reuse: Code-Files (.py/.js/.ts/.html/.css/.json/.yaml/.sql) gehen durch den existierenden `code_chunker.chunk_code` (P122 — AST/Regex-basiert). Prosa (.md/.txt/Default) durch einen neuen lokalen Para-Splitter mit weichen Absatz-Grenzen (max 1500 Zeichen, snap an Doppel-Newline, Sentence-Split-Fallback für überlange Absätze). Bei Python-SyntaxError im Code-Pfad: Fallback auf Prose, damit kaputte Dateien trotzdem indexiert werden.

Idempotenz: Pro `file_id` höchstens ein Chunk-Set im Index. Beim Re-Index wird der alte Block für die file_id zuerst entfernt — gleicher `sha256` ergibt funktional dasselbe Ergebnis (Hash-Embedder ist deterministisch), anderer `sha256` ersetzt den alten Block. Das vermeidet Doubletten beim Re-Upload mit gleichem `relative_path`.

Trigger-Punkte: (a) `upload_project_file_endpoint` NACH `register_file` — neuer File wandert direkt in den Index. (b) `materialize_template` ruft am Ende `index_project_file` für jede neu angelegte Skelett-Datei — Template-Inhalte sind sofort retrievbar. (c) `delete_project_file_endpoint` ruft `remove_file_from_index` NACH dem DB-Delete — keine stale Treffer mehr. (d) `delete_project_endpoint` löscht den ganzen `_rag/`-Ordner. Alle Trigger sind Best-Effort: Indexing-Fehler brechen den Hauptpfad NICHT ab; der Eintrag steht in der DB, der Index lässt sich später nachziehen.

Wirkung im Chat: Nach P197 (Persona-Merge), nach P184 (`_wrap_persona`), nach P185 (Runtime-Info), nach P118a (Decision-Box) und nach P190 (Prosodie), aber VOR `messages.insert(0, system)`. Der Block beginnt mit `[PROJEKT-RAG — Kontext aus Projektdateien]` als eindeutigem Marker, gefolgt von einer kurzen Anweisung an das LLM und Top-K Hits in Markdown-Sektionen pro File. Best-Effort: jeder Fehler (Embedder fehlt, Index kaputt) → kein Block, Chat läuft normal weiter. Logging-Tag `[RAG-199]` mit `project_id`/`slug`/`chunks_used`. Pro Chat-Request höchstens ein Embed-Call (User-Query) + ein Linearscan über den Projekt-Index — Latenz ~10ms typisch.

Feature-Flags: `ProjectsConfig.rag_enabled: bool = True` (kann via config.yaml ausgeschaltet werden, nützlich für Tests/Setups ohne `sentence-transformers`). `ProjectsConfig.rag_top_k: int = 5` (max Anzahl Chunks pro Query, vom Chat-Endpoint genutzt). `ProjectsConfig.rag_max_file_bytes: int = 5 * 1024 * 1024` (5 MB — drüber: skip beim Indexen, weil's typisch Bilder/Archive sind).

Defensive Behaviors: leere Datei → skip mit `reason="empty"`, binäre Datei (UTF-8-Decode-Fehler) → skip mit `reason="binary"`, Datei zu groß → skip mit `reason="too_large"`, Bytes nicht im Storage → skip mit `reason="bytes_missing"`, Embed-Fehler → skip mit `reason="embed_failed"`, Embedder-Dim-Wechsel zwischen Sessions → Index wird komplett neu aufgebaut (Dim-Mismatch im `top_k_indices` liefert leere Ergebnisliste statt Crash). Inkonsistenter Index (nur eine der zwei Dateien existiert) → leere Basis, der nächste `index_project_file`-Call baut sauber auf.

- **Neuer Helper:** [`zerberus/core/projects_rag.py`](zerberus/core/projects_rag.py) — Pure-Functions `_split_prose`, `chunk_file_content`, `top_k_indices`, `format_rag_block`. File-I/O `load_index`, `save_index`, `remove_project_index`, `index_paths_for`. Embedder-Wrapper `_embed_text` (lazy MiniLM-L6-v2). Async `index_project_file`, `remove_file_from_index`, `query_project_rag`. Konstanten `RAG_SUBDIR`, `VECTORS_FILENAME`, `META_FILENAME`, `PROJECT_RAG_BLOCK_MARKER`, `DEFAULT_EMBED_MODEL`, `DEFAULT_EMBED_DIM`.
- **Verdrahtung Hel:** [`hel.py::upload_project_file_endpoint`](zerberus/app/routers/hel.py) ruft NACH `register_file` `await projects_rag.index_project_file(project_id, file_id, base)` auf — Response erweitert um `rag: {chunks, skipped, reason}`. [`hel.py::delete_project_file_endpoint`](zerberus/app/routers/hel.py) ruft `remove_file_from_index` — Response erweitert um `rag_chunks_removed`. [`hel.py::delete_project_endpoint`](zerberus/app/routers/hel.py) merkt den Slug VOR dem Delete und ruft `remove_project_index` danach.
- **Verdrahtung Materialize:** [`projects_template.py::materialize_template`](zerberus/core/projects_template.py) ruft am Ende JEDES erfolgreichen `register_file` `await projects_rag.index_project_file(project_id, registered_id, base_dir)` auf — frisch materialisierte Skelett-Files sind sofort im Index.
- **Verdrahtung Chat:** [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py) — nach P190 (Prosodie), VOR `messages.insert(0, system)`: wenn `active_project_id` + `project_slug` + `last_user_msg` + `rag_enabled`, dann `query_project_rag` und `format_rag_block` an den `sys_prompt` anhängen.
- **Feature-Flags:** `ProjectsConfig.rag_enabled: bool = True`, `rag_top_k: int = 5`, `rag_max_file_bytes: int = 5 * 1024 * 1024` — alle Defaults im Pydantic-Modell (config.yaml gitignored).
- **Logging:** Neuer `[RAG-199]`-Tag mit `slug`/`file_id`/`path`/`chunks`/`total` beim Indexen, `chunks_used` beim Chat-Query, `chunks_removed` beim Delete. Inkonsistenter/kaputter Index → WARN; Embed/Query-Fehler → WARN mit Exception-Text.
- **Tests:** 46 neue Tests in [`test_projects_rag.py`](zerberus/tests/test_projects_rag.py) — 5 Prose-Splitter-Edge-Cases, 5 Chunk-File-Cases (Code/Markdown/Unknown-Extension/SyntaxError-Fallback/Empty), 5 Top-K-Cases (leer/k=0/sortiert/cap-at-size/Dim-Mismatch), 4 Save-Load-Roundtrip + Inconsistent + Corrupted, 2 Remove-Index, 7 Index-File-Cases (Markdown/Idempotent/Empty/Binary/Too-Large/Bytes-Missing/Rag-Disabled), 2 Remove-File + Drop-Empty-Index, 4 Query-Cases (Hit/Empty-Query/Missing-Project/Missing-Index), 2 Format-Block, 3 End-to-End via Upload/Delete-File/Delete-Project, 1 Materialize-Indexes-Templates, 6 Source-Audit. `fake_embedder`-Fixture mit Hash-basiertem 8-dim-Pseudo-Embedder verhindert das Laden des echten SentenceTransformer in Unit-Tests.
- **Teststand:** 1487 → **1533 passed** (+46), 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Ziel #3:** ✅ Projekte haben eigenes Wissen. Damit sind die Ziele #1 (Backend P194 + UI P195), #2 (Templates P198) und #3 (RAG-Index) durch. Decision 3 (Persona-Merge-Layer) seit P197 aktiv. Datei-Upload aus Hel-UI (P196) seit dem auch indexiert. Nächste sinnvolle Patches: #5 (Code-Execution P200) oder #4 abschließen via Nala-Tab "Projekte" (P201).

**Patch 198** — Phase 5a #2: Template-Generierung beim Anlegen (2026-05-02)

Schließt Phase-5a-Ziel #2 ("Projekte haben Struktur") ab. Ein neu angelegtes Projekt startete bisher leer — der User mußte selbst eine Struktur hochladen, bevor das LLM überhaupt etwas zu lesen hatte. P198 generiert beim Anlegen zwei Skelett-Dateien: `ZERBERUS_<SLUG>.md` als Projekt-Bibel (analog zu `ZERBERUS_MARATHON_WORKFLOW.md` mit Sektionen "Ziel", "Stack", "Offene Entscheidungen", "Dateien", "Letzter Stand") und ein kurzes `README.md`. Inhalt rendert die Project-Daten ein (Name, Slug, Description, Anlegedatum) — der User hat sofort einen sinnvollen Ausgangspunkt.

Architektur-Entscheidung: Templates landen im SHA-Storage (`data/projects/<slug>/<sha[:2]>/<sha>` — gleiche Konvention wie P196-Uploads), DB-Eintrag in `project_files` mit lesbarem `relative_path`. Damit erscheinen Templates nahtlos in der Hel-Datei-Liste, sind im RAG-Index (P199) indexierbar und in der Code-Execution-Pipeline (P200) sichtbar — ohne Sonderpfad. Pure-Python-String-Templates (kein Jinja, weil das den Stack nicht rechtfertigt). Render-Funktionen sind synchron + I/O-frei und unit-bar; Persistenz liegt separat in der async `materialize_template`.

Idempotenz: Existierende `relative_path`-Einträge werden NICHT überschrieben. Wenn der User in einer früheren Session schon eigene Inhalte angelegt hat, bleiben die unangetastet. Helper liefert nur die TATSÄCHLICH neu angelegten Files zurück — leer, wenn alles schon existiert. Die UNIQUE-Constraint `(project_id, relative_path)` aus P194 wäre der zweite Fallback, aber wir prüfen vorher explizit über `list_files`.

Best-Effort-Verdrahtung: Wenn `materialize_template` crasht (Disk-Full, DB-Lock, was auch immer), bricht das Anlegen NICHT ab. Der Projekt-Eintrag steht, der User sieht eine 200-Antwort, Templates lassen sich notfalls nachgenerieren oder per Hand anlegen. Crash-Path mit Source-Audit-Test verifiziert.

Git-Init bewusst weggelassen: Der SHA-Storage ist kein Working-Tree (Bytes liegen unter Hash-Pfaden, nicht unter `relative_path`). `git init` ergibt erst Sinn mit einem echten `_workspace/`-Layout, das mit der Code-Execution-Pipeline (P200, Phase 5a #5) kommt. Bis dahin: kein halbgares Git-Init, das später wieder umgebogen werden müßte.

- **Neuer Helper:** [`zerberus/core/projects_template.py`](zerberus/core/projects_template.py) — `render_project_bible(project, *, now=None)` + `render_readme(project)` als Pure-Functions, `template_files_for(project, *, now=None)` als Komposit, `materialize_template(project, base_dir, *, dry_run=False, now=None)` als async DB+Storage-Schicht, `_write_atomic()` als lokale Kopie aus `hel._store_uploaded_bytes` (Helper soll auch ohne FastAPI-Stack laufen können). Konstanten `PROJECT_BIBLE_FILENAME_TEMPLATE`, `README_FILENAME` exportiert.
- **Verdrahtung:** [`hel.py::create_project_endpoint`](zerberus/app/routers/hel.py) ruft NACH `projects_repo.create_project()` `materialize_template(project, _projects_storage_base())` auf; Response-Feld `template_files` listet die neu angelegten Einträge.
- **Feature-Flag:** `ProjectsConfig.auto_template: bool = True` (Default `True`, Default in `config.py` weil `config.yaml` gitignored). Kann für Migrations-Tests/Bulk-Imports abgeschaltet werden.
- **Logging:** Neuer `[TEMPLATE-198]`-INFO-Log mit `slug`/`path`/`size`/`sha[:8]` pro neu angelegter Datei + `skip slug=... path=... (already exists)` bei Idempotenz-Skip. Bei Crash WARNING via `logger.exception` mit Slug.
- **Tests:** 23 neue Tests in [`test_projects_template.py`](zerberus/tests/test_projects_template.py) — 6 Pure-Function-Cases (Slug-Uppercase, Datum, Sektionen, Description-Block, Empty-Description-Placeholder, Missing-Keys-Defaults), 6 Materialize-Cases (Two-Files, SHA-Storage-Pfad, Idempotenz, User-Content-Schutz, Dry-Run-no-side-effect, Content-Render), 3 End-to-End (Flag-on, Flag-off, Crash-Resilienz), 3 Source-Audit (Imports, Flag-Honor, Konstanten). `disable_auto_template`-Autouse-Fixture in `test_projects_endpoints.py` + `test_projects_files_upload.py` hält deren File-Counts stabil.
- **Teststand:** 1464 → **1487 passed** (+23), 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Ziel #2:** ✅ Projekte haben Struktur. Damit sind die Ziele #1 (Backend+UI) + #2 (Struktur) abgeschlossen — als nächstes #3 (Projekt-RAG-Index, P199) oder #5 (Code-Execution, P200).

**Patch 197** — Phase 5a Decision 3: Persona-Merge-Layer aktiviert (2026-05-02)

Schließt die Lücke zwischen P194 (Persona-Overlay-JSON in der DB) / P195 (Hel-UI-Editor dafür) und der eigentlichen Wirkung im LLM-Call. Bisher konnte Chris in der Hel-UI ein `system_addendum` und `tone_hints` pro Projekt pflegen — aber das landete nirgends im System-Prompt. P197 verdrahtet das.

Aktivierungs-Mechanismus: Header `X-Active-Project-Id: <int>` am `POST /v1/chat/completions`-Request. Header-basierte Auswahl (statt persistenter Spalte) gewinnt im ersten Schritt — keine Schema-Änderung, kein Migration-Risiko, der Frontend-Caller (Nala-Tab "Projekte" sobald gebaut, oder externe Clients) entscheidet pro Request. Persistente Auswahl (Spalte `active_project_id` an `chat_sessions`) ist später trivial nachrüstbar — der Reader `read_active_project_id` ist die einzige Stelle, die geändert werden muss.

Merge-Reihenfolge laut Decision 3 (2026-05-01): System-Default → User-Persona ("Mein Ton") → Projekt-Overlay. Die ersten beiden stecken bereits zusammen in `system_prompt_<profile>.json` (eine Datei pro Profil); P197 hängt nur den Projekt-Layer als markierten Block hinten dran. Der Block beginnt mit `[PROJEKT-KONTEXT — verbindlich für diese Session]` als eindeutigem Marker (Substring-Check für Tests/Logs + Schutz gegen Doppel-Injection in derselben Pipeline). Optional ist eine `Projekt: <slug>`-Zeile drin, damit das LLM beim Self-Talk korrekt referenziert.

Position der Verdrahtung: VOR `_wrap_persona` (P184), damit der `# AKTIVE PERSONA — VERBINDLICH`-Marker auch das Projekt-Overlay umschließt — sonst stünde der Projekt-Block AUSSERHALB der "verbindlichen Persona" und das LLM könnte ihn entwerten. Die Reihenfolge ist explizit per Source-Audit-Test verifiziert (`test_persona_merge.TestSourceAudit.test_merge_runs_before_wrap`).

Defensive Behaviors: archivierte Projekte → kein Overlay (Slug wird trotzdem geloggt, damit Bug-Reports diagnostizierbar bleiben); unbekannte ID → kein Crash, einfach kein Overlay; kaputter Header (Buchstaben, negative Zahl) → ignoriert; leerer Overlay (kein `system_addendum`, leere `tone_hints`) → kein Block; `tone_hints` mit Duplikaten/Leer-Strings → bereinigt (case-insensitive Dedupe, erstes Vorkommen gewinnt). Die DB-Auflösung `resolve_project_overlay` ist von der Pure-Function `merge_persona` getrennt — der Helper bleibt I/O-frei und damit synchron testbar.

Telegram bewusst aus P197 ausgeklammert: Huginn hat eine eigene Persona-Welt (zynischer Rabe) ohne User-Profile und ohne Verbindung zu Nala-Projekten. Project-Awareness in Telegram bräuchte eigene UX (`/project <slug>`-Befehl oder persistente Bind-Tabelle) — eigener Patch wenn der Bedarf entsteht.

- **Neuer Helper:** [`zerberus/core/persona_merge.py`](zerberus/core/persona_merge.py) — `merge_persona(base_prompt, overlay, project_slug=None)` als Pure-Function, `read_active_project_id(headers)` mit Lowercase-Fallback (FastAPI-`Headers` ist case-insensitive, ein Test-`dict` nicht), `resolve_project_overlay(project_id, *, skip_archived=True)` als async DB-Schnittstelle (lazy-Import von `projects_repo` gegen Zirkular-Importe). Konstanten `ACTIVE_PROJECT_HEADER` und `PROJECT_BLOCK_MARKER` exportiert.
- **Verdrahtung:** [`legacy.py::chat_completions`](zerberus/app/routers/legacy.py) — Reihenfolge jetzt `load_system_prompt` → **`merge_persona` (P197, NEU)** → `_wrap_persona` (P184) → `append_runtime_info` (P185) → `append_decision_box_hint` (P118a) → Prosodie-Inject (P190) → `messages.insert(0, system)`.
- **Logging:** Neuer `[PERSONA-197]`-INFO-Log mit `project_id`, `slug`, `base_len`, `project_block_len`. Bei Lookup-Fehlern WARNING mit Exception-Text. Bei archiviertem Projekt INFO mit Slug.
- **Tests:** 33 neue Tests in [`test_persona_merge.py`](zerberus/tests/test_persona_merge.py) — 12 Edge-Cases für `merge_persona` (kein Overlay/leeres Overlay/nur Addendum/nur Hints/beide/Dedupe case-insensitive/leere Strings strippen/Doppel-Injection-Schutz/leerer Base-Prompt/Slug-Anzeige/unerwartete Typen/Separator-Format), 7 Header-Reader-Cases (fehlt/None/leer/valid/Lowercase-Fallback/Non-Numeric/negativ), 5 async DB-Cases via `tmp_db`-Fixture (None-ID/Unknown-ID/Existing/Archived/Archived-with-Skip-False/ohne Overlay), 4 End-to-End über `chat_completions` mit Mock-LLM (Overlay erscheint im messages[0], kein Header → kein Overlay, unbekannte ID → kein Crash, archiviert → übersprungen), 5 Source-Audit-Cases (Log-Marker, Imports, Reihenfolge merge-vor-wrap).
- **Teststand:** 1431 → **1464 passed** (+33), 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — bekannte Schulden).
- **Phase 5a Decision 3:** ✅ Persona-Merge-Layer aktiv. Die in P194/P195 vorbereitete Persona-Overlay-Pflege wirkt jetzt im LLM-Call.

**Patch 196** — Phase 5a #4: Datei-Upload-Endpoint + UI (2026-05-02)

Erster Schritt für Phase-5a-Ziel #4 ("Dateien kommen ins Projekt"). `POST /hel/admin/projects/{id}/files` (multipart) und `DELETE /hel/admin/projects/{id}/files/{file_id}`. Bytes liegen weiter unter `data/projects/<slug>/<sha[:2]>/<sha>` (P194-Konvention) — kein Doppel-Schreiben, wenn der SHA im Projekt-Slug-Pfad schon existiert. Validierung: Filename-Sanitize (`..`/Backslashes/leere Segmente raus), Extension-Blacklist (`.exe`, `.bat`, `.sh`, ...), 50 MB Default-Limit. Delete-Logik: wenn der `sha256` woanders noch referenziert wird, bleibt die Storage-Datei liegen (Schutz vor versehentlichem Cross-Project-Delete); sonst wird sie atomar entfernt + leere Parent-Ordner aufgeräumt. Atomic Write per `tempfile.mkstemp` + `os.replace` verhindert halbe Dateien nach Server-Kill.

UI-seitig ersetzt eine Drop-Zone in der Detail-Card den P195-Platzhalter ("Upload kommt in P196"). Drag-and-drop UND klickbarer File-Picker (multiple), pro Datei eigene Progress-Zeile per `XMLHttpRequest.upload.progress`-Event (sequenzielle Uploads, damit der Server bei Massen-Drops nicht mit parallelen Streams überrannt wird). Datei-Liste bekommt einen Lösch-Button mit Confirm-Dialog; Hinweistext klärt, dass die Bytes nur entfernt werden, wenn sie nirgends sonst referenziert sind.

- **Schema:** Neue `ProjectsConfig` in `core/config.py` (`data_dir`, `max_upload_bytes`, `blocked_extensions`) — Defaults im Pydantic-Modell, weil `config.yaml` gitignored ist (sonst fehlt der Schutz nach `git clone`). Neue Repo-Helper `count_sha_references()`, `is_extension_blocked()`, `sanitize_relative_path()` in `core/projects_repo.py`.
- **Endpoints:** `_projects_storage_base()` als Indirektion in `hel.py` — Tests können den Storage-Pfad per Monkeypatch umbiegen, ohne die globalen Settings anzufassen. `_store_uploaded_bytes()` schreibt atomar; `_cleanup_storage_path()` räumt Datei + leere Parent-Ordner bis zum `data_dir`-Anker auf (best-effort).
- **Tests:** 49 neue Tests — 17 Upload-Endpoint (`test_projects_files_upload.py`: Happy-Path, Subdir, Dedup, Extension-Block, Path-Traversal, Empty-Filename, Empty-Data, Too-Large, 409-Dup, Delete-Unique, Delete-Shared, Cross-Project-404, Storage-Cleanup), 21 Repo-Helper (`test_projects_repo.py`: Sanitize, Extension-Block, count-sha-references), 11 UI-Source-Inspection (`test_projects_ui.py`: Drop-Zone, Progress, Delete-Button, Drag-and-Drop-Events).
- **Teststand:** 1382 → **1431 passed** (+49). 0 neue Failures, 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts — bekannte Schulden).
- **Phase 5a Ziel #4:** ✅ Datei-Upload geöffnet. Indexierung in projekt-spezifischen RAG (Ziel #3) folgt mit P199.

**Patch 195** — Phase 5a #1: Hel-UI-Tab "Projekte" — schließt Ziel #1 ab (2026-05-02)

UI-Hülle über das P194-Backend. Neuer Tab `📁 Projekte` im Hel-Dashboard zwischen Huginn und Links. Liste, Anlegen-Modal (Form-Overlay statt extra CSS-Lib), Edit/Archive/Unarchive/Delete inline, Datei-Liste read-only (Upload kommt P196). Persona-Overlay-Editor: `system_addendum` (Textarea) + `tone_hints` (Komma-Liste). Mobile-first: 44px Touch-Targets durchgehend, scrollbare Tab-Nav, Form-Overlay mit `flex-start`-Top für kleine Screens. Slug-Override nur beim Anlegen editierbar (Slug ist immutable per Repo-Vertrag P194). Lazy-Load via `activateTab('projects')`.

- **Tests:** 20 Source-Inspection-Tests in [`test_projects_ui.py`](zerberus/tests/test_projects_ui.py) (Pattern wie `test_patch170_hel_kosmetik.py`). Decken Tab-Reihenfolge, Section-Markup, JS-Funktionen (`loadProjects`, `saveProjectForm`, `archive/unarchive/delete`, `loadProjectFiles`), 44px-Touch-Targets, Lazy-Load-Verdrahtung.
- **Teststand:** 1365 → **1382 passed** (+17, da `test_projects_endpoints.py` schon mitgezählt war), 0 neue Failures, 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — nicht blockierend).
- **Phase 5a Ziel #1:** ✅ vollständig (Backend P194 + UI P195). Nächste Patches greifen sukzessive Ziele #2 (Templates), #3 (RAG-Index pro Projekt), #4 (Datei-Upload-Endpoint).

**Patch 194** — Phase 5a #1: Projekte als Entität, Backend-Layer (2026-05-02)

Erster Patch der Phase 5a. Tabellen `projects` + `project_files` in `bunker_memory.db` (Decision 1, 2026-05-01), Repo + Hel-CRUD-Endpoints + 46 neue Tests. UI-Tab folgt in P195. Teststand 1316 → **1365 passed** (+49: 28 Repo + 18 Endpoints + 3 weitere), 0 neue Failures, 4 xfailed (pre-existing).

- **Schema:** `projects(id, slug UNIQUE, name, description, persona_overlay JSON, is_archived, created_at, updated_at)` + `project_files(id, project_id, relative_path, sha256, size_bytes, mime_type, storage_path, uploaded_at, UNIQUE(project_id, relative_path))`. Soft-Delete via `is_archived`. Cascade per Repo (`delete_project` ⇒ `DELETE FROM project_files WHERE project_id = ?`), nicht per FK — Models bleiben dependency-frei (keine ORM-Relations). Persona-Overlay als JSON-TEXT für Decision 3 (Merge-Layer System → User → Projekt).
- **Storage-Konvention:** `data/projects/<slug>/<sha[:2]>/<sha>` — Sha-Prefix als Sub-Verzeichnis, damit kein Hotspot-Ordner entsteht. Bytes liegen NICHT in der DB. Helper `storage_path_for()` + `compute_sha256()` in `projects_repo.py`.
- **Endpoints:** `/hel/admin/projects` (list, create) + `/hel/admin/projects/{id}` (get, patch, delete) + `/admin/projects/{id}/archive` + `/unarchive` + `/files`. Admin-only via Hel `verify_admin` (Basic Auth). Bewusst nicht unter `/v1/` — `/v1/` ist exklusiv Dictate-App-Lane (Hotfix 103a, lessons.md Regel 8).
- **Slug-Generator:** Lower-case, special-chars → `-`, max 64 Zeichen, Kollisions-Suffix `-2`/`-3`/.... `slugify("AI Research v2")` → `"ai-research-v2"`.
- **Migration:** Alembic-Revision `b03fbb0bd5e3` (down_revision `7feab49e6afe`), idempotent via `_has_table()`-Guard. Indexe: `uq_projects_slug`, `idx_projects_is_archived`, `idx_project_files_project`, `idx_project_files_sha`. UNIQUE-Constraint `(project_id, relative_path)` direkt in `op.create_table()` über `sa.UniqueConstraint`.
- **Lesson dokumentiert:** Composite-UNIQUE-Constraints MÜSSEN im Model (`__table_args__`) deklariert werden, nicht nur als raw `CREATE UNIQUE INDEX` in `init_db` — sonst greift der Constraint nicht in Test-Fixtures, die nur `Base.metadata.create_all` aufrufen.

- **P192 — Sentiment-Triptychon UI:** Drei Chips (BERT 📝 + Prosodie 🎙️ + Konsens 🎯) an jeder Chat-Bubble, Sichtbarkeit per Hover/`:active` analog Toolbar-Pattern (P139). Neue Utility [`zerberus/utils/sentiment_display.py`](zerberus/utils/sentiment_display.py) mit `bert_emoji()`, `prosody_emoji()`, `consensus_emoji()`, `compute_consensus()`, `build_sentiment_payload()`. Backend liefert `sentiment: {user, bot}` ADDITIV in `/v1/chat/completions`-Response — OpenAI-Schema bleibt formal kompatibel. Mehrabian-Regel: bei `confidence > 0.5` dominiert die Prosodie, sonst Fallback auf BERT. Inkongruenz 🤔 wenn BERT positiv und Prosodie-Valenz < -0.2. 22 Tests in [`test_sentiment_triptych.py`](zerberus/tests/test_sentiment_triptych.py).
- **P193 — Whisper-Endpoint Prosodie/Sentiment-Enrichment:** `/v1/audio/transcriptions` Response erweitert: `text` bleibt IMMER (Backward-Compat für Dictate / SillyTavern / Generic-Clients), zusätzlich optional `prosody` (P190) + neu `sentiment.bert` + `sentiment.consensus`. `/nala/voice` identisch erweitert + zusätzlich named SSE-Events `event: prosody` und `event: sentiment` über `/nala/events` — Triptychon-Frontend kann sync (JSON) oder async (SSE) konsumieren. Fail-open: BERT-Fehler erzeugt nur Logger-Warnung, Endpoint läuft sauber durch. 16 Tests in [`test_whisper_enrichment.py`](zerberus/tests/test_whisper_enrichment.py). Logging-Tag `[ENRICHMENT-193]`.

### Doku-Konsolidierung (Phase-4-Abschluss)

- `lessons.md` aktualisiert: neue Sektionen Prosodie/Audio (P188-191), RAG/FAISS (P187+), Frontend (P186+P192), Whisper-Enrichment (P193). Veraltete Hinweise auf MiniLM als „aktiver Embedder" durch DualEmbedder-Beschreibung ersetzt (in CLAUDE_ZERBERUS.md).
- `CLAUDE_ZERBERUS.md` aktualisiert: neue Top-Sektionen Sentiment-Triptychon (P192) + Whisper-Endpoint Enrichment (P193).
- `SUPERVISOR_ZERBERUS.md` (diese Datei) auf < 400 Zeilen gestrafft. Patch-Details vor P177 leben jetzt in [`docs/PROJEKTDOKUMENTATION.md`](docs/PROJEKTDOKUMENTATION.md).
- Neues Übergabedokument [`docs/HANDOVER_PHASE_5.md`](docs/HANDOVER_PHASE_5.md) für die nächste Supervisor-Session (Phase 5 / Nala-Projekte).

---

## Phase 4 — ABGESCHLOSSEN ✅ (P119–P193)

Vollständige Patch-Historie in [`docs/PROJEKTDOKUMENTATION.md`](docs/PROJEKTDOKUMENTATION.md). Hier nur die letzten Meilensteine ab P186:

| Patch | Datum | Zusammenfassung |
|-------|-------|-----------------|
| **P203d-3** | 2026-05-03 | Phase 5a Ziel #5 ABGESCHLOSSEN: UI-Render im Nala-Frontend — `renderCodeExecution(wrapperEl, codeExec)` baut Code-Card (Sprach-Tag + exit-Badge + escapter Code) plus collapsible Output-Card (44×44px Toggle, stdout/stderr, Truncated-Marker), `addMessage` retourniert Wrapper-Element, eigener `escapeHtml`-Helper, XSS-Min-Count, `node --check` ueber NALA_HTML-Scripts + 30 Tests |
| **P203d-2** | 2026-05-03 | Phase 5a #5 Output-Synthese: zweiter LLM-Call (Prompt+Code+stdout/stderr → menschenlesbare Antwort), Pure-Function-Schicht in `modules/sandbox/synthesis.py`, `should_synthesize`-Trigger, Bytes-genau Truncate (5KB), `[SYNTH-203d-2]`-Logging, store_interaction-Reorder (assistant-Insert nach Synthese), fail-open + 47 Tests |
| **P203d-1** | 2026-05-02 | Phase 5a #5 Backend-Pfad: Code-Detection + Sandbox-Roundtrip im `/v1/chat/completions` — `first_executable_block` + `execute_in_workspace(writable=False)` + additives `code_execution`-JSON-Field, Sechs-Stufen-Gate (Header + Slug + Overlay-not-None + Sandbox-enabled + Fenced-Block + Result), fail-open + 19 Tests |
| **P204** | 2026-05-02 | Phase 5a #17 ABGESCHLOSSEN: Prosodie-Kontext im LLM — `[PROSODIE]`-Block, BERT+Gemma-Konsens via Mehrabian, Worker-Protection (keine Zahlen) + 33 Tests |
| **P203c** | 2026-05-02 | Phase 5a #5 Zwischenschritt: Sandbox-Workspace-Mount + `execute_in_workspace` — RO-Default, Mount-Validation, Defense-in-Depth + 17 Tests |
| **P203b** | 2026-05-02 | Hel-UI-Hotfix: kaputtes Quote-Escaping → Event-Delegation via `data-*`-Attribute, JS-Integrity-Test + 10 Tests |
| **P203a** | 2026-05-02 | Phase 5a #5 Vorbereitung: Project-Workspace-Layout — Hardlink+Copy-Fallback, atomic write, sync_workspace + 36 Tests |
| **P202** | 2026-05-02 | PWA-Auth-Hotfix: SW skipt Top-Level-Navigation, Cache v2-Bump + 5 Tests |
| **P201** | 2026-05-02 | Phase 5a #4: Nala-Tab "Projekte" + zentraler Header-Setter `X-Active-Project-Id` in `profileHeaders()` + 21 Tests |
| **P200** | 2026-05-02 | Phase 5a #16: PWA-Verdrahtung Nala + Hel — Manifest + SW pro App, Kintsugi-Icons + 39 Tests |
| **P199** | 2026-05-02 | Phase 5a #3: Projekt-RAG-Index — Pure-Numpy-Linearscan, MiniLM-L6-v2 + 46 Tests |
| **P198** | 2026-05-02 | Phase 5a #2: Template-Generierung beim Anlegen (Bibel + README) + 23 Tests |
| **P197** | 2026-05-02 | Phase 5a Decision 3: Persona-Merge-Layer aktiviert — Header `X-Active-Project-Id` + `merge_persona`-Helper + 33 Tests |
| **P196** | 2026-05-02 | Phase 5a #4: Datei-Upload-Endpoint + Drop-Zone-UI (öffnet Ziel #4) — SHA-Dedup-Delete, Extension-Blacklist, atomic write + 49 Tests |
| **P195** | 2026-05-02 | Phase 5a #1: Hel-UI-Tab "Projekte" (schließt Ziel #1 ab) — Liste/Form/Persona-Overlay + 20 Tests |
| **P194** | 2026-05-02 | Phase 5a #1: Projekte als Entität (Backend) — Schema + Repo + Hel-CRUD + 46 Tests |
| P192–P193 | 2026-05-01 | Sentiment-Triptychon + Whisper-Enrichment + Phase-4-Abschluss + Doku-Konsolidierung |
| P189–P191 | 2026-05-01 | Prosodie-Pipeline komplett: Gemma-Client + Pipeline + Consent-UI + Worker-Protection |
| P186–P188 | 2026-05-01 | Auto-TTS + FAISS-Migration (DualEmbedder DE/EN) + Prosodie-Foundation |
| P183–P185 | 2026-05-01 | Black-Bug VIERTER (endgültig) + Persona-Wrap + Runtime-Info-Block |
| P180–P182 | 2026-04-30 | Live-Findings L-178: Guard+RAG-Kontext, Telegram-Allowlist, ADMIN-Plausi, Unsupported-Media |
| P178–P179 | 2026-04-29 | Huginn-RAG-Selbstwissen (system-Kategorie, Default-Whitelist, Backlog-Konsolidierung) |
| P177 | 2026-04-29 | Pipeline-Cutover-Feature-Flag (`use_message_bus`, default false, live-switch) |
| P173–P176 | 2026-04-28 | Phase-E-Skelett (Message-Bus, Adapter, Pipeline, Sandbox-Live, Coda-Autonomie) |
| P119–P172 | 2026-04-09…04-28 | Aufbau Phase 4: RAG-Pipeline, Guard, Sentiment, Memory-Extraction, Telegram-Bot, HitL, Sandbox |

### Phase-4-Bilanz

| Bereich | Highlights |
|---------|-----------|
| **Guard** | Mistral Small 3 (P120/P180), `caller_context` + `rag_context` ohne Halluzinations-Risiko, fail-open |
| **Huginn (Telegram)** | Long-Polling, Allowlist, Intent-Router via JSON-Header, HitL persistent (SQLite), RAG via system-Kategorie, Sandbox-Hook |
| **Nala UI** | Mobile-first Bubbles, HSL-Slider, TTS, Auto-TTS, Katzenpfoten, Feuerwerk, Triptychon |
| **RAG** | Code-Chunker, DualEmbedder (DE GPU + EN CPU), FAISS-Migration, Soft-Delete, Category-Boost |
| **Prosodie** | Gemma 4 E2B (lokal, Q4_K_M), Whisper+Gemma parallel via `asyncio.gather`, Consent-UI, Triptychon |
| **Pipeline** | Message-Bus + Telegram/Nala/Rosa-Adapter, DI-only Pipeline, Feature-Flag-Cutover |
| **Security** | Input-Sanitizer (NFKC + Patterns), Callback-Spoofing-Schutz, Allowlist, Worker-Protection (Audio nicht in DB) |
| **Infrastruktur** | Docker-Sandbox (`--network none --read-only`), Pacemaker, pytest-Marker (e2e/guard_live/docker), Coda-Autonomie |
| **Bugs** | 🪦 Black Bug (4 Anläufe — P183 hat ihn endgültig getötet), HSL-Parse, Config-Save, Terminal-Hygiene |

---

## Phase 5 — Nala-Projekte (Roadmap)

**Referenz-Dokumente:**
- [`nala-projekte-features.md`](nala-projekte-features.md) — 100+ Features, konsolidiert
- [`NALA_PROJEKTE_PRIORISIERUNG.md`](NALA_PROJEKTE_PRIORISIERUNG.md) — Priorisierung für Zerberus-Kontext
- [`docs/HANDOVER_PHASE_5.md`](docs/HANDOVER_PHASE_5.md) — Übergabedokument mit Tech-Stack, VRAM, offenen Items

### Phase 5a — Grundgerüst (erste 10 Patches)

| # | Feature | Beschreibung |
|---|---------|-------------|
| P194 | Projekt-DB (Backend) | SQLite-Schema, Repo, Hel-CRUD-Endpoints ✅ |
| P195 | Hel-UI-Tab Projekte | Liste + Anlegen/Edit/Archive/Delete + Persona-Overlay-Editor ✅ |
| P196 | Datei-Upload-Endpoint + UI | `POST /hel/admin/projects/{id}/files` + Drop-Zone + SHA-Dedup-Delete ✅ |
| P197 | Persona-Merge-Layer | System → User → Projekt-Overlay im LLM-Prompt aktivieren |
| P198 | Template-Generierung | `ZERBERUS_X.md`, Ordnerstruktur, Git-Init |
| P199 | Projekt-RAG-Index | Isolierter FAISS pro Projekt |
| P200 | Code-Execution-Pipeline | Intent `PROJECT_CODE` → LLM → Sandbox |
| P201 | HitL-Gate für Code | Sicherheit vor Ausführung (Default-ON) |
| P202 | Snapshot/Backup-System | `.bak` + Rollback vor jeder Dateiänderung |
| P203 | Diff-View | User sieht jede Änderung vor Bestätigung |

### Phase 5b — Power-Features (danach)

- Multi-LLM Evaluation (Bench-Mode pro Projekt-Aufgabe)
- Bugfix-Workflow + Test-Agenten (Loki/Fenrir/Vidar Per-Projekt)
- Multi-Agent Orchestrierung, Debugging-Konsole
- Reasoning-Modi, LLM-Wahl, Cost-Transparency
- Agent-Observability (Chain-of-Thought Inspector)

### Abhängigkeiten / Was steht schon

- Docker-Sandbox (P171) existiert + Images gezogen (P176)
- HitL-Mechanismus (P167) existiert, SQLite-persistent
- Pipeline-Cutover (P177) existiert, Feature-Flag bereit
- Guard (P120/P180) funktioniert mit RAG- und Persona-Kontext
- Prosodie (P189–P191) funktioniert — kann in Projekt-Kontext integriert werden (z. B. Stimmung als Code-Quality-Indikator)

---

## Architektur-Referenz

### Aktiver Tech-Stack

| Komponente | Modell / Technologie |
|------------|---------------------|
| **Cloud-LLM** | DeepSeek V3.2 (OpenRouter) |
| **Guard** | Mistral Small 3 (OpenRouter, `mistralai/mistral-small-24b-instruct-2501`) |
| **Prosodie** | Gemma 4 E2B (lokal, `llama-mtmd-cli`, Q4_K_M, ~3.4 GB) |
| **ASR / Whisper** | faster-whisper large-v3 (FP16, Docker Port 8002) |
| **Sentiment (Text)** | `oliverguhr/german-sentiment-bert` (lokal) |
| **Embeddings DE** | `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` (GPU) |
| **Embeddings EN** | `intfloat/multilingual-e5-large` (CPU, optional Index) |
| **Reranker** | `BAAI/bge-reranker-v2-m3` |
| **DB** | SQLite (`bunker_memory.db`, WAL-Modus, Alembic-Migrations seit P92) |
| **Frontend** | Nala (Mobile-first), Hel (Admin-Dashboard) |
| **Bot** | Huginn (Telegram, Long-Polling, Tailscale-intern) |

### VRAM-Belegung (Modus „Nala aktiv" mit Prosodie)

```
Whisper 4.5 + BERT 0.5 + Gemma E2B 3.0 + DualEmbedder 0.5 + Reranker 1.0 + Windows 0.8
= ~10.3 GB / 12 GB (RTX 3060)
```

### Repos (alle 3 müssen synchron sein)

- **Zerberus** (Code, lokal): `C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus`
- **Ratatoskr** (Doku-Sync, GitHub): `C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr`
- **Claude** (universelle Lessons, GitHub): `C:\Users\chris\Python\Claude`

---

## Offene Items / Bekannte Schulden

→ Konsolidiert in [`BACKLOG_ZERBERUS.md`](BACKLOG_ZERBERUS.md) (seit Patch 179). Hier nur strukturelle Schulden:

- **Persona-Hierarchie** (Hel vs. Nala „Mein Ton") — löst sich mit SillyTavern/ChatML-Wrapper (B-071)
- **`interactions`-Tabelle ohne User-Spalte** — Per-User-Metriken erst nach Alembic-Schema-Fix vertrauenswürdig
- **`scripts/verify_sync.ps1`** existiert nicht — `sync_repos.ps1` Output muss manuell geprüft werden
- **`system_prompt_chris.json` Trailing-Newline-Diff** — `git checkout` zum Bereinigen, kein echter Bug
- **Voice-Messages in Telegram-DM** funktionieren nicht (P182 Unsupported-Media-Handler antwortet höflich) — B-072 für echte Whisper-Pipeline

## Architektur-Warnungen

- **Rosa Security Layer:** NICHT implementiert — Dateien im Projektordner sind nur Vorbereitung
- **JWT** blockiert externe Clients komplett — `static_api_key` ist der einzige Workaround (Dictate, SillyTavern)
- **/v1/-Endpoints** MÜSSEN auth-frei bleiben (Dictate-Tastatur kann keine Custom-Headers) — Bypass via `_JWT_EXCLUDED_PREFIXES`
- **Chart.js / zoom-plugin / hammer.js** via CDN — bei Air-Gap ist das Metriken-Dashboard tot
- **Prosodie-Audio-Bytes** dürfen NICHT in `interactions`-Tabelle landen (Worker-Protection P191)

---

## Sync-Pflicht (Patch 164+)

`sync_repos.ps1` muss nach jedem `git push` ausgeführt werden — der Patch gilt erst als abgeschlossen, wenn Zerberus, Ratatoskr und Claude-Repo synchron sind. Coda pusht zuverlässig nach Zerberus, vergisst aber den Sync regelmäßig, sodass Ratatoskr und Claude-Repo driften.

Falls Claude Code den Sync nicht selbst ausführen kann (z. B. PowerShell nicht verfügbar oder Skript wirft Fehler), MUSS er das explizit melden — etwa mit „⚠️ sync_repos.ps1 nicht ausgeführt — bitte manuell nachholen". Stillschweigendes Überspringen ist nicht zulässig. Die durable Regel steht in `CLAUDE_ZERBERUS.md` unter „Repo-Sync-Pflicht".

## Langfrist-Vision

- **Phase 5 — Nala-Projekte:** Zerberus wird zur persönlichen Code-Werkstatt, Nala vermittelt zwischen Chris und LLMs/Sandboxes
- **Metric Engine** = kognitives Tagebuch + Frühwarnsystem für Denkmuster-Drift
- **Rosa Corporate Security Layer** = letzter Baustein vor kommerziellem Einsatz
- **Telegram-Bot** als Zero-Friction-Frontend für Dritte (keine Tailscale-Installation nötig)

## Don'ts für Supervisor

- **PROJEKTDOKUMENTATION.md NICHT vollständig laden** (5000+ Zeilen = Kontextverschwendung) — nur gezielt nach Patch-Nummern grep'en
- **Memory-Edits max 500 Zeichen** pro Eintrag
- **Session-ID ≠ User-Trennung** — Metriken pro User erst nach DB-Architektur-Fix vertrauenswürdig
- **Patch-Prompts IMMER als `.md`-Datei** — NIE inline im Chat. Claude Code erhält den Inhalt per Copy-Paste aus der Datei (Patch 101)
- **Dateinamen `CLAUDE_ZERBERUS.md` und `SUPERVISOR_ZERBERUS.md` sind FINAL** — in Patch-Prompts nie mit alten Namen (`CLAUDE.md`, `HYPERVISOR.md`) referenzieren (Patch 100/101)
- **Lokale Pfade:** Ratatoskr liegt unter `C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr\` (nicht `Rosa\Ratatoskr\`), Bmad82/Claude unter `C:\Users\chris\Python\Claude\` (nicht `Rosa\Claude\`). Patch-Prompts mit falschen Pfaden → immer erst verifizieren, nicht raten

---

## Für die nächste Supervisor-Session (Phase 5 Start)

1. `SUPERVISOR_ZERBERUS.md` von GitHub fetchen (frisch konsolidiert in P192–P193)
2. [`docs/HANDOVER_PHASE_5.md`](docs/HANDOVER_PHASE_5.md) lesen — Tech-Stack, VRAM, offene Items, Phase-5-Roadmap stehen dort vollständig
3. Phase-5a mit P194 (Projekt-DB + Workspace) starten — Doppel- oder Dreiergruppen-Rhythmus beibehalten
4. Prosodie-Live-Test steht noch aus — `llama-mtmd-cli` muss im PATH sein, sonst läuft Pfad A nicht
5. Pipeline-Feature-Flag (`use_message_bus`) ist bereit für Live-Switch-Tests, aber Default-OFF bis Chris explizit umstellt
