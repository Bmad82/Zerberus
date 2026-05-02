# SUPERVISOR_ZERBERUS.md – Zerberus Pro 4.0
*Strategischer Stand für die Supervisor-Instanz (claude.ai Chat)*
*Letzte Aktualisierung: Patch 196 (2026-05-02) – Phase 5a #4 Datei-Upload-Endpoint + UI*

---

## Aktueller Patch

**Patch 196** — Phase 5a #4: Datei-Upload-Endpoint + UI (2026-05-02)

Erster Schritt für Phase-5a-Ziel #4 ("Dateien kommen ins Projekt"). `POST /hel/admin/projects/{id}/files` (multipart) und `DELETE /hel/admin/projects/{id}/files/{file_id}`. Bytes liegen weiter unter `data/projects/<slug>/<sha[:2]>/<sha>` (P194-Konvention) — kein Doppel-Schreiben, wenn der SHA im Projekt-Slug-Pfad schon existiert. Validierung: Filename-Sanitize (`..`/Backslashes/leere Segmente raus), Extension-Blacklist (`.exe`, `.bat`, `.sh`, ...), 50 MB Default-Limit. Delete-Logik: wenn der `sha256` woanders noch referenziert wird, bleibt die Storage-Datei liegen (Schutz vor versehentlichem Cross-Project-Delete); sonst wird sie atomar entfernt + leere Parent-Ordner aufgeräumt. Atomic Write per `tempfile.mkstemp` + `os.replace` verhindert halbe Dateien nach Server-Kill.

UI-seitig ersetzt eine Drop-Zone in der Detail-Card den P195-Platzhalter ("Upload kommt in P196"). Drag-and-drop UND klickbarer File-Picker (multiple), pro Datei eigene Progress-Zeile per `XMLHttpRequest.upload.progress`-Event (sequenzielle Uploads, damit der Server bei Massen-Drops nicht mit parallelen Streams überrannt wird). Datei-Liste bekommt einen Lösch-Button mit Confirm-Dialog; Hinweistext klärt, dass die Bytes nur entfernt werden, wenn sie nirgends sonst referenziert sind.

- **Schema:** Neue `ProjectsConfig` in `core/config.py` (`data_dir`, `max_upload_bytes`, `blocked_extensions`) — Defaults im Pydantic-Modell, weil `config.yaml` gitignored ist (sonst fehlt der Schutz nach `git clone`). Neue Repo-Helper `count_sha_references()`, `is_extension_blocked()`, `sanitize_relative_path()` in `core/projects_repo.py`.
- **Endpoints:** `_projects_storage_base()` als Indirektion in `hel.py` — Tests können den Storage-Pfad per Monkeypatch umbiegen, ohne die globalen Settings anzufassen. `_store_uploaded_bytes()` schreibt atomar; `_cleanup_storage_path()` räumt Datei + leere Parent-Ordner bis zum `data_dir`-Anker auf (best-effort).
- **Tests:** 49 neue Tests — 17 Upload-Endpoint (`test_projects_files_upload.py`: Happy-Path, Subdir, Dedup, Extension-Block, Path-Traversal, Empty-Filename, Empty-Data, Too-Large, 409-Dup, Delete-Unique, Delete-Shared, Cross-Project-404, Storage-Cleanup), 21 Repo-Helper (`test_projects_repo.py`: Sanitize, Extension-Block, count-sha-references), 11 UI-Source-Inspection (`test_projects_ui.py`: Drop-Zone, Progress, Delete-Button, Drag-and-Drop-Events).
- **Teststand:** 1382 → **1431 passed** (+49). 0 neue Failures, 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts — bekannte Schulden).
- **Phase 5a Ziel #4:** ✅ Datei-Upload geöffnet. Indexierung in projekt-spezifischen RAG (Ziel #3) folgt mit P199.

**Patch 195** — Phase 5a #1: Hel-UI-Tab "Projekte" — schließt Ziel #1 ab (2026-05-02)

