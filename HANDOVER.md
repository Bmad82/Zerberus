## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P203c — Sandbox-Workspace-Mount + execute_in_workspace (Phase 5a #5 Zwischenschritt)
**Tests:** 1705 passed (+20), 4 xfailed (pre-existing), 0 neue Failures
**Commit:** _wird beim Push nachgetragen_
**Repos synchron:** _wird beim Push nachgetragen_

---

## Zuletzt passiert (1 Patch in dieser Session)

**P203c — Sandbox-Workspace-Mount + execute_in_workspace (Phase 5a #5 Zwischenschritt).** Dritter Sub-Patch der P203-Aufteilung (P203a Workspace-Layout + P203b Hel-Hotfix + P203c Sandbox-Mount). Damit ist das Werkzeug für die Code-Execution-Pipeline komplett — was fehlt ist nur noch die Verdrahtung mit Tool-Use-LLM und UI (P203d).

**Was vorher fehlte:** P203a hatte den Working-Tree unter `<data_dir>/projects/<slug>/_workspace/` aufgebaut (Hardlink-primary + Copy-Fallback). Die Sandbox aus P171 hatte aber explizit "Kein Volume-Mount vom Host" als harte Sicherheitsregel. Es gab keinen Weg, den Workspace in den Container reinzubringen.

**Was P203c baut:**

`SandboxManager.execute()` in [`zerberus/modules/sandbox/manager.py`](zerberus/modules/sandbox/manager.py) bekommt zwei keyword-only Parameter:

```python
async def execute(
    self,
    code: str,
    language: str,
    timeout: Optional[int] = None,
    *,
    workspace_mount: Optional[Path] = None,
    mount_writable: bool = False,
) -> Optional[SandboxResult]:
    ...
```

Default-Pfad ohne Mount bleibt **unverändert** (Backwards-Compat für den Huginn-Pipeline-Flow — Test 01 prüft das explizit). Bei gesetztem Mount: `_run_in_container` ergänzt die docker-args um:

```
-v <abs>:/workspace[:ro]
--workdir /workspace
```

Read-Only ist der konservative Default — `mount_writable=True` muss der Caller ausdrücklich setzen. Pfad-Resolution via `Path.resolve(strict=False)` damit Symlinks/8.3-Windows-Namen Docker nicht verwirren.

**Mount-Validation als Early-Reject.** Vor dem `docker run`-Aufruf prüft `execute()`: `workspace_mount.exists()` UND `workspace_mount.is_dir()`. Bei Fail: `SandboxResult(exit_code=-1, error="...")` ohne docker-Aufruf. Macht das Failure-Mode deterministisch testbar (kein Docker-Daemon nötig).

**Convenience-Wrapper `execute_in_workspace`** in [`zerberus/core/projects_workspace.py`](zerberus/core/projects_workspace.py):

```python
async def execute_in_workspace(
    project_id: int, code: str, language: str, base_dir: Path,
    *, writable: bool = False, timeout: Optional[int] = None,
) -> Optional[Any]:
    ...
```

Zieht Slug aus DB via `projects_repo.get_project`, baut `workspace_root` via `workspace_root_for(slug, base_dir)`, validiert per `is_inside_workspace(workspace_root, base_dir)` (Defense-in-Depth gegen Slug-Manipulation — falls ein entartetes `slug=../etc` jemals durch den Sanitizer rutscht), legt den Workspace-Ordner on-demand an, reicht durch an `get_sandbox_manager().execute(...)`. Returns `None` bei unbekanntem Projekt, deaktivierter Sandbox oder Sicherheits-Reject — Caller kann einheitlich auf `None`-Pfade reagieren (Datei-Fallback wie in P171).

**Tests:** 17 in [`test_p203c_sandbox_workspace.py`](zerberus/tests/test_p203c_sandbox_workspace.py): docker-args-Audit ohne Mount unverändert (Test 1 — Backwards-Compat), Mount-RO-Default (Test 2), Mount-Writable (Test 3), Mount-nonexistent → Error (Test 4), Mount-is-File → Error (Test 5), Disabled-Sandbox + Mount → None (Test 6), Blocked-Pattern-Vorrang (Test 7 — short-circuit), execute_in_workspace mit fehlendem Projekt → None (Test 8), korrekter Mount-Pfad-Passthrough (Test 9), writable-Passthrough (Test 10), Workspace-Anlage on-demand (Test 11), Slug-Traversal-Reject (Test 12), Source-Audits Mount + Convenience (Tests 13/14), Disabled-Sandbox-Passthrough via Convenience (Test 15), Timeout-Passthrough (Test 16), Mount-Pfad-resolve()-stable (Test 17).

**Was P203c bewusst NICHT macht:**

- **Kein HitL-Gate.** Die Mensch-bestätigt-Schicht (Phase-5a-Ziel #6) hängt davor, kommt mit P206. P203c läuft direkt durch — RO-Mount + hart-isolierte Sandbox (no-network, read-only-rootfs, no-new-privileges, pids/cpu/memory-limits) halten den Blast-Radius klein.
- **Kein Tool-Use-LLM-Pfad.** P203d verdrahtet die Chat-Pipeline (Code-Detection im LLM-Output, Sandbox-Roundtrip, Output-Synthese als zweiter LLM-Call, UI-Render mit Code+stdout+stderr).
- **Kein Sync-After-Write.** Falls `writable=True`: Caller muss `await sync_workspace(project_id, base_dir)` hinterher rufen, damit DB + RAG die Änderungen sehen. P203c liefert die Brücke, der Caller die Konsistenz-Logik.
- **Keine Image-Pull-Logik.** Healthcheck (P171) bleibt unverändert — Caller muss `manager.healthcheck()` prüfen.
- **Kein Per-Project-Image.** Mount ist projekt-spezifisch, Image bleibt global (`python:3.12-slim` / `node:20-slim`).

## Nächster Schritt — sofort starten

**P203d: Chat-Pipeline-Verdrahtung — Tool-Use-LLM, Output-Synthese, UI.** Schließt Phase-5a-Ziel #5 ab. P203d ist der größte Brocken der Aufteilung — Coda darf gerne noch mal in Sub-Patches schneiden, falls eine Session nicht reicht (Vorschlag unten).

**Konkret (Coda darf abweichen):**

1. **Code-Detection im LLM-Output.** Im `legacy.py`-Stream-Handler nach dem ersten DeepSeek-Output suchen — gibt es einen `python`/`javascript`-Code-Block? `code_extractor.first_executable_block(text, ["python", "javascript"])` ist die Pure-Function-API aus P171/P122.
2. **Active-Project-Check.** Code-Execution NUR bei `X-Active-Project-Id`-Header (P201) — sonst Datei-Fallback wie P171. Projekt muss existieren und nicht archiviert.
3. **Sandbox-Roundtrip.** `result = await execute_in_workspace(project_id, code, language, base_dir, writable=False, timeout=...)`. Bei `result is None`: Sandbox disabled oder Slug-Reject → Datei-Fallback. Bei `result.exit_code != 0`: Output mit stderr in die Synthese-Stufe geben.
4. **Output-Synthese.** Zweiter LLM-Call mit dem ursprünglichen Prompt + Code + stdout + stderr → menschenlesbare Antwort. Pattern wie Telegram-Format aus `format_sandbox_result` (P171), nur diesmal als LLM-Eingabe statt direkter Telegram-Output.
5. **UI-Render.** Im Nala-Frontend: Code-Block mit Syntax-Highlighting + stdout/stderr-Block. Ein paar CSS-Klassen + ein zusätzlicher SSE-Event (`event: code_execution`) sind realistisch — keine eigene UI-Library.
6. **Tests:** End-to-End mit Mock-LLM (Mock-DeepSeek antwortet mit Code-Block, Mock-Sandbox liefert SandboxResult); Source-Audit für die Verdrahtung in `legacy.py`; UI-Source-Audit für den neuen SSE-Event.

**Vorschlag zur Aufteilung von P203d** (falls eine Session zu eng wird):

- **P203d-1**: Backend-Pfad — Code-Detection + Active-Project-Gate + Sandbox-Call + JSON-Response-Erweiterung. KEIN Tool-Use-LLM, kein Synthese-Step. Reicht den raw `SandboxResult` als JSON-Field zurück. End-to-End-Tests mit Mock-LLM.
- **P203d-2**: Output-Synthese — zweiter LLM-Call mit Prompt+Code+Output. Tests mit Mock-LLM.
- **P203d-3**: UI-Render — Nala-Frontend-Patch (Code-Block + Output-Block + ggf. neuer SSE-Event). Source-Audit-Tests.

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):

- **P203d** Chat-Pipeline-Verdrahtung — schließt Ziel #5
- **P205** RAG-Toast in Hel-UI — Upload-Response enthält `rag.{chunks, skipped, reason}`, Frontend ignoriert es bisher
- **P206** HitL-Gate vor Code-Execution — Ziel #6
- **P207** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P208** Spec-Contract / Ambiguitäts-Check — Ziel #8

## Vorhandene Bausteine (NICHT neu bauen)

Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200)**, **Nala-Tab "Projekte" + Header-Setter (P201)**, **PWA-Auth-Hotfix (P202)**, **Project-Workspace-Layout (P203a)**, **Hel-UI-Hotfix Event-Delegation (P203b)**, **Prosodie-Kontext im LLM (P204)**, **Sandbox-Workspace-Mount + execute_in_workspace (P203c)**.

