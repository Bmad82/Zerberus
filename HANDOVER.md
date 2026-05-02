## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-03
**Letzter Patch:** P203d-3 — UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (Phase 5a Ziel #5 ABGESCHLOSSEN)
**Tests:** 1797 passed (+30 neue P203d-3 aus 1767 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`)
**Commit:** 8243a37 — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude — alle drei gepusht (Ratatoskr `cfc1565`, Claude `6613d83`, 0 unpushed Commits in allen drei). `verify_sync.ps1` meldet Zerberus working-tree dirty: `system_prompt_chris.json` wurde von Hel/Persona-Editor live geaendert (Mutzenbacher-Persona-Experiment, vom User am 2026-05-01 als "gedroppt" markiert — Schulden-Liste). Die Datei ist NICHT Teil von P203d-3 und bleibt unangetastet. Naechster Coda kann sie ggf. via `git checkout system_prompt_chris.json` zuruecksetzen oder via `git rm --cached` + `.gitignore` aus dem Tracking nehmen (analog `.gitignore data/projects/` aus dem letzten Mini-Cleanup `3284760`).

---

## Zuletzt passiert (1 Patch in dieser Session)

**P203d-3 — UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (Phase 5a Ziel #5 ABGESCHLOSSEN).** Dritter und letzter Sub-Patch der P203d-Aufteilung (P203d-1 Backend-Pfad ✅ / P203d-2 Output-Synthese ✅ / P203d-3 UI-Render ✅). Nach P203d-3 ist Ziel #5 von Phase 5a vollstaendig — User sieht im Nala-PWA jetzt nicht nur die menschenlesbare Synthese-Antwort, sondern auch den ausgefuehrten Code als eigene Karte plus stdout/stderr (collapsible) als zweite Karte.

**Was vorher fehlte:** P203d-2 hatte den `answer`-String durch eine LLM-Synthese ersetzt — der Bot-Bubble lieferte einen menschenlesbaren Text wie `Das Ergebnis ist 4.`. Der ausgefuehrte Code und die Roh-Ausgabe steckten weiter im `code_execution`-Feld der HTTP-Response, das Nala-Frontend ignorierte das Feld jedoch komplett. Der User konnte also nicht nachvollziehen, welcher Code gelaufen ist, wie lange er brauchte oder ob der Output gekuerzt wurde — Transparenz-Loch.

**Was P203d-3 baut:**

In [`zerberus/app/routers/nala.py`](zerberus/app/routers/nala.py):

1. **CSS** (im `<style>`-Block): neue Klassen `.code-card`, `.code-card-header`, `.lang-tag`, `.exit-badge` (mit `.exit-ok` gruen / `.exit-fail` rot), `.exec-meta`, `.code-content` (`overflow-x: auto` fuer Mobile, max-height 380px), `.exec-error-banner`, `.output-card` (default `.collapsed`), `.output-card-header`, `.code-toggle` (44×44px Touch-Target), `.output-content` (mit `.output-stderr`-Variant in rot), `.truncated-marker`. Der Toggle erbt das Kintsugi-Gold-Pattern von `.expand-toggle` (P124).

2. **`escapeHtml(s)`** als neuer 3-Zeilen-Helper neben `escapeProjectText` (P201). Delegiert an `escapeProjectText` — gleiche Semantik, eigener Name, damit der XSS-Audit-Test `escapeHtml(`-Aufrufe im Renderer-Body zaehlen kann.

3. **`renderCodeExecution(wrapperEl, codeExec)`** — neuer JS-Renderer. Liest `codeExec.{language, code, exit_code, stdout, stderr, execution_time_ms, truncated, error}`, baut zwei Karten:

   - **Code-Card**: Header mit `lang-tag` + `exit-badge` (gruen/rot je nach exit_code) + `exec-meta` (Laufzeit ms). Body als `<pre><code>` mit `escapeHtml`-escapeden Code. Optional `exec-error-banner` wenn `error != null`.
   - **Output-Card** (nur wenn stdout oder stderr da): Header mit Label (`📤 Ausgabe` bei exit=0, `⚠️ Ausgabe (Fehler)` sonst) + 44×44px Toggle-Button. Body collapsed-by-default, expandiert auf Klick. Enthaelt `<pre>`-Bloecke fuer stdout/stderr (escaped) plus optional `truncated-marker` wenn `truncated: true`.

   Insertion-Punkt: vor dem `.sentiment-triptych`-Element (Visual-Order: bubble → toolbar → code-card → output-card → triptych → export-row).

4. **`addMessage(text, sender, tsOverride)` returns wrapper.** Die Funktion gibt jetzt das DOM-Wrapper-Element zurueck, damit der Caller (sendMessage) den Renderer nachtraeglich einhaengen kann. Backwards-Compat: alle bisherigen Caller (Voice-Input, History-Replay, Late-Fallback) ignorieren den Return-Value, ihr Verhalten bleibt identisch.

5. **Verdrahtung in `sendMessage`** direkt nach dem Bot-Bubble-Render:

```js
const botWrapper = addMessage(reply, 'bot');
// Patch 203d-3: Code-Card + Output-Card unter Bot-Bubble (fail-quiet).
if (data.code_execution) {
    try { renderCodeExecution(botWrapper, data.code_execution); } catch (_e) {}
}
loadSessions();
```

Fail-quiet: Renderer-Crash darf den Chat-Loop nicht unterbrechen.

**Was P203d-3 bewusst NICHT macht:**

- **Keine Syntax-Highlighting-Library.** Code wird als Plain-Text in `<pre><code>` gerendert — keine Prism.js, keine highlight.js. Bewusst: PWA-Bundle bleibt leichtgewichtig, kein zusaetzlicher Asset-Pfad. Falls spaeter gewuenscht: ueber CDN nachladen oder als optionalen Toggle.
- **Kein Edit-Knopf am Code.** Der Code ist read-only. Nala bleibt Chat-Interface — wer den Code anpassen will, kopiert ihn aus dem Bubble (Toolbar hat `📋`-Button) und schickt eine neue Frage.
- **Keine Re-Run-Funktion.** Code laeuft im Backend ein Mal pro LLM-Call. Wer den gleichen Block neu pruefen will, schickt die Frage erneut (Retry-Button an User-Bubble).
- **Keine Output-Card im Skip-Fall.** Wenn `code_execution.code` leer/fehlend ist (alter Backend, kein aktives Projekt, kein Block, Sandbox disabled), rendert der Renderer NICHTS — Bot-Bubble erscheint normal. Backwards-Compat zu Backends die `code_execution` nicht kennen.
- **Keine SSE-Stream-Frames.** Code-Card und Output-Card erscheinen synchron mit dem Bot-Bubble (Chat ist non-streaming bis P203d-2). SSE-Erweiterung waere `[SANDBOX-203d]`/`[SYNTH-203d-2]`-Frames — kein P203d-3-Thema.
- **Kein Telegram-Pfad.** Huginn (Telegram-Adapter) bleibt auf dem Text-Sandbox-Output via `format_sandbox_result` (P171). Nur `/v1/chat/completions` → Nala-PWA-Renderer bekommt das neue UI.

**Tests:** 30 in [`test_p203d3_nala_code_render.py`](zerberus/tests/test_p203d3_nala_code_render.py).

`TestRendererExists` (2) — Funktion definiert + Signatur `(wrapperEl, codeExec)`.

`TestRendererLiestSchemaFelder` (8) — Renderer liest `code`/`language`/`exit_code`/`stdout`/`stderr`/`truncated`/`error`/`execution_time_ms` aus dem P203d-1-Schema.

`TestRendererFallbacks` (2) — Null-Check im Eingang, Skip bei leerem Code (kein Render einer leeren Code-Card).

`TestRendererInsertionPoint` (1) — `insertBefore(.sentiment-triptych)` haelt die Visual-Order.

`TestSendMessageVerdrahtung` (5) — `addMessage` retournt wrapper, Caller bindet ihn (`= addMessage(reply, 'bot')`), Renderer-Aufruf in `sendMessage` mit `data.code_execution`, Reihenfolge addMessage-vor-Renderer, try/catch um den Renderer-Aufruf.

`TestXssEscape` (4) — `escapeHtml`-Helper definiert, `escapeProjectText` nicht geloescht (P201-Audit darf nicht brechen), Min-Count 4 `escapeHtml(`-Aufrufe im Renderer (lang+code+stdout+stderr), keine `innerHTML`-Assigns ohne `escapeHtml` im Renderer-Body.

`TestCss` (5) — `.code-card`/`.output-card`-Klassen definiert, `.code-toggle` mit `min-height: 44px` UND `min-width: 44px` (Mobile-first), `.code-content` mit `overflow-x: auto`, `.output-card.collapsed`-State, `.exit-badge.exit-ok`/`.exit-fail`-Farbcodes.

`TestJsSyntaxIntegrity` (1, skipped wenn node fehlt) — `node --check` ueber alle inline `<script>`-Bloecke aus `NALA_HTML`. Lesson aus P203b: ein einzelner SyntaxError invalidiert den gesamten Block.

`TestNalaEndpointSmoke` (1) — `GET /nala/` liefert HTML mit `function renderCodeExecution`, `data.code_execution`, `.code-card`, `.output-card`.

**Logging-Tag**: keiner. Frontend-only-Patch, alle Logs (`[SANDBOX-203d]`/`[SYNTH-203d-2]`) bleiben aus P203d-1/2.

## Nächster Schritt — sofort starten

**P205: RAG-Toast in Hel-UI.** Phase-5a-Schuld aus dem RAG-Pfad (P199). Upload-Response enthaelt `rag.{chunks, skipped, reason}`, das Hel-Frontend ignoriert es bisher — der User sieht nicht ob/wieviel indiziert wurde.

**Konkret (Coda darf abweichen):**

1. **Backend-Check.** `POST /hel/admin/projects/{id}/files` retourniert bereits `{file_id, sha256, size, rag: {...}}`. Sicherstellen dass `rag` immer ein Dict ist (`{"chunks": N, "skipped": false, "reason": null}` im Happy-Path, `{"chunks": 0, "skipped": true, "reason": "..."}` bei Skip — Reason z.B. `too_large`/`empty`/`unsupported`).
2. **Frontend-Toast** in `hel.py::ADMIN_HTML`. Nach Drop-Zone-Upload-XHR-Success: kleiner Toast unten rechts (analog Notification-Pattern aus PWA-Patches), 3-4 Sekunden sichtbar, mit Inhalt `📚 12 Chunks indiziert` oder `⚠ Datei nicht indiziert: zu gross (60 MB > 50 MB)`.
3. **Reason-Mapping**: kurze de-DE-Strings fuer die paar Reason-Codes (`too_large` → "zu gross", `unsupported` → "Format nicht unterstuetzt", `empty` → "leere Datei", default → "uebersprungen").
4. **Mobile-first.** Toast 44px hoch min., Touch-Tap dismissed sofort. Kein Stacking — neuer Toast ersetzt alten.
5. **XSS-Sanity.** Reason-String durch `_escapeHtml` (Hel hat den Helper schon) bevor er ins DOM kommt.
6. **Tests:** Source-Audit dass der Toast den `rag.chunks` liest und `_escapeHtml` nutzt, plus Smoke gegen den ADMIN_HTML-Block. Manueller Test #65 vorgemerkt.

**Reihenfolge-Vorschlag fuer die naechsten Patches** (Coda darf abweichen):

- **P205** RAG-Toast in Hel-UI — Phase-5a-Schuld
- **P206** HitL-Gate vor Code-Execution — Ziel #6
- **P207** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P208** Spec-Contract / Ambiguitäts-Check — Ziel #8

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200)**, **Nala-Tab "Projekte" + Header-Setter (P201)**, **PWA-Auth-Hotfix (P202)**, **Project-Workspace-Layout (P203a)**, **Hel-UI-Hotfix Event-Delegation (P203b)**, **Sandbox-Workspace-Mount + execute_in_workspace (P203c)**, **Prosodie-Kontext im LLM (P204)**, **Code-Detection + Sandbox-Roundtrip im Chat (P203d-1)**, **Output-Synthese fuer Sandbox-Code-Execution (P203d-2)**, **UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (P203d-3)**.

Helper aus P203d-3 die in P206/P207 direkt nutzbar sind:
- `addMessage(text, sender, tsOverride)` retourniert jetzt das Wrapper-Element. Wer eine Karte/Bubble nachtraeglich anhaengen will (z.B. P206 HitL-Confirm-Card), bekommt den Wrapper out-of-the-box.
- `escapeHtml(s)` als generischer XSS-Helper im Frontend — Pattern fuer jede neue Karte/Banner.
- `renderCodeExecution(wrapper, codeExec)`-Pattern (Card-Header + Card-Body collapsible + 44px Toggle) ist die Vorlage fuer kuenftige Karten (P207 Diff-Card, P206 HitL-Card).
- `.code-card`/`.output-card`-CSS uebernimmt das Kintsugi-Gold-Theme — P206/P207 koennen die Klassen mit-nutzen oder klonen, ohne neuen Theme-Code zu schreiben.

Helper aus P203d-2 die in P206/P207 direkt nutzbar sind: siehe vorherigen HANDOVER. Insbesondere `code_execution`-Dict-Schema und Marker-Disjunktheit `[CODE-EXECUTION]` vs. `[PROJEKT-RAG]` / `[PROJEKT-KONTEXT]` / `[PROSODIE]`.

Helper aus P204 die in spätere Patches direkt nutzbar sind: siehe vorherigen HANDOVER. Marker-Disjunktheit gilt — wenn ein neuer Brueckenmarker dazukommt, MUSS er Substring-disjoint zu allen vorhandenen sein.

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#63-65 (P203d-3: Push, End-to-End-UI live, Output-Toggle live, Truncated-Marker live)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 1797 passed (+30 P203d-3 aus 1767 baseline)**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** — wie zuvor.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat keinen `node --check`-Pass** — **NEU mit P203d-3 GELOEST**: `TestJsSyntaxIntegrity` in `test_p203d3_nala_code_render.py` faehrt `node --check` ueber alle inline `<script>`-Bloecke (analog zu P203b fuer Hel). Skipped wenn `node` nicht im PATH.
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **Hel-UI zeigt RAG-Status noch nicht prominent an** — P205 vorgemerkt.
- **Pre-existing Files ohne Workspace** — wie zuvor.
- **Pre-existing Files ohne RAG-Index** — wie zuvor.
- **Lazy-Embedder-Latenz** — wie zuvor.
- **PWA-Cache-Bust nach Patches** — **NEU P203d-3**: nach dem ersten Open der PWA muss der Service-Worker den Cache invalidieren — Cache-Name-Bump auf `nala-shell-v3` waere sauber, ist aber nicht zwingend (network-first faengt das auf).
- **PWA-Icons nur Initial-Buchstabe** — wie zuvor.
- **PWA hat keinen Offline-Modus für die Hauptseite** — wie zuvor.
- **P201 Nala-Tab zeigt nur Auswählen, kein Anlegen** — wie zuvor.
- **Hardlink vs. Copy auf Windows** — wie zuvor.
- **P204 BERT-Header-Reuse aus P193** — wie zuvor.
- **P203c Mount-Spec auf Windows** — wie zuvor.
- **P203c keine Sync-After-Write-Verdrahtung** — bleibt offen, P203d-1/2/3 forcieren `writable=False`.
- **P203d-1: Token-Cost-Tracking für `code_execution`-Pfad** — Synthese-LLM-Call addiert Tokens, die NICHT in `interactions.cost` aufsummiert werden. Saubere Loesung: gemeinsamer Cost-Buffer pro Request, beide Calls schreiben rein, am Ende ein `save_cost(...)` mit Summe. Nicht blockierend.
- **P203d-1: `code_execution` ist nicht in der DB**. Nur in der HTTP-Response. Falls Hel später Code-Execution-Audit-Trails will: separate Tabelle (`code_executions(project_id, session_id, language, exit_code, ts)`) erwägen — kommt mit P206 (HitL-Gate braucht den Audit-Trail sowieso).
- **P203d-2: Synthese-LLM nutzt aktuell den Default-Cloud-Modus.** Kein `model_override`, kein `temperature_override`. Bei lokalen Modellen via `--` koennte der Synthese-Call trotzdem auf Cloud gehen.
- **P203d-2: Synthese kann den Code-Block aus dem `answer` nicht entfernen** — wenn die LLM-Synthese den Code wiederholt (entgegen System-Prompt), steht er zweimal drin (einmal in der Synthese, einmal in der separaten Code-Card aus P203d-3).
- **NEU P203d-3: Keine Syntax-Highlighting-Library im Frontend.** Code wird als Plain-Text in `<pre><code>` gerendert. Falls in Live-Use unleserlich: Prism.js via CDN als optionaler Toggle.
- **NEU P203d-3: Code-Card hat keinen Copy-to-Clipboard-Button.** Der User kann den Code via Browser-Long-Press kopieren, aber kein dedizierter Button. Toolbar des Bot-Bubble hat einen `📋`-Button, der kopiert aber den ganzen Synthese-Text inkl. evtl. Code-Block (wenn die Synthese ihn behaelt) — nicht den Code aus der Card.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt: nach LLM-Call wird `first_executable_block(answer, allowed_languages)` geprüft, bei aktiv-nicht-archiviertem Projekt und enabled Sandbox läuft `execute_in_workspace(..., writable=False)`, Result kommt als additives `code_execution`-Feld in der OpenAI-kompatiblen Chat-Response (P203d-1) | Output-Synthese im Chat-Endpunkt: zweiter LLM-Call mit `[CODE-EXECUTION]`-Marker-Block ersetzt `answer` (P203d-2) | **UI-Render im Nala-Frontend: `renderCodeExecution(wrapperEl, codeExec)` baut Code-Card (Sprach-Tag + exit-Badge + escapter Code) plus Output-Card (collapsible stdout/stderr mit 44px-Toggle, optional `truncated-marker` und `exec-error-banner`) unter dem Bot-Bubble; `addMessage` retourniert jetzt das Wrapper-Element fuer nachtraegliches Einhaengen; eigener `escapeHtml`-Helper neben `escapeProjectText`; XSS-Min-Count im Audit-Test; `node --check` ueber alle inline `<script>`-Bloecke (P203d-3)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt toleriert RAG-Fehler (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig (P199) | PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden (P200) | Service-Worker-Scope folgt aus dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen (P200) | `persona_overlay` darf NIEMALS in einer User-sichtbaren Response auftauchen (P201) | Header-Injektion für Cross-Cutting-Concerns MUSS zentral in `profileHeaders()` passieren (P201) | Zombie-ID-Schutz: nach jedem List-Refresh prüfen, ob die im localStorage gemerkte Auswahl noch existiert; sonst räumen (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test für alle User-eingegebenen Felder im DOM-Renderer (P201+P203d-3) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen, wenn die Page WWW-Authenticate-Mechanismen nutzt (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig: `is_inside_workspace` für jeden Schreib-/Löschvorgang im Workspace, plus `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet (P203a) | Hardlink primär, Copy als Fallback bei `OSError` — Methode wird im Return ausgewiesen (P203a) | Atomic-Write-Pattern (tempfile + os.replace) gilt auch für Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat über benutzergenerierte Daten ist verboten in HTML-im-Python-`"""..."""`-Strings — IMMER `data-*`-Attribute + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehört in jede Test-Suite, die HTML in Python-Source baut (P203b+P203d-3) | Prosodie-Brücken-Block enthält NIEMALS numerische Werte — Worker-Protection P191 (P204) | Mehrabian-Schwellen für Konsens-Bestimmung sind identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Brücken-Blöcke (`[PROJEKT-KONTEXT]`, `[PROJEKT-RAG]`, `[PROSODIE]`, `[CODE-EXECUTION]`) MÜSSEN substring-disjoint sein (P204+P203d-2) | Sandbox-Workspace-Mount: Read-Only ist der konservative Default (P203c) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte werden konservativ geblockt — `project_overlay is not None` ist der Diskriminator (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollständiges Sandbox-Result liegt vor (P203d-1) | `writable=False` ist hardcoded in P203d-1 (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open (P203d-1) | Two-stufige LLM-Pipelines (Erst-Call + Synthese-Call): `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren (P203d-2) | Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (P203d-2) | Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding (P203d-2) | Synthese-LLM-Pipeline ist fail-open auf jeder Stufe (P203d-2) | **Frontend-Renderer fuer additive Backend-Felder muss BACKWARDS-COMPAT bleiben: bei `data.<feld> === null/undefined` rendert er NICHTS und der Bot-Bubble erscheint normal (P203d-3)** | **DOM-Insertion-Funktionen (`addMessage`) retournen das Wrapper-Element, damit Caller nachtraeglich Karten/Banner einhaengen koennen (P203d-3)** | **Frontend-Renderer fuer User-/LLM-Strings: jedes innerHTML-Statement muss einen `escapeHtml(`-Aufruf enthalten — Audit-Test prueft das per Regex auf den Renderer-Body (P203d-3)** | **Mobile-first 44×44px Touch-Target gilt fuer JEDEN neuen Toggle/Button im Frontend — `min-height: 44px` UND `min-width: 44px` als CSS-Test verifiziert (P203d-3)**
