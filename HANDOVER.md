## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-03
**Letzter Patch:** P205 — RAG-Toast in Hel-UI nach Datei-Upload (Phase 5a Schuld aus P199)
**Tests:** 1817 passed (+20 neue P205 aus 1797 baseline), 4 xfailed (pre-existing), 3 failed (alle pre-existing — `edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch lokalen `config.yaml`-Drift `deepseek-v4-pro`), 1 skipped (existing)
**Commit:** 28e4f3c — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude — alle drei gepusht (Ratatoskr `033a18d`, Claude `4dd29b2`, 0 unpushed Commits in allen drei). `verify_sync.ps1` meldet Zerberus working-tree dirty: `system_prompt_chris.json` wurde von Hel/Persona-Editor live geaendert (Mutzenbacher-Persona-Experiment, vom User am 2026-05-01 als "gedroppt" markiert — Schulden-Liste). Die Datei ist NICHT Teil von P205 und bleibt unangetastet. Stand identisch zum Vorgaenger-HANDOVER nach P203d-3. Naechster Coda kann sie ggf. via `git checkout system_prompt_chris.json` zuruecksetzen oder via `git rm --cached` + `.gitignore` aus dem Tracking nehmen.

---

## Zuletzt passiert (1 Patch in dieser Session)

**P205 — RAG-Toast in Hel-UI nach Datei-Upload (Phase 5a Schuld aus P199 geschlossen).** Reine Frontend-Lese-Schuld. Backend retournierte seit P199 ein `rag`-Dict in der Upload-Response (`{chunks, skipped, reason}`), das Hel-Frontend hatte das Feld komplett ignoriert.

**Was vorher fehlte:** User droppt Datei in Hel-Drop-Zone, Progress-Zeile zeigt `✓ <name> — fertig`. Das Backend-`rag`-Feld lag ungelesen in der HTTP-Response. Bei einer 60-MB-Datei (`reason: too_large`) blieb der Upload-Status grün, aber der RAG-Index hat die Datei nicht gesehen. User bemerkt das erst beim nächsten Chat-Test (`Mein Geheimrezept ist Banane mit Senf` → LLM kennt das Rezept nicht). Transparenz-Loch.

**Was P205 baut:**

In [`zerberus/app/routers/hel.py`](zerberus/app/routers/hel.py):

1. **CSS** (im `<style>`-Block, ~30 Zeilen direkt vor `</style>`): neue Klassen `.rag-toast` (fixed bottom-right, `min-height: 44px` Mobile-first Touch-Dismiss, `pointer-events: none` im Hidden-State, fade-in via opacity + translateY-Transition), `.rag-toast.visible` (opacity 1 + translateY 0), Border-Varianten `.rag-toast.success` (cyan `#4ecdc4`) und `.rag-toast.warn` (rot `#ff6b6b`); Default-Border Kintsugi-Gold `#c8941f`.

2. **Reason-Mapping** als JS-Konstante `_RAG_REASON_LABELS` direkt vor `_uploadProjectFiles`: kurze de-DE-Strings für alle Codes aus `zerberus/core/projects_rag.py::index_project_file` (`rag_disabled`/`too_large` → "zu gross"/`binary` → "Binärdatei"/`empty`/`no_chunks`/`embed_failed`/`file_not_found`/`project_not_found`/`bytes_missing`/`exception`). Default-Fallback `"übersprungen"` für unbekannte Codes.

3. **`_showRagToast(rag)`** als JS-Renderer (~25 Zeilen). Liest `rag.skipped`/`rag.chunks`/`rag.reason`, baut Text via Mapping-Lookup (`📚 N Chunks indiziert` bei Erfolg, `⚠ Datei nicht indiziert: <Label>` bei Skip), setzt `el.textContent` (XSS-immun — Reason-Strings stammen NIE direkt aus dem Server-Pfad), toggelt `success`/`warn`-Klassen, fügt `.visible` hinzu. Auto-Timeout 3500 ms via Singleton-Slot `_showRagToast._t` (cancelt vorigen Auto-Hide bevor neuer startet — Replace-Pattern bei Multi-Upload). Klick auf den Toast cancelt Timeout und versteckt sofort.

4. **DOM-Element** direkt vor `</body>` (vor dem SW-Reg-Script): `<div id="ragToast" class="rag-toast" role="status" aria-live="polite"></div>`. Ein einziger Toast-Container, kein Stacking.

5. **Verdrahtung** im bestehenden Drop-Zone-Upload `_uploadProjectFiles` → `xhr.onload` Success-Branch direkt nach dem `'fertig'`-Render der Progress-Zeile:

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

Drei Schutzschichten: try/catch um den JSON.parse, `body && body.rag`-Guard für Backwards-Compat, `if (!el || !rag) return` im Renderer.

**Was P205 bewusst NICHT macht:**

- **Kein neuer Backend-Endpoint, keine Schema-Aenderung.** Reine Frontend-Lese-Schuld.
- **Keine Sammel-Aggregation bei Multi-Upload.** Replace-Pattern wie HANDOVER-Spec — letzter Toast gewinnt, Progress-Liste oben zeigt pro File den Roh-Status.
- **Kein Stacking.** Ein einziger `#ragToast`-Container, CSS-State-Toggle.
- **Kein Nala-Pfad.** Nala hat aktuell keinen Datei-Upload (Projekt-Anlage ist Hel-only).
- **Keine i18n.** de-DE hardgecodet.
- **Kein Telegram-Pfad.** Huginn hat keine Datei-Uploads dieser Art.
- **`_escapeHtml`-Doppelung in hel.py** (Zeile 1653 + 3096) bleibt als bestehende Schuld stehen.

**Tests:** 20 in [`test_p205_hel_rag_toast.py`](zerberus/tests/test_p205_hel_rag_toast.py).

`TestToastFunctionExists` (2) — Funktion definiert + Signatur `_showRagToast(rag)`.

`TestReasonMapping` (6) — alle 5 Hauptcodes (`too_large`, `binary`, `empty`, `no_chunks`, `embed_failed`) parametrisiert plus expliziter `'too_large' → 'zu gross'`-Check.

`TestRagToastCss` (4) — `.rag-toast` definiert, `min-height: 44px` (Mobile-first), `position: fixed`, Toggle-Klasse-Variante (`.visible`).

`TestToastDom` (1) — `<div id="ragToast">` im HTML.

`TestUploadWiring` (3) — `body.rag` im Upload-Block, `_showRagToast(...)`-Aufruf im Block, Reihenfolge `'fertig'` vor `_showRagToast` (Toast NACH Render).

`TestToastXss` (1) — `_showRagToast`-Body nutzt `textContent` ODER `_escapeHtml`.

`TestJsSyntaxIntegrity` (1, skipped wenn node fehlt) — `node --check` über alle inline `<script>`-Blöcke aus `ADMIN_HTML`. Lesson aus P203b: ein einzelner SyntaxError invalidiert den gesamten Block.

`TestHelHtmlSmoke` (2) — `ADMIN_HTML` enthält alle Toast-Pieces, genau ein `id="ragToast"`.

**Kollateral-Fix:** `test_projects_ui::TestP196JsFunctions::test_uploads_are_sequential` hatte einen `[:3000]`-Slice auf `_uploadProjectFiles`-Body — durch das neue `_showRagToast` und die Toast-Verdrahtung im onload-Branch wuchs der Body um ~600 Zeichen, der `for (let i = 0; i < files.length`-Marker rutschte über die Slice-Grenze. Slice auf 4500 erhöht (gleiche Datei).

**Logging-Tag:** keiner. Frontend-only-Patch, alle Backend-Logs `[RAG-199]` aus P199 bleiben.

## Nächster Schritt — sofort starten

**P206: HitL-Gate vor Code-Execution (Phase 5a Ziel #6).** Bevor die Sandbox einen Code-Block aus dem Chat-LLM ausführt, soll der User "Yay/Nay" klicken können. Bisher (P203d-1) läuft `execute_in_workspace(...)` direkt nach LLM-Antwort durch — RO-Mount macht das vergleichsweise sicher, aber Phase-5a-Ziel #6 fordert explizite Confirmation.

**Konkret (Coda darf abweichen):**

1. **Mechanismus reuse** aus P167 (`hitl_repo.py`, `pending_actions`-Tabelle, Long-Polling-Endpoint `GET /v1/hitl/poll`). Existierender Pfad: Tool-Use-Aufrufe queuen ein Pending-Item und warten auf User-Approval.
2. **Trigger im Chat-Endpoint:** in `legacy.py::chat_completions` direkt nach `first_executable_block(...)` UND vor `execute_in_workspace(...)` ein `await hitl_repo.create_pending(...)` mit `payload={code, language, project_slug, project_id}`. Dann Long-Poll mit Timeout (z.B. 60 s konfigurierbar). User klickt in Nala-PWA "Ausführen" / "Abbrechen" → `POST /v1/hitl/resolve` setzt das Pending auf `approved`/`rejected`. Bei `approved`: `execute_in_workspace(...)` läuft. Bei `rejected` oder Timeout: `code_execution = {"skipped": true, "reason": "rejected"|"timeout"}` — Synthese-Pfad behandelt das als "User wollte nicht".
3. **UI in Nala-PWA:** wenn ein HitL-Pending zum aktuellen Chat-Turn gehört, zeigt die Karte direkt unter dem Bot-Bubble die Code-Card (P203d-3 Pattern!) plus zwei 44×44px-Buttons "✅ Ausführen" / "❌ Abbrechen". Nach Klick: Spinner, dann Output-Card folgt (P203d-3) sobald Sandbox fertig.
4. **Feature-Flag** `projects.hitl_enabled: bool = True`. Bei false: Verhalten wie bisher (P203d-1 direkt durch ohne Gate).
5. **Audit-Trail.** `interactions.metadata` (oder neue `code_executions`-Tabelle aus dem Backlog) bekommt `hitl_status: approved|rejected|timeout` mit Timestamp.
6. **Tests:** Pending-create-and-resolve-Flow, Timeout-Pfad, Reject-Pfad, UI-Card-Render, Telegram-Skip (Huginn hat keinen HitL-Pfad für Code-Execution — bleibt out).

Reuse-Helper aus P203d-3 die hier direkt nutzbar sind:
- `addMessage(text, sender, tsOverride)` retourniert das Wrapper-Element — ideal für nachträgliches Einhängen der HitL-Confirm-Card.
- `escapeHtml(s)` als XSS-Helper für jedes Code-Snippet das in der Confirm-Card auftaucht.
- `.code-card`/`.output-card`-CSS aus P203d-3 — die Confirm-Card kann das Card-Design mitnutzen.

**Reihenfolge-Vorschlag fuer die naechsten Patches** (Coda darf abweichen):

- **P206** HitL-Gate vor Code-Execution — Ziel #6
- **P207** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P208** Spec-Contract / Ambiguitäts-Check — Ziel #8
- **P209** Zweite Meinung vor Ausführung — Ziel #7

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200)**, **Nala-Tab "Projekte" + Header-Setter (P201)**, **PWA-Auth-Hotfix (P202)**, **Project-Workspace-Layout (P203a)**, **Hel-UI-Hotfix Event-Delegation (P203b)**, **Sandbox-Workspace-Mount + execute_in_workspace (P203c)**, **Prosodie-Kontext im LLM (P204)**, **Code-Detection + Sandbox-Roundtrip im Chat (P203d-1)**, **Output-Synthese fuer Sandbox-Code-Execution (P203d-2)**, **UI-Render im Nala-Frontend fuer Sandbox-Code-Execution (P203d-3)**, **RAG-Toast in Hel-UI (P205)**.

Helper aus P205 die in P206/P207 direkt nutzbar sind:
- `_RAG_REASON_LABELS`/`_showRagToast`-Pattern (statisches Reason-Mapping mit Default-Fallback + textContent-Renderer + Singleton-Timeout-Slot) ist die Vorlage für jeden weiteren Status-Toast in Hel — z.B. "📦 N Files in Workspace synchronisiert" nach P207-Snapshot oder "✅ HitL approved" nach P206-Resolve.
- `.rag-toast`-CSS klont sich trivial zu `.workspace-toast` / `.hitl-toast` falls man verschiedene Farb-Codes oder Positionen will (links unten / rechts oben).
- Replace-Pattern via `id="*Toast"` + `.visible`-Toggle hält die UI ruhig — kein Stacking-Risiko bei Multi-Operation-Bursts.

Helper aus P203d-3 die in P206/P207 direkt nutzbar sind: siehe vorherigen HANDOVER. Insbesondere `addMessage`-Wrapper-Return, `escapeHtml`, `renderCodeExecution`-Pattern, `.code-card`/`.output-card`-CSS.

Helper aus P203d-2 die in P206/P207 direkt nutzbar sind: siehe vorherigen HANDOVER. Insbesondere `code_execution`-Dict-Schema und Marker-Disjunktheit `[CODE-EXECUTION]` vs. `[PROJEKT-RAG]` / `[PROJEKT-KONTEXT]` / `[PROSODIE]`.

Helper aus P204 die in spätere Patches direkt nutzbar sind: siehe vorherigen HANDOVER. Marker-Disjunktheit gilt — wenn ein neuer Brueckenmarker dazukommt, MUSS er Substring-disjoint zu allen vorhandenen sein.

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: alle alten + **#66-70 (P205: Push, Happy-Path-Toast, Skip-Pfade, Multi-Upload-Replace, XSS-Sanity)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), **3 pre-existing Test-Failures** (`edge-tts`, `test_rag_dual_switch.test_fallback_logic`, `test_patch185_runtime_info.test_contains_cloud_model_short_name` durch `config.yaml`-Drift `deepseek-v4-pro`) — **lokaler Stand 1817 passed (+20 P205 aus 1797 baseline)**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — wie zuvor.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Zeile 1653 + 3096) — wie zuvor. P205 nutzt `textContent` und braucht den Helper nicht; die Konsolidierung wäre ein eigener Cleanup-Patch.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — wie zuvor.
- **NALA_HTML hat einen `node --check`-Pass** seit P203d-3, **ADMIN_HTML hat seit P203b** — beide sind durch `TestJsSyntaxIntegrity`-Tests abgedeckt (skipped wenn `node` nicht im PATH).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **Hel-UI zeigt RAG-Status jetzt prominent an** — **NEU MIT P205 GELÖST**: dezenter Toast nach jedem Datei-Upload zeigt Indizierung-Status (`📚 N Chunks indiziert` bei Erfolg, `⚠ Datei nicht indiziert: <Label>` bei Skip).
- **Pre-existing Files ohne Workspace** — wie zuvor.
- **Pre-existing Files ohne RAG-Index** — wie zuvor.
- **Lazy-Embedder-Latenz** — wie zuvor.
- **PWA-Cache-Bust nach Patches** — wie zuvor: nach dem ersten Open der PWA muss der Service-Worker den Cache invalidieren — Cache-Name-Bump auf `hel-shell-v3` wäre sauber, ist aber nicht zwingend (network-first fängt das auf). Bei P205 nicht nötig — der Toast-Code ist im inline-`<script>` innerhalb von `/hel/`-HTML, das via network-first eh frisch kommt.
- **PWA-Icons nur Initial-Buchstabe** — wie zuvor.
- **PWA hat keinen Offline-Modus für die Hauptseite** — wie zuvor.
- **P201 Nala-Tab zeigt nur Auswählen, kein Anlegen** — wie zuvor.
- **Hardlink vs. Copy auf Windows** — wie zuvor.
- **P204 BERT-Header-Reuse aus P193** — wie zuvor.
- **P203c Mount-Spec auf Windows** — wie zuvor.
- **P203c keine Sync-After-Write-Verdrahtung** — bleibt offen, P203d-1/2/3 forcieren `writable=False`.
- **P203d-1: Token-Cost-Tracking für `code_execution`-Pfad** — wie zuvor.
- **P203d-1: `code_execution` ist nicht in der DB** — wie zuvor. P206 (HitL-Audit-Trail) wäre der natürliche Ort für eine `code_executions`-Tabelle.
- **P203d-2: Synthese-LLM nutzt aktuell den Default-Cloud-Modus** — wie zuvor.
- **P203d-2: Synthese kann den Code-Block aus dem `answer` nicht entfernen** — wie zuvor.
- **P203d-3: Keine Syntax-Highlighting-Library im Frontend** — wie zuvor.
- **P203d-3: Code-Card hat keinen Copy-to-Clipboard-Button** — wie zuvor.
- **NEU P205: Sammel-Toast bei Multi-Upload bewusst nicht implementiert** — Replace-Pattern. Falls User-Feedback "Ich seh nur den letzten Status, schick was Aggregiertes": Frontend kann eine zweite Variante einführen, die nach `Promise.all`-Ende einen Sammel-Toast `📚 N Chunks aus K Dateien indiziert (M übersprungen)` rendert.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) | Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default | Code-Detection im Chat-Endpunkt (P203d-1) | Output-Synthese im Chat-Endpunkt (P203d-2) | UI-Render im Nala-Frontend (P203d-3) | **RAG-Toast in Hel-UI: `_showRagToast(rag)` liest `body.rag` aus dem Upload-Response (`{chunks, skipped, reason}`), zeigt einen 3.5-Sek-Toast unten rechts via `#ragToast`-DOM (`📚 N Chunks indiziert` cyan / `⚠ Datei nicht indiziert: <Label>` rot), Reason-Mapping als statisches `_RAG_REASON_LABELS`-Dict mit Default-Fallback `"übersprungen"`, `textContent` statt `innerHTML` (XSS-immun), Singleton-Timeout-Slot `_showRagToast._t` für Replace-Pattern bei Multi-Upload, Klick-Dismiss + 44×44px Touch-Target (P205)**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt toleriert RAG-Fehler (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig (P199) | PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden (P200) | Service-Worker-Scope folgt aus dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen (P200) | `persona_overlay` darf NIEMALS in einer User-sichtbaren Response auftauchen (P201) | Header-Injektion für Cross-Cutting-Concerns MUSS zentral in `profileHeaders()` passieren (P201) | Zombie-ID-Schutz: nach jedem List-Refresh prüfen, ob die im localStorage gemerkte Auswahl noch existiert; sonst räumen (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test für alle User-eingegebenen Felder im DOM-Renderer (P201+P203d-3) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen, wenn die Page WWW-Authenticate-Mechanismen nutzt (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig: `is_inside_workspace` für jeden Schreib-/Löschvorgang im Workspace, plus `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet (P203a) | Hardlink primär, Copy als Fallback bei `OSError` — Methode wird im Return ausgewiesen (P203a) | Atomic-Write-Pattern (tempfile + os.replace) gilt auch für Workspace-Spiegelung (P203a) | Inline `onclick="fn(...)"` mit String-Concat über benutzergenerierte Daten ist verboten in HTML-im-Python-`"""..."""`-Strings — IMMER `data-*`-Attribute + Event-Delegation (P203b) | JS-Integrity-Test (`node --check`) gehört in jede Test-Suite, die HTML in Python-Source baut (P203b+P203d-3+P205) | Prosodie-Brücken-Block enthält NIEMALS numerische Werte — Worker-Protection P191 (P204) | Mehrabian-Schwellen für Konsens-Bestimmung sind identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) (P204) | Voice-only-Garantie zwei-stufig (P204) | LLM-Brücken-Blöcke (`[PROJEKT-KONTEXT]`, `[PROJEKT-RAG]`, `[PROSODIE]`, `[CODE-EXECUTION]`) MÜSSEN substring-disjoint sein (P204+P203d-2) | Sandbox-Workspace-Mount: Read-Only ist der konservative Default (P203c) | Mount-Existence-Validation als Early-Reject vor `docker run` (P203c) | Pfad-Sicherheit bei Workspace-Mount zwei-stufig (P203c) | Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` (P203c) | Code-Execution im Chat-Endpunkt: archived-Projekte werden konservativ geblockt — `project_overlay is not None` ist der Diskriminator (P203d-1) | Code-Execution-Pfad ist additiv: `code_execution`-Field in `ChatCompletionResponse` ist `None` ausser ein vollständiges Sandbox-Result liegt vor (P203d-1) | `writable=False` ist hardcoded in P203d-1 (P203d-1) | Sandbox-Pipeline-Fehler im Chat-Endpunkt sind fail-open (P203d-1) | Two-stufige LLM-Pipelines (Erst-Call + Synthese-Call): `store_interaction("assistant", answer)` MUSS NACH dem finalen Synthese-Schritt passieren (P203d-2) | Trigger-Gate fuer optionale LLM-Folge-Calls als pure Funktion (P203d-2) | Bytes-genau truncaten fuer LLM-Prompts mit User-Output-Embedding (P203d-2) | Synthese-LLM-Pipeline ist fail-open auf jeder Stufe (P203d-2) | Frontend-Renderer fuer additive Backend-Felder muss BACKWARDS-COMPAT bleiben: bei `data.<feld> === null/undefined` rendert er NICHTS (P203d-3+P205) | DOM-Insertion-Funktionen (`addMessage`) retournen das Wrapper-Element (P203d-3) | Frontend-Renderer fuer User-/LLM-Strings: jedes innerHTML-Statement muss einen `escapeHtml(`-Aufruf enthalten — Audit-Test prueft das per Regex auf den Renderer-Body (P203d-3) | Mobile-first 44×44px Touch-Target gilt fuer JEDEN neuen Toggle/Button im Frontend — `min-height: 44px` UND `min-width: 44px` als CSS-Test verifiziert (P203d-3) | **Status-Toasts: `textContent` statt `innerHTML` fuer Reason-Strings, auch wenn die Reason-Codes vom eigenen Backend stammen — statisches Reason-Mapping mit Default-Fallback ist die saubere Trennung (P205)** | **Toast-Replace-Pattern: ein einziger DOM-Container plus CSS-State-Toggle, Singleton-Timeout-Slot fuer Auto-Hide-Reset bei Multi-Operation-Bursts (P205)** | **`pointer-events: none` im Hidden-State von fixed-positionierten Toasts/Overlays, sonst kann das unsichtbare Element Klicks im Hintergrund abfangen (P205)**