Helper aus P203c die in P203d direkt nutzbar sind:
- `projects_workspace.execute_in_workspace(project_id, code, language, base_dir, *, writable=False, timeout=None)` — die einzige öffentliche API für Workspace-gebundene Code-Execution. Zieht Slug aus DB, validiert Pfad-Sicherheit (Defense-in-Depth gegen Slug-Manipulation), legt Workspace on-demand an, ruft Sandbox. Returns `SandboxResult` oder `None` (Projekt fehlt / Sandbox disabled / Slug-Reject)
- `SandboxManager.execute(code, language, *, workspace_mount=Path, mount_writable=bool, timeout=None)` — die generische Schicht. Wenn ein P203d-Caller die Pfad-Logik selbst machen will (z.B. einen Mount auf einen Tempfolder statt auf einen Project-Workspace), ist das die Schnittstelle
- `SandboxManager._run_in_container` enthält den Mount-Block — wenn weitere Mount-Optionen nötig (z.B. ein zweiter Mount für read-only-Bibliothek), hier andocken
- `sync_workspace(project_id, base_dir)` aus P203a — nach `writable=True`-Calls vom Caller aufrufen, damit DB + RAG den neuen Workspace-Stand sehen

Helper aus P204 die in spätere Patches direkt nutzbar sind:
- `injector.build_prosody_block(prosody, *, bert_label, bert_score)` — Pure-Function, baut den `[PROSODIE]...[/PROSODIE]`-Block. Idempotent.
- `injector._consensus_label(bert_label, bert_score, prosody)` — Pure-Function für Mehrabian-Konsens. Schwellen identisch zu `utils/sentiment_display.py`. Falls UI-Konsens-Logik ändert: HIER MIT-ÄNDERN.
- `injector.PROSODY_BLOCK_MARKER` / `PROSODY_BLOCK_CLOSE` — String-Konstanten, eindeutig (substring-disjoint von `PROJECT_BLOCK_MARKER` und `PROJECT_RAG_BLOCK_MARKER`).

