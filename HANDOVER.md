## HANDOVER.md
> Wird von jeder Coda-Instanz am Session-Ende überschrieben.

**Datum:** 2026-05-02
**Letzter Patch:** P197 — Phase 5a Decision 3: Persona-Merge-Layer aktiviert
**Tests:** 1464 passed (+33), 4 xfailed (pre-existing), 2 pre-existing Failures (unrelated)
**Commit:** 598755e — gepusht zu origin/main
**Repos synchron:** Zerberus / Ratatoskr / Claude ✅ (verify_sync.ps1: 3/3 clean, 0 unpushed)

---

## Zuletzt passiert
P197 ausgeliefert: Decision 3 (Merge System → User → Projekt-Overlay) aktiviert. Die in P194/P195 vorbereitete Persona-Overlay-Pflege (`projects.persona_overlay`) wirkt jetzt im LLM-Call. Aktivierung über Header `X-Active-Project-Id: <int>` am `POST /v1/chat/completions`.

Neuer Helper [`zerberus/core/persona_merge.py`](zerberus/core/persona_merge.py) mit drei Funktionen: `merge_persona(base, overlay, project_slug=None)` als Pure-Function (synchron testbar), `read_active_project_id(headers)` mit Lowercase-Fallback (FastAPI-Headers vs. dict-Case-Sensitivity), `resolve_project_overlay(project_id, *, skip_archived=True)` als async DB-Schicht. Konstanten `ACTIVE_PROJECT_HEADER` und `PROJECT_BLOCK_MARKER` exportiert.

Verdrahtung in `legacy.py::chat_completions`: Reihenfolge jetzt `load_system_prompt` → **`merge_persona`** → `_wrap_persona` (P184) → `append_runtime_info` (P185) → `append_decision_box_hint` (P118a) → Prosodie-Inject (P190) → `messages.insert(0, system)`. Position der Verdrahtung ist kritisch — `merge_persona` MUSS vor `_wrap_persona` laufen, damit der `# AKTIVE PERSONA — VERBINDLICH`-Marker auch das Projekt-Overlay umschließt. Source-Audit-Test verifiziert die Reihenfolge per Substring-Position.

Der Projekt-Block beginnt mit `[PROJEKT-KONTEXT — verbindlich für diese Session]` als eindeutigem Marker (Substring-Check für Tests/Logs + Schutz gegen Doppel-Injection in derselben Pipeline). Optional ist eine `Projekt: <slug>`-Zeile drin, damit das LLM beim Self-Talk korrekt referenziert. `tone_hints` werden case-insensitive dedupliziert (erstes Vorkommen gewinnt, Schreibweise behalten), Whitespace-only/Nicht-Strings gefiltert.

Defensive Behaviors: archivierte Projekte → kein Overlay (Slug wird trotzdem geloggt, damit Bug-Reports diagnostizierbar bleiben); unbekannte ID → kein Crash, einfach kein Overlay; kaputter Header (Buchstaben, negative Zahl, 0) → ignoriert; leerer Overlay (kein `system_addendum`, leere `tone_hints`) → kein Block. Logging-Tag `[PERSONA-197]` zeigt `project_id`/`slug`/`base_len`/`project_block_len`.

33 neue Tests in [`zerberus/tests/test_persona_merge.py`](zerberus/tests/test_persona_merge.py): 12 Pure-Function-Edge-Cases, 7 Header-Reader-Cases, 5 async DB-Cases via `tmp_db`-Fixture, 4 End-to-End über `chat_completions` mit Mock-LLM (verifiziert dass Overlay tatsächlich im finalen System-Prompt landet), 5 Source-Audit-Cases (Log-Marker, Imports, Reihenfolge merge-vor-wrap). Teststand 1431 → **1464 passed**.

**Telegram bewusst ausgeklammert.** Huginn (`zerberus/modules/telegram/bot.py`) hat eigene Persona-Welt (zynischer Rabe, optional Hel-überschrieben) ohne User-Profile und ohne Verbindung zu Nala-Projekten. Project-Awareness in Telegram braucht eigene UX (`/project <slug>`-Befehl oder persistente Bind-Tabelle) — eigener Patch wenn der Bedarf konkret entsteht. Bewusst dokumentiert in CLAUDE_ZERBERUS.md (Sektion "Nala Prompt-Assembly"), lessons.md (Sektion "Persona-Layer-Merge") und PROJEKTDOKUMENTATION.md.

