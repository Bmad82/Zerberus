# SUPERVISOR_ZERBERUS.md – Zerberus Pro 4.0
*Strategischer Stand für die Supervisor-Instanz (claude.ai Chat)*
*Letzte Aktualisierung: Patch 192–193 (2026-05-01) – Sentiment-Triptychon + Whisper-Enrichment + Phase-4-Abschluss*

---

## Aktueller Patch

**Patch 192** + **Patch 193** — Sentiment-Triptychon + Whisper-Enrichment (2026-05-01)

Letztes Doppel-Patch von Phase 4. Teststand 1260 → **1316** (+56 neue Tests, 0 Failures, 2 pre-existing unrelated). Phase 4 ist damit abgeschlossen. Phase 5 (Nala-Projekte) startet ab P194.

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
| P194 | Projekt-DB + Workspace | SQLite-Schema, CRUD, Hel-Tab |
| P195 | Template-Generierung | `ZERBERUS_X.md`, Ordnerstruktur, Git-Init |
| P196 | Projekt-RAG-Index | Isolierter FAISS pro Projekt |
| P197 | Datei-Upload im Chat | Tag-Indexierung, Drag-and-Drop |
| P198 | Code-Execution-Pipeline | Intent `PROJECT_CODE` → LLM → Sandbox |
| P199 | HitL-Gate für Code | Sicherheit vor Ausführung (Default-ON) |
| P200 | Sancho-Panza v2 | Erweiterte Veto-Logik, Wandschlag-Erkennung |
| P201 | Spec-to-Task Contract | Erst klären, dann coden |
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