Helper aus P203a die in P203d direkt nutzbar sind:
- `projects_workspace.workspace_root_for(slug, base_dir)` — Pfad-Konvention. Wird von P203c bereits intern genutzt; manueller Aufruf wenn der Caller den Pfad braucht (z.B. zur Anzeige in der UI)
- `projects_workspace.materialize_file(workspace_root, relative_path, source_path)` — Sync FS-Operation, idempotent. Falls P203d nach Code-Execution Files in den Workspace zurückrollt: hier ist die Pure-Schicht
- `projects_workspace.sync_workspace(project_id, base_dir)` — Komplett-Resync. Empfohlen nach `writable=True`-Sandbox-Calls
- `projects_workspace.is_inside_workspace(target, root)` — Pfad-Sicherheits-Check, Pure. Verwendbar auch in P203d für UI-eingegebene Pfade
- `projects_workspace.wipe_workspace(workspace_root)` — Complete-Removal mit Sicherheits-Check (Pfad muss auf `_workspace` enden)

Helper aus P203b — wenn neue Hel-Renderer entstehen, die User-Daten in DOM schreiben:
- **Pattern: `data-*`-Attribute + Event-Delegation statt inline `onclick`-String-Concat.** Siehe `loadProjectFiles` als Vorbild. Quote-immun, XSS-sicher.
- **JS-Integrity-Test laufen lassen.** `test_p203b_hel_js_integrity.py::TestJsSyntaxIntegrity` ist generisch — es deckt ALLE inline `<script>`-Blöcke aus `ADMIN_HTML` ab. Wenn neue Bug-Pattern eingeschleust werden, fällt der Test SOFORT.

