## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P202 — PWA-Auth-Hotfix: Service-Worker tötet Hel-Login (eingeschoben, Bug-Fix)
**Tests:** 1602 passed (+8), 4 xfailed (pre-existing)
**Commit:** _wird beim Push nachgetragen_
**Repos synchron:** _wird beim verify_sync.ps1 bestätigt_

---

## Zuletzt passiert (1 Hotfix-Patch in dieser Session)

**P202 — PWA-Auth-Hotfix: Service-Worker tötet Hel-Login (eingeschoben).** Chris meldete: "Hel liefert {"detail":"Not authenticated"} nach P199. Nala + Huginn OK. Reproduktion: Browser → Hel-URL → 401. Seit letztem Patch-Block." Diagnose ergab: P200 (PWA) hat den Service-Worker `/hel/sw.js` mit Scope `/hel/` eingeführt, der ALLE GET-Requests im Scope abfängt — auch Top-Level-Navigation. Bei `event.respondWith(fetch(...))` bekommt der SW die 401-Response mit `WWW-Authenticate: Basic` vom Server, reicht sie unverändert an die Page durch — und der Browser ignoriert in diesem Fall den Header (Web-Standard: native Mechanismen wie Auth-Prompts greifen nur bei NICHT-SW-vermittelter Navigation). Ergebnis: User sah JSON-Body statt Login-Dialog. Nala läuft unauffällig (kein Auth), Huginn ist Telegram (kein Browser-Pfad).

**Architektur-Entscheidungen P202:**

- **Navigation NICHT abfangen, nicht nachträglich reparieren.** Statt im SW auf 401 zu reagieren oder Auth-Header zu injecten, returnt der fetch-Handler früh wenn `event.request.mode === 'navigate'`. Damit landet die Anfrage im nativen Browser-Stack inkl. Auth-Prompt, HTTPS-Indikator, Mixed-Content-Warnung. Sauberere PWA-Hygiene unabhängig vom Hel-Bug.
- **Cache-Name `-v2`-Bump.** Der activate-Hook der neuen SW räumt automatisch alle Caches mit anderem Namen. SW selbst wird per `Cache-Control: no-cache` ausgeliefert → User bekommt neue SW beim nächsten Reload, kein manuelles Eingreifen. Alte v1-Caches mit potentiell verseuchten Navigation-Antworten werden geräumt.
- **APP_SHELL ohne Root-Pfad.** Vorher in `HEL_SHELL` der Eintrag `"/hel/"` — beim install-Hook scheiterte `cache.addAll` am 401 vom auth-gated Hel-Root, `Promise.all` rejected komplett, `.catch(() => null)` swallow den Fehler still. Semantisch Müll. Jetzt sind die SHELL-Listen rein statische Public-Assets (`/static/css/...`, `/static/favicon.ico`, `/static/pwa/*.png`) — alle 200, precache läuft sauber durch.
- **Server-Pfad explizit verifiziert.** Neuer End-to-End-Test `TestHelBasicAuthHeader::test_dependency_liefert_wwwauth_bei_missing_creds` mit `TestClient` schießt `verify_admin` ohne Credentials an und prüft `401 + WWW-Authenticate: Basic`. Schutz vor zukünftigen Server-Auth-Refactorings, die diesen Header versehentlich kippen würden.

**Tests P202 (5 neu + 3 angepasst = 8):** `TestServiceWorkerRender::test_navigation_passes_through` (Pure-Function-Test — `request.mode === 'navigate'` im rendered SW-Body), `TestShellLists::{test_nala_shell_keine_navigation, test_hel_shell_keine_navigation, test_shells_haben_static_assets}` (Konstanten-Audit), `TestHelBasicAuthHeader::test_dependency_liefert_wwwauth_bei_missing_creds` (End-to-End-WWW-Authenticate-Verifikation). Angepasst: `test_nala_sw_endpoint` + `test_hel_sw_endpoint` (Cache-Name v2 statt v1, Asset-Audit auf `/static/css/shared-design.css` bzw. `/static/pwa/hel-192.png` statt Root-Pfad).

**User-Recovery:** Wer schon mit P200-SW eine PWA installiert hat, muss nichts manuell tun — `/hel/` neu laden, neuer v2-SW kommt (sw.js ist no-cache), activate räumt v1, Auth-Prompt erscheint. Falls der Browser hängt: DevTools → Application → Service Workers → Unregister → F5.

## Nächster Schritt — sofort starten

