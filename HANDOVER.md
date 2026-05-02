## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P196 — Phase 5a #4 Datei-Upload-Endpoint + UI (öffnet Ziel #4)
**Tests:** 1431 passed, 4 xfailed (pre-existing), 2 pre-existing Failures (unrelated)
**Commit:** _wird nach Push nachgetragen_
**Repos synchron:** _wird nach `sync_repos.ps1` + `verify_sync.ps1` bestätigt_

---

## Zuletzt passiert
P196 ausgeliefert: Multipart-Upload + Delete für Projekt-Dateien. Endpoints `POST /hel/admin/projects/{id}/files` und `DELETE /hel/admin/projects/{id}/files/{file_id}` in `zerberus/app/routers/hel.py`, neue `ProjectsConfig` in `core/config.py` (`data_dir`, `max_upload_bytes=50MB`, `blocked_extensions`), drei Repo-Helper in `core/projects_repo.py` (`count_sha_references`, `is_extension_blocked`, `sanitize_relative_path`).

Validierung an drei Achsen: Filename-Sanitize (`..` strippen, Backslash → `/`, leading-slash weg, leere Segmente kollabieren), Extension-Blacklist (`.exe`, `.bat`, `.cmd`, `.com`, `.msi`, `.dll`, `.scr`, `.sh`, `.ps1`, `.vbs`, `.jar`), Size-Limit (default 50 MB → 413). Atomic Write per `tempfile.mkstemp(dir=target.parent)` + `os.replace` verhindert halbe Dateien nach Server-Kill. SHA-Dedup-Schutz beim Delete: `count_sha_references(sha, exclude_file_id=...)` entscheidet, ob die Bytes mit weggeräumt werden — wenn ein anderes File denselben SHA referenziert, bleibt die Storage-Datei liegen. Cross-Project-Schutz: Endpoint prüft `file_meta["project_id"] == project_id`, sonst 404.

UI ersetzt den P195-Platzhalter "Upload kommt in P196" durch eine Drop-Zone in der Detail-Card. Drag-and-Drop UND klickbarer File-Picker (`<input type="file" multiple>`), pro Datei eine eigene Progress-Zeile via `XMLHttpRequest.upload.progress`-Event. Sequenzieller Upload (`for + await`, NICHT `Promise.all`) damit der Server bei Massen-Drops nicht überrannt wird und die Progress-Anzeige nachvollziehbar bleibt. Datei-Liste bekommt einen Lösch-Button mit Confirm-Dialog ("Bytes werden entfernt, wenn sie nirgends sonst referenziert sind"). Drop-Zone-Events werden nur einmal verdrahtet (Flag `_projectDropWired`), `data-project-id` auf dem DOM-Knoten verbindet beim Tab-Wechsel die richtige Project-ID.

49 neue Tests verteilt auf drei Dateien — `test_projects_files_upload.py` (17 Endpoint-Tests, `_FakeUpload`-Mock statt FastAPI-`UploadFile`), `test_projects_repo.py` (21 Helper-Tests für Sanitize/Extension/Counter), `test_projects_ui.py` (11 Source-Inspection-Tests für Drop-Zone, XHR-Progress, Drag-and-Drop-Events, Delete-Button). Teststand 1382 → **1431 passed**, keine neuen Failures.

**Phase 5a Ziel #4 (Datei-Upload) ist über Hel jetzt offen.** Nala-Chat-Upload + RAG-Indexierung kommen mit P198/P199.

## Nächster Schritt — sofort starten
**P197: Persona-Merge-Layer aktivieren** — Phase 5a Decision 3 (2026-05-01: Merge System → User → Projekt) ist UI-seitig seit P195 vorbereitet, das Backend speichert `persona_overlay`-JSONs seit P194, aber im LLM-Prompt taucht das noch nicht auf. P197 verdrahtet das.