UI-Hülle über das P194-Backend. Neuer Tab `📁 Projekte` im Hel-Dashboard zwischen Huginn und Links. Liste, Anlegen-Modal (Form-Overlay statt extra CSS-Lib), Edit/Archive/Unarchive/Delete inline, Datei-Liste read-only (Upload kommt P196). Persona-Overlay-Editor: `system_addendum` (Textarea) + `tone_hints` (Komma-Liste). Mobile-first: 44px Touch-Targets durchgehend, scrollbare Tab-Nav, Form-Overlay mit `flex-start`-Top für kleine Screens. Slug-Override nur beim Anlegen editierbar (Slug ist immutable per Repo-Vertrag P194). Lazy-Load via `activateTab('projects')`.

- **Tests:** 20 Source-Inspection-Tests in [`test_projects_ui.py`](zerberus/tests/test_projects_ui.py) (Pattern wie `test_patch170_hel_kosmetik.py`). Decken Tab-Reihenfolge, Section-Markup, JS-Funktionen (`loadProjects`, `saveProjectForm`, `archive/unarchive/delete`, `loadProjectFiles`), 44px-Touch-Targets, Lazy-Load-Verdrahtung.
- **Teststand:** 1365 → **1382 passed** (+17, da `test_projects_endpoints.py` schon mitgezählt war), 0 neue Failures, 4 xfailed (pre-existing), 2 pre-existing Failures (SentenceTransformer-Mock + edge-tts-Install — nicht blockierend).
- **Phase 5a Ziel #1:** ✅ vollständig (Backend P194 + UI P195). Nächste Patches greifen sukzessive Ziele #2 (Templates), #3 (RAG-Index pro Projekt), #4 (Datei-Upload-Endpoint).

**Patch 194** — Phase 5a #1: Projekte als Entität, Backend-Layer (2026-05-02)

Erster Patch der Phase 5a. Tabellen `projects` + `project_files` in `bunker_memory.db` (Decision 1, 2026-05-01), Repo + Hel-CRUD-Endpoints + 46 neue Tests. UI-Tab folgt in P195. Teststand 1316 → **1365 passed** (+49: 28 Repo + 18 Endpoints + 3 weitere), 0 neue Failures, 4 xfailed (pre-existing).

- **Schema:** `projects(id, slug UNIQUE, name, description, persona_overlay JSON, is_archived, created_at, updated_at)` + `project_files(id, project_id, relative_path, sha256, size_bytes, mime_type, storage_path, uploaded_at, UNIQUE(project_id, relative_path))`. Soft-Delete via `is_archived`. Cascade per Repo (`delete_project` ⇒ `DELETE FROM project_files WHERE project_id = ?`), nicht per FK — Models bleiben dependency-frei (keine ORM-Relations). Persona-Overlay als JSON-TEXT für Decision 3 (Merge-Layer System → User → Projekt).
- **Storage-Konvention:** `data/projects/<slug>/<sha[:2]>/<sha>` — Sha-Prefix als Sub-Verzeichnis, damit kein Hotspot-Ordner entsteht. Bytes liegen NICHT in der DB. Helper `storage_path_for()` + `compute_sha256()` in `projects_repo.py`.
- **Endpoints:** `/hel/admin/projects` (list, create) + `/hel/admin/projects/{id}` (get, patch, delete) + `/admin/projects/{id}/archive` + `/unarchive` + `/files`. Admin-only via Hel `verify_admin` (Basic Auth). Bewusst nicht unter `/v1/` — `/v1/` ist exklusiv Dictate-App-Lane (Hotfix 103a, lessons.md Regel 8).
- **Slug-Generator:** Lower-case, special-chars → `-`, max 64 Zeichen, Kollisions-Suffix `-2`/`-3`/.... `slugify("AI Research v2")` → `"ai-research-v2"`.
- **Migration:** Alembic-Revision `b03fbb0bd5e3` (down_revision `7feab49e6afe`), idempotent via `_has_table()`-Guard. Indexe: `uq_projects_slug`, `idx_projects_is_archived`, `idx_project_files_project`, `idx_project_files_sha`. UNIQUE-Constraint `(project_id, relative_path)` direkt in `op.create_table()` über `sa.UniqueConstraint`.
- **Lesson dokumentiert:** Composite-UNIQUE-Constraints MÜSSEN im Model (`__table_args__`) deklariert werden, nicht nur als raw `CREATE UNIQUE INDEX` in `init_db` — sonst greift der Constraint nicht in Test-Fixtures, die nur `Base.metadata.create_all` aufrufen.