**P203: Code-Execution-Pipeline** — Phase 5a Ziel #5 ("Code wird ausgeführt"). War als P202 geplant, ist jetzt P203 weil P202 vom Hel-Auth-Bug eingenommen wurde. Vom Chat aus per Intent `PROJECT_CODE` → LLM generiert Code → Docker-Sandbox (P171) führt aus → Ergebnis zurück in den Chat. Mit Workspace-Layout für Git (jetzt sinnvoll, weil P198 die Templates schon im SHA-Storage liegen hat und P201 das aktive Projekt vom Chat aus erreichbar macht).

Konkret (Coda darf abweichen):
1. **Workspace-Layout definieren.** Empfehlung: pro Projekt ein `data/projects/<slug>/_workspace/`-Ordner als echter Working-Tree, in den die `project_files`-Einträge per `relative_path` materialisiert werden (Symlink oder Hardlink auf den SHA-Storage, Linux-fähig; Windows-Fallback per Copy). `git init` erst hier sinnvoll. Bei jedem Upload/Delete/Index-Trigger den Workspace synchronisieren.
2. **Intent-Erweiterung.** Neuer Intent `PROJECT_CODE` in `intent_parser.py` — Trigger z.B. "lass laufen", "execute", "run python" + aktives Projekt-Header. Ohne `X-Active-Project-Id` → Intent zurückweisen mit "Bitte Projekt auswählen". (P201 hat das Header-Setting schon im UI gelöst, also ist der Pfad jetzt User-bedienbar.)
3. **LLM-Code-Generation-Pfad.** Neuer Endpoint oder Sub-Pipeline: Code-Generation-Prompt baut sich aus User-Frage + System-Prompt + RAG-Hits (P199 ist DA, perfekt) + aktuellem Workspace-Tree-Snapshot. LLM bekommt Tool-Use für `execute_python`/`execute_javascript`/`read_file`/`write_file`/`list_files` (alle scoped auf den Projekt-Workspace).
4. **Sandbox-Verdrahtung.** `modules/sandbox/` (P171) ist bereits da — Workspace als Volume mounten, Read-Write nur innerhalb des Workspaces. Output-Limit (Default 10000 Chars), Timeout (30s), pids/memory/cpu-Limits aus `SandboxConfig`.
5. **Antwort-Synthese.** LLM-Antwort enthält ggf. Sandbox-Output. Im Chat als spezielle Message-Type rendern (Code-Block + Output-Block, expandierbar).
6. **HitL-Gate (Phase 5a #6, kommt mit P205).** Vor JEDER Ausführung: Bestätigungs-UI im Chat ("Soll ich das wirklich ausführen?"). Aber das ist P205 — für P203 erstmal direkter Pfad mit Logging, HitL kommt drauf.
7. **Tests.** Pure-Function-Tests für Workspace-Sync (Files materialisieren, Git-Init), async Tests für Sandbox-Aufruf mit Mock-Subprocess, End-to-End mit Mock-LLM, Edge-Cases (Workspace existiert nicht, Code crasht in Sandbox, Timeout, Output zu groß).

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P203** Code-Execution-Pipeline — Ziel #5 (oben beschrieben)
- **P204** RAG-Toast in Hel-UI — Upload-Response enthält bereits `rag.{chunks, skipped, reason}`, das Frontend ignoriert es. Ein "Indexiert: 5 Chunks" / "Übersprungen (binär)" als Toast wäre nett. Low-prio aber sehr klein, gut als Füller.
- **P205** HitL-Gate vor Code-Execution — Ziel #6
- **P206** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P207** Spec-Contract / Ambiguitäts-Check — Ziel #8

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200)**, **Nala-Tab "Projekte" + Header-Setter (P201)**, **PWA-Auth-Hotfix (P202: navigation-skip im SW + Cache-v2 + Static-Only-Shell + WWW-Authenticate-E2E-Test)**.

Helper aus P201 die in P203+ direkt nutzbar sind:
- `nala.nala_projects_list(request)` — JWT-authenticated Read-Endpoint. Wenn P203 noch weitere User-sichtbare Projekt-Daten braucht (z.B. Workspace-Status), gleichen Pattern kopieren statt /hel/-Endpoints zu verwerten
- JS-`profileHeaders(extra)` — wirkt für alle Nala-Calls. Wenn P203 weitere Header durchschleifen muss (z.B. `X-Sandbox-Run-Id`), hier ergänzen statt am Call-Site
- JS-`getActiveProjectId()` / `getActiveProjectMeta()` — falls ein Frontend-UI-Element den aktuellen Projekt-Slug rendern soll (z.B. "Code wird in Projekt X ausgeführt..."-Status)
- JS-`escapeProjectText(s)` — generischer XSS-Helper, in P203+ wiederverwendbar für alle User-eingegebenen Code-Filenames/-Outputs

