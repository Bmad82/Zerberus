# HANDOVER — Phase 5 (Nala-Projekte)

*Übergabedokument für die nächste Supervisor-Session. Wird beim Start von Phase 5 als erstes gelesen.*

---

## Phase 4 Abschluss-Status

- **Letzter Patch:** P193 (Whisper-Endpoint Enrichment)
- **Tests:** 1316 passed, 0 neue Failures (2 pre-existing unrelated zu P192/P193 — `test_rag_dual_switch::test_fallback_logic` SentenceTransformer-Mock-Issue, `test_tts_integration::test_leerer_text_raises_value_error` edge-tts-Modul nicht installiert)
- **Repos:** Zerberus, Ratatoskr, Claude — alle synchron nach P193-Push
- **Git-HEAD bei Übergabe:** `P192-193: Sentiment-Triptychon + Whisper-Enrichment + Phase-4-Abschluss + Doku-Konsolidierung`

### Was in Phase 4 gebaut wurde (P119–P193)

| Bereich | Highlights |
|---------|-----------|
| **Guard** | Mistral Small 3 (P120/P180), `caller_context` + `rag_context` ohne Halluzinations-Risiko, fail-open Default |
| **Huginn (Telegram)** | Long-Polling, Allowlist (P181), Intent-Router via JSON-Header (P164), HitL persistent in SQLite (P167), RAG via system-Kategorie (P178), Sandbox-Hook (P171), Coda-Autonomie kodifiziert (P176) |
| **Nala UI** | Mobile-first Bubbles, HSL-Slider, TTS, Auto-TTS (P186), Katzenpfoten, Feuerwerk-Easter-Egg, Sentiment-Triptychon (P192) |
| **RAG** | Code-Chunker (AST), DualEmbedder DE+EN (P187), FAISS-Migration mit Backup, Soft-Delete, Category-Boost, system-Kategorie als Datenschutz-Schicht |
| **Prosodie** | Gemma 4 E2B (lokal, llama-mtmd-cli, Q4_K_M), Whisper+Gemma parallel via `asyncio.gather` (P190), Consent-UI (P191), Triptychon-Integration (P192) |
| **Pipeline** | Message-Bus + Telegram/Nala/Rosa-Adapter (P173–P175), DI-only `core.pipeline` (P174), Feature-Flag-Cutover (P177) |
| **Security** | Input-Sanitizer mit NFKC (P162/P173), Callback-Spoofing-Schutz (P162), Telegram-Allowlist (P181), Worker-Protection für Audio (P191) |
| **Infrastruktur** | Docker-Sandbox `--network none --read-only` (P171), Pacemaker, pytest-Marker (e2e/guard_live/docker), Coda-Autonomie (P176) |
| **Bugs** | 🪦 Black Bug (4 Anläufe — P109 → P153 → P169 → P183 endgültig), HSL-Parse-Bug, Config-Save-Stale, Terminal-Hygiene (P166) |

### Aktiver Tech-Stack

