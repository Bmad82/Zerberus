## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-03
**Letzter Patch:** P203d-2 — Output-Synthese fuer Sandbox-Code-Execution im Chat (Phase 5a #5 Backend-Loop schliesst)
**Tests:** 1767 passed (+47 neue P203d-2 aus 1720 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`)
**Commit:** dfdd693 — gepusht zu origin/main (plus 3284760 Mini-Cleanup `.gitignore: data/projects/`)
**Repos synchron:** Zerberus / Ratatoskr / Claude ✅ (verify_sync.ps1: 3/3 clean, 0 unpushed)

---

## Zuletzt passiert (1 Patch in dieser Session)

**P203d-2 — Output-Synthese fuer Sandbox-Code-Execution im Chat (Phase 5a #5 Backend-Loop schliesst).** Zweiter Sub-Patch der P203d-Aufteilung (P203d-1 Backend-Pfad ✅ / P203d-2 Output-Synthese ✅ / P203d-3 UI). Nach P203d-2 ist der Backend-Loop von Phase-5a-Ziel #5 vollstaendig — der `answer`-String ist jetzt menschenlesbar, das `code_execution`-Feld bleibt zusaetzlich fuer P203d-3 UI-Render.

**Was vorher fehlte:** P203d-1 hatte den ersten Loop-Strang geschlossen (Code-Detection + Sandbox-Roundtrip + JSON-Field), aber der `answer`-String enthielt weiter den Original-Code-Block. Bei einem `print(2+2)`-Block las der User in Nala nur ```python\nprint(2+2)\n``` — der `stdout=42\n` musste manuell aus der separaten JSON-Response gefischt werden. Auf einem Mobile-Frontend ohne UI-Render-Logik (P203d-3 noch offen) war das unbrauchbar.

**Was P203d-2 baut:**

Neues Modul [`zerberus/modules/sandbox/synthesis.py`](zerberus/modules/sandbox/synthesis.py) — Pure-Function-Schicht plus Async-Wrapper, Pattern analog zu P204 (`prosody/injector.py`) und P197 (`persona_merge.py`):

```python
def should_synthesize(payload) -> bool:
    """True wenn exit_code != 0 (Crash → Erklaerung) ODER
    exit_code == 0 mit nicht-leerem stdout (Output → Aufbereitung).
    False bei None/Non-Dict/fehlendem exit_code/exit=0+leer-stdout."""

def _truncate(text, limit=5000) -> str:
    """Bytes-genau (UTF-8-encoded), errors='ignore' fuer Multi-Byte-safety,
    plus ASCII-Marker '\n…[gekuerzt]' am Ende."""

def build_synthesis_messages(user_prompt, payload) -> list[dict]:
    """System+User-Messages mit Marker [CODE-EXECUTION — Sprache: ... |
    exit_code: ...] und [/CODE-EXECUTION] (substring-disjunkt zu
    PROJEKT-RAG/PROJEKT-KONTEXT/PROSODIE)."""

async def synthesize_code_output(user_prompt, payload, llm_service,
                                  session_id) -> str | None:
    """Trigger-Check, dann LLMService.call(messages, session_id).
    Fail-open: jeder Crash, leere/whitespace-Antwort, falsches
    Result-Format → None → Caller behaelt Original-Answer."""
```

In [`zerberus/app/routers/legacy.py::chat_completions`](zerberus/app/routers/legacy.py) direkt nach dem P203d-1-Block (vor Sentiment-Triptychon):

```python
if code_execution_payload is not None:
    try:
        from zerberus.modules.sandbox.synthesis import synthesize_code_output

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

Plus `store_interaction`-Reorder: User-Insert frueh (Eingabe ist endgueltig), Assistant-Insert + `update_interaction()` ans Ende (nach Synthese), damit der gespeicherte Text der finale Output ist und Sentiment-Triptychon den finalen `answer` liest.

**Trigger-Logik (`should_synthesize`):**

- `exit_code != 0` → triggern, auch bei leerem stderr (Sandbox-Timeouts liefern keinen stderr, aber der User braucht trotzdem eine Erklaerung).
- `exit_code == 0` UND nicht-leerer stdout → triggern (Output braucht Aufbereitung — `42\n` allein im Chat sieht aus wie ein DB-Dump).
- `exit_code == 0` UND leerer stdout → SKIP (Code lief erfolgreich aber produzierte keine Ausgabe; nichts zu sagen — Original-Code-Block bleibt im `answer`).
- `payload is None`, kein Dict, fehlendes `exit_code` → SKIP.

**Was P203d-2 bewusst NICHT macht:**

- **Kein UI-Render im Nala-Frontend.** P203d-3 baut die Code-Card + Output-Card unter dem Synthesized-Bubble. Aktuell sieht der User die Synthese im normalen `choices[0].message.content`-Pfad, das `code_execution`-Feld wird ignoriert (wenn das Frontend es noch nicht kennt) — Backwards-Compat.
- **Kein zweiter `store_interaction`-Eintrag fuer Roh-Output.** Die `interactions`-Tabelle bekommt nur den finalen `answer`. Audit-Trail-Tabelle (`code_executions(project_id, session_id, language, exit_code, ts)`) ist Phase-5a-Schuld — kommt mit P206/HitL.
- **Keine Cost-Aggregation in `interactions.cost`.** Der Synthese-Call addiert eigene Tokens, wird aber nicht aufsummiert. Saubere Loesung: gemeinsamer Cost-Buffer pro Request — vermerkt fuer spaeter.
- **Kein Streaming.** `chat_completions` bleibt synchron. SSE `code_execution`/`synth`-Frames sind P203d-3-Thema.
- **Keine writable-Mount-Aenderung.** P203d-1 forciert weiter `writable=False`. Sync-After-Write kommt mit P207.
- **Kein eigener Fehler-Markierer im `answer`.** Bei Synthese-Fail bleibt der Roh-Output mit Code-Block — kein "Synthese fehlgeschlagen, hier ist der Roh-Output"-Hinweis. Frontend sieht das implizit am `code_execution`-Feld.

**Tests:** 47 in [`test_p203d2_chat_synthesis.py`](zerberus/tests/test_p203d2_chat_synthesis.py).

`TestShouldSynthesize` (8) — Trigger-Gate Pure-Function: None / Non-Dict / fehlendes exit_code / exit=None / exit=0+leer / exit=0+stdout / exit!=0+leer / exit!=0+stderr.

`TestTruncate` (5) — Short / empty / at-limit / over-limit-mit-marker / Multi-Byte-UTF-8-safe (CJK 3-Byte mit Limit=4 → kein Crash).

`TestBuildSynthesisMessages` (9) — Two-Message-Format, User-Prompt im Body, Code-Fence, stdout/stderr nur wenn vorhanden, exit_code im Marker-Header, Marker-Disjunktheit (kein Substring zu PROJEKT-RAG/PROJEKT-KONTEXT/PROSODIE), System-Prompt sagt "wiederhole" + "menschenlesbar", Truncate-bei-10000-Zeichen-stdout.

`TestSynthesizeCodeOutput` (8) — Skip-bei-None-payload, Skip-bei-exit0+leer, Happy-Path mit Messages-Inspection, exit!=0-Pfad, Fail-Open bei LLM-Crash / leere-Antwort / Whitespace / Non-Tuple-Result.

`TestP203d2SourceAudit` (7) — Modul-Existenz + Helper-Exports + `SYNTH_LOG_TAG`-Konstante, Imports + Logging-Tag in legacy.py, korrekte Args-Reihenfolge im Aufruf (`user_prompt=` / `payload=` / `llm_service=` / `session_id=`), Reihenfolge synth-vor-assistant-store (`synth_idx < assistant_store_idx` per File-Index), User-Store-VOR-sandbox-Block, `SYNTH_MAX_OUTPUT_BYTES`-Konstante.

`TestE2ESynthesis` (10) — End-to-End ueber `chat_completions` mit Two-Step-Mock-LLM via `_make_two_step_llm`-Helper (Counter + Messages-Recorder): Erst-Call Code-Block, Zweit-Call Synthese — happy-path-replaces-answer, exit!=0-erklaert-Fehler, User-Prompt-im-Synthese-Call, kein-Synthese-bei-Plain-Text, kein-bei-leerem-Output, kein-ohne-Projekt, kein-bei-disabled-Sandbox, Original-bei-Synthese-Crash, Original-bei-leerer-Synthese, OpenAI-Schema-stabil.

**Logging-Tag `[SYNTH-203d-2]`** — separat von `[SANDBOX-203d]` aus P203d-1, damit Operations-Logs den Synthese-Pfad isoliert beobachten koennen. Pro Synthese-Call eine Zeile (`synthesized exit_code=N raw_output_len=N synth_len=N`), bei Fail-Open eine Warning-Zeile.

## Nächster Schritt — sofort starten

**P203d-3: UI-Render im Nala-Frontend.** Schliesst Phase-5a-Ziel #5 endgueltig.

**Konkret (Coda darf abweichen):**

1. **Frontend-Patch** in `nala_ui.py` (oder wo auch immer das Nala-PWA-HTML lebt). Renderer fuer `code_execution`-Feld in der Chat-Response: unter dem Synthesized-Bubble eine "Code-Card" mit Sprach-Tag + Syntax-Highlighting (Prism.js oder einfach `<pre><code>`) plus eine "Output-Card" mit stdout/stderr (collapsed by default, expandable).
2. **State-Handling.** Aktuell rendert der Bot-Bubble nur `choices[0].message.content` — das ist die Synthese (gut). Der Code-Block ist im `code_execution.code`-Feld, nicht mehr im `answer`. Frontend muss den Block aus dem JSON ziehen.
3. **Mobile-first.** 44px-Touch-Targets fuer den Expand-Button, Code-Card scrollbar fuer lange Zeilen, kein horizontal-overflow-Bruch.
4. **XSS-Sanity.** Code-Inhalte gehen durch `escapeHtml` bevor sie ins DOM kommen — analog dem `escapeProjectText`-Pattern aus P201. Source-Audit-Test mit Min-Count.
5. **Fallback fuer alte Backends.** Wenn `code_execution` im Response-JSON `null` ist (alter Backend, kein aktives Projekt, kein Block) → Code-Card nicht rendern, Bot-Bubble normal anzeigen.
6. **Tests:** Source-Audit dass der Renderer das Feld liest, dass `escapeHtml` aufgerufen wird, plus Smoke-Test gegen den HTML-Endpunkt. Browser-Live-Test ist Manual-Test #63 (vorgemerkt fuer Coda zum Eintragen).

**Reihenfolge-Vorschlag fuer die naechsten Patches** (Coda darf abweichen):

- **P203d-3** UI-Render im Nala-Frontend — Schliesst Phase-5a Ziel #5
- **P205** RAG-Toast in Hel-UI — Upload-Response enthaelt `rag.{chunks, skipped, reason}`, Frontend ignoriert es bisher
- **P206** HitL-Gate vor Code-Execution — Ziel #6
- **P207** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P208** Spec-Contract / Ambiguitäts-Check — Ziel #8

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200)**, **Nala-Tab "Projekte" + Header-Setter (P201)**, **PWA-Auth-Hotfix (P202)**, **Project-Workspace-Layout (P203a)**, **Hel-UI-Hotfix Event-Delegation (P203b)**, **Sandbox-Workspace-Mount + execute_in_workspace (P203c)**, **Prosodie-Kontext im LLM (P204)**, **Code-Detection + Sandbox-Roundtrip im Chat (P203d-1)**, **Output-Synthese fuer Sandbox-Code-Execution (P203d-2)**.

Helper aus P203d-2 die in P203d-3 direkt nutzbar sind:
- `code_execution`-Dict-Schema in der HTTP-Response — fertig serialisiert mit `language`/`code`/`exit_code`/`stdout`/`stderr`/`execution_time_ms`/`truncated`/`error`. P203d-3 liest das, rendert Code-Card aus `language`+`code`, Output-Card aus `stdout`+`stderr`.
- `choices[0].message.content` ist der finale Synthese-Text (oder Original-LLM-Output bei Skip/Fail). Bot-Bubble rendert das wie immer.
- Marker `[CODE-EXECUTION]` ist nur im Synthese-Prompt — taucht NICHT im finalen `answer` auf (das LLM bekommt ihn als Strukturhilfe, schreibt ihn aber nicht in seine Antwort). Frontend muss damit nicht umgehen.
- `code_execution.error`-Feld ist gefuellt wenn Sandbox-Setup-Fehler (z.B. Mount-Reject) auftraten. P203d-3 sollte den als kleinen Banner unter der Code-Card anzeigen wenn nicht None.

Helper aus P203d-1 die in P203d-3 direkt nutzbar sind:
- `code_execution.truncated` (boolean) — wenn True, Output wurde von der Sandbox abgeschnitten (P171-Limit). UI sollte das als "[Output gekuerzt]"-Marker anzeigen.
- `code_execution.execution_time_ms` — kann als Hover/Tooltip an die Output-Card.

Helper aus P203c (vorhanden, ungenutzt von P203d-1/2):
- `execute_in_workspace(..., writable=True, ...)` — wenn P207 Sync-After-Write baut. P203d-1 forciert `writable=False`.
- `sync_workspace(project_id, base_dir)` aus P203a — analog wenn writable-Mount kommt.

Helper aus P204 die in spätere Patches direkt nutzbar sind: siehe vorherigen HANDOVER. Marker-Disjunktheit (`[PROSODIE]` / `[PROJEKT-RAG]` / `[PROJEKT-KONTEXT]` / `[CODE-EXECUTION]`) gilt — wenn ein neuer Brueckenmarker dazukommt, MUSS er Substring-disjoint zu allen vier sein.

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#59-62 (P203d-2: Push, End-to-End-Synthese live, Skip-bei-leerem-Output, Synthese-bei-Crash)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 1767 passed (+47 P203d-2 aus 1720 baseline)**, 4 xfailed sichtbar — nicht blockierend. Chris kann den `config.yaml`-Test fixen indem er das Modell-Mapping im `runtime_info`-Helper aktualisiert oder den Test parametrisiert.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
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
- **P203c keine Sync-After-Write-Verdrahtung** — bleibt offen, P203d-1/2 forcieren `writable=False`.
- **P203d-1: Token-Cost-Tracking für `code_execution`-Pfad** — der Sandbox-Call selbst kostet keine LLM-Tokens, aber **NEU P203d-2: Synthese-LLM-Call addiert Tokens, die NICHT in `interactions.cost` aufsummiert werden**. Aktuell zaehlt nur der Erst-Call ueber `save_cost(...)`. Saubere Loesung: gemeinsamer Cost-Buffer pro Request, beide Calls schreiben rein, am Ende ein `save_cost(...)` mit Summe. Nicht blockierend fuer P203d-3.
- **P203d-1: `code_execution` ist nicht in der DB**. Nur in der HTTP-Response. Falls Hel später Code-Execution-Audit-Trails will: separate Tabelle (`code_executions(project_id, session_id, language, exit_code, ts)`) erwägen — kommt mit P206 (HitL-Gate braucht den Audit-Trail sowieso).
- **NEU P203d-2: Synthese-LLM nutzt aktuell den Default-Cloud-Modus.** Kein `model_override`, kein `temperature_override`. Bei lokalen Modellen via `--` koennte der Synthese-Call trotzdem auf Cloud gehen, wenn der User-Erst-Call `--`-suffix-routet. Vermutlich akzeptabel (Synthese will faktisch sein, niedrige Temperatur), aber bei Bedarf in `synthesize_code_output(...)` einen Parameter durchschleifen.
- **NEU P203d-2: Synthese kann den Code-Block aus dem `answer` nicht entfernen.** Die LLM-Antwort selbst (Erst-Call) hat den Code-Block — der Synthese-Call generiert eine NEUE Antwort und ersetzt `answer` komplett. Falls die Synthese aber den Code wiederholt (entgegen System-Prompt-Anweisung), steht er zweimal drin. Aktuell verlassen wir uns auf das System-Prompt ("wiederhole nicht stumpf"). Falls in Live-Test Probleme: post-Process-Regex der den Code-Block aus `synthesized` entfernt (riskant), oder explizite Anweisung "antworte ohne Code-Fences".

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt: nach LLM-Call wird `first_executable_block(answer, allowed_languages)` geprüft, bei aktiv-nicht-archiviertem Projekt und enabled Sandbox läuft `execute_in_workspace(..., writable=False)`, Result kommt als additives `code_execution`-Feld in der OpenAI-kompatiblen Chat-Response (P203d-1) | **Output-Synthese im Chat-Endpunkt: wenn `code_execution_payload` da und Trigger-Gate zustimmt, ruft `synthesize_code_output(user_prompt, payload, llm_service, session_id)` einen zweiten LLM-Call mit `[CODE-EXECUTION]`-Marker-Block, das Result ersetzt den `answer` — Pure-Function-Schicht in `modules/sandbox/synthesis.py`, Bytes-genau Truncate auf 5KB pro Stream, fail-open auf jeder Stufe (P203d-2)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt toleriert RAG-Fehler (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig (P199) | PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden (P200) | Service-Worker-Scope folgt aus dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen (P200) | `persona_overlay` darf NIEMALS in einer User-sichtbaren Response auftauchen (P201) | Header-Injektion für Cross-Cutting-Concerns MUSS zentral in `profileHeaders()` passieren (P201) | Zombie-ID-Schutz: nach jedem List-Refresh prüfen, ob die im localStorage gemerkte Auswahl noch existiert; sonst räumen (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test für alle User-eingegebenen Felder im DOM-Renderer (P201) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen, wenn die Page WWW-Authenticate-Mechanismen nutzt (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig: `is_inside_workspace` für jeden Schreib-/Löschvorgang im Workspace, plus `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet (P203a) | Hardlink primär, Copy als Fallback bei `OSError` — Methode wird im Return ausgewiesen (P203a) | Atomic-Write-Pattern (tempfile + os.replace) gilt auch für Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat über benutzergenerierte Daten ist verboten in HTML-im-Python-`"""..."""`-Strings — IMMER `data-*`-Attribute + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehört in jede Test-Suite, die HTML in Python-Source baut (P203b) | Prosodie-Brücken-Block enthält NIEMALS numerische Werte — Worker-Protection P191 (P204) | Mehrabian-Schwellen für Konsens-Bestimmung sind identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Brücken-Blöcke (`[PROJEKT-KONTEXT]`, `[PROJEKT-RAG]`, `[PROSODIE]`, `[CODE-EXECUTION]`) MÜSSEN substring-disjoint sein (P204+P203d-2) | Sandbox-Workspace-Mount: Read-Only ist der konservative Default (P203c) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte werden konservativ geblockt — `project_overlay is not None` ist der Diskriminator zwischen aktiv-ohne-Overlay (`{}`) und archiviert (`None`) (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollständiges Sandbox-Result liegt vor — OpenAI-SDK-Clients sehen weiter nur `choices`/`model`/`finish_reason` (P203d-1) | `writable=False` ist hardcoded in P203d-1 — Schreibender Mount erst nach explizitem Sync-After-Write-Pfad (P207) (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open: jeder Crash in `execute_in_workspace` oder `first_executable_block` lässt den Endpunkt mit `code_execution=None` durchlaufen — Chat darf nicht durch Sandbox-Probleme blockiert werden (P203d-1) | **Two-stufige LLM-Pipelines (Erst-Call + Synthese-Call): `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren — sonst landet der Roh-Output in der DB statt dem finalen User-sichtbaren Text. User-Insert kann frueh bleiben, Assistant-Insert + `update_interaction()` wandern als Block ans Ende. Source-Audit-Test verifiziert die Reihenfolge (P203d-2)** | **Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (`should_synthesize(payload) -> bool`) — Skip wenn Output trivial leer (`exit_code == 0` UND leer-stdout), Trigger bei Crash auch ohne stderr (Sandbox-Timeouts liefern keinen stderr) (P203d-2)** | **Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding: `text.encode('utf-8')[:limit].decode(errors='ignore') + ASCII_MARKER` — `len(text)` ist falsch fuer Multi-Byte-UTF-8, `errors='ignore'` schuetzt vor Crash bei abgeschnittenem Multi-Byte-Symbol (P203d-2)** | **Synthese-LLM-Pipeline ist fail-open auf jeder Stufe: LLM-Crash, leere Antwort, Whitespace-only, falsches Result-Format → Returns `None` → Caller behaelt Original-Answer; `code_execution`-Feld bleibt zusaetzlich in der Response, Frontend kann Roh-Output ggf. selbst rendern (P203d-2)**