- **P192 — Sentiment-Triptychon UI:** Drei Chips (BERT 📝 + Prosodie 🎙️ + Konsens 🎯) an jeder Chat-Bubble, Sichtbarkeit per Hover/`:active` analog Toolbar-Pattern (P139). Neue Utility [`zerberus/utils/sentiment_display.py`](zerberus/utils/sentiment_display.py) mit `bert_emoji()`, `prosody_emoji()`, `consensus_emoji()`, `compute_consensus()`, `build_sentiment_payload()`. Backend liefert `sentiment: {user, bot}` ADDITIV in `/v1/chat/completions`-Response — OpenAI-Schema bleibt formal kompatibel. Mehrabian-Regel: bei `confidence > 0.5` dominiert die Prosodie, sonst Fallback auf BERT. Inkongruenz 🤔 wenn BERT positiv und Prosodie-Valenz < -0.2. 22 Tests in [`test_sentiment_triptych.py`](zerberus/tests/test_sentiment_triptych.py).
- **P193 — Whisper-Endpoint Prosodie/Sentiment-Enrichment:** `/v1/audio/transcriptions` Response erweitert: `text` bleibt IMMER (Backward-Compat für Dictate / SillyTavern / Generic-Clients), zusätzlich optional `prosody` (P190) + neu `sentiment.bert` + `sentiment.consensus`. `/nala/voice` identisch erweitert + zusätzlich named SSE-Events `event: prosody` und `event: sentiment` über `/nala/events` — Triptychon-Frontend kann sync (JSON) oder async (SSE) konsumieren. Fail-open: BERT-Fehler erzeugt nur Logger-Warnung, Endpoint läuft sauber durch. 16 Tests in [`test_whisper_enrichment.py`](zerberus/tests/test_whisper_enrichment.py). Logging-Tag `[ENRICHMENT-193]`.

### Doku-Konsolidierung (Phase-4-Abschluss)

- `lessons.md` aktualisiert: neue Sektionen Prosodie/Audio (P188-191), RAG/FAISS (P187+), Frontend (P186+P192), Whisper-Enrichment (P193). Veraltete Hinweise auf MiniLM als „aktiver Embedder" durch DualEmbedder-Beschreibung ersetzt (in CLAUDE_ZERBERUS.md).
- `CLAUDE_ZERBERUS.md` aktualisiert: neue Top-Sektionen Sentiment-Triptychon (P192) + Whisper-Endpoint Enrichment (P193).
- `SUPERVISOR_ZERBERUS.md` (diese Datei) auf < 400 Zeilen gestrafft. Patch-Details vor P177 leben jetzt in [`docs/PROJEKTDOKUMENTATION.md`](docs/PROJEKTDOKUMENTATION.md).
- Neues Übergabedokument [`docs/HANDOVER_PHASE_5.md`](docs/HANDOVER_PHASE_5.md) für die nächste Supervisor-Session (Phase 5 / Nala-Projekte).

---

## Phase 4 — ABGESCHLOSSEN ✅ (P119–P193)

Vollständige Patch-Historie in [`docs/PROJEKTDOKUMENTATION.md`](docs/PROJEKTDOKUMENTATION.md). Hier nur die letzten Meilensteine ab P186:

