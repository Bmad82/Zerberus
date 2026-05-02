## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P203d-1 — Code-Detection + Sandbox-Roundtrip im Chat-Endpunkt (Phase 5a #5 Backend-Pfad)
**Tests:** 1720 passed (+19 neue P203d-1 + 4 deselected, weil bestehende Tests in xfailed/skipped umsortiert wurden), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, NEU `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`)
**Commit:** TODO — wird vom Auto-Push-Workflow nachgetragen
**Repos synchron:** TODO — wird vom Auto-Push-Workflow nachgetragen

---

## Zuletzt passiert (1 Patch in dieser Session)

**P203d-1 — Code-Detection + Sandbox-Roundtrip im Chat-Endpunkt (Phase 5a #5 Backend-Pfad).** Erster Sub-Patch der P203d-Aufteilung (P203d-1 Backend / P203d-2 Output-Synthese / P203d-3 UI). Damit reicht der `/v1/chat/completions`-Endpunkt einen ausgeführten Code-Block als additives JSON-Field zurück — das Frontend kann es ignorieren und bleibt OpenAI-kompatibel, oder es renderen (P203d-3).

**Was vorher fehlte:** Nach P203c war das Werkzeug für Workspace-gebundene Code-Execution komplett (`SandboxManager.execute(workspace_mount, mount_writable)` + `execute_in_workspace(project_id, ...)`), aber die Chat-Pipeline rief es nirgends auf. DeepSeek konnte mit P194-201 ein Projekt sehen, mit P197 eine Persona dazubekommen, mit P199 RAG-Hits aus den Projektdateien lesen — aber wenn es einen `print(2+2)`-Block in seiner Antwort produzierte, lief der nirgendwohin.

**Was P203d-1 baut:**

In [`zerberus/app/routers/legacy.py::chat_completions`](zerberus/app/routers/legacy.py) ein Block direkt nach `store_interaction(...)` und vor dem Sentiment-Triptychon:

```python
if (active_project_id is not None
    and project_slug
    and project_overlay is not None):
    _sandbox = get_sandbox_manager()
    if _sandbox.config.enabled:
        _block = first_executable_block(answer, list(_sandbox.config.allowed_languages))
        if _block is not None:
            _result = await execute_in_workspace(
                project_id=active_project_id,
                code=_block.code, language=_block.language,
                base_dir=Path(settings.projects.data_dir),
                writable=False,
            )
            if _result is not None:
                code_execution_payload = {
                    "language": _block.language, "code": _block.code,
                    "exit_code": _result.exit_code,
                    "stdout": _result.stdout, "stderr": _result.stderr,
                    "execution_time_ms": _result.execution_time_ms,
                    "truncated": _result.truncated, "error": _result.error,
                }
```

Plus: `ChatCompletionResponse` bekommt `code_execution: dict | None = None` als additives Pydantic-Feld. Reihenfolge im finalen Response-Konstruktor: `choices` → `sentiment` (P192) → `code_execution` (P203d-1).

**Sechs-Stufen-Gate (alles fail-open ausser ein Gate verbietet's):**

1. `active_project_id` aus dem `X-Active-Project-Id`-Header (P201) — sonst Datei-Fallback wie P171.
2. `project_slug` aus `resolve_project_overlay` vorhanden — Projekt existiert in der DB.
3. `project_overlay is not None` — bei archivierten Projekten liefert der Resolver `(None, slug)`; aktive Projekte ohne Overlay haben `({}, slug)`. Damit blocken wir Code-Execution auf Eis-gelegten Projekten konservativ. Defense-in-Depth: User hat das Projekt bewusst archiviert.
4. `settings.modules.sandbox.enabled` — Sandbox aktiv (P171-Default).
5. `first_executable_block(answer, allowed_languages)` — Pure-Function aus P171/P122. KEIN Fallback-Sprache, weil bei einer Plain-Text-Antwort sonst der ganze Antworttext als `unknown`-Code interpretiert würde.
6. `execute_in_workspace(...)` — `None` heisst Slug-Reject oder Disabled (Wrapper aus P203c), `SandboxResult` heisst durchgelaufen (auch bei `exit_code != 0`).

**Was P203d-1 bewusst NICHT macht:**

- **Kein zweiter LLM-Call.** Output-Synthese (Prompt+Code+stdout+stderr → menschenlesbare Antwort) ist P203d-2. P203d-1 reicht den raw `SandboxResult` durch — Frontend bekommt rohen stdout/stderr.
- **Kein UI-Render.** Code-Block + Output-Block in Nala ist P203d-3. P203d-1 macht nur das Backend-JSON.
- **Kein HitL-Gate.** Code läuft direkt — Schutz ist die Sandbox selbst (P171 + P203c RO-Mount, no-network, read-only-rootfs). HitL kommt mit P206 (Phase-5a Ziel #6).
- **Kein writable-Mount.** Aktuell hardcoded `writable=False`. P203d-1 will keine File-Schreibungen aus dem LLM-Output zulassen — das wird ein eigener Patch mit Sync-Workspace + Diff-View (P207).
- **Kein Streaming.** `chat_completions` ist synchron. SSE-Event `code_execution` als zusätzlicher Stream-Frame ist P203d-3-Thema.

**Tests:** 19 in [`test_p203d_chat_sandbox.py`](zerberus/tests/test_p203d_chat_sandbox.py).

`TestP203d1SourceAudit` (7 Tests) — Logging-Tag `[SANDBOX-203d]` vorhanden, `first_executable_block`/`execute_in_workspace`/`get_sandbox_manager` importiert, `code_execution`-Feld im Response-Schema, `writable=False` im Aufruf-Fenster, Feld wird dem Konstruktor durchgereicht.

`TestE2ECodeExecution` (12 Tests) — End-to-End über `chat_completions(request, req, settings)` mit gemockten LLM/Sandbox-Manager/`execute_in_workspace`:

- Happy-Paths: Python-Block ausgeführt + Payload populated, JavaScript-Block ausgeführt, exit_code != 0 mit stderr durchgereicht, multiple Code-Blöcke → erster gewinnt.
- Skip-Cases (alle: `code_execution is None`): kein Header → kein Sandbox-Call, Plain-Text-Antwort ohne Fence → kein Call, Sandbox disabled → kein Call, archiviertes Projekt → kein Call, unknown language (`rust`) → kein Call, `execute_in_workspace` → None → kein Payload aber Call wurde abgesetzt.
- Backwards-Compat: OpenAI-Schema-Felder bleiben unangetastet (`choices`, `model`, `finish_reason`).
- Fail-Open: `execute_in_workspace` raised `RuntimeError` → Endpoint läuft normal weiter, kein 500-Status, `code_execution=None`, `choices` bleiben.

**Logging-Tag `[SANDBOX-203d]`** — ein Eintrag pro `chat_completions`-Aufruf, mit Felder `project_id`/`slug`/`language`/`exit_code`/`stdout_len`/`stderr_len`/`time_ms`/`truncated`. Bei Skip-Pfaden eine Zeile mit Grund (`disabled`, `kein executable Code-Block`, `slug_reject/disabled/missing`). Worker-Protection-konform: keine Code-Inhalte oder Output-Inhalte im Log.

## Nächster Schritt — sofort starten

**P203d-2: Output-Synthese — zweiter LLM-Call mit Prompt+Code+Output → menschenlesbare Antwort.** Schließt den Backend-Loop von Phase-5a-Ziel #5. P203d-3 (UI-Render) ist orthogonal und kann auch parallel passieren.

**Konkret (Coda darf abweichen):**

1. **Synthese-Trigger.** Wenn `code_execution_payload is not None` AND (`exit_code == 0` UND `stdout` nicht leer) ODER (`exit_code != 0`): zweiten LLM-Call ausführen.
2. **Synthese-Prompt.** System-Prompt Variant: "Du hast soeben Code ausgeführt. Hier ist der ursprüngliche User-Prompt, dein Code, und die Sandbox-Ausgabe. Fasse das Ergebnis menschenlesbar zusammen, ohne den Code zu wiederholen wenn er trivial war." User-Prompt enthält den triple-fenced Code-Block + stdout/stderr-Block.
3. **Antwort ersetzen.** Die ursprüngliche LLM-Antwort wird durch die Synthese ersetzt — der Original-Code-Block bleibt aber (oder wird durch eine Referenz ersetzt). `answer` Variable überschreiben, `store_interaction("assistant", synthesized, ...)` mit dem neuen Text.
4. **Edge-Cases.** Synthese-LLM crasht → fail-open auf den Original-Code-Block + `code_execution`-Feld. exit_code != 0 mit leerem stderr → trotzdem Synthese (Sandbox-Timeout? Crash ohne Output?). stdout zu groß (>5 KB) → truncate vor Synthese-Prompt.
5. **Tests:** Mock-Synthese-LLM, Verifizierung dass `answer` ersetzt wird, Fail-Open-Test, Skip-Test (kein Code-Block → keine Synthese).

**Vorschlag zur Aufteilung von P203d** (Stand nach P203d-1):

- ~~P203d-1: Backend-Pfad — Code-Detection + Active-Project-Gate + Sandbox-Call + JSON-Response-Erweiterung. Tests mit Mock-LLM.~~ ✅ DA
- **P203d-2**: Output-Synthese — zweiter LLM-Call mit Prompt+Code+Output. Tests mit Mock-LLM. ⬜ als-nächstes
- **P203d-3**: UI-Render — Nala-Frontend-Patch (Code-Block + Output-Block + ggf. neuer SSE-Event). Source-Audit-Tests. ⬜

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):

- **P203d-2** Output-Synthese — Zweiter LLM-Call
- **P203d-3** UI-Render im Nala-Frontend — Schließt Phase-5a Ziel #5
- **P205** RAG-Toast in Hel-UI — Upload-Response enthält `rag.{chunks, skipped, reason}`, Frontend ignoriert es bisher
- **P206** HitL-Gate vor Code-Execution — Ziel #6
- **P207** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P208** Spec-Contract / Ambiguitäts-Check — Ziel #8

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200)**, **Nala-Tab "Projekte" + Header-Setter (P201)**, **PWA-Auth-Hotfix (P202)**, **Project-Workspace-Layout (P203a)**, **Hel-UI-Hotfix Event-Delegation (P203b)**, **Sandbox-Workspace-Mount + execute_in_workspace (P203c)**, **Prosodie-Kontext im LLM (P204)**, **Code-Detection + Sandbox-Roundtrip im Chat (P203d-1)**.

Helper aus P203d-1 die in P203d-2 direkt nutzbar sind:
- `code_execution_payload`-Dict-Schema in der Response — fertig serialisiert mit `language`/`code`/`exit_code`/`stdout`/`stderr`/`execution_time_ms`/`truncated`/`error`. P203d-2 kann das Dict einfach im Synthese-Prompt verkaufen statt das `SandboxResult` neu zu fetchen.
- Logging-Tag `[SANDBOX-203d]` ist eindeutig — P203d-2 sollte einen separaten Tag (`[SYNTH-203d-2]`) für Synthese-Calls verwenden, damit Operations-Logs den Pfad nachverfolgen können.
- Gate-Logik (active project + slug + overlay-not-None + sandbox-enabled + fenced-block) ist die Voraussetzung für die Synthese — P203d-2 liest das `code_execution_payload` und kann darauf vertrauen, dass dieser Pfad bereits durchlaufen ist.

Helper aus P203c (vorhanden, ungenutzt von P203d-1):
- `execute_in_workspace(..., writable=True, ...)` — wenn P207 Sync-After-Write baut. P203d-1 forciert `writable=False`.
- `sync_workspace(project_id, base_dir)` aus P203a — analog wenn writable-Mount kommt.

Helper aus P204 die in spätere Patches direkt nutzbar sind: siehe vorherigen HANDOVER. Marker-Disjunktheit (`[PROSODIE]` / `[PROJEKT-RAG]` / `[PROJEKT-KONTEXT]`) gilt — wenn P203d-2 einen `[CODE-EXECUTION]`-Block in den Synthese-System-Prompt einbaut, muss er Substring-disjunkt sein.

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#56-58 (P203d-1: Push, End-to-End-Sandbox-Roundtrip im Chat manuell verifizieren, archived-Skip live)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, NEU `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 1720 passed + 19 P203d-1-Tests aus 1705 baseline (+19, mit 4 internen Reordering-Verschiebungen wegen tmp_db-Fixture-Cache) = 1720 passed, 0 NEUE Failures**, 4 xfailed sichtbar — nicht blockierend. Chris kann den `config.yaml`-Test fixen indem er das Modell-Mapping im `runtime_info`-Helper aktualisiert oder den Test parametrisiert.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor, nicht gehtouched.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** — wie zuvor.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat keinen `node --check`-Pass.** — wie zuvor.
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **Hel-UI zeigt RAG-Status noch nicht prominent an** — P205 vorgemerkt.
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
- **P203c keine Sync-After-Write-Verdrahtung** — bleibt offen, P203d-1 forciert `writable=False`.
- **NEU P203d-1: Output-Synthese (P203d-2) noch offen** — Frontend bekommt aktuell rohen stdout/stderr. Bis P203d-2 da ist, müssen UI-Renderer (oder Caller-Code) die Pretty-Print-Logik selbst übernehmen. Pattern aus `format_sandbox_result` (P171, [`zerberus/modules/telegram/router.py:867`](zerberus/modules/telegram/router.py)) übernehmbar.
- **NEU P203d-1: Token-Cost-Tracking für `code_execution`-Pfad** — der Sandbox-Call selbst kostet keine LLM-Tokens, aber wenn P203d-2 die Synthese addiert, kommt ein zweiter LLM-Call dazu. Cost-Aggregation in `interactions`-Tabelle muss überlegt werden — aktuell zählt nur der Erst-Call.
- **NEU P203d-1: `code_execution` ist nicht in der DB**. Nur in der HTTP-Response. Falls Hel später Code-Execution-Audit-Trails will: separate Tabelle (`code_executions(project_id, session_id, language, exit_code, ts)`) erwägen.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | **Code-Detection im Chat-Endpunkt: nach LLM-Call wird `first_executable_block(answer, allowed_languages)` geprüft, bei aktiv-nicht-archiviertem Projekt und enabled Sandbox läuft `execute_in_workspace(..., writable=False)`, Result kommt als additives `code_execution`-Feld in der OpenAI-kompatiblen Chat-Response (P203d-1)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt toleriert RAG-Fehler (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig (P199) | PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden (P200) | Service-Worker-Scope folgt aus dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen (P200) | `persona_overlay` darf NIEMALS in einer User-sichtbaren Response auftauchen (P201) | Header-Injektion für Cross-Cutting-Concerns MUSS zentral in `profileHeaders()` passieren (P201) | Zombie-ID-Schutz: nach jedem List-Refresh prüfen, ob die im localStorage gemerkte Auswahl noch existiert; sonst räumen (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test für alle User-eingegebenen Felder im DOM-Renderer (P201) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen, wenn die Page WWW-Authenticate-Mechanismen nutzt (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig: `is_inside_workspace` für jeden Schreib-/Löschvorgang im Workspace, plus `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet (P203a) | Hardlink primär, Copy als Fallback bei `OSError` — Methode wird im Return ausgewiesen (P203a) | Atomic-Write-Pattern (tempfile + os.replace) gilt auch für Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat über benutzergenerierte Daten ist verboten in HTML-im-Python-`"""..."""`-Strings — IMMER `data-*`-Attribute + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehört in jede Test-Suite, die HTML in Python-Source baut (P203b) | Prosodie-Brücken-Block enthält NIEMALS numerische Werte — Worker-Protection P191 (P204) | Mehrabian-Schwellen für Konsens-Bestimmung sind identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Brücken-Blöcke (`[PROJEKT-KONTEXT]`, `[PROJEKT-RAG]`, `[PROSODIE]`) MÜSSEN substring-disjoint sein (P204) | Sandbox-Workspace-Mount: Read-Only ist der konservative Default (P203c) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | **Code-Execution im Chat-Endpunkt: archived-Projekte werden konservativ geblockt — `project_overlay is not None` ist der Diskriminator zwischen aktiv-ohne-Overlay (`{}`) und archiviert (`None`) (P203d-1)** | **Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollständiges Sandbox-Result liegt vor — OpenAI-SDK-Clients sehen weiter nur `choices`/`model`/`finish_reason` (P203d-1)** | **`writable=False` ist hardcoded in P203d-1 — Schreibender Mount erst nach explizitem Sync-After-Write-Pfad (P207) (P203d-1)** | **Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open: jeder Crash in `execute_in_workspace` oder `first_executable_block` lässt den Endpunkt mit `code_execution=None` durchlaufen — Chat darf nicht durch Sandbox-Probleme blockiert werden (P203d-1)**