Helper aus P201 die in P203d direkt nutzbar sind:
- `nala.nala_projects_list(request)` — JWT-authenticated Read-Endpoint
- JS-`profileHeaders(extra)` — wirkt für alle Nala-Calls. Wenn P203d weitere Header durchschleifen muss (z.B. `X-Sandbox-Run-Id`), hier ergänzen statt am Call-Site
- JS-`getActiveProjectId()` / `getActiveProjectMeta()` — falls ein Frontend-UI-Element den aktuellen Projekt-Slug rendern soll
- JS-`escapeProjectText(s)` — generischer XSS-Helper, in P203d wiederverwendbar für User-eingegebene Code-Filenames/-Outputs

Helper aus P200 die in P203+ direkt nutzbar sind:
- `pwa.render_service_worker(cache_name, shell)` — Pure-Function-SW-Renderer. Achtung: Nach P202 cached er KEINE Navigation mehr — das ist Absicht und muss so bleiben
- `scripts/generate_pwa_icons.py` — deterministischer PIL-Renderer

Helper aus P199 die in P203+ direkt nutzbar sind:
- `projects_rag.query_project_rag(project_id, query, base_dir, *, k=5)` — Top-K-Hits für eine Query. Nutzt P203d für den Code-Generation-Prompt
- `projects_rag.format_rag_block(hits, project_slug=None)` — markdown-formatierter Block für System-Prompt-Anhang
- `projects_rag.chunk_file_content(text, relative_path)` — Pure-Function-Chunker für ein einzelnes File
- `projects_rag.index_project_file(project_id, file_id, base_dir)` / `remove_file_from_index(...)` — bequeme async-API für Trigger-Punkte