| Patch | Datum | Zusammenfassung |
|-------|-------|-----------------|
| **P196** | 2026-05-02 | Phase 5a #4: Datei-Upload-Endpoint + Drop-Zone-UI (öffnet Ziel #4) — SHA-Dedup-Delete, Extension-Blacklist, atomic write + 49 Tests |
| **P195** | 2026-05-02 | Phase 5a #1: Hel-UI-Tab "Projekte" (schließt Ziel #1 ab) — Liste/Form/Persona-Overlay + 20 Tests |
| **P194** | 2026-05-02 | Phase 5a #1: Projekte als Entität (Backend) — Schema + Repo + Hel-CRUD + 46 Tests |
| P192–P193 | 2026-05-01 | Sentiment-Triptychon + Whisper-Enrichment + Phase-4-Abschluss + Doku-Konsolidierung |
| P189–P191 | 2026-05-01 | Prosodie-Pipeline komplett: Gemma-Client + Pipeline + Consent-UI + Worker-Protection |
| P186–P188 | 2026-05-01 | Auto-TTS + FAISS-Migration (DualEmbedder DE/EN) + Prosodie-Foundation |
| P183–P185 | 2026-05-01 | Black-Bug VIERTER (endgültig) + Persona-Wrap + Runtime-Info-Block |
| P180–P182 | 2026-04-30 | Live-Findings L-178: Guard+RAG-Kontext, Telegram-Allowlist, ADMIN-Plausi, Unsupported-Media |
| P178–P179 | 2026-04-29 | Huginn-RAG-Selbstwissen (system-Kategorie, Default-Whitelist, Backlog-Konsolidierung) |
| P177 | 2026-04-29 | Pipeline-Cutover-Feature-Flag (`use_message_bus`, default false, live-switch) |
| P173–P176 | 2026-04-28 | Phase-E-Skelett (Message-Bus, Adapter, Pipeline, Sandbox-Live, Coda-Autonomie) |
| P119–P172 | 2026-04-09…04-28 | Aufbau Phase 4: RAG-Pipeline, Guard, Sentiment, Memory-Extraction, Telegram-Bot, HitL, Sandbox |

### Phase-4-Bilanz

| Bereich | Highlights |
|---------|-----------|
| **Guard** | Mistral Small 3 (P120/P180), `caller_context` + `rag_context` ohne Halluzinations-Risiko, fail-open |
| **Huginn (Telegram)** | Long-Polling, Allowlist, Intent-Router via JSON-Header, HitL persistent (SQLite), RAG via system-Kategorie, Sandbox-Hook |
| **Nala UI** | Mobile-first Bubbles, HSL-Slider, TTS, Auto-TTS, Katzenpfoten, Feuerwerk, Triptychon |
| **RAG** | Code-Chunker, DualEmbedder (DE GPU + EN CPU), FAISS-Migration, Soft-Delete, Category-Boost |
| **Prosodie** | Gemma 4 E2B (lokal, Q4_K_M), Whisper+Gemma parallel via `asyncio.gather`, Consent-UI, Triptychon |
| **Pipeline** | Message-Bus + Telegram/Nala/Rosa-Adapter, DI-only Pipeline, Feature-Flag-Cutover |
| **Security** | Input-Sanitizer (NFKC + Patterns), Callback-Spoofing-Schutz, Allowlist, Worker-Protection (Audio nicht in DB) |
| **Infrastruktur** | Docker-Sandbox (`--network none --read-only`), Pacemaker, pytest-Marker (e2e/guard_live/docker), Coda-Autonomie |
| **Bugs** | 🪦 Black Bug (4 Anläufe — P183 hat ihn endgültig getötet), HSL-Parse, Config-Save, Terminal-Hygiene |

---

## Phase 5 — Nala-Projekte (Roadmap)

**Referenz-Dokumente:**
- [`nala-projekte-features.md`](nala-projekte-features.md) — 100+ Features, konsolidiert
- [`NALA_PROJEKTE_PRIORISIERUNG.md`](NALA_PROJEKTE_PRIORISIERUNG.md) — Priorisierung für Zerberus-Kontext
- [`docs/HANDOVER_PHASE_5.md`](docs/HANDOVER_PHASE_5.md) — Übergabedokument mit Tech-Stack, VRAM, offenen Items

### Phase 5a — Grundgerüst (erste 10 Patches)

| # | Feature | Beschreibung |
|---|---------|-------------|
| P194 | Projekt-DB (Backend) | SQLite-Schema, Repo, Hel-CRUD-Endpoints ✅ |
| P195 | Hel-UI-Tab Projekte | Liste + Anlegen/Edit/Archive/Delete + Persona-Overlay-Editor ✅ |
| P196 | Datei-Upload-Endpoint + UI | `POST /hel/admin/projects/{id}/files` + Drop-Zone + SHA-Dedup-Delete ✅ |
| P197 | Persona-Merge-Layer | System → User → Projekt-Overlay im LLM-Prompt aktivieren |
| P198 | Template-Generierung | `ZERBERUS_X.md`, Ordnerstruktur, Git-Init |
| P199 | Projekt-RAG-Index | Isolierter FAISS pro Projekt |
| P200 | Code-Execution-Pipeline | Intent `PROJECT_CODE` → LLM → Sandbox |
| P201 | HitL-Gate für Code | Sicherheit vor Ausführung (Default-ON) |
| P202 | Snapshot/Backup-System | `.bak` + Rollback vor jeder Dateiänderung |
| P203 | Diff-View | User sieht jede Änderung vor Bestätigung |

