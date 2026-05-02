## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P200 — Phase 5a #16: PWA für Nala + Hel
**Tests:** 1572 passed (+39), 4 xfailed (pre-existing), 2 pre-existing Failures (unrelated)
**Commit:** 7e783e9 — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude ✅ (verify_sync.ps1: 3/3 clean, 0 unpushed)

---

## Zuletzt passiert
P200 ausgeliefert: Phase 5a Ziel #16 ("Nala + Hel als PWA") abgeschlossen — eingeschoben als unabhängiges Füller-Ziel zwischen P199 (Projekt-RAG) und der eigentlich nächst geplanten Code-Execution-Pipeline. Beide Web-UIs sind jetzt installierbare Progressive Web Apps: iPhone Safari → "Zum Home-Bildschirm", Android Chrome → "App installieren" — Browser-Chrome verschwindet, Splash-Screen + Theme-Color stimmen, Icon im Kintsugi-Stil (Gold auf Blau für Nala, Rot auf Anthrazit für Hel). Bewusst weggelassen: Offline-Modus (Heimserver muss eh laufen), Push-Notifications (Huginn besetzt das), Background-Sync.

**Architektur-Entscheidung Eigener Router statt Erweiterung.** Der Hel-Router hat router-weite Basic-Auth-Dependency (`dependencies=[Depends(verify_admin)]`), die jeden Endpoint dort gated. Hätte ich `/hel/manifest.json` und `/hel/sw.js` einfach in `hel.py` definiert, hätte der Browser beim Manifest-Fetch eine 401-Auth-Challenge bekommen — der "App installieren"-Prompt wäre nie erschienen. Lösung: separater [`zerberus/app/routers/pwa.py`](zerberus/app/routers/pwa.py) ohne Dependencies, in [`main.py`](zerberus/main.py) VOR `hel.router` als ERSTER `include_router`-Call eingehängt. FastAPI matcht URL-Pfade global; weil `hel.router` keine `/manifest.json`/`/sw.js`-Routes definiert, gibt es keinen Konflikt. Ein expliziter Routing-Order-Test (`pwa_pos < hel_pos` im main.py-Source) verhindert spätere Regression.

**SW-Scope via Pfad-Konvention statt Header-Akrobatik.** Browser begrenzen den SW-Scope per Default auf das Verzeichnis, von dem die SW-Datei ausgeliefert wird. `/nala/sw.js` → scope `/nala/`, `/hel/sw.js` → scope `/hel/`. Kein `Service-Worker-Allowed`-Header nötig, keine Root-Position für die SW-Datei, keine Cross-Contamination zwischen den Apps. Genau die richtige Granularität.

**Per-App-Manifeste, nicht ein gemeinsames.** Beide Apps brauchen separate Icons, Theme-Colors, Namen — zwei Manifest-Endpoints sind sauberer als Conditional-Logic in einem Manifest. `NALA_MANIFEST` und `HEL_MANIFEST` sind Pure-Python-Dicts in `pwa.py`, direkt testbar ohne TestClient. Beide haben `name`/`short_name`/`start_url`/`scope`/`display: "standalone"`/`theme_color`/`background_color`/`icons` (192+512, `purpose: "any maskable"` für Android-Adaptive-Icons).

**Service-Worker-Logik minimal.** [`pwa.py::render_service_worker(cache_name, shell)`](zerberus/app/routers/pwa.py) ist Pure-Function, rendert SW-JS aus Template + Cache-Name + Shell-URL-Liste. Install precacht App-Shell (HTML + shared-design.css + favicon + Icons), activate räumt Caches mit anderem Versionsschlüssel, fetch macht network-first mit cache-fallback (Updates kommen direkt durch). Non-GET-Requests passen unverändert durch. `skipWaiting()` + `clients.claim()` schalten neue SW-Versionen sofort online ohne Reload-Pflicht.