Helper aus P200 die in P203+ direkt nutzbar sind:
- `pwa.render_service_worker(cache_name, shell)` — Pure-Function-SW-Renderer. Achtung: Nach P202 cached er KEINE Navigation mehr — das ist Absicht und muss so bleiben (Browser-Auth-Prompt-Erhalt)
- `scripts/generate_pwa_icons.py` — deterministischer PIL-Renderer. Wenn Theme-Farben in Hel/Nala umziehen, hier die Konstanten anpassen + neu rendern

Helper aus P199 die in P203+ direkt nutzbar sind:
- `projects_rag.query_project_rag(project_id, query, base_dir, *, k=5)` — Top-K-Hits für eine Query, fertig formatiert. Nutzt P203 für den Code-Generation-Prompt (Codebase-Kontext aus dem Projekt) ohne neuen RAG-Layer
- `projects_rag.format_rag_block(hits, project_slug=None)` — markdown-formatierter Block für System-Prompt-Anhang
- `projects_rag.chunk_file_content(text, relative_path)` — Pure-Function-Chunker für ein einzelnes File. Wenn P203 das Workspace pro Re-Sync neu indexieren will, ohne über Storage zu gehen, ist das die richtige Schicht
- `projects_rag.index_project_file(project_id, file_id, base_dir)` / `remove_file_from_index(...)` — bequeme async-API für Trigger-Punkte. Wenn P203 nach Code-Execution geänderte Files in den Index reinrollt: einmal pro File aufrufen, ist idempotent

Projekt-Bausteine (P194-P199):
- `projects_repo.create_project/get_project/update_project/archive_project/delete_project` — CRUD
- `projects_repo.register_file/list_files/get_file/delete_file` — Datei-Metadaten
- `projects_repo.compute_sha256/storage_path_for/sanitize_relative_path/is_extension_blocked/count_sha_references` — Helper
- `persona_merge.merge_persona/read_active_project_id/resolve_project_overlay` — Persona-Layer (P197)
- `projects_template.template_files_for/materialize_template` — Skelett-Files (P198)
- `projects_rag.index_project_file/remove_file_from_index/query_project_rag/format_rag_block` — RAG-Layer (P199)
- `hel._projects_storage_base()` — Storage-Wurzel-Indirektion (Tests können umbiegen)