| Komponente | Modell / Technologie |
|------------|---------------------|
| **Cloud-LLM** | DeepSeek V3.2 (OpenRouter) |
| **Guard** | Mistral Small 3 (`mistralai/mistral-small-24b-instruct-2501`, OpenRouter) |
| **Prosodie** | Gemma 4 E2B (lokal, `llama-mtmd-cli`, Q4_K_M ~3.4 GB) — Modelle unter `C:\Users\chris\models\gemma4-e2b\` |
| **ASR / Whisper** | faster-whisper large-v3 (FP16, Docker Port 8002) |
| **Sentiment (Text)** | `oliverguhr/german-sentiment-bert` (lokal) |
| **Embeddings DE** | `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` (GPU) |
| **Embeddings EN** | `intfloat/multilingual-e5-large` (CPU, optional Index) |
| **Reranker** | `BAAI/bge-reranker-v2-m3` |
| **DB** | SQLite (`bunker_memory.db`, WAL-Modus, Alembic seit P92) |
| **Frontend** | Nala (Mobile-first), Hel (Admin-Dashboard) |
| **Bot** | Huginn (Telegram, Long-Polling, Tailscale-intern) |

### VRAM-Belegung (Modus „Nala aktiv" mit Prosodie)

```
Whisper 4.5 + BERT 0.5 + Gemma E2B 3.0 + DualEmbedder 0.5 + Reranker 1.0 + Windows 0.8
= ~10.3 GB / 12 GB (RTX 3060)
```

---

## Phase 5 — Nala-Projekte

**Vision:** Zerberus wird zur persönlichen Code-Werkstatt. Nala vermittelt zwischen Chris und LLMs/Sandboxes — der User formuliert ein Ziel, Nala fragt nach (Spec-to-Task), schreibt den Code, lässt ihn in der Sandbox laufen, zeigt das Diff, fragt vor jeder Ausführung. Sancho Panza v2 macht Reality-Check.

### Referenz-Dokumente

- [`nala-projekte-features.md`](../nala-projekte-features.md) — 100+ Features, konsolidiert
- [`NALA_PROJEKTE_PRIORISIERUNG.md`](../NALA_PROJEKTE_PRIORISIERUNG.md) — Priorisierung für Zerberus-Kontext
- [`SUPERVISOR_ZERBERUS.md`](../SUPERVISOR_ZERBERUS.md) — strategischer Stand (frisch konsolidiert in P192–P193)
- [`CLAUDE_ZERBERUS.md`](../CLAUDE_ZERBERUS.md) — durable Regeln für Claude Code
- [`lessons.md`](../lessons.md) — projektspezifische Lessons (P186-P193 ergänzt)

### Phase 5a — Grundgerüst (erste 10 Patches)

| # | Feature | Beschreibung |
|---|---------|-------------|
| **P194** | Projekt-DB + Workspace | SQLite-Schema (`projects`-Tabelle), CRUD, Hel-Tab „Projekte". UUID-Hex-IDs analog `hitl_tasks`-Tabelle aus P167. |
| **P195** | Template-Generierung | `ZERBERUS_X.md`-Generator pro Projekt, Ordnerstruktur (`projects/<slug>/{src,docs,tests}`), `git init` automatisch. |
| **P196** | Projekt-RAG-Index | Isolierter FAISS pro Projekt (`projects/<slug>/index.faiss` + `meta.json`). DualEmbedder-Pattern aus P187 wiederverwenden. |
| **P197** | Datei-Upload im Chat | Drag-and-Drop in Nala-Chat, Tag-Indexierung, Auto-Kategorie (Extension-Map aus P111). |
| **P198** | Code-Execution-Pipeline | Neuer Intent `PROJECT_CODE` im Intent-Router (P164) → LLM → Sandbox (P171). |
| **P199** | HitL-Gate für Code | Default-ON für `PROJECT_CODE`-Intent. Inline-Keyboard ✅/❌ (P167-Pattern), Diff-Vorschau im HitL-Prompt. |
| **P200** | Sancho-Panza v2 | Erweiterte Veto-Logik. Wandschlag-Erkennung (LLM macht 3× denselben Fehler → Sancho stoppt mit Ratschlag). |
| **P201** | Spec-to-Task Contract | Erst klären, dann coden. Nala fragt strukturiert nach: Inputs, Outputs, Edge-Cases, Tests. |
| **P202** | Snapshot/Backup-System | `.bak` + Rollback-CLI vor jeder Dateiänderung. Pattern aus P176 (Docker-Sandbox-Cleanup) übertragen. |
| **P203** | Diff-View | User sieht jede Änderung als unified-diff vor Bestätigung. Markdown-Rendering in Nala. |

### Phase 5b — Power-Features (danach)

- Multi-LLM Evaluation (Bench-Mode pro Projekt-Aufgabe — DeepSeek vs. Sonnet vs. lokal)
- Bugfix-Workflow + Test-Agenten Per-Projekt (Loki/Fenrir/Vidar als Templates)
- Multi-Agent Orchestrierung, Debugging-Konsole
- Reasoning-Modi, LLM-Wahl per Aufgabe, Cost-Transparency in Hel
- Agent-Observability (Chain-of-Thought Inspector mit redacted Sensitive-Patterns)

### Abhängigkeiten / Was steht schon

- **Docker-Sandbox (P171)** existiert + Images gezogen (P176) — Code kann sicher laufen
- **HitL-Mechanismus (P167)** existiert, SQLite-persistent, Sweep-Loop läuft
- **Pipeline-Cutover (P177)** existiert, Feature-Flag `modules.pipeline.use_message_bus` bereit für Live-Switch
- **Guard (P120/P180)** funktioniert mit `caller_context` + `rag_context`
- **Prosodie (P189–P191)** funktioniert — kann optional in Projekte integriert werden (z. B. Stimmung als Code-Quality-Indikator: gestresster Programmierer ↔ defensiver Code-Review)
- **Triptychon (P192/P193)** zeigt Sentiment pro Bubble — kann in Projekt-Konsole als Debug-Indikator dienen

### Offene Items / Bekannte Schulden

→ Konsolidiert in [`BACKLOG_ZERBERUS.md`](../BACKLOG_ZERBERUS.md) (seit Patch 179). Strukturelle Schulden:

- **Persona-Hierarchie** (Hel vs. Nala „Mein Ton") → löst sich mit SillyTavern/ChatML-Wrapper (B-071)
- **`interactions`-Tabelle ohne User-Spalte** → Per-User-Metriken erst nach Alembic-Schema-Fix vertrauenswürdig
- **`scripts/verify_sync.ps1`** existiert nicht → `sync_repos.ps1` Output muss manuell geprüft werden
- **`system_prompt_chris.json`** Trailing-Newline-Diff → `git checkout` zum Bereinigen, kein echter Bug
- **Voice-Messages in Telegram-DM** → P182 Unsupported-Media-Handler antwortet höflich, B-072 für echte Whisper-Pipeline
- **Prosodie-Live-Test steht aus** — `llama-mtmd-cli` muss im PATH sein, sonst läuft Pfad A nicht (Fallback auf Stub funktioniert)
- **Pre-existing Test-Failures** (2 Stück, beide unrelated zu P192/P193): `test_rag_dual_switch::test_fallback_logic` (SentenceTransformer-Mock auf falschem Modul), `test_tts_integration::test_leerer_text_raises_value_error` (edge-tts nicht installiert)

---

## Für die nächste Supervisor-Session (Phase 5 Start)

1. **Frische Doku fetchen:** `SUPERVISOR_ZERBERUS.md` von GitHub holen (in P192–P193 auf 180 Zeilen gestrafft, alle Patch-Details vor P186 leben in `docs/PROJEKTDOKUMENTATION.md`)
2. **Diesen Handover lesen** — Tech-Stack, VRAM, offene Items, Phase-5-Roadmap stehen hier vollständig
3. **Phase-5a mit P194** (Projekt-DB + Workspace) starten
4. **Doppel- oder Dreiergruppen-Rhythmus** beibehalten — hat sich in P186-P193 bewährt (3× drei Patches in Folge, je ~60 Tests)
5. **Nicht im selben Patch:** Modell-Downloads + Code + Eval + Server-Restart — das hat P133 nicht überlebt. Lieber Migration als eigener Schritt mit `--dry-run` Default.
6. **Coda-Autonomie nutzen** (P176): docker pull, pip install, curl, Testdaten, Sync — alles Coda. Chris nur für Auth/Hardware/UX-Gefühl.

### Sanity-Checks vor Phase 5 Start

- [ ] `pytest --tb=line -q` läuft → ~1316 passed, 0 neue Failures (2 pre-existing dürfen rot sein)
- [ ] `python -c "from zerberus.utils.sentiment_display import compute_consensus; print(compute_consensus('positive', 0.8, None))"` → `{'emoji': '😊', 'incongruent': False, 'source': 'bert_only'}`
- [ ] `git log --oneline -5` zeigt P192–P193-Commit
- [ ] `sync_repos.ps1` verifiziert keine Drift in Ratatoskr / Claude-Repo

---

*Erstellt 2026-05-01 als Teil von P192–P193 (Phase-4-Abschluss). Wenn dieses Dokument noch existiert und aktuell ist, wird Phase 5 noch nicht begonnen haben.*