**Phase 5a Decision 3 ist damit abgeschlossen.** Die in P194/P195 vorbereitete Pflege wirkt jetzt im Chat. Nala-Frontend setzt den Header noch nicht — das kommt mit dem Nala-Tab "Projekte" (kein eigener Patch nötig, der Tab setzt beim Wechsel den Header). Manueller Test: per `curl -H "X-Active-Project-Id: 1" -X POST .../v1/chat/completions ...` oder über die Hel-UI verifizieren.

## Nächster Schritt — sofort starten
**P198: Template-Generierung beim Anlegen** — Phase 5a Ziel #2 ("Projekte haben Struktur"). Ein neu angelegtes Projekt soll nicht leer starten, sondern mit einer Mindest-Skelett-Struktur, die das LLM und der User direkt nutzen können.

Konkret (Coda darf abweichen):
1. **Template-Vorlage definieren.** Empfehlung: ein `ZERBERUS_<SLUG>.md` als Projekt-Bibel (analog `ZERBERUS_MARATHON_WORKFLOW.md`) mit Sektionen "Ziel", "Stack", "Offene Entscheidungen", "Dateien", "Letzter Stand". Plus optional eine `README.md` und eine leere Ordnerstruktur (`docs/`, `src/`, `data/`).
2. **Template-Engine.** Pure-Python-String-Templates (kein Jinja, weil Overhead und nicht im Stack). Helper in `zerberus/core/projects_repo.py` (oder neuer `projects_template.py` falls das Repo zu groß wird) — `materialize_template(project_dict, target_dir, *, dry_run=False)` legt die Files an.
3. **Aufruf.** Im `create_project_endpoint` (Hel) NACH dem `create_project`-Repo-Call, mit `await materialize_template(...)`. Idempotenz: existierende Files NICHT überschreiben (User könnte schon Inhalte haben). Optional Feature-Flag `auto_template: bool = True` in `ProjectsConfig`, damit man's für Migrations-Tests abschalten kann.
4. **Optional Git-Init.** Wenn `git` im PATH ist (Healthcheck wie Sandbox in P171): `git init` im Projekt-Storage-Dir + leerer Initial-Commit "P198 Template". Wenn `git` fehlt → skip ohne Fehler. Macht spätere Roll-back-Logik (Ziel #9) trivialer.
5. **Hel-UI-Erweiterung.** Optional: Beim Anlegen eine Checkbox "Template generieren" (Default true). Beim erfolgreichen Anlegen die generierten Files in der Datei-Liste anzeigen.
6. **Tests.** Repo-Test für `materialize_template` (Files entstehen, idempotent, dry_run macht nichts); Endpoint-Test (Create → Files in DB sichtbar); UI-Source-Inspection für Checkbox falls gebaut.

**Reihenfolge-Vorschlag für die nächsten Patches** (Coda darf abweichen):
- **P198** Template-Generierung beim Anlegen (oben beschrieben) — macht Phase 5a Ziel #2
- **P199** Projekt-RAG-Index — Phase 5a Ziel #3 (isolierter FAISS pro Projekt-Slug, indexiert die in P196 hochgeladenen Files)
- **P200** Code-Execution-Pipeline — Ziel #5 (Intent `PROJECT_CODE` → LLM → Sandbox)
- **P201** Nala-Tab "Projekte" + Header-Setter — verdrahtet das Nala-Frontend mit P197 (User wählt aktives Projekt im Chat-UI)

## Vorhandene Bausteine (NICHT neu bauen)
Docker-Sandbox (P171), HitL (P167), Pipeline-Bus (P174/P177), Guard (P120/P180), Prosodie (P189-191), Triptychon (P192), Whisper-Enrichment (P193), **Projekte-Backend (P194)**, **Projekte-UI (P195)**, **Projekt-Datei-Upload (P196)**, **Persona-Merge-Layer (P197: Header + merge_persona + Resolver + Verdrahtung)**.

Helper aus P197 die in P198+ direkt nutzbar sind:
- `persona_merge.merge_persona(base, overlay, slug)` — wenn ein anderer Code-Pfad einen System-Prompt mit Projekt-Overlay anreichern will (z.B. zukünftige Telegram-Verdrahtung), Pure-Function ohne I/O
- `persona_merge.read_active_project_id(headers)` — wenn ein anderer Endpoint auch Projekt-Awareness braucht (z.B. `/v1/audio/transcriptions` für Project-Aware-Whisper-Korrekturen)
- `persona_merge.resolve_project_overlay(project_id, *, skip_archived=True)` — bequeme async-Schnittstelle zur Projekt-Auflösung mit Archivier-Check
- `persona_merge.PROJECT_BLOCK_MARKER` + `ACTIVE_PROJECT_HEADER` — Konstanten, wenn andere Module den Block erkennen müssen (z.B. ein zukünftiger RAG-Filter, der den Projekt-Block aus dem Prompt extrahiert)

Persona-Pipeline-Bausteine (P184/P185/P197):
- `legacy.load_system_prompt(profile_name)` — User-Persona-Resolver (profil-spezifisch → generic → empty)
- `legacy._wrap_persona(prompt)` — AKTIVE-PERSONA-Marker davor
- `legacy._is_profile_specific_prompt(profile_name)` — Trigger für den Wrap
- `runtime_info.append_runtime_info(prompt, settings)` — Live-Modell/Sandbox/RAG-Block
- `prompt_features.append_decision_box_hint(prompt, settings)` — Decision-Box-Syntax-Anweisung

## Offenes
- DECISIONS_PENDING leer — Decision 3 mit P197 abgeschlossen.
- Manuelle Tests in WORKFLOW.md offen: #2-4 (Prosodie/Triptychon, pre-P194), #5-7 (P194), #8-10 (P195), #11-16 (P196), **#17-18 (P197: Header-Test mit curl, Hel-UI Persona-Overlay-Edit-→-LLM-Wirkung)**.
- Schulden in WORKFLOW.md unten: interactions-User-Spalte (Alembic), 2 pre-existing Test-Failures (4 xfailed sichtbar — nicht blockierend).
- Storage-GC-Job für verwaiste SHAs nach Projekt-Delete (P194 `delete_project` löscht aktuell nur DB-Cascade, Bytes können verwaisen wenn der SHA nirgends sonst referenziert ist) — low-prio bis Storage-Volumen relevant.
- **Nala-Frontend setzt `X-Active-Project-Id` noch nicht** — kommt mit dem Nala-Tab "Projekte" (P201-Vorschlag oben). Bis dahin ist P197 nur über externe Clients (`curl`, SillyTavern, eigene Skripte) nutzbar.

## Stack
DeepSeek V3.2 (OpenRouter) | Mistral Small 3 Guard | Gemma E2B Prosodie (lokal) | faster-whisper FP16 (Docker :8002) | SQLite WAL | Nala + Hel | Huginn Telegram

## Invarianten (nie brechen)
/v1/ auth-frei (Dictate-Lane) | Mobile-first 44px | Bibel für LLM-Dateien | Prosa für Menschen | Tests grün vor Commit | Push erst nach `sync_repos.ps1` + `verify_sync.ps1` | Composite-UNIQUE-Constraints IMMER ins Model (`__table_args__`), nicht nur in `init_db` | Slug ist immutable nach Anlage (Rename per Drop+Recreate) | Atomic Write für jeden Upload-Pfad (`tempfile.mkstemp` im Ziel-Ordner + `os.replace`) | SHA-Dedup-Schutz beim Delete von Multi-Owner-Storage (P196) | **Persona-Layer-Reihenfolge: User-Persona vor Projekt-Overlay vor `_wrap_persona`-Marker (P197) — Source-Audit-Test verifiziert** | **Pure-Function vs. DB-Schicht trennen, wenn ein Helper sowohl unit- als auch integration-getestet werden soll**