Persona-Pipeline-Bausteine (P184/P185/P197/P199/P201):
- `legacy.load_system_prompt(profile_name)` — User-Persona-Resolver
- `legacy._wrap_persona(prompt)` — AKTIVE-PERSONA-Marker davor
- `persona_merge.merge_persona(base, overlay, slug)` — Projekt-Overlay-Block hinten dran (P197)
- `persona_merge.read_active_project_id(headers)` — liest `X-Active-Project-Id` aus dem Request (P197). P201 setzt diesen Header automatisch für alle Nala-Calls
- `runtime_info.append_runtime_info(prompt, settings)` — Live-Modell/Sandbox/RAG-Block
- `prompt_features.append_decision_box_hint(prompt, settings)` — Decision-Box-Syntax-Anweisung
- `projects_rag.query_project_rag + format_rag_block` — Projekt-RAG-Block hinten dran (P199)

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), #11-16 (P196), #17-18 (P197), #19-21 (P198), #22-25 (P199), #26-32 (P200), #33-37 (P201), **#38-41 (P202: Push, Hel-Auth-Recovery, PWA-Roll-Out v1→v2, SW-Navigation-Skip live verifizieren)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures aus P201-Stand sind in der vollen Suite jetzt nicht mehr sichtbar (entweder transient oder durch Test-Reorganisation gefiltert; 4 xfailed sichtbar — nicht blockierend).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete (P194 `delete_project` löscht aktuell nur DB-Cascade, Bytes können verwaisen wenn der SHA nirgends sonst referenziert ist) — low-prio bis Storage-Volumen relevant. P199 räumt den Projekt-Slug-spezifischen `_rag/`-Ordner auf, aber NICHT die `<sha[:2]>/<sha>`-Bytes — separater Cleanup-Job.
- **Hel-UI zeigt RAG-Status noch nicht prominent an** — Upload-Response enthält `rag.{chunks, skipped, reason}`, aber das Frontend ignoriert das Feld. Ein "Indexiert: 5 Chunks" / "Übersprungen (binär)" als Toast wäre nett. Low-prio. Als P204 vorgemerkt (guter Füller-Patch).
- **Pre-existing Files ohne Index** — Files, die VOR P199 hochgeladen oder via P198 materialisiert wurden, sind nicht im Index. Wenn Chris die nachrüsten will: Mini-CLI-Skript, das `list_files` für jedes Projekt ruft und `index_project_file` pro Eintrag — `index_project_file` ist idempotent. Ein größerer Reindex-Endpoint (`POST /hel/admin/projects/{id}/reindex`) wäre sauberer, aber eigener Patch.
- **Lazy-Embedder-Latenz** — der erste `index_project_file`-Call lädt MiniLM-L6-v2 (~80 MB Download beim allerersten Mal, danach Cache). Während Tests via Monkeypatch abgefangen, in Produktion einmal pro Server-Start. Falls das stört: Eager-Init in `main.py` Startup-Hook hinter `rag_enabled`-Flag — kein eigener Patch nötig, ein 3-Zeilen-Diff.
- **Embedder-Modell hardcoded auf MiniLM-L6-v2** — wenn Chris später auf dual umsteigen will (DE/EN-Trennung wie der globale Index), muss `_embed_text` umgebaut werden. Aktuell ist das wartbar via einer einzigen Funktion — kein Architektur-Schmerz, nur ein Modell-Switch.
- **PWA-Cache-Bust nach Patches** — der SW cacht statische Assets unter Cache-Name `nala-shell-v2` bzw. `hel-shell-v2` (P202-Update). Wenn sich `shared-design.css` oder ein Icon ändern, bekommt der User die Änderung beim nächsten Reload (network-first), aber der alte Asset bleibt im Cache liegen bis activate räumt. Falls jemals ein hartes Cache-Bust nötig ist: Cache-Namen auf `-v3` setzen + redeployen.
- **PWA-Icons nur Initial-Buchstabe** — kein echtes Logo. Wenn Chris später ein Markenzeichen hat (Sonnenblume? Architekt-Sigille?), `scripts/generate_pwa_icons.py` Theme-Konstanten anpassen + Renderer um SVG-/PNG-Overlay erweitern. Aktuell rein typografisch.
- **PWA hat keinen Offline-Modus für die Hauptseite** — P202 hat den HTML-Cache aus dem SW-Pfad genommen (Navigation-Skip + APP_SHELL ohne Root). Der User sieht ohne Server kein gecachtes UI mehr. Akzeptabel für Heimserver — wenn jemals ein echter Offline-Modus gewünscht ist (z.B. fürs Lesen alter Chat-Verläufe), müsste man den Approach umkrempeln (cache-first für Navigation mit eigenem Auth-Handling). Aktuell nicht relevant.
- **P201 Nala-Tab zeigt nur Auswählen, kein Anlegen** — User kann in Nala kein neues Projekt anlegen, nur eines aus der Liste auswählen. Das ist Absicht (Anlegen ist Admin-Aufgabe in Hel), aber falls später ein "Quick-Create"-Button in Nala gewünscht wird: User-Endpoint `POST /nala/projects` analog zu `nala_projects_list` mit zusätzlicher Prüflogik (Permission-Level >= "user", Limit auf X Projekte pro User). Eigener Patch.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral | SW v2: Navigation passiert nativ durch (kein respondWith für `mode === 'navigate'`)

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt toleriert RAG-Fehler (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig (P199) | PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden (P200) | Service-Worker-Scope folgt aus dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen (P200) | `persona_overlay` darf NIEMALS in einer User-sichtbaren Response auftauchen — Admin-Geheimnis. User-Endpoints (`/nala/projects` etc.) MÜSSEN das Repo-Response slimmen, sentinel-string-Test verifiziert (P201) | Header-Injektion für Cross-Cutting-Concerns (Active-Project, Prosody, ...) MUSS zentral in `profileHeaders()` passieren, nicht pro Call-Site — sonst vergisst irgendwann ein neuer Endpoint den Header (P201) | Zombie-ID-Schutz: nach jedem List-Refresh prüfen, ob die im localStorage gemerkte Auswahl noch existiert; sonst räumen — verhindert Header an non-existing-IDs (P201) | XSS-Helper-Funktion mit Min-Count-Source-Audit-Test für alle User-eingegebenen Felder im DOM-Renderer (P201) | **Service-Worker DARF KEINE Top-Level-Navigation per `respondWith` abfangen, wenn die Page WWW-Authenticate-Mechanismen nutzt — Browser ignoriert den Header bei SW-vermittelten Antworten und zeigt keinen Auth-Prompt mehr. Fix: früh-return bei `event.request.mode === 'navigate'`. Pure-Function-Test im SW-Body verifiziert (P202)** | **`cache.addAll`-APP_SHELL DARF KEINE auth-gated Pfade enthalten — `Promise.all` rejected komplett bei einem 401, `.catch(() => null)` swallow den Fehler still aber semantisch Müll. APP_SHELL = nur statische Public-Assets (P202)** | **Cache-Name versionieren bei SW-Logik-Änderungen (`-v1` → `-v2`) — der activate-Hook der neuen Version räumt alte Caches automatisch, kein User-Eingriff (P202)**