Projekt-Bausteine (P194-P199):
- `projects_repo.create_project/get_project/update_project/archive_project/delete_project` — CRUD
- `projects_repo.register_file/list_files/get_file/delete_file` — Datei-Metadaten
- `projects_repo.compute_sha256/storage_path_for/sanitize_relative_path/is_extension_blocked/count_sha_references` — Helper
- `persona_merge.merge_persona/read_active_project_id/resolve_project_overlay` — Persona-Layer (P197)
- `projects_template.template_files_for/materialize_template` — Skelett-Files (P198)
- `projects_rag.index_project_file/remove_file_from_index/query_project_rag/format_rag_block` — RAG-Layer (P199)
- `projects_workspace.workspace_root_for/materialize_file/remove_file/wipe_workspace/sync_workspace/execute_in_workspace` — Workspace-Layer (P203a/c)
- `hel._projects_storage_base()` — Storage-Wurzel-Indirektion (Tests können umbiegen)

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), #11-16 (P196), #17-18 (P197), #19-21 (P198), #22-25 (P199), #26-32 (P200), #33-37 (P201), #38-41 (P202), #42-45 (P203a), #46-48 (P203b), #49-52 (P204), **#53-55 (P203c: Push, Sandbox-Mount manuell mit Docker, RO-Default beweisen)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (`edge-tts` + `test_rag_dual_switch.test_fallback_logic`) — **lokaler Stand 1685 baseline + 17 P203c-Tests + 3 indirekt = 1705 passed, 0 neue Failures**, 4 xfailed sichtbar — nicht blockierend.
- **Schwellen-Synchronisation `_consensus_label` ↔ `consensus_emoji`** — die Mehrabian-Logik in `modules/prosody/injector.py` (P204) und `utils/sentiment_display.py` (P192) hat heute IDENTISCHE Schwellen (BERT_HIGH=0.7, PROSODY_DOMINATES=0.5, VALENCE_NEGATIVE=-0.2). Wenn jemand sie in einer Datei ändert, MÜSSEN sie in der anderen mit. Cross-Test-Verifikation kann das einfangen, ist aber nicht heute geschrieben — wenn die Logik nochmal getouched wird: gemeinsamen Helper extrahieren erwägen.
- **Doppelte `_escapeHtml`-Definitionen in `hel.py`** (Z. 1653 + Z. 3096) — JS überschreibt im non-strict Mode silent, beide unterschiedlich (Z. 1653 escaped `&<>"`, Z. 3096 zusätzlich `'`). Nicht akut, aber Sauberkeits-Schmerzpunkt — bei Gelegenheit konsolidieren auf die strengere Version.
- **`onclick`-Attribute mit String-Concat in HTML-Strings** — Pattern war fragil, P203b hat NUR den `loadProjectFiles`-Renderer gefixt. Andere Renderer in `ADMIN_HTML` mit ähnlichem Pattern sollten beim nächsten Touch auf Event-Delegation migriert werden.
- **NALA_HTML hat keinen `node --check`-Pass.** Schwester-Test wäre sinnvoll — aktuell unklar ob NALA_HTML denselben Bug-Vektor hat.
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete.
- **Hel-UI zeigt RAG-Status noch nicht prominent an** — P205 vorgemerkt.
- **Pre-existing Files ohne Workspace** — Files VOR P203a hochgeladen sind nicht im Workspace. Recovery-Pfad steht (`projects_workspace.sync_workspace`).
- **Pre-existing Files ohne RAG-Index** — separat (P199-Kontext).
- **Lazy-Embedder-Latenz** — beim ersten `index_project_file`-Call lädt MiniLM-L6-v2.
- **PWA-Cache-Bust nach Patches** — Cache-Namen `nala-shell-v2`/`hel-shell-v2`. Falls nochmal harter Cache-Bust nötig: auf `-v3` setzen.
- **PWA-Icons nur Initial-Buchstabe** — `scripts/generate_pwa_icons.py` Theme-Konstanten anpassen falls echtes Logo.
- **PWA hat keinen Offline-Modus für die Hauptseite** — akzeptabel für Heimserver.
- **P201 Nala-Tab zeigt nur Auswählen, kein Anlegen** — Quick-Create-Button später möglich.
- **Hardlink vs. Copy auf Windows** — `os.link` funktioniert auf NTFS, schlägt aber bei FAT32/exFAT/cross-device fehl. Der Copy-Fallback ist live-getestet via Monkeypatch.
- **P204 BERT-Header-Reuse aus P193** — Whisper-Endpoint hat schon BERT berechnet, aber Frontend reicht das nicht weiter. P204 macht BERT server-seitig nochmal (O(ms)). Falls Latenz drückt: `X-Sentiment-Context`-Header analog `X-Prosody-Context` einführen.
- **P203c Mount-Spec auf Windows** — Docker-Desktop unter Windows macht aus `C:\Users\...` → `/c/Users/...` automatisch. `Path.resolve(strict=False)` liefert auf Windows den Backslash-Pfad — Docker-Desktop konvertiert das im Hintergrund. Falls jemand auf einem System ohne Docker-Desktop-Path-Translation arbeitet (raw `dockerd` auf WSL2-Linux): Pfad muss bereits in POSIX-Form übergeben werden. Aktuell akzeptabel, weil Chris auf Docker-Desktop ist.
- **P203c keine Sync-After-Write-Verdrahtung** — wenn ein zukünftiger Caller `writable=True` setzt und der Sandbox-Code Files erzeugt, müssen DB + RAG manuell gesynced werden via `sync_workspace`. P203d wird das im Tool-Use-LLM-Pfad verdrahten.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch | Project-Workspace `_workspace/` mit Hardlink+Copy-Fallback (P203a) | Hel-UI File-Delete via Event-Delegation auf `data-*`-Attributen (P203b) | Prosodie-Brücke zum LLM via `[PROSODIE — Stimmungs-Kontext aus Voice-Input]`-Block (P204) — qualitative Labels, keine Zahlen, Mehrabian-Konsens identisch zu UI-Triptychon | **Sandbox-Volume-Mount via `SandboxManager.execute(workspace_mount, mount_writable)` + Convenience `execute_in_workspace(project_id, ...)` (P203c) — RO-Default, Mount-Existence-Validation als Early-Reject, `is_inside_workspace`-Check als Defense-in-Depth gegen Slug-Manipulation, Pfad-Resolution via `Path.resolve(strict=False)`**

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt toleriert RAG-Fehler (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig (P199) | PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden (P200) | Service-Worker-Scope folgt aus dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen (P200) | `persona_overlay` darf NIEMALS in einer User-sichtbaren Response auftauchen (P201) | Header-Injektion für Cross-Cutting-Concerns MUSS zentral in `profileHeaders()` passieren (P201) | Zombie-ID-Schutz: nach jedem List-Refresh prüfen, ob die im localStorage gemerkte Auswahl noch existiert; sonst räumen (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test für alle User-eingegebenen Felder im DOM-Renderer (P201) | Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen, wenn die Page WWW-Authenticate-Mechanismen nutzt (P202) | `cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten (P202) | Cache-Name versionieren bei SW-Logik-Änderungen (P202) | Workspace-Pfad-Sicherheit zwei-stufig: `is_inside_workspace` für jeden Schreib-/Löschvorgang im Workspace, plus `wipe_workspace` lehnt jeden Pfad ab, der nicht auf `_workspace` endet — verhindert Slug-Manipulations-Angriffe (P203a) | Hardlink primär, Copy als Fallback bei `OSError` — die Methode wird im Return ausgewiesen damit Tests/Logs sehen welcher Pfad griff (P203a) | Atomic-Write-Pattern (tempfile + os.replace) gilt auch für Workspace-Spiegelung — parallele Sandbox-Reads dürfen nie ein halb-geschriebenes File sehen (P203a) | Inline `onclick="fn(...)"` mit String-Concat über benutzergenerierte Daten ist verboten in HTML-im-Python-`"""..."""`-Strings — IMMER `data-*`-Attribute + Event-Delegation per `addEventListener` verwenden (P203b) | JS-Integrity-Test (`node --check` über alle inline `<script>`-Blöcke) gehört in jede Test-Suite, die HTML in Python-Source baut (P203b) | Prosodie-Brücken-Block enthält NIEMALS numerische Werte (Confidence/Score/Valence/Arousal) — nur qualitative Labels — Worker-Protection P191 (P204) | Mehrabian-Schwellen für Konsens-Bestimmung sind identisch in `utils/sentiment_display.py` (UI) und `modules/prosody/injector.py` (LLM) — UI-Emoji und LLM-Label dürfen nicht voneinander abweichen (P204) | Voice-only-Garantie zwei-stufig: (1) Frontend setzt Prosody-Context-Header NUR nach Whisper-Roundtrip; (2) Backend filtert defense-in-depth über Stub-Source-Check (P204) | LLM-Brücken-Blöcke (`[PROJEKT-KONTEXT]`, `[PROJEKT-RAG]`, `[PROSODIE]`) MÜSSEN substring-disjoint sein und Idempotenz-Check via Marker-Substring im Prompt machen (P204) | **Sandbox-Workspace-Mount: Read-Only ist der konservative Default (`mount_writable=False`); Read-Write nur explizit per separatem Flag, zwingt Caller über Sync-Back nachzudenken (P203c)** | **Mount-Existence-Validation als Early-Reject vor `docker run`: `path.exists()` UND `path.is_dir()` — sonst `SandboxResult(exit_code=-1, error=...)` ohne Container-Aufruf (P203c)** | **Pfad-Sicherheit bei Workspace-Mount zwei-stufig: Slug-Sanitizer in `projects_repo` (primär) + `is_inside_workspace(workspace_root, base_dir)`-Check in `execute_in_workspace` (Defense-in-Depth gegen Slug-Manipulation aus alten Datenbanken/Migrations) (P203c)** | **Pfad-Resolution bei Container-Mounts immer `Path.resolve(strict=False)` — schützt vor Symlink/8.3/Relative-Path-Verwirrungen (P203c)**