### Phase 5b — Power-Features (danach)

- Multi-LLM Evaluation (Bench-Mode pro Projekt-Aufgabe)
- Bugfix-Workflow + Test-Agenten (Loki/Fenrir/Vidar Per-Projekt)
- Multi-Agent Orchestrierung, Debugging-Konsole
- Reasoning-Modi, LLM-Wahl, Cost-Transparency
- Agent-Observability (Chain-of-Thought Inspector)

### Abhängigkeiten / Was steht schon

- Docker-Sandbox (P171) existiert + Images gezogen (P176)
- HitL-Mechanismus (P167) existiert, SQLite-persistent
- Pipeline-Cutover (P177) existiert, Feature-Flag bereit
- Guard (P120/P180) funktioniert mit RAG- und Persona-Kontext
- Prosodie (P189–P191) funktioniert — kann in Projekt-Kontext integriert werden (z. B. Stimmung als Code-Quality-Indikator)

---

## Architektur-Referenz

### Aktiver Tech-Stack

| Komponente | Modell / Technologie |
|------------|---------------------|
| **Cloud-LLM** | DeepSeek V3.2 (OpenRouter) |
| **Guard** | Mistral Small 3 (OpenRouter, `mistralai/mistral-small-24b-instruct-2501`) |
| **Prosodie** | Gemma 4 E2B (lokal, `llama-mtmd-cli`, Q4_K_M, ~3.4 GB) |
| **ASR / Whisper** | faster-whisper large-v3 (FP16, Docker Port 8002) |
| **Sentiment (Text)** | `oliverguhr/german-sentiment-bert` (lokal) |
| **Embeddings DE** | `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` (GPU) |
| **Embeddings EN** | `intfloat/multilingual-e5-large` (CPU, optional Index) |
| **Reranker** | `BAAI/bge-reranker-v2-m3` |
| **DB** | SQLite (`bunker_memory.db`, WAL-Modus, Alembic-Migrations seit P92) |
| **Frontend** | Nala (Mobile-first), Hel (Admin-Dashboard) |
| **Bot** | Huginn (Telegram, Long-Polling, Tailscale-intern) |

### VRAM-Belegung (Modus „Nala aktiv" mit Prosodie)

```
Whisper 4.5 + BERT 0.5 + Gemma E2B 3.0 + DualEmbedder 0.5 + Reranker 1.0 + Windows 0.8
= ~10.3 GB / 12 GB (RTX 3060)
```

### Repos (alle 3 müssen synchron sein)

- **Zerberus** (Code, lokal): `C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus`
- **Ratatoskr** (Doku-Sync, GitHub): `C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr`
- **Claude** (universelle Lessons, GitHub): `C:\Users\chris\Python\Claude`

---

## Offene Items / Bekannte Schulden

→ Konsolidiert in [`BACKLOG_ZERBERUS.md`](BACKLOG_ZERBERUS.md) (seit Patch 179). Hier nur strukturelle Schulden:

