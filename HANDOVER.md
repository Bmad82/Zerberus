## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-03
**Letzter Patch:** P208 — Spec-Contract / Ambiguitäts-Check (Phase 5a Ziel #8 ABGESCHLOSSEN)
**Tests:** 2035 passed (+89 P208 aus 1946 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 1 skipped (existing)
**Manuelle Tests:** 1 / 85 ✅
**Commit:** _wird nach Push nachgetragen_
**Repos synchron:** _wird nach sync_repos.ps1 + verify_sync.ps1 nachgetragen_

---

## Zuletzt passiert (1 Patch in dieser Session)

**P208 — Spec-Contract / Ambiguitäts-Check (Phase 5a Ziel #8 ABGESCHLOSSEN).** Erst verstehen, dann coden. Vor dem ersten Haupt-LLM-Call schaetzt eine Pure-Function-Heuristik die Ambiguitaet der User-Eingabe; bei Score >= Threshold (Default 0.65) faehrt eine schmale Spec-Probe (ein LLM-Call, eine Frage), das Frontend rendert eine Klarstellungs-Karte mit Original-Message + Frage + Textarea + drei Buttons. User antwortet, klickt "Trotzdem versuchen" oder bricht ab. Erst danach laeuft der eigentliche Code-/Antwort-Pfad mit ggf. angereichertem Prompt weiter.

**Was P208 baut:**

1. **Neues Modul** [`zerberus/core/spec_check.py`](zerberus/core/spec_check.py) mit drei Schichten:
   - **Pure-Function** — `compute_ambiguity_score(message, *, source="text"|"voice") -> float` (Length-Penalty <4w +0.40 / <8w +0.20 / <14w +0.05, Pronomen-Dichte max +0.30, Code-Verb ohne Sprachangabe +0.20, generisches Verb ohne Substantiv-Anker +0.15, Code-Verb ohne IO-Spec +0.10, Voice-Bonus +0.20, leerer Input → 1.0, clamped auf [0, 1]). `should_ask_clarification(score, *, threshold=0.65) -> bool` Trigger-Gate. `build_spec_probe_messages(message)` baut die zwei-Element-`messages`-Liste fuer den Probe-LLM (System-Prompt mit "EINE Frage, max 1 Satz, max 160 Zeichen"-Constraint, User-Prompt mit eingebetteter Original-Anfrage). `build_clarification_block(question, answer)` plus `enrich_user_message(original, question, answer)` haengen `[KLARSTELLUNG]…[/KLARSTELLUNG]`-Block an (Marker substring-disjunkt zu `[PROJEKT-RAG]`/`[PROJEKT-KONTEXT]`/`[PROSODIE]`/`[CODE-EXECUTION]`/`[AKTIVE-PERSONA]`).
   - **Async-Wrapper** — `run_spec_probe(message, llm_service, session_id) -> Optional[str]` ruft `llm_service.call` mit `temperature_override=0.3`, fail-open auf jeder Stufe (LLM-Crash, leere/whitespace-Antwort, Non-Tuple-Result → None), Bytes-genau truncate auf SPEC_PROBE_MAX_BYTES=400.
   - **Pending-Registry** — `ChatSpecGate`-Singleton (analog `ChatHitlGate` aus P206, In-Memory only). `ChatSpecPending`-Dataclass mit `id` (UUID4-hex), `session_id`, `project_id`, `project_slug`, `original_message`, `question`, `score` (Float), `source` (text|voice), `status` (pending|answered|bypassed|cancelled|timeout), `answer_text` (nullable, nur bei answered), `created_at`, `resolved_at`. Methoden: `create_pending`/`wait_for_decision`/`resolve` mit Cross-Session-Defense (`session_id`-Match)/`list_for_session`/`cleanup`. **Drei Decision-Werte** statt zwei (vs. P206 HitL `approved`/`rejected`): `answered` (mit non-empty `answer_text`, sonst False) / `bypassed` ("Trotzdem versuchen") / `cancelled` (User verwirft).

2. **Neue DB-Tabelle** `clarifications` in [`zerberus/core/database.py`](zerberus/core/database.py): Spalten `id` (PK), `pending_id` (UUID4 INDEX), `session_id`, `project_id` (INDEX), `project_slug`, `original_message`, `question`, `answer_text`, `score` (Float), `source` (text|voice), `status` (answered|bypassed|cancelled|timeout|error INDEX), `created_at`, `resolved_at`. Persistente Spur fuer Threshold-Tuning. `store_clarification_audit(...)` Best-Effort, jeder Fehler wird geloggt + verschluckt.

3. **Verdrahtung in** [`zerberus/app/routers/legacy.py`](zerberus/app/routers/legacy.py)`::chat_completions` zwischen `last_user_msg`-Cleanup (Cloud/Local-Suffix entfernt) und Intent-Detection:
   - **Source-Detection**: voice wenn `X-Prosody-Context` + `X-Prosody-Consent: true` Header gesetzt sind (P204-Konvention), sonst text
   - `compute_ambiguity_score(last_user_msg, source=...)` → bei `should_ask_clarification(score, threshold)` → `run_spec_probe(...)` → bei nicht-leerer Frage Pending erzeugen + `wait_for_decision(timeout)`
   - **Drei Decision-Pfade**: `answered`+`answer_text` → `enrich_user_message` baut `[KLARSTELLUNG]`-Block, `req.messages` mitgespiegelt → Haupt-LLM-Call sieht Frage+Antwort. `bypassed` → Original durch (User akzeptiert Risiko). `cancelled` → early-return mit Hinweis-Antwort `model="spec-cancelled"`, **kein** Haupt-LLM-Call. `timeout` → wie `bypassed` (defensiver fuer User-Experience: User schaut nicht hin → eher durchlassen statt frustrieren; bei HitL ist Timeout = reject, weil Code-Execution explizite Bestaetigung braucht; bei Spec ist Timeout = "Default-Vertrauen").

4. **Zwei neue auth-freie Endpoints** in `legacy.py` (per `/v1/`-Invariante kein JWT):
   - `GET /v1/spec/poll` — Long-Poll-Endpoint, liefert das aelteste pending Spec-Pending der Session als JSON oder `{"pending": null}`. Header `X-Session-ID` als Owner-Diskriminator.
   - `POST /v1/spec/resolve` — Body `{pending_id, decision, session_id?, answer_text?}`. Idempotent + Cross-Session-Block. `answered` braucht non-empty `answer_text`, sonst `ok=False`.

5. **Nala-Frontend** in [`zerberus/app/routers/nala.py`](zerberus/app/routers/nala.py):
   - **CSS-Block** (~135 Zeilen): `.spec-card` mit Kintsugi-Gold-Border, `.spec-card-header` (Icon ❔ + Source-Tag), `.spec-original` (klein, italic, grau — Original-Message als Referenz), `.spec-question` (gross, prominent, white-space pre-wrap), `.spec-answer-input` textarea (60-160px, focus-Border in Gold), `.spec-actions` flex-row mit drei Buttons in **44x44px Touch-Targets** (`.spec-answer-btn` gold / `.spec-bypass-btn` gruen / `.spec-cancel-btn` rot). Post-Klick-States `.spec-card.spec-{answered,bypassed,cancelled}` (gruen/gold/rot Border + Resolved-Text). Karte bleibt sichtbar als Audit-Spur.
   - **JS-Funktionen** (~150 Zeilen direkt nach dem HitL-Block):
     - `startSpecPolling(abortSignal, snapshotSessionId)` analog HitL — `setInterval(tick, 1000)` pollt `/v1/spec/poll`, bei Pending → `renderSpecCard(...)` + stopPolling (einer reicht pro Turn)
     - `renderSpecCard(pending)` baut DOM mit Original-Message + Frage via `textContent` (XSS-safe by default), Textarea fuer Antwort, drei Buttons mit `addEventListener` (kein inline onclick — P203b-Lesson). Textarea bekommt direkt nach Render Fokus.
     - `resolveSpecPending(pendingId, decision, answerText)` POSTed `/v1/spec/resolve`, sperrt alle Buttons+Textarea sofort (kein Doppel-Klick), updatet Card-State (gruen/gold/rot) plus Resolved-Text
     - `clearSpecState()`/`stopSpecPolling()` analog HitL
   - **`sendMessage`-Verdrahtung**: nach `clearHitlState() + startHitlPolling(...)` analog `clearSpecState() + startSpecPolling(myAbort.signal, reqSessionId)`. Beide laufen unabhaengig — Spec **vor** dem LLM-Call, HitL **nach** dem Code-Block.

6. **Neue Feature-Flags** in [`zerberus/core/config.py`](zerberus/core/config.py)`::ProjectsConfig`:
   - `spec_check_enabled: bool = True` — Master-Switch off → kein Probe, kein Pending, kein Block
   - `spec_check_threshold: float = 0.65` — konservativ (kurze Saetze ohne Sprachangabe triggern, einfaches Smalltalk nicht)
   - `spec_check_timeout_seconds: int = 60` — Long-Poll-Fenster pro Request

**Tests:** 89 in [`test_p208_spec_contract.py`](zerberus/tests/test_p208_spec_contract.py).

`TestComputeAmbiguityScore` (9) — Leer/Range/Length/Voice/Code-Verb/Pronoun/Clear/Clamp/IO.

`TestShouldAskClarification` (5) — Above/Below/Exact/Invalid/Custom-Threshold.

`TestBuildSpecProbeMessages` (5) — List-Shape/System-Content/User-Content/Empty/System-Constraint.

`TestEnrichUserMessage` (6) — Marker/Preserve/Q+A/Empty/Answer-Only/Build-Block.

`TestMarkerUniqueness` (2) — Disjunkt zu PROJEKT-RAG/PROJEKT-KONTEXT/PROSODIE/CODE-EXECUTION/AKTIVE-PERSONA, Format `[KLARSTELLUNG]`/`[/KLARSTELLUNG]`.

`TestRunSpecProbe` (7) — Happy/Strip/Empty/Whitespace/Crash/Non-Tuple/Truncate.

`TestChatSpecGate` (13) — UUID/Dict-Keys/List-Filter/Answered+Text/Answered-Empty-Reject/Bypassed/Cancelled/Invalid/Session-Mismatch/Wait-Immediate/Wait-Timeout/Cleanup/Answer-Truncated.

`TestStoreClarificationAudit` (3) — Happy/Truncate/No-DB-Silent-Skip.

`TestSpecPollResolveEndpoints` (6) — Empty/By-Session/No-Leak/Answered/Bypassed/Unknown.

`TestLegacySourceAudit` (13) — Logging-Tag, Imports, Endpoints, Pydantic-Models, source-Kwarg, Threshold/Timeout/Enabled-Flags, Cancelled-Early-Return, Enrich, Voice-Detection, Audit-Call.

`TestNalaSourceAudit` (10) — JS-Funktionen, Endpoints, sendMessage-Verdrahtung, CSS-Klassen, 44px-Touch, escapeHtml/textContent, Textarea, Drei-Decisions, Audit-Trail-States.

`TestE2ESpecCheck` (5) — Non-ambig skip / Bypassed / Answered+Enrichment / Cancelled+Early-Return / Disabled-Flag.

`TestJsSyntaxIntegrity` (1, skipped wenn node fehlt) — `node --check` ueber alle inline `<script>`-Bloecke aus NALA_HTML.

`TestSmoke` (4) — Config-Flags, `clarifications`-Tabelle, Endpoints registriert, Module-Exports.

**Logging-Tag:** `[SPEC-208]` mit `pending_create id=... session=... score=... source=... question_len=... msg_len=...` / `decision id=... session=... status=... answer_len=...` / `ambig score=... threshold=... source=... session=...` / `not_ambig score=... threshold=... source=... session=...` / `probe_returned_empty session=... (fail-open, kein Block)` / `audit_written session=... project_id=... status=... source=... score=...` / `Pipeline-Fehler (fail-open): ...`. Worker-Protection-konform: kein Klartext aus Score-Heuristik, keine User-Antwort-Inhalte im Log — nur Längen-Metriken.

## Nächster Schritt — sofort starten

**P209: Zweite Meinung vor Ausführung — Sancho Panza (Phase 5a Ziel #7).** Veto-Logik: ein zweites Modell (z.B. Mistral-Small-3 als Guard) bewertet den vom ersten Modell generierten Code auf "macht das wirklich was der User will + ist es sicher". Bei Veto landet der Code nicht im HitL-Gate sondern in einem "Wandschlag-Banner" mit Veto-Begruendung. Schmaler Scope, bestehende Sandbox-/HitL-Pipeline bleibt; Veto ist eine Pre-Filter-Stufe vor P206.

**Konkret (Coda darf abweichen):**

1. **Pure-Function-Schicht** in neuem Modul `zerberus/core/code_veto.py`: `should_run_veto(code, language) -> bool` (Trigger-Gate, z.B. nur bei nicht-Python-Code oder bei bestimmten gefährlichen Keywords ohne Kontext). `build_veto_messages(code, language, user_prompt)` baut den Veto-Prompt fuer das zweite Modell (System: "Bewerte ob dieser Code dem User-Wunsch entspricht und sicher ist. Antworte NUR mit `pass`/`veto` plus optionaler Begründung."). `parse_veto_verdict(text) -> VetoVerdict` parst die Antwort.
2. **Async-Wrapper** `run_veto(code, language, user_prompt, llm_service) -> VetoVerdict` mit `temperature=0.1` (deterministisch). Fail-open auf Crash.
3. **Verdrahtung in `legacy.py::chat_completions`** zwischen `first_executable_block` und HitL-Pending-Erzeugung: bei `should_run_veto` → `run_veto(...)` → bei `verdict.veto=True` → kein HitL-Pending, kein Sandbox-Run, sondern Veto-Banner-Payload im `code_execution`-Field (`vetoed=True`, `veto_reason=...`).
4. **Frontend**: Wandschlag-Banner-Card analog `.diff-card` (gleiches Vokabular: rote Border, Begründung prominent, kein Approve-Button). Skip-Banner in Code-Card.
5. **Audit-Tabelle** `code_vetoes` mit `pending_id`/`session_id`/`project_id`/`code_text`/`verdict`/`reason`/`created_at`.
6. **Tests**: Score-Heuristik-Unit-Tests, Verdict-Parser-Boundary, LLM-Mock fuer Probe-Call, Source-Audit, JS-Integrity.
7. **Was P209 NICHT macht**: keine Kosten-Aggregation des Veto-Calls in `interactions.cost` (Schuld), kein Mehr-Modell-Voting (genau ein Veto-Call pro Run), kein Lern-Loop ueber `code_vetoes`-History.

Alternativ falls P209 zu viel: **P210 GPU-Queue für VRAM-Konsumenten (Ziel #11)** — kooperatives Scheduling zwischen Whisper, Gemma, Embeddings und Reranker. Pure-Token-Bucket-Pattern + asyncio.Lock pro VRAM-Slot, fail-fast bei Overrun.

**Reihenfolge-Vorschlag** (Coda darf abweichen):
- **P209** Zweite Meinung vor Ausführung (Sancho Panza) — Ziel #7
- **P210** GPU-Queue für VRAM-Konsumenten — Ziel #11
- **P211** Secrets bleiben geheim — Ziel #12

Helper aus P208 die in P209/P210/P211 direkt nutzbar sind:
- **`clarifications`-Tabelle** mit `pending_id`/`session_id`/`project_id`/`status`-Korrelationsschluessel als Audit-Vorlage fuer `code_vetoes` (P209) oder `gpu_queue_audits` (P210).
- **`.spec-card`-CSS-Pattern** (Header → Original → Question → Input → Actions → Resolved-State) klont sich trivial zu `.veto-card` (P209 Wandschlag-Banner) oder `.gpu-queue-card` (P210 Wartezeit-Anzeige).
- **`renderSpecCard`/`resolveSpecPending`-Pattern** (Render mit textContent + Async-Resolve mit Card-State-Update + Audit-Spur-Erhalt) ist die zweite Vorlage neben P207 fuer User-Interaktions-Karten.
- **`compute_ambiguity_score`-Heuristik-Set** ist erweiterbar (neue Penalties einbauen, Tests parametrisieren) ohne API-Bruch — P209 kann fuer `should_run_veto` denselben Stil uebernehmen.
- **`run_spec_probe`-Pattern** (isolierter LLM-Call mit minimal-Kontext + `temperature=0.3` + fail-open + Bytes-Truncate) ist die Vorlage fuer den Veto-Probe-Call in P209.

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL-Telegram (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), Projekte-Backend (P194), Projekte-UI (P195), Projekt-Datei-Upload (P196), Persona-Merge-Layer (P197), Projekt-Templates (P198), Projekt-RAG-Index (P199), PWA-Verdrahtung Nala + Hel (P200), Nala-Tab "Projekte" + Header-Setter (P201), PWA-Auth-Hotfix (P202), Project-Workspace-Layout (P203a), Hel-UI-Hotfix Event-Delegation (P203b), Sandbox-Workspace-Mount + execute_in_workspace (P203c), Prosodie-Kontext im LLM (P204), Code-Detection + Sandbox-Roundtrip im Chat (P203d-1), Output-Synthese fuer Sandbox-Code-Execution (P203d-2), UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (P203d-3), RAG-Toast in Hel-UI (P205), HitL-Gate vor Code-Execution + Audit-Tabelle code_executions + HANDOVER-Teststand-Konvention (P206), Workspace-Snapshots + Diff-View + Rollback + workspace_snapshots-Tabelle + sandbox_writable/snapshots_enabled-Flags + POST /v1/workspace/rollback (P207), **Spec-Contract / Ambiguitäts-Check + spec_check.py-Modul + clarifications-Tabelle + spec_check_enabled/threshold/timeout-Flags + GET /v1/spec/poll + POST /v1/spec/resolve + Klarstellungs-Karte mit Textarea + drei Decision-Werten (P208)**.

Helper aus P207/P206 die in P208 schon recycelt wurden: `pending_id`-Korrelationsschluessel-Pattern (in `clarifications`), `ChatHitlGate`-Singleton-Pattern (kopiert zu `ChatSpecGate` mit drei Decisions statt zwei), `.hitl-card`-CSS-Vokabular (Border, Action-Footer, Post-Klick-State — kopiert zu `.spec-card`), Long-Poll-Endpoint-Pattern (`/v1/hitl/poll`+`/v1/hitl/resolve` als Vorlage fuer `/v1/spec/poll`+`/v1/spec/resolve`), `String.fromCharCode(10)`-Trick (nicht gebraucht in P208 — keine Newlines im JS-String-Literal), `node --check`-JS-Integrity-Test-Pattern, `escapeHtml(`-Audit-Test (kopiert).

Helper aus P205/P203d-3/P203d-2/P203d-1/P204: siehe vorherigen HANDOVER. `addMessage`-Wrapper-Return, `escapeHtml`, `renderCodeExecution`-Pattern, `.code-card`/`.output-card`-CSS, `code_execution`-Dict-Schema, Marker-Disjunktheit `[CODE-EXECUTION]` vs. `[PROJEKT-RAG]` / `[PROJEKT-KONTEXT]` / `[PROSODIE]` / **`[KLARSTELLUNG]`**, RAG-Toast-Pattern, JS-Integrity-Test.

## Offenes
- **NEU 2026-05-03 (Chris-Feature-Request, ausserhalb Patch-Zaehlung umgesetzt):** [`docs/huginn_kennt_zerberus.md`](docs/huginn_kennt_zerberus.md) ist Doku-Pflicht. Wurde inhaltlich auf P208-Stand gehoben (Phase-5a-Ziel #8 erledigt, Spec-Contract/Klarstellungs-Karte mit Score-Heuristik + drei Decisions; Schicht 3 der Sicherheitsarchitektur erweitert um Spec-Probe; `[SPEC-208]`-Logging-Tag in Audit-Liste; Tests-Zahl 2035 statt 1946). Spiegel-Kopie unter [`docs/RAG Testdokumente/huginn_kennt_zerberus.md`](docs/RAG%20Testdokumente/huginn_kennt_zerberus.md) ebenfalls aktualisiert (kompaktere Variante ohne Code-Bloecke fuer den Test-Fixture-Pfad — TestSelfKnowledgeDoc + TestRagDocUpdate gruen). Coda hat NICHT committed — Chris reviewed die neue Doku zuerst und uploaded selbst per `curl -u Chris:... -F file=@docs/huginn_kennt_zerberus.md -F category=system http://localhost:5000/hel/admin/rag/upload`.
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#82-85 (P208: Push, Spec-Probe Happy mit Answered, Bypass+Cancel, Voice-Bonus + Threshold-Anpassung + Disabled-Flag)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 2035 passed (+89 P208 aus 1946 baseline)**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Zeile 1653 + 3096) — wie zuvor.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat einen `node --check`-Pass** seit P203d-3 — abgedeckt durch `TestJsSyntaxIntegrity`-Tests in P203d-3 + P206 + P207 + **P208** (skipped wenn `node` nicht im PATH).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **P207 Storage-GC fuer alte Snapshot-Tars** — bei jedem writable-Run entstehen zwei neue `.tar`-Files. Es gibt aktuell keinen Cleanup-Job. Ein "behalte die letzten N Snapshots pro Projekt"-Sweep waere ein eigener Patch.
- **P207 Hardlink-Snapshots statt Tar** — Tar ist Tests-tauglich aber bei grossen Workspaces (>100 MB) wird das Kopieren teuer. Variante: pro Datei einen Hardlink in `_snapshots/<id>/<rel_path>`. Erst bei messbaren Disk-Problemen anfassen.
- **P207 Per-File-Rollback** — aktuell ist Snapshot atomare Einheit. User koennte "nur diese eine Datei zurueckdrehen" wuenschen. Geht via separaten `restore_single_file(snapshot_id, rel_path)`-Aufruf, nicht hochpriorisiert.
- **P207 Cross-Project-Diff** — bewusst weggelassen.
- **P207 Branch-Mechanik** — bewusst weggelassen. Linear forward/reverse only.
- **P207 Automatischer Rollback bei `exit_code != 0`** — bewusst nicht.
- **P207 Token-Cost-Tracking fuer Snapshot-Pfad** — Snapshots erzeugen keine LLM-Kosten.
- **NEU P208: Token-Cost-Tracking fuer Spec-Probe-Call** — der Probe-LLM-Call (~50 Tokens, einmal pro ambiger Message) wird aktuell nicht in `interactions.cost` aufaggregiert. Schuld analog P203d-1/P203d-2 (Synthese-Call ist auch nicht in `interactions.cost`). Bei P210/P211 mitlösen oder als eigener Patch.
- **NEU P208: Multi-Turn-Klarstellung** — bewusst weggelassen. Eine Frage pro Turn. Wenn User auf die Klarstellungs-Antwort eine Folge-Frage hat, ist das ein neuer Turn.
- **NEU P208: Edit-Vor-Run** — User kann Original-Prompt nicht editieren in der Card, nur antworten oder bypassen. Bei Bedarf neue Message schicken.
- **NEU P208: Telegram-Pfad** — bewusst weggelassen. Huginn hat eigenen P167-HitL; Spec-Probe waere dort Overkill (Telegram-Threads sind asynchroner als Chat).
- **NEU P208: Persistierung der Pendings** — In-Memory only, analog P206-Konvention. Pendings sterben beim Server-Restart, was bei Long-Poll-Requests sowieso passiert.
- **NEU P208: Threshold-Tuning ueber `clarifications`-Tabelle** — der Default-Threshold 0.65 ist ein Schaetzwert; wenn nach einigen Wochen die Audit-Daten zeigen "Bypass-Rate > 80%", muss der Threshold hoch (zu viele Probe-Calls); "Cancelled-Rate > 30%", muss die Frage-Formulierung verbessert werden (Probe-LLM lernt nicht von alleine — System-Prompt anpassen). Aktuell manueller SQL-Query auf `SELECT status, COUNT(*) FROM clarifications GROUP BY status`.
- **Pre-existing Files ohne Workspace** — wie zuvor.
- **Pre-existing Files ohne RAG-Index** — wie zuvor.
- **Lazy-Embedder-Latenz** — wie zuvor.
- **PWA-Cache-Bust nach Patches** — wie zuvor: nach dem ersten Open der PWA muss der Service-Worker den Cache invalidieren — Cache-Name-Bump auf `nala-shell-v3` waere sauber. Bei P208 nicht zwingend (network-first faengt das auf), aber empfohlen weil die JS-Surface noch weiter gewachsen ist.
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
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt (P203d-1) | Output-Synthese im Chat-Endpunkt (P203d-2) | UI-Render im Nala-Frontend (P203d-3) | RAG-Toast in Hel-UI (P205) | HitL-Gate vor Sandbox-Code-Execution: `ChatHitlGate`-Singleton, `/v1/hitl/poll`+`/v1/hitl/resolve`, Confirm-Karte, `code_executions`-Audit-Tabelle (P206) | Workspace-Snapshots + Diff + Rollback: `projects_snapshots.py` mit Pure-Function-`build_workspace_manifest`+`diff_snapshots`+`_is_safe_member`-Tar-Defense, Sync-FS-`materialize_snapshot`+`restore_snapshot` mit ustar-Tars unter `data/projects/<slug>/_snapshots/<id>.tar`, neue `workspace_snapshots`-Tabelle mit `pending_id`/`parent_snapshot_id`-Korrelation zu P206. Chat-Endpunkt schiesst before/after-Snapshots wenn `projects.sandbox_writable=True` UND `projects.snapshots_enabled=True` UND HitL approved, Diff-Liste plus zwei Snapshot-IDs im additiven `code_execution.diff`/`before_snapshot_id`/`after_snapshot_id`-Feld. Frontend rendert `.diff-card` unter Output-Card mit Status-Badges, `↩️ Aenderungen zurueckdrehen`-Button (44x44 Touch) ruft `POST /v1/workspace/rollback`. `[SNAPSHOT-207]`-Logging-Tag (P207) | **Spec-Contract / Ambiguitäts-Check vor Haupt-LLM-Call: `spec_check.py` mit Pure-Function-`compute_ambiguity_score` (Heuristik 0-1 mit Length/Pronoun/Code-Verb/IO/Voice-Penalties) + `should_ask_clarification` (Trigger-Gate) + `run_spec_probe` (isolierter LLM-Call mit `temperature=0.3`) + `enrich_user_message` (`[KLARSTELLUNG]`-Block). `ChatSpecGate`-Singleton mit drei Decisions answered/bypassed/cancelled. Neue `clarifications`-Tabelle mit `pending_id`/`session_id`/`project_id`/`score`/`source`/`status`. Chat-Endpunkt schaetzt Score zwischen `last_user_msg`-Cleanup und Intent-Detection; bei Score >= Threshold (Default 0.65) Probe-LLM → Pending → Long-Poll. answered → enrich, bypassed → durch, cancelled → early-return mit `model="spec-cancelled"` (kein Haupt-LLM-Call), timeout → wie bypassed (defensiver UX). Source-Detection via `X-Prosody-Context` + `X-Prosody-Consent: true` (P204-Header). Voice-Bonus +0.20. Frontend rendert `.spec-card` mit Original-Message + Frage + Textarea + drei 44x44px Buttons (`✉️ Antwort senden` gold / `→ Trotzdem versuchen` gruen / `✗ Abbrechen` rot). `GET /v1/spec/poll`+`POST /v1/spec/resolve` parallel zu HitL-Endpoints. `sendMessage` startet Polling parallel zum HitL — Spec VOR LLM-Call, HitL NACH Code-Block. Defaults `spec_check_enabled=True`+`threshold=0.65`+`timeout=60s`. `[SPEC-208]`-Logging-Tag (P208)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`) | Slug ist immutable nach Anlage | Atomic Write für jeden Upload-Pfad | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge (P197) | Pure-Function vs. DB-Schicht trennen | Templates als reguläre `project_files`-Einträge (P198) | Idempotenz-Check VOR Schreiben für jeden Generator (P198) | Best-Effort-Verdrahtung im Endpoint (P198) | Per-Projekt-RAG-Index isoliert (P199) | Embedder-Wrapper als monkeypatchbare Funktion (P199) | Best-Effort-Indexing (P199) | RAG-Block-Marker `[PROJEKT-RAG]` (P199) | PWA-Endpoints auth-frei in separatem Router VOR auth-gated Hel-Router (P200) | SW-Scope folgt dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch (P200) | `persona_overlay` NIEMALS in User-sichtbarer Response (P201) | Header-Injektion fuer Cross-Cutting zentral in `profileHeaders()` (P201) | Zombie-ID-Schutz nach List-Refresh (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test (P201+P203d-3+P206+P207+P208) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig (P203a) | Hardlink primär, Copy als Fallback (P203a) | Atomic-Write-Pattern auch fuer Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat ueber benutzergenerierte Daten ist verboten — IMMER `data-*` + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehoert in jede Test-Suite die HTML in Python-Source baut (P203b+P203d-3+P205+P206+P207+P208) | Prosodie-Bruecken-Block enthaelt NIEMALS numerische Werte (P204) | Mehrabian-Schwellen identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Bruecken-Bloecke MUESSEN substring-disjunkt sein (P204+P203d-2+**P208 KLARSTELLUNG**) | Sandbox-Workspace-Mount: Read-Only ist konservativer Default (P203c+P207) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte konservativ geblockt — `project_overlay is not None` als Diskriminator (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollstaendiges Sandbox-Result liegt vor (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open (P203d-1) | Two-stufige LLM-Pipelines: `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren (P203d-2) | Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (P203d-2) | Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding (P203d-2+**P208 SPEC_ANSWER_MAX_BYTES**) | Synthese-LLM-Pipeline ist fail-open auf jeder Stufe (P203d-2) | Frontend-Renderer fuer additive Backend-Felder muss BACKWARDS-COMPAT bleiben (P203d-3+P205+P206+P207) | DOM-Insertion-Funktionen retournen Wrapper-Element (P203d-3) | Frontend-Renderer fuer User-/LLM-Strings: jedes innerHTML-Statement muss einen `escapeHtml(`-Aufruf enthalten — Audit-Test prueft das per Regex (P203d-3+P206+P207) | **Frontend-Renderer fuer User-Strings DARF auch `textContent` statt `innerHTML` nutzen — Audit-Test akzeptiert beide (P208 .spec-card)** | Mobile-first 44x44px Touch-Target gilt fuer JEDEN neuen Toggle/Button im Frontend (P203d-3+P206+P207+**P208 .spec-actions**) | Status-Toasts: `textContent` statt `innerHTML` fuer Reason-Strings (P205) | Toast-Replace-Pattern: ein einziger DOM-Container plus CSS-State-Toggle (P205) | `pointer-events: none` im Hidden-State von fixed-positionierten Toasts/Overlays (P205) | HitL-Pending-IDs sind UUID4-hex (32 chars), Cross-Session-Defense via Session-Match im Resolve, niemals Integer-IDs (P206+**P208**) | HitL-Pendings im Chat-Pfad sind transient (In-Memory), nicht persistiert (P206+**P208 ChatSpecGate**) | Synthese-LLM-Calls (P203d-2) DUERFEN NICHT auf `skipped=True`-Payloads laufen (P206) | Audit-Trail-Schreibungen sind Best-Effort: jeder Crash darf den Hauptpfad nicht blockieren (P206+P207+**P208 store_clarification_audit**) | HANDOVER-Header enthaelt die Zeile `**Manuelle Tests:** X / Y ✅` aus der WORKFLOW-Tabelle, ohne Reminder-Text oder Bewertung (P206-Konvention) | Sandbox-Writable-Mount: Default `projects.sandbox_writable=False` — RO-Konvention bleibt Standard, opt-in via Settings-Flag (P207) | Snapshot-Tars unter `<data>/projects/<slug>/_snapshots/<uuid4-hex>.tar`, atomar via Tempname + `os.replace` (P207) | Tar-Member-Validation `_is_safe_member` MUSS bei jedem Restore aufgerufen werden — kein Symlink/Hardlink, kein absoluter Pfad, kein `..`, Member-Resolve innerhalb dest_root (P207) | Cross-Project-Defense: `rollback_snapshot_async` MUSS `expected_project_id` gegen Snapshot-Eigentuemer pruefen, sonst Reject (P207) | JavaScript-String-Literale mit Newline (`\\n`) DUERFEN NICHT direkt aus Python-Source kommen — `String.fromCharCode(10)` als Workaround. Lesson aus P203b wieder relevant geworden (P207) | Workspace-Snapshot-Pfad ist additiv und opt-in: ohne `sandbox_writable=True`+`snapshots_enabled=True` bleibt das `code_execution`-Feld P206-kompatibel (P207) | **Spec-Contract-Pfad ist additiv und opt-in: ohne `spec_check_enabled=True` faehrt das Chat-Verhalten unveraendert wie pre-P208 (P208)** | **Spec-Probe-LLM-Call MUSS isoliert sein (Persona-frei, kein RAG, kein Prosody-Block) — Werkzeug, kein Gespraech (P208)** | **Drei Decision-Werte answered/bypassed/cancelled — `answered` braucht non-empty `answer_text`, sonst False; `cancelled` early-return mit `model="spec-cancelled"` ohne Haupt-LLM-Call; `timeout` = bypass (defensiver UX) (P208)** | **Source-Detection ueber `X-Prosody-Context` + `X-Prosody-Consent: true` (P204-Konvention) — Voice-Bonus +0.20 im Score (P208)** | **`[KLARSTELLUNG]`-Marker substring-disjunkt zu allen anderen LLM-Markern (PROJEKT-RAG/PROJEKT-KONTEXT/PROSODIE/CODE-EXECUTION/AKTIVE-PERSONA) (P208)** | **`enrich_user_message`-Pfad: bei `answered` MUSS sowohl `last_user_msg` als auch `req.messages[-letzter user-msg].content` aktualisiert werden, sonst sieht der Haupt-LLM-Call die Original-Message (P208)**