Konkret:
1. **Aktives Projekt pro Session bestimmen.** Vorschlag: Neues Feld `active_project_id` an `chat_sessions` (oder analoge Tabelle) ODER pro Request via Header `X-Active-Project-Id`. Erste Variante ist persistenter, zweite einfacher. Coda entscheidet — Empfehlung: **Header-basiert für P197**, Persistenz später wenn ein Nala-Tab "Projekt wählen" dazu kommt.
2. **Merge-Helper** in `zerberus/core/persona_merge.py` (neu) oder als Funktion in `projects_repo.py`. Signatur z.B. `build_system_prompt(base_prompt: str, user_persona: dict | None, project_overlay: dict | None) -> str`. Reihenfolge: System-Default zuerst, dann User-Persona-Addendum, zuletzt Projekt-`system_addendum`. `tone_hints` aus beiden Layern kombinieren (Set-Union? Liste? — Liste mit Doppel-Dedupe ist robust).
3. **Aufrufer:** `/v1/chat/completions` (legacy.py, ggf. nala.py) und Telegram-Pfad in `modules/telegram/bot.py`. Header lesen, Projekt holen (`projects_repo.get_project`), Merge-Helper aufrufen, in den System-Prompt einsetzen.
4. **Logging-Tag** `[PERSONA-197]` mit `project_slug` + Längen der drei Layer im Debug-Log, damit man sieht ob der Merge greift.
5. **Tests:** `test_persona_merge.py` mit Edge-Cases — kein Projekt aktiv (nur User-Persona), nur Projekt (kein User-Overlay), beide kombiniert, leere `tone_hints`-Liste, Doppel-Hint zwischen User und Projekt, fehlendes `system_addendum` (nur `tone_hints`). Plus ein End-to-End-Test über `/v1/chat/completions`-Endpoint mit Mock-LLM, der prüft dass der finale System-Prompt die richtigen Strings enthält.

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P197** Persona-Merge-Layer (oben beschrieben) — macht Ziel #1 für den Chat tatsächlich nutzbar
- **P198** Template-Generierung beim Anlegen — Phase 5a Ziel #2 (`ZERBERUS_X.md`, Ordnerstruktur, optional Git-Init)
- **P199** Projekt-RAG-Index — Phase 5a Ziel #3 (isolierter FAISS pro Projekt-Slug, indexiert die in P196 hochgeladenen Files)
- **P200** Code-Execution-Pipeline — Ziel #5 (Intent `PROJECT_CODE` → LLM → Sandbox)

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196: Endpoints + Drop-Zone + SHA-Dedup-Delete)**.

Helper aus P196 die in P197+ direkt nutzbar sind:
- `projects_repo.count_sha_references(sha, exclude_file_id)` — für Cleanup-Jobs / Storage-GC
- `projects_repo.sanitize_relative_path(filename)` — universell für jeden weiteren File-Path-Input
- `projects_repo.is_extension_blocked(filename, blocked)` — case-insensitiver Suffix-Check
- `hel._projects_storage_base()` — Storage-Wurzel-Indirektion (Tests können sie monkeypatchen)
- `hel._store_uploaded_bytes(target, data)` + `hel._cleanup_storage_path(path, base)` — atomic write + leere-Parent-Cleanup
- `ProjectsConfig` in `core/config.py` — Datei-Upload-Limits sind ab jetzt zentral konfigurierbar

Persona-Overlay-Bausteine aus P194/P195:
- `Project.persona_overlay` als JSON-TEXT in der DB → schon parsed im Repo (`_project_to_dict` liefert Dict)
- Hel-UI-Editor (P195) füllt das Feld bereits sauber — `system_addendum` (str) + `tone_hints` (list[str])
- `EMPTY_OVERLAY = {"system_addendum": "", "tone_hints": []}` als sicherer Default

## Offenes
- DECISIONS_PENDING leer — Decision 3 ist mit P197 dran (Merge-Layer aktivieren).
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), **#11-16 (P196: push, Drop-Zone Drag&Drop + Klick, Reject-Pfade .exe/zu-gross, Delete + Storage-Ordner-Check, Cross-Project-Dedup-Sanity, iPhone-Touch)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (4 xfailed sichtbar — nicht blockierend).
- Storage-GC-Job: theoretisch könnten beim harten Projekt-Delete (P194 `delete_project`) Bytes verwaisen, wenn der SHA nirgends sonst referenziert wird — `delete_project` löscht aktuell nur DB-Cascade. Falls relevant, wäre ein eigener GC-Patch sinnvoll (low-prio, weil Storage-Volumen klein bleibt solange Chris keine GBs hochlädt).

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196)