**Icon-Generierung deterministisch via PIL.** Skript [`scripts/generate_pwa_icons.py`](scripts/generate_pwa_icons.py) zeichnet 4 PNGs (Nala 192/512, Hel 192/512). Kintsugi-Stil: dunkler Hintergrund, großer goldener (#f0b429) bzw. roter (#ff6b6b) Initial-Buchstabe, drei dünne Bruchnaht-Adern in der Akzentfarbe. Deterministisch (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen — Git-Diff bleibt sauber. Font-Suche: Georgia → Times → DejaVuSerif → Arial → PIL-Default-Font als Fallback.

**HTML-Verdrahtung.** Beide `<head>` um sieben Tags erweitert: `<link rel="manifest">`, `<meta name="theme-color">`, vier Apple-Mobile-Web-App-Meta-Tags (capable yes, status-bar black-translucent, title), zwei Apple-Touch-Icons (192/512). SW-Registrierung als 8-Zeilen-Script vor `</body>`: Feature-Detect (`'serviceWorker' in navigator`), `window.addEventListener('load', ...)`, `register('/<app>/sw.js', { scope: '/<app>/' })`, `.catch()` loggt nach `console.warn` mit Tag `[PWA-200]` (kein UI-Block bei SW-Fail).

39 neue Tests in [`test_projects/test_pwa.py`](zerberus/tests/test_pwa.py), eigentlich [`test_pwa.py`](zerberus/tests/test_pwa.py): 5 Pure-Function für `render_service_worker` (Cache-Name, Shell-URLs, alle drei Event-Listener, skipWaiting+clients.claim, GET-only-Caching), 6 Manifest-Dict-Validierung (Pflichtfelder, 192+512-Icons pro App, Themes unterscheiden sich, JSON-serialisierbar), 4 Endpoint-Direct-Calls (Status 200, korrekte Media-Types, Body-Inhalte, Cache-Control-Header), 16 Source-Audit pro HTML (Manifest-Link, Theme-Color, alle Apple-Tags, beide Touch-Icons, SW-Registrierung mit korrektem Scope), 4 Icon-Existenz inkl. PNG-Magic-Byte-Check, 3 Routing-Order in main.py (pwa-Import, pwa-Include, pwa VOR hel via Substring-Position-Vergleich), 2 Generator-Skript-Existenz. Teststand 1533 → **1572 passed**.

**Phase 5a Ziel #16 ist damit abgeschlossen.** Ziele #1 (Backend P194 + UI P195 + Datei-Upload P196), #2 (Templates P198), #3 (RAG-Index P199), #16 (PWA P200) sind durch. Decision 3 (Persona-Merge-Layer) seit P197 aktiv.

## Nächster Schritt — sofort starten
**P201: Code-Execution-Pipeline** — Phase 5a Ziel #5 ("Code wird ausgeführt"). War vorher als P200 geplant, ist durch das eingeschobene PWA-Patch (P200) jetzt P201. Vom Chat aus per Intent `PROJECT_CODE` → LLM generiert Code → Docker-Sandbox (P171) führt aus → Ergebnis zurück in den Chat. Mit Workspace-Layout für Git (jetzt sinnvoll, weil P198 die Templates schon im SHA-Storage liegen hat).

Konkret (Coda darf abweichen):
1. **Workspace-Layout definieren.** Empfehlung: pro Projekt ein `data/projects/<slug>/_workspace/`-Ordner als echter Working-Tree, in den die `project_files`-Einträge per `relative_path` materialisiert werden (Symlink oder Hardlink auf den SHA-Storage, Linux-fähig; Windows-Fallback per Copy). `git init` erst hier sinnvoll. Bei jedem Upload/Delete/Index-Trigger den Workspace synchronisieren.
2. **Intent-Erweiterung.** Neuer Intent `PROJECT_CODE` in `intent_parser.py` — Trigger z.B. "lass laufen", "execute", "run python" + aktives Projekt-Header. Ohne `X-Active-Project-Id` → Intent zurückweisen mit "Bitte Projekt auswählen".
3. **LLM-Code-Generation-Pfad.** Neuer Endpoint oder Sub-Pipeline: Code-Generation-Prompt baut sich aus User-Frage + System-Prompt + RAG-Hits (P199 ist DA, perfekt) + aktuellem Workspace-Tree-Snapshot. LLM bekommt Tool-Use für `execute_python`/`execute_javascript`/`read_file`/`write_file`/`list_files` (alle scoped auf den Projekt-Workspace).
4. **Sandbox-Verdrahtung.** `modules/sandbox/` (P171) ist bereits da — Workspace als Volume mounten, Read-Write nur innerhalb des Workspaces. Output-Limit (Default 10000 Chars), Timeout (30s), pids/memory/cpu-Limits aus `SandboxConfig`.
5. **Antwort-Synthese.** LLM-Antwort enthält ggf. Sandbox-Output. Im Chat als spezielle Message-Type rendern (Code-Block + Output-Block, expandierbar).
6. **HitL-Gate (Phase 5a #6, kommt mit P203).** Vor JEDER Ausführung: Bestätigungs-UI im Chat ("Soll ich das wirklich ausführen?"). Aber das ist P203 — für P201 erstmal direkter Pfad mit Logging, HitL kommt drauf.
7. **Tests.** Pure-Function-Tests für Workspace-Sync (Files materialisieren, Git-Init), async Tests für Sandbox-Aufruf mit Mock-Subprocess, End-to-End mit Mock-LLM, Edge-Cases (Workspace existiert nicht, Code crasht in Sandbox, Timeout, Output zu groß).

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P201** Code-Execution-Pipeline — Ziel #5 (oben beschrieben)
- **P202** Nala-Tab "Projekte" + Header-Setter — verdrahtet das Nala-Frontend mit P197 (User wählt aktives Projekt im Chat-UI), damit P197+P199 endlich von Nala aus erreichbar werden
- **P203** HitL-Gate vor Code-Execution — Ziel #6
- **P204** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P205** Spec-Contract / Ambiguitäts-Check — Ziel #8

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200: `pwa.router` mit Per-App-Manifeste + SW-Scope-via-Pfad + Kintsugi-Icons + HTML-Meta-Tags + 8-Zeilen-Registrierungs-Script)**.

Helper aus P200 die in P201+ direkt nutzbar sind:
- `pwa.render_service_worker(cache_name, shell)` — Pure-Function-SW-Renderer. Falls P201/P204 weitere PWA-Apps oder ein Web-Worker für Code-Execution-Snapshots braucht, ist das die Schicht
- `pwa.NALA_MANIFEST` / `pwa.HEL_MANIFEST` — Pure-Python-Dicts. Falls Manifest-Felder dynamisch werden müssen (z.B. `name` aus Config), Wrapper-Funktion drumlegen statt Konstante neu zu schreiben
- `scripts/generate_pwa_icons.py` — deterministischer PIL-Renderer. Wenn Theme-Farben in Hel/Nala umziehen, hier die Konstanten (`NALA_THEME`, `HEL_THEME`) anpassen + neu rendern

Helper aus P199 die in P201+ direkt nutzbar sind:
- `projects_rag.query_project_rag(project_id, query, base_dir, *, k=5)` — Top-K-Hits für eine Query, fertig formatiert mit Score und Metadata. Nutzt P201 für den Code-Generation-Prompt (Codebase-Kontext aus dem Projekt) ohne neuen RAG-Layer
- `projects_rag.format_rag_block(hits, project_slug=None)` — markdown-formatierter Block für System-Prompt-Anhang. Wenn P201 einen separaten "Code-Kontext"-Block brauchen sollte, die gleiche Funktion mit anderem Marker forkbar
- `projects_rag.chunk_file_content(text, relative_path)` — Pure-Function-Chunker für ein einzelnes File. Wenn P201 das Workspace pro Re-Sync neu indexieren will, ohne über Storage zu gehen, ist das die richtige Schicht
- `projects_rag.index_project_file(project_id, file_id, base_dir)` — bequeme async-API für Trigger-Punkte. Wenn P201 nach Code-Execution geänderte Files in den Index reinrollt: einmal pro File aufrufen, ist idempotent
- `projects_rag.remove_file_from_index(project_id, file_id, base_dir)` — Inverse zu obigem; nötig wenn P201 Files aus dem Workspace löschen können soll

Projekt-Bausteine (P194-P199):
- `projects_repo.create_project/get_project/update_project/archive_project/delete_project` — CRUD
- `projects_repo.register_file/list_files/get_file/delete_file` — Datei-Metadaten
- `projects_repo.compute_sha256/storage_path_for/sanitize_relative_path/is_extension_blocked/count_sha_references` — Helper
- `persona_merge.merge_persona/read_active_project_id/resolve_project_overlay` — Persona-Layer (P197)
- `projects_template.template_files_for/materialize_template` — Skelett-Files (P198)
- `projects_rag.index_project_file/remove_file_from_index/query_project_rag/format_rag_block` — RAG-Layer (P199)
- `hel._projects_storage_base()` — Storage-Wurzel-Indirektion (Tests können umbiegen)

Persona-Pipeline-Bausteine (P184/P185/P197/P199):
- `legacy.load_system_prompt(profile_name)` — User-Persona-Resolver
- `legacy._wrap_persona(prompt)` — AKTIVE-PERSONA-Marker davor
- `persona_merge.merge_persona(base, overlay, slug)` — Projekt-Overlay-Block hinten dran (P197)
- `runtime_info.append_runtime_info(prompt, settings)` — Live-Modell/Sandbox/RAG-Block
- `prompt_features.append_decision_box_hint(prompt, settings)` — Decision-Box-Syntax-Anweisung
- `projects_rag.query_project_rag + format_rag_block` — Projekt-RAG-Block hinten dran (P199)

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), #11-16 (P196), #17-18 (P197), #19-21 (P198), #22-25 (P199), **#26-32 (P200: Push, iPhone-Nala, iPhone-Hel, Android-Nala, Android-Hel, SW-Sanity, Hel-PWA-Auth)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (4 xfailed sichtbar — nicht blockierend).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete (P194 `delete_project` löscht aktuell nur DB-Cascade, Bytes können verwaisen wenn der SHA nirgends sonst referenziert ist) — low-prio bis Storage-Volumen relevant. P199 räumt den Projekt-Slug-spezifischen `_rag/`-Ordner auf, aber NICHT die `<sha[:2]>/<sha>`-Bytes — das ist immer noch der separate Cleanup-Job.
- **Nala-Frontend setzt `X-Active-Project-Id` noch nicht** — kommt mit dem Nala-Tab "Projekte" (P202-Vorschlag). Bis dahin ist die Kombination P197+P199 nur über externe Clients (`curl`, SillyTavern, eigene Skripte) nutzbar.
- **Hel-UI zeigt RAG-Status noch nicht prominent an** — Upload-Response enthält `rag.{chunks, skipped, reason}`, aber das Frontend ignoriert das Feld. Ein "Indexiert: 5 Chunks" / "Übersprungen (binär)" als Toast wäre nett. Low-prio.
- **Pre-existing Files ohne Index** — Files, die VOR P199 hochgeladen oder via P198 materialisiert wurden, sind nicht im Index. Wenn Chris die nachrüsten will: ein Mini-CLI-Skript, das `list_files` für jedes Projekt ruft und `index_project_file` pro Eintrag — `index_project_file` ist idempotent. Ein größerer Reindex-Endpoint (`POST /hel/admin/projects/{id}/reindex`) wäre sauberer, aber eigener Patch.
- **Lazy-Embedder-Latenz** — der erste `index_project_file`-Call lädt MiniLM-L6-v2 (~80 MB Download beim allerersten Mal, danach Cache). Während Tests via Monkeypatch abgefangen, in Produktion einmal pro Server-Start. Falls das stört: Eager-Init in `main.py` Startup-Hook hinter `rag_enabled`-Flag — kein eigener Patch nötig, ein 3-Zeilen-Diff.
- **Embedder-Modell hardcoded auf MiniLM-L6-v2** — wenn Chris später auf dual umsteigen will (DE/EN-Trennung wie der globale Index), muss `_embed_text` umgebaut werden. Aktuell ist das wartbar via einer einzigen Funktion — kein Architektur-Schmerz, nur ein Modell-Switch.
- **PWA-Cache-Bust nach Patches** — der SW cacht die App-Shell unter Cache-Name `nala-shell-v1` bzw. `hel-shell-v1`. Wenn sich `shared-design.css` oder das HTML ändern, bekommt der User die Änderung beim nächsten Reload (network-first), aber der alte Shell bleibt im Cache liegen bis activate räumt. Falls jemals ein hartes Cache-Bust nötig ist: Cache-Namen auf `-v2` setzen + redeployen, alte SW räumt beim nächsten activate.
- **PWA-Icons nur Initial-Buchstabe** — kein echtes Logo. Wenn Chris später ein Markenzeichen hat (Sonnenblume? Architekt-Sigille?), `scripts/generate_pwa_icons.py` Theme-Konstanten anpassen + Renderer um SVG-/PNG-Overlay erweitern. Aktuell rein typografisch.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (Crash beim Materialisieren bricht Anlegen nicht ab — P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index — projekt-spezifische Inhalte verschmutzen nie den globalen Memory-Layer (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen — `_embed_text` ist die einzige Stelle, die Tests umlenken müssen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt (Upload/Materialize/Delete/Chat-Query) toleriert RAG-Fehler — der Hauptpfad bleibt grün (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig, damit Doppel-Injection-Schutz und Test-Substring-Checks greifen (P199) | **PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden — sonst kein Install-Prompt im Browser (P200)** | **Service-Worker-Scope folgt aus dem PFAD der SW-Datei — `/<app>/sw.js` ist die richtige Position für Per-App-Scope, kein `Service-Worker-Allowed`-Header verbiegen (P200)** | **Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen und der Git-Diff sauber bleibt (P200)**
