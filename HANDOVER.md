## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P201 — Phase 5a #4 komplett abgeschlossen: Nala-Tab "Projekte" + Header-Setter
**Tests:** 1594 passed (+22), 4 xfailed (pre-existing), 2 pre-existing Failures (unrelated)
**Commit:** f0a586a — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude ✅ (verify_sync.ps1: 3/3 clean, 0 unpushed)

---

## Zuletzt passiert (2 Patches in dieser Session)

**P200 — PWA für Nala + Hel (Ziel #16, eingeschoben).** Beide Web-UIs sind installierbare PWAs (iPhone Safari → "Zum Home-Bildschirm", Android Chrome → "App installieren"). Eigener `pwa.router` ohne Auth-Dependencies, in main.py VOR hel.router eingehängt — sonst gated `verify_admin` den Manifest-Fetch. SW-Scope via Pfad-Konvention (`/nala/sw.js` → scope `/nala/`). Per-App-Manifeste, Kintsugi-Icons via deterministisches PIL-Skript. 39 neue Tests. Vorher schon committet als 7e783e9 + 01af02e (HANDOVER-Fixup).

**P201 — Nala-Tab "Projekte" + Header-Setter (Ziel #4 KOMPLETT).** Schließt den letzten Hop, damit Nala-User vom Chat aus ein aktives Projekt auswählen können — ab da fließt Persona-Overlay (P197) und Datei-Wissen (P199-RAG) automatisch in jede Antwort. Vorher war die Kombination P197+P199 nur über externe Clients (curl, SillyTavern) erreichbar — die Hel-CRUD-UI (P195) konnte zwar Projekte anlegen und Dateien hochladen, aber kein User in Nala konnte sie aktivieren. Mit P201 ist Phase-5a-Ziel #4 ("Dateien kommen ins Projekt") komplett: Hel-Upload (P196) + Indexierung (P199) + Nala-Auswahl (P201) hängen lückenlos.

**Architektur-Entscheidungen P201:**

- **Eigener `/nala/projects`-Endpoint statt Wiederverwendung von `/hel/admin/projects`.** Drei Gründe: Hel-CRUD ist Basic-Auth-gated und Nala-User haben JWT (zwei Auth-Welten, kein Bridge); Nala-User darf NIEMALS `persona_overlay` sehen (Admin-Geheimnis, kann Prompt-Engineering-Spuren enthalten — Slim-Response auf `{id, slug, name, description, updated_at}`); archivierte Projekte werden hier per Default ausgeblendet (User soll keine "alten" Projekte sehen).
- **Header-Injektion ZENTRAL in [`profileHeaders()`](zerberus/app/routers/nala.py).** Statt nur den Chat-Fetch zu modifizieren, hängt P201 den `X-Active-Project-Id` in die Helper-Funktion, die ALLE Nala-Calls verwenden. Damit ist garantiert, dass der Projekt-Kontext konsistent gegated ist — keine Möglichkeit, dass ein neuer Endpoint den Header "vergisst".
- **State in zwei localStorage-Keys.** `nala_active_project_id` (numerisch, für Header-Injektion) + `nala_active_project_meta` (JSON `{id, slug, name}`, für Header-Chip-Renderer ohne Re-Fetch beim Page-Reload). Beim Logout (handle401) NICHT geräumt — gleicher User auf gleichem Browser will Auswahl behalten.
- **Zombie-ID-Schutz.** Wenn `loadNalaProjects` ein Projekt nicht mehr in der Liste findet (gelöscht/archiviert in der Zwischenzeit), wird die aktive Auswahl automatisch geräumt. Sonst hängt der Header-Chip an einer Zombie-ID. Backend (P197) ignoriert das gracefully, aber sauberer ist client-seitig zu räumen.
- **Settings-Modal-Tab statt eigenes Modal.** Bestehende Tab-Mechanik (P142 B-015) wiederverwendet, kein neues Konstrukt. Lazy-Loading: `loadNalaProjects()` läuft erst beim Tab-Klick, nicht beim Modal-Öffnen — spart Roundtrip wenn der User nur Theme ändern will.
- **XSS-Schutz im Renderer.** Drei User-Felder (name, slug, description) laufen alle durch `escapeProjectText()`. Source-Audit-Test zählt mindestens 3 Aufrufe in `renderNalaProjectsList`, damit ein vergessener Aufruf in zukünftigen Refactorings sofort auffällt.

**Wirkung im Chat (P201 verschmilzt mit P197+P199):** Wenn User in Nala ein Projekt aktiviert, sendet jeder `/v1/chat/completions`-Request ab dann den Header `X-Active-Project-Id: <id>`. Das Backend (P197) löst den Persona-Overlay aus der DB und merged ihn an den System-Prompt. P199 macht zusätzlich einen Per-Projekt-RAG-Lookup mit der User-Query, formatiert die Top-K-Hits als Markdown-Sektion und hängt sie an. Server-Logs `[PERSONA-197]` und `[RAG-199]` zeigen die Wirkung. Ohne aktives Projekt → kein Header → keine Logs → Chat läuft wie vor P197.

**Tests P201 (21 neu + 1 nachgeschärft = 22):** 6 Endpoint-Tests in [`test_nala_projects_tab.py`](zerberus/tests/test_nala_projects_tab.py) (401 ohne Login, leere Liste, gelistete Projekte, archived versteckt, persona_overlay NICHT im Response mit sentinel-string-check, minimal-Felder), 6 Source-Audit für Tab/Panel/Chip, 4 für JS-Funktionen + localStorage-Keys + fetch-Endpoint + 401-Handling, 2 für Header-Injektion (zentral in profileHeaders, nicht pro Call), 2 für XSS-Schutz inkl. Min-Count escapeProjectText, 1 für Zombie-ID-Schutz, 1 für Tab-Lazy-Load. Plus 1 nachgeschärfter Test in `test_settings_umbau.py`: alter `openSettingsModal()`-Proxy-Check → spezifischer 🔧-Emoji + `icon-btn`-Pattern (P201 erlaubt openSettingsModal im Header NUR via Project-Chip, der gleichzeitig switchSettingsTab('projects') ruft).

**Phase 5a Ziel #4 ist damit komplett abgeschlossen.** Status der Ziele: #1 (Backend P194 + UI P195 + Datei-Upload P196), #2 (Templates P198), #3 (RAG-Index P199), **#4 (Dateien-Upload + Indexierung + Nala-Auswahl P196+P199+P201)**, #16 (PWA P200) sind durch. Decision 3 (Persona-Merge-Layer) seit P197 aktiv.

## Nächster Schritt — sofort starten
**P202: Code-Execution-Pipeline** — Phase 5a Ziel #5 ("Code wird ausgeführt"). War vorher als P200 geplant, dann P201, ist durch die zwei eingeschobenen Patches (P200 PWA + P201 Nala-Tab) jetzt P202. Vom Chat aus per Intent `PROJECT_CODE` → LLM generiert Code → Docker-Sandbox (P171) führt aus → Ergebnis zurück in den Chat. Mit Workspace-Layout für Git (jetzt sinnvoll, weil P198 die Templates schon im SHA-Storage liegen hat und P201 das aktive Projekt vom Chat aus erreichbar macht).

Konkret (Coda darf abweichen):
1. **Workspace-Layout definieren.** Empfehlung: pro Projekt ein `data/projects/<slug>/_workspace/`-Ordner als echter Working-Tree, in den die `project_files`-Einträge per `relative_path` materialisiert werden (Symlink oder Hardlink auf den SHA-Storage, Linux-fähig; Windows-Fallback per Copy). `git init` erst hier sinnvoll. Bei jedem Upload/Delete/Index-Trigger den Workspace synchronisieren.
2. **Intent-Erweiterung.** Neuer Intent `PROJECT_CODE` in `intent_parser.py` — Trigger z.B. "lass laufen", "execute", "run python" + aktives Projekt-Header. Ohne `X-Active-Project-Id` → Intent zurückweisen mit "Bitte Projekt auswählen". (P201 hat das Header-Setting schon im UI gelöst, also ist der Pfad jetzt User-bedienbar.)
3. **LLM-Code-Generation-Pfad.** Neuer Endpoint oder Sub-Pipeline: Code-Generation-Prompt baut sich aus User-Frage + System-Prompt + RAG-Hits (P199 ist DA, perfekt) + aktuellem Workspace-Tree-Snapshot. LLM bekommt Tool-Use für `execute_python`/`execute_javascript`/`read_file`/`write_file`/`list_files` (alle scoped auf den Projekt-Workspace).
4. **Sandbox-Verdrahtung.** `modules/sandbox/` (P171) ist bereits da — Workspace als Volume mounten, Read-Write nur innerhalb des Workspaces. Output-Limit (Default 10000 Chars), Timeout (30s), pids/memory/cpu-Limits aus `SandboxConfig`.
5. **Antwort-Synthese.** LLM-Antwort enthält ggf. Sandbox-Output. Im Chat als spezielle Message-Type rendern (Code-Block + Output-Block, expandierbar).
6. **HitL-Gate (Phase 5a #6, kommt mit P204).** Vor JEDER Ausführung: Bestätigungs-UI im Chat ("Soll ich das wirklich ausführen?"). Aber das ist P204 — für P202 erstmal direkter Pfad mit Logging, HitL kommt drauf.
7. **Tests.** Pure-Function-Tests für Workspace-Sync (Files materialisieren, Git-Init), async Tests für Sandbox-Aufruf mit Mock-Subprocess, End-to-End mit Mock-LLM, Edge-Cases (Workspace existiert nicht, Code crasht in Sandbox, Timeout, Output zu groß).

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P202** Code-Execution-Pipeline — Ziel #5 (oben beschrieben)
- **P203** RAG-Toast in Hel-UI — Upload-Response enthält bereits `rag.{chunks, skipped, reason}`, das Frontend ignoriert es. Ein "Indexiert: 5 Chunks" / "Übersprungen (binär)" als Toast wäre nett. Low-prio aber sehr klein, gut als Füller.
- **P204** HitL-Gate vor Code-Execution — Ziel #6
- **P205** Diff-View / Snapshots / Rollback — Ziel #9 + #10
- **P206** Spec-Contract / Ambiguitäts-Check — Ziel #8

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197)**, **Projekt-Templates (P198)**, **Projekt-RAG-Index (P199)**, **PWA-Verdrahtung Nala + Hel (P200)**, **Nala-Tab "Projekte" + Header-Setter (P201: `nala.nala_projects_list`-Endpoint + `profileHeaders()`-Header-Injektion + Settings-Tab + Active-Project-Chip + localStorage-State + Zombie-ID-Schutz + XSS-Helper)**.

Helper aus P201 die in P202+ direkt nutzbar sind:
- `nala.nala_projects_list(request)` — JWT-authenticated Read-Endpoint. Wenn P202 noch weitere User-sichtbare Projekt-Daten braucht (z.B. Workspace-Status), gleichen Pattern kopieren statt /hel/-Endpoints zu verwerten
- JS-`profileHeaders(extra)` — wirkt fuer alle Nala-Calls. Wenn P202 weitere Header durchschleifen muss (z.B. `X-Sandbox-Run-Id`), hier ergaenzen statt am Call-Site
- JS-`getActiveProjectId()` / `getActiveProjectMeta()` — falls ein Frontend-UI-Element den aktuellen Projekt-Slug rendern soll (z.B. "Code wird in Projekt X ausgefuehrt..."-Status)
- JS-`escapeProjectText(s)` — generischer XSS-Helper, in P202+ wiederverwendbar fuer alle User-eingegebenen Code-Filenames/-Outputs

Helper aus P200 die in P202+ direkt nutzbar sind:
- `pwa.render_service_worker(cache_name, shell)` — Pure-Function-SW-Renderer. Falls P202+ ein Web-Worker für Code-Execution-Snapshots braucht, ist das die Schicht
- `scripts/generate_pwa_icons.py` — deterministischer PIL-Renderer. Wenn Theme-Farben in Hel/Nala umziehen, hier die Konstanten anpassen + neu rendern

Helper aus P199 die in P202+ direkt nutzbar sind:
- `projects_rag.query_project_rag(project_id, query, base_dir, *, k=5)` — Top-K-Hits für eine Query, fertig formatiert. Nutzt P202 für den Code-Generation-Prompt (Codebase-Kontext aus dem Projekt) ohne neuen RAG-Layer
- `projects_rag.format_rag_block(hits, project_slug=None)` — markdown-formatierter Block für System-Prompt-Anhang
- `projects_rag.chunk_file_content(text, relative_path)` — Pure-Function-Chunker für ein einzelnes File. Wenn P202 das Workspace pro Re-Sync neu indexieren will, ohne über Storage zu gehen, ist das die richtige Schicht
- `projects_rag.index_project_file(project_id, file_id, base_dir)` / `remove_file_from_index(...)` — bequeme async-API für Trigger-Punkte. Wenn P202 nach Code-Execution geänderte Files in den Index reinrollt: einmal pro File aufrufen, ist idempotent

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
- `persona_merge.read_active_project_id(headers)` — liest `X-Active-Project-Id` aus dem Request (P197). P201 setzt diesen Header automatisch fuer alle Nala-Calls
- `runtime_info.append_runtime_info(prompt, settings)` — Live-Modell/Sandbox/RAG-Block
- `prompt_features.append_decision_box_hint(prompt, settings)` — Decision-Box-Syntax-Anweisung
- `projects_rag.query_project_rag + format_rag_block` — Projekt-RAG-Block hinten dran (P199)

## Offenes
- DECISIONS_PENDING leer.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), #11-16 (P196), #17-18 (P197), #19-21 (P198), #22-25 (P199), #26-32 (P200), **#33-37 (P201: Push, Nala-Tab + Chip + localStorage, End-to-End P197+P199 von Nala aus, Zombie-ID-Schutz, XSS-Sanity)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (4 xfailed sichtbar — nicht blockierend).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete (P194 `delete_project` löscht aktuell nur DB-Cascade, Bytes können verwaisen wenn der SHA nirgends sonst referenziert ist) — low-prio bis Storage-Volumen relevant. P199 räumt den Projekt-Slug-spezifischen `_rag/`-Ordner auf, aber NICHT die `<sha[:2]>/<sha>`-Bytes — separater Cleanup-Job.
- **Hel-UI zeigt RAG-Status noch nicht prominent an** — Upload-Response enthält `rag.{chunks, skipped, reason}`, aber das Frontend ignoriert das Feld. Ein "Indexiert: 5 Chunks" / "Übersprungen (binär)" als Toast wäre nett. Low-prio. Als P203 vorgemerkt (guter Füller-Patch).
- **Pre-existing Files ohne Index** — Files, die VOR P199 hochgeladen oder via P198 materialisiert wurden, sind nicht im Index. Wenn Chris die nachrüsten will: Mini-CLI-Skript, das `list_files` für jedes Projekt ruft und `index_project_file` pro Eintrag — `index_project_file` ist idempotent. Ein größerer Reindex-Endpoint (`POST /hel/admin/projects/{id}/reindex`) wäre sauberer, aber eigener Patch.
- **Lazy-Embedder-Latenz** — der erste `index_project_file`-Call lädt MiniLM-L6-v2 (~80 MB Download beim allerersten Mal, danach Cache). Während Tests via Monkeypatch abgefangen, in Produktion einmal pro Server-Start. Falls das stört: Eager-Init in `main.py` Startup-Hook hinter `rag_enabled`-Flag — kein eigener Patch nötig, ein 3-Zeilen-Diff.
- **Embedder-Modell hardcoded auf MiniLM-L6-v2** — wenn Chris später auf dual umsteigen will (DE/EN-Trennung wie der globale Index), muss `_embed_text` umgebaut werden. Aktuell ist das wartbar via einer einzigen Funktion — kein Architektur-Schmerz, nur ein Modell-Switch.
- **PWA-Cache-Bust nach Patches** — der SW cacht die App-Shell unter Cache-Name `nala-shell-v1` bzw. `hel-shell-v1`. Wenn sich `shared-design.css` oder das HTML ändern, bekommt der User die Änderung beim nächsten Reload (network-first), aber der alte Shell bleibt im Cache liegen bis activate räumt. Falls jemals ein hartes Cache-Bust nötig ist: Cache-Namen auf `-v2` setzen + redeployen, alte SW räumt beim nächsten activate.
- **PWA-Icons nur Initial-Buchstabe** — kein echtes Logo. Wenn Chris später ein Markenzeichen hat (Sonnenblume? Architekt-Sigille?), `scripts/generate_pwa_icons.py` Theme-Konstanten anpassen + Renderer um SVG-/PNG-Overlay erweitern. Aktuell rein typografisch.
- **P201 Nala-Tab zeigt nur Auswaehlen, kein Anlegen** — User kann in Nala kein neues Projekt anlegen, nur eines aus der Liste auswaehlen. Das ist Absicht (Anlegen ist Admin-Aufgabe in Hel), aber falls spaeter ein "Quick-Create"-Button in Nala gewuenscht wird: User-Endpoint `POST /nala/projects` analog zu `nala_projects_list` mit zusaetzlicher Prueflogik (Permission-Level >= "user", Limit auf X Projekte pro User). Eigener Patch.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram | MiniLM-L6-v2 für Projekt-RAG (Pure-Numpy-Linearscan) | PWA-Manifeste pro App (Nala + Hel) mit Service-Worker-Scope-via-Pfad | `X-Active-Project-Id`-Header in `profileHeaders()` zentral

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert | Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll | Templates als reguläre `project_files`-Einträge im SHA-Storage (kein Sonderpfad) — sichtbar in Hel/RAG/Sandbox ohne Spezialfall (P198) | Idempotenz-Check VOR Schreiben für jeden Generator, der User-Content-Schutz braucht (P198) | Best-Effort-Verdrahtung im Endpoint, wenn der Hauptpfad NICHT vom Nebeneffekt abhängt (P198) | Per-Projekt-RAG-Index ist isoliert vom globalen Index (P199) | Embedder-Wrapper als monkeypatchbare Funktion, niemals als Modul-Singleton ins Test-Setup ziehen (P199) | Best-Effort-Indexing: jeder Trigger-Punkt toleriert RAG-Fehler (P199) | RAG-Block-Marker `[PROJEKT-RAG — Kontext aus Projektdateien]` ist eindeutig (P199) | PWA-Endpoints (Manifest + SW) MÜSSEN auth-frei sein und in einem separaten Router VOR dem auth-gated Hel-Router via `include_router` eingehängt werden (P200) | Service-Worker-Scope folgt aus dem PFAD der SW-Datei (P200) | Icon-Generierung deterministisch halten (kein RNG, kein Timestamp), damit Re-Runs bytes-identische PNGs erzeugen (P200) | **`persona_overlay` darf NIEMALS in einer User-sichtbaren Response auftauchen — Admin-Geheimnis. User-Endpoints (`/nala/projects` etc.) MÜSSEN das Repo-Response slimmen, sentinel-string-Test verifiziert (P201)** | **Header-Injektion fuer Cross-Cutting-Concerns (Active-Project, Prosody, ...) MUSS zentral in `profileHeaders()` passieren, nicht pro Call-Site — sonst vergisst irgendwann ein neuer Endpoint den Header (P201)** | **Zombie-ID-Schutz: nach jedem List-Refresh prüfen, ob die im localStorage gemerkte Auswahl noch existiert; sonst räumen — verhindert Header an non-existing-IDs (P201)** | **XSS-Helper-Funktion mit Min-Count-Source-Audit-Test fuer alle User-eingegebenen Felder im DOM-Renderer (P201)**