- **Persona-Hierarchie** (Hel vs. Nala „Mein Ton") — löst sich mit SillyTavern/ChatML-Wrapper (B-071)
- **`interactions`-Tabelle ohne User-Spalte** — Per-User-Metriken erst nach Alembic-Schema-Fix vertrauenswürdig
- **`scripts/verify_sync.ps1`** existiert nicht — `sync_repos.ps1` Output muss manuell geprüft werden
- **`system_prompt_chris.json` Trailing-Newline-Diff** — `git checkout` zum Bereinigen, kein echter Bug
- **Voice-Messages in Telegram-DM** funktionieren nicht (P182 Unsupported-Media-Handler antwortet höflich) — B-072 für echte Whisper-Pipeline

## Architektur-Warnungen

- **Rosa Security Layer:** NICHT implementiert — Dateien im Projektordner sind nur Vorbereitung
- **JWT** blockiert externe Clients komplett — `static_api_key` ist der einzige Workaround (Dictate, SillyTavern)
- **/v1/-Endpoints** MÜSSEN auth-frei bleiben (Dictate-Tastatur kann keine Custom-Headers) — Bypass via `_JWT_EXCLUDED_PREFIXES`
- **Chart.js / zoom-plugin / hammer.js** via CDN — bei Air-Gap ist das Metriken-Dashboard tot
- **Prosodie-Audio-Bytes** dürfen NICHT in `interactions`-Tabelle landen (Worker-Protection P191)

---

## Sync-Pflicht (Patch 164+)

`sync_repos.ps1` muss nach jedem `git push` ausgeführt werden — der Patch gilt erst als abgeschlossen, wenn Zerberus, Ratatoskr und Claude-Repo synchron sind. Coda pusht zuverlässig nach Zerberus, vergisst aber den Sync regelmäßig, sodass Ratatoskr und Claude-Repo driften.

Falls Claude Code den Sync nicht selbst ausführen kann (z. B. PowerShell nicht verfügbar oder Skript wirft Fehler), MUSS er das explizit melden — etwa mit „⚠️ sync_repos.ps1 nicht ausgeführt — bitte manuell nachholen". Stillschweigendes Überspringen ist nicht zulässig. Die durable Regel steht in `CLAUDE_ZERBERUS.md` unter „Repo-Sync-Pflicht".

## Langfrist-Vision

- **Phase 5 — Nala-Projekte:** Zerberus wird zur persönlichen Code-Werkstatt, Nala vermittelt zwischen Chris und LLMs/Sandboxes
- **Metric Engine** = kognitives Tagebuch + Frühwarnsystem für Denkmuster-Drift
- **Rosa Corporate Security Layer** = letzter Baustein vor kommerziellem Einsatz
- **Telegram-Bot** als Zero-Friction-Frontend für Dritte (keine Tailscale-Installation nötig)

## Don'ts für Supervisor

- **PROJEKTDOKUMENTATION.md NICHT vollständig laden** (5000+ Zeilen = Kontextverschwendung) — nur gezielt nach Patch-Nummern grep'en
- **Memory-Edits max 500 Zeichen** pro Eintrag
- **Session-ID ≠ User-Trennung** — Metriken pro User erst nach DB-Architektur-Fix vertrauenswürdig
- **Patch-Prompts IMMER als `.md`-Datei** — NIE inline im Chat. Claude Code erhält den Inhalt per Copy-Paste aus der Datei (Patch 101)
- **Dateinamen `CLAUDE_ZERBERUS.md` und `SUPERVISOR_ZERBERUS.md` sind FINAL** — in Patch-Prompts nie mit alten Namen (`CLAUDE.md`, `HYPERVISOR.md`) referenzieren (Patch 100/101)
- **Lokale Pfade:** Ratatoskr liegt unter `C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr\` (nicht `Rosa\Ratatoskr\`), Bmad82/Claude unter `C:\Users\chris\Python\Claude\` (nicht `Rosa\Claude\`). Patch-Prompts mit falschen Pfaden → immer erst verifizieren, nicht raten

---

## Für die nächste Supervisor-Session (Phase 5 Start)

1. `SUPERVISOR_ZERBERUS.md` von GitHub fetchen (frisch konsolidiert in P192–P193)
2. [`docs/HANDOVER_PHASE_5.md`](docs/HANDOVER_PHASE_5.md) lesen — Tech-Stack, VRAM, offene Items, Phase-5-Roadmap stehen dort vollständig
3. Phase-5a mit P194 (Projekt-DB + Workspace) starten — Doppel- oder Dreiergruppen-Rhythmus beibehalten
4. Prosodie-Live-Test steht noch aus — `llama-mtmd-cli` muss im PATH sein, sonst läuft Pfad A nicht
5. Pipeline-Feature-Flag (`use_message_bus`) ist bereit für Live-Switch-Tests, aber Default-OFF bis Chris explizit umstellt
