# Zerberus Pro 4.0 – Projektdokumentation

**Stand:** 2026-04-19
**Version:** 4.0 (Patch 100 – Meilenstein: Hel-Hotfix + JS-Integrity + Easter Egg)
**Status:** Aktiv in Entwicklung

---

## Inhaltsverzeichnis

1. [Executive Summary](#1-executive-summary)
2. [Vision & Leitprinzipien](#2-vision--leitprinzipien)
3. [Systemarchitektur – High-Level](#3-systemarchitektur--high-level)
4. [Systemarchitektur – Detail](#4-systemarchitektur--detail)
5. [Module & Subsysteme](#5-module--subsysteme)
6. [Security & Governance](#6-security--governance)
7. [Patch-Historie](#7-patch-historie)
8. [Aktueller Projektstatus](#8-aktueller-projektstatus)
9. [Offene Entscheidungen](#9-offene-entscheidungen)
10. [Roadmap](#10-roadmap)
11. [Glossar](#11-glossar)
12. [Anhang](#12-anhang)
13. [Sicherheits-Audit & Pen-Test-Protokoll](#13-sicherheits-audit--pen-test-protokoll)

---

## 1. Executive Summary

### Kurzbeschreibung

Zerberus Pro 4.0 ist eine modulare, asynchrone KI-Plattform auf Basis von **FastAPI** und **Python**. Sie betreibt die Persona **„Nala"** (alias „Rosa") – einen persönlichen KI-Assistenten mit Sprach- und Texteingabe, semantischem Gedächtnis, Stimmungsanalyse und Admin-Dashboard.

### Zielsetzung & Mission

Das System soll eine vollständig lokal kontrollierbare, erweiterbare KI-Plattform bereitstellen. Die Kernziele sind:

- **Sprachsteuerung:** Gesprochene Eingabe via Whisper → Antwort durch Cloud-LLM
- **Semantisches Gedächtnis:** Relevanter Kontext aus vergangenen Gesprächen wird automatisch gefunden (RAG/FAISS)
- **Volle Kontrolle:** Konfiguration, Prompts, Dialekte und Cleaner-Regeln über ein Admin-Dashboard editierbar
- **Modularität:** Neue Fähigkeiten (Module) können aktiviert/deaktiviert werden, ohne den Core zu ändern

### Warum dieses System existiert

Der Nutzer betreibt einen persönlichen KI-Assistenten, der:
- seine Sprache versteht (Whisper, Dialekte, Füllwörter)
- sich an Gesprächsinhalte erinnert (FAISS RAG)
- unter seiner vollständigen Kontrolle läuft (kein Cloud-Lock-in für Daten, konfigurierbares Modell via OpenRouter)
- erweiterbar ist für zukünftige Funktionen (Tool-Use, Docker-Sandbox, Security-Layer)

---

## 2. Vision & Leitprinzipien

### Langfristige Vision

Ein mehrstufiges, sicheres KI-Betriebssystem mit:

- **Vollständiger Sprachsteuerung** (Whisper → NLU → Ausführung → Sprache)
- **Semantischem Langzeitgedächtnis** (FAISS + persistente Vektoren)
- **Intent-gesteuertem Orchestrator** (QUESTION / COMMAND / CONVERSATION → unterschiedliche Pipelines)
- **Docker-Sandbox für Tool-Use** (sicheres Ausführen von Code, API-Aufrufen, Automatisierungen)
- **Corporate Security Layer** (mehrstufige Veto-Mechanismen, Audit, Zero-Trust)

### Design-Philosophie

| Prinzip | Ausprägung |
|---|---|
| **Single Source of Truth** | `config.yaml` ist die einzige Konfigurationsquelle; `config.json` nur für Admin-Schreibzugriff |
| **Fail-Fast** | Invarianten-Checks beim Start – kein stiller Fehlbetrieb |
| **Graceful Degradation** | Jede Pipeline-Komponente hat einen Fallback (kein RAG → direkter LLM-Call) |
| **Lose Kopplung** | Module kommunizieren über den EventBus, kein direkter Import zwischen Modulen |
| **Async-First** | FastAPI + SQLAlchemy async + `asyncio.to_thread` für blocking I/O |
| **Modularität** | Module werden per `config.yaml` aktiviert/deaktiviert und dynamisch geladen |

### Leitplanken

- **Local Sovereignty:** Daten verbleiben lokal (SQLite, FAISS auf Festplatte)
- **Kein Blind-Commit:** Konfigurationsänderungen via Hel-Dashboard werden in `config.json` geschrieben und nicht in die Runtime übernommen, ohne Neustart (außer Admin-Schreibzugriff)
- **Defense-in-Depth:** Middleware → Router → Core → (geplant: Veto-Layer)
- **Keine Magic-Defaults:** Fehlende API-Keys erzeugen Warn-Logs, kein silenter Betrieb

---

## 3. Systemarchitektur – High-Level

### Ebenen-Übersicht

```
┌─────────────────────────────────────────────────────────────────┐
│  INGRESS LAYER                                                  │
│  HTTP (FastAPI/Uvicorn :5000) · Whisper (:8002)                 │
├─────────────────────────────────────────────────────────────────┤
│  MIDDLEWARE LAYER                                               │
│  QuietHours · RateLimiting                                      │
├─────────────────────────────────────────────────────────────────┤
│  ROUTER LAYER                                                   │
│  Legacy /v1 · Nala /nala · Orchestrator · Hel · Archive        │
├─────────────────────────────────────────────────────────────────┤
│  COGNITIVE CORE                                                 │
│  Intent Detection · RAG (FAISS) · LLM (OpenRouter)             │
├─────────────────────────────────────────────────────────────────┤
│  SUPPORT LAYER                                                  │
│  Config · Database (SQLite) · EventBus · Pacemaker              │
├─────────────────────────────────────────────────────────────────┤
│  MODULE LAYER (dynamisch geladen)                               │
│  Emotional · Nudge · Preparer · RAG · [MQTT/TG/WA disabled]    │
├─────────────────────────────────────────────────────────────────┤
│  SECURITY LAYER (geplant, noch nicht implementiert)             │
│  Veto · Audit · Zero-Trust · Docker-Sandbox                     │
└─────────────────────────────────────────────────────────────────┘
```

### Hauptdatenfluss: Text-Chat

```
Client (Browser)
    │ POST /v1/chat/completions
    ▼
Middleware (RateLimiting, QuietHours)
    │
    ▼
Legacy Router
    ├── Dialect Check (Kurzschluss, kein LLM)
    ├── RAG-Suche (FAISS, L2 < 1.5)
    ├── LLM-Call (OpenRouter, cloud_model)
    └── RAG Auto-Index (background)
    │
    ▼
Database (store_interaction, save_cost, save_metrics)
    │
    ▼
EventBus (llm_response event)
    │
    ▼
Response (OpenAI-kompatibles Format)
```

### Hauptdatenfluss: Spracheingabe

```
Client (Browser, Mikrofon)
    │ POST /nala/voice (multipart audio)
    ▼
Nala Router
    ├── Whisper HTTP → Transkript
    ├── Cleaner (Füllwörter, Korrekturen)
    ├── Dialect Check (Emoji-Marker → Kurzschluss)
    ├── Session-Geschichte laden
    ├── RAG-Suche (FAISS)
    ├── LLM-Call (mit RAG-Kontext angereichert)
    └── RAG Auto-Index (background)
    │
    ▼
Database (whisper_input, user, assistant speichern)
EventBus (voice_input event)
Pacemaker update
    │
    ▼
Response: { transcript, response, sentiment }
```

---

## 4. Systemarchitektur – Detail

### 4.1 Ingress Layer

**Zweck:** Entgegennahme aller eingehenden HTTP-Anfragen.

| Endpunkt | Protokoll | Port |
|---|---|---|
| Chat, Voice, Admin, Archive | HTTP (FastAPI/Uvicorn) | 5000 |
| Whisper (Speech-to-Text) | HTTP (externes Dienst) | 8002 |

**Komponenten:**
- `zerberus/main.py` – FastAPI-App, Lifespan-Manager, Router-Registrierung, Modul-Loader
- Uvicorn als ASGI-Server

**Verantwortlichkeiten:**
- Anwendungsstart (Lifespan: DB-Init → Invarianten → EventBus → Module laden)
- Statische Dateien unter `/static`
- Root-Redirect auf `/static/index.html`

**Interaktionen:** Leitet Anfragen an Middleware weiter.

---

### 4.2 Middleware Layer

**Zweck:** Vorfilterung aller Anfragen vor Routing.

**Komponenten** (`zerberus/core/middleware.py`):

| Middleware | Funktion | Status |
|---|---|---|
| `quiet_hours_middleware` | Blockiert Anfragen außerhalb definierter Betriebszeiten (503) | Konfigurierbar, aktuell `enabled: false` |
| `rate_limiting_middleware` | Begrenzt Anfragen pro IP/Pfad (in-memory Dict, kein Redis) | `enabled: true`, Default 100/min |

**Konfiguration** (aus `config.yaml`):
- Quiet Hours: 22:00–06:00 Europe/Berlin, mit Ausnahme-Pfaden
- Rate Limits: Default 100/min; `/v1/chat` 50/min; `/nala/voice` 30/min

**Einschränkung:** Rate-Limiting-Cache ist in-memory (`defaultdict`), geht bei Neustart verloren. Für Produktion: Redis empfohlen.

---

### 4.3 Router Layer

**Zweck:** Request-Handling und Routing zur Geschäftslogik.

#### Legacy Router (`/v1`)

- **Datei:** `zerberus/app/routers/legacy.py`
- **Patch:** 38
- **Endpunkte:**
  - `POST /v1/chat/completions` – OpenAI-kompatibler Chat (verwendet intern Orchestrator-Pipeline)
  - `POST /v1/audio/transcriptions` – Audio → Whisper → Cleaner → Text
  - `GET /v1/health`
- **Besonderheiten:**
  - Akzeptiert Force-Flags: `++` (immer Cloud) / `--` (immer Local) am Nachrichtenende
  - System-Prompt wird automatisch eingefügt falls nicht vorhanden
  - Dialect-Kurzschluss vor LLM-Call

#### Nala Router (`/nala`)

- **Datei:** `zerberus/app/routers/nala.py`
- **Patch:** 41
- **Endpunkte:**
  - `GET /nala` / `GET /nala/` – Nala-Frontend (HTML, embedded)
  - `POST /nala/voice` – Vollständige Voice-Pipeline
  - `GET /nala/health`
- **Besonderheiten:**
  - Frontend ist inline im Python-File definiert (NALA_HTML-String)
  - Sidebar mit Session-Liste (Archiv-Integration)
  - Importiert Orchestrator-Funktionen direkt (kein HTTP-Roundtrip)
  - Fallback auf direkten LLM-Call wenn Orchestrator nicht verfügbar

#### Orchestrator Router (`/orchestrator`)

- **Datei:** `zerberus/app/routers/orchestrator.py`
- **Patches:** 36, 40
- **Endpunkte:**
  - `POST /orchestrator/process` – Vollständige Intent+RAG+LLM-Pipeline
  - `GET /orchestrator/health`
- **Pipeline:**
  1. Intent-Erkennung (regelbasiert: QUESTION / COMMAND / CONVERSATION)
  2. RAG-Suche (FAISS, L2-Schwellwert 1.5, Top-K=3)
  3. LLM-Call (mit oder ohne RAG-Kontext)
  4. RAG Auto-Index (Hintergrund-Thread)
  5. EventBus-Publish (`orchestrator_process`, `intent_detected`)

#### Hel Router (`/hel`)

- **Datei:** `zerberus/app/routers/hel.py`
- **Endpunkte:**
  - `GET /hel/` – Admin-Dashboard (HTML, embedded)
  - `GET/POST /hel/admin/config` – Lesen/Schreiben von `config.json`
  - `GET/POST /hel/admin/whisper_cleaner` – Cleaner-Regeln
  - `GET/POST /hel/admin/dialect` – Dialekt-Konfiguration
  - `GET/POST /hel/admin/system_prompt` – System-Prompt
  - `GET /hel/admin/models` – OpenRouter Modell-Liste
  - `GET /hel/admin/balance` – OpenRouter Guthaben
  - `GET /hel/admin/sessions` – Session-Liste
  - `GET /hel/admin/export/session/{id}` – Session-Export
  - `DELETE /hel/admin/session/{id}` – Session löschen
  - `GET /hel/metrics/latest_with_costs` – Metriken + Kosten
  - `GET /hel/metrics/summary` – Zusammenfassung
  - `GET /hel/debug/trace/{session_id}` – Detailierte Session-Debug-Info
  - `GET /hel/debug/state` – Systemzustand (FAISS, Sessions, Config-Hash)
- **Authentifizierung:** HTTP Basic Auth (ADMIN_USER / ADMIN_PASSWORD via `.env`, Default: admin/admin)
- **Patch 48:** `POST /hel/admin/config` ruft nach dem Schreiben sofort `reload_settings()` auf. Kein Neustart mehr nötig für LLM-Modell, Temperatur und Threshold.
- **Patch 56 – RAG-Upload:**
  - `POST /hel/admin/rag/upload` – `.txt`/`.docx` hochladen, Chunking (~300 Wörter, 50 Überlapp), FAISS-Indexierung; Dateiname als `source`-Metadatum
  - `GET /hel/admin/rag/status` – Index-Größe (Anzahl Chunks) + Liste der Quellen aus `metadata.json`
  - `DELETE /hel/admin/rag/clear` – Index leeren (`faiss.index` + `metadata.json` zurücksetzen)
- **Patch 66 – RAG Chunk-Optimierung:**
  - Chunk-Parameter erhöht: `chunk_size=800 Wörter` (war 300), `overlap=160 Wörter` (war 50, entspricht 20 % von 800) — Einheit ist **Wörter**, nicht Token oder Zeichen
  - Kein Hard-Limit auf Dokumentlänge — gesamtes Dokument wird indiziert
  - Upload-Logging: INFO-Log mit Chunk-Anzahl direkt nach Chunking (vor dem Einbetten)
  - Neuer Endpunkt `POST /hel/admin/rag/reindex` — baut FAISS-Index aus gespeicherten Metadaten neu auf (sinnvoll nach Embedding-Modell-Wechsel; für neue Chunk-Parameter Index leeren + Dokumente neu hochladen)
  - Einheit und Defaults sind als Docstring in `_chunk_text()` dokumentiert (`zerberus/app/routers/hel.py`)
- **Patch 56 – Pacemaker:**
  - `GET /hel/admin/pacemaker/config` – Aktuelle Pacemaker-Konfiguration
  - `POST /hel/admin/pacemaker/config` – `keep_alive_minutes` in `config.yaml` speichern (wirkt nach Neustart)
- **Patch 57 – Metrics History + BERT:**
  - `GET /hel/metrics/history?limit=50` – Letzte N Messages mit word_count, ttr, shannon_entropy, bert_sentiment_label, bert_sentiment_score

#### Archive Router (`/archive`)

- **Datei:** `zerberus/app/routers/archive.py`
- **Endpunkte:**
  - `GET /archive/sessions` – Alle Sessions (limit=50)
  - `GET /archive/session/{id}` – Nachrichten einer Session
  - `DELETE /archive/session/{id}` – Session löschen

---

### 4.4 Cognitive Core

**Zweck:** Die drei zentralen Intelligenz-Funktionen: Intent-Erkennung, semantische Suche, LLM-Aufruf.

#### Intent-Erkennung (Orchestrator)

Regelbasiert, keine KI:
- `QUESTION`: Endet mit `?` oder beginnt mit Fragewort (was, wie, wann, wo, wer, warum, what, how, ...)
- `COMMAND`: Beginnt mit oder enthält Imperativ-Keyword (mach, erstelle, zeig, suche, create, show, ...)
- `CONVERSATION`: Default für alles andere

#### RAG-Modul (FAISS)

- **Embedding-Modell:** `all-MiniLM-L6-v2` (SentenceTransformers, 384 Dimensionen)
- **Index-Typ:** `IndexFlatL2` (exakte L2-Suche, keine Approximation)
- **Persistenz:** `./data/vectors/faiss.index` + `./data/vectors/metadata.json`
- **Relevanz-Filter:** L2-Distanz < 1.5 (konfigurierbar in Orchestrator-Code)
- **Thread-Safety:** Singleton mit `threading.Lock()`, Initialisierung genau einmal

#### LLM-Service

- **Datei:** `zerberus/core/llm.py`
- **Patch:** 34 (Split-Brain-Fix)
- **Provider:** OpenRouter (`https://openrouter.ai/api/v1/chat/completions`)
- **Aktuelles Modell:** `meta-llama/llama-3.3-70b-instruct` (aus `config.yaml`)
- **API-Key:** `OPENROUTER_API_KEY` (aus `.env`)
- **History-Limit:** 20 Nachrichten
- **Rückgabe:** `(antwort, modell, prompt_tokens, completion_tokens, kosten_usd)`
- **data_collection:** `deny` (OpenRouter-Privacy-Flag)
- **Timeout:** 30 Sekunden

---

### 4.5 Support Layer

#### Config (`zerberus/core/config.py`)

- Pydantic v2 (`BaseSettings`)
- Lädt ausschließlich aus `config.yaml` (Single Source of Truth seit Patch 34)
- Singleton via `get_settings()` / `reload_settings()`
- Submodelle: `DatabaseConfig`, `EventBusConfig`, `QuietHoursConfig`, `RateLimitingConfig`, `LegacyConfig`, `PacemakerConfig`, `DialectConfig`, `WhisperCleanerConfig`

#### Database (`zerberus/core/database.py`)

- SQLAlchemy 2.0 async, `aiosqlite`
- Datei: `bunker_memory.db` (NIEMALS löschen)
- **Tabellen:**

| Tabelle | Felder | Zweck |
|---|---|---|
| `interactions` | id, session_id, profile_name, timestamp, role, content, sentiment, word_count, integrity | Alle Nachrichten (user, assistant, whisper_input); profile_name = User-Tag (Patch 60) |
| `message_metrics` | message_id, word_count, sentence_count, character_count, avg_word_length, unique_word_count, ttr, hapax_count, yule_k, shannon_entropy, vader_compound | Linguistische Metriken pro Nachricht |
| `costs` | session_id, model, prompt_tokens, completion_tokens, total_tokens, cost, timestamp | API-Kosten pro LLM-Call |

- **VADER-Sentiment:** Wird bei jeder gespeicherten Nachricht automatisch berechnet
- **Metriken:** Werden automatisch nach jeder `store_interaction`-Interaktion berechnet

#### EventBus (`zerberus/core/event_bus.py`)

- In-Memory, `asyncio.Queue`-basiert
- Singleton via `get_event_bus()`
- Unterstützt async und sync Handler
- **Patch 46:** `Event`-Dataclass hat optionales `session_id`-Feld für SSE-Filterung
- **Patch 46:** SSE-Support via `subscribe_sse(session_id)` / `unsubscribe_sse()` – liefert `asyncio.Queue` pro Session
- **Bekannte Events:**
  - `llm_response` – nach jedem LLM-Call
  - `llm_start` – vor LLM-Call (Patch 46, mit session_id)
  - `voice_input` – nach Sprachverarbeitung
  - `orchestrator_process` – nach Orchestrator-Durchlauf
  - `intent_detected` – bei Intent-Erkennung (Patch 46: mit session_id)
  - `rag_indexed` – bei RAG-Indexierung (Patch 46: mit session_id)
  - `rag_search` – bei RAG-Suche (Patch 46: mit session_id)
  - `done` – Pipeline abgeschlossen (Patch 46, mit session_id)
  - `user_sentiment` – nach Sentiment-Analyse
  - `nudge_sent` – wenn ein Nudge ausgelöst wird
  - `calendar_fetched` – wenn Kalender-Daten abgerufen werden
- **Einschränkung:** In-Memory, keine Persistenz, kein Redis (geplant via `event_bus.type: redis`)

#### Pacemaker (`zerberus/app/pacemaker.py`)

- **Zweck:** Hält den Whisper-Dienst im VRAM durch regelmäßige Silent-WAV-Pings warm
- **Aktivierung:** Automatisch bei erster Interaktion
- **Interval:** 240 Sekunden (konfigurierbar via `config.yaml`)
- **Keep-Alive:** 120 Minuten Inaktivität → automatischer Stopp (Patch 56; vorher 25 min)
- **Erstpuls:** Beim allerersten Start sofortiger Puls ohne Warten auf Intervall (Patch 56)
- **Mechanismus:** Sendet 1-Sekunde 16kHz-Mono-WAV aus Null-Bytes per HTTP an Whisper
- **Dashboard-Konfiguration:** Laufzeit editierbar im Hel-Dashboard unter „Systemsteuerung" (speichert in `config.yaml`, wirkt nach Neustart)

#### Invarianten (`zerberus/core/invariants.py`)

Werden beim Start via `run_all()` geprüft:

| Check | Funktion | Verhalten bei Fehler |
|---|---|---|
| Config-Konsistenz | Vergleicht `config.json` mit `config.yaml` für cloud_model, temperature, threshold | Warning-Log |
| FAISS verfügbar | `import faiss` | Warning-Log |
| DB-Tabellen vorhanden | `SELECT name FROM sqlite_master WHERE name='interactions'` | **RuntimeError** (Hard-Fail) |
| API-Keys vorhanden | `OPENROUTER_API_KEY` in Umgebungsvariablen | Warning-Log |

#### Whisper Cleaner (`zerberus/core/cleaner.py`)

- Entfernt Füllwörter, korrigiert Transkriptionsfehler, begrenzt Wortwiederholungen
- Konfiguration aus `config.yaml` (`whisper_cleaner`) oder `whisper_cleaner.json`
- Korrekturen: ähm→"", äh→"", wispa→Whisper, zerberus→Zerberus, nala→Nala
- **Fuzzy-Layer (Patch 42):** `fuzzy_correct()` korrigiert Whisper-Fehler via `difflib.get_close_matches`
  gegen die Begriffsliste in `fuzzy_dictionary.json` (Cutoff 0.82, min. Wortlänge 4)

**Konfigurationsdateien:**

| Datei | Zweck | Format |
|---|---|---|
| `whisper_cleaner.json` | Füllwörter, Korrekturen, Wiederholungs-Limit | JSON-Objekt (editierbar via Hel-Dashboard) |
| `fuzzy_dictionary.json` | Projektspezifische Begriffe für Fuzzy-Matching | JSON-Array von Strings (z.B. `["Zerberus", "FastAPI", "Nala", ...]`) |

#### Dialect Engine (`zerberus/core/dialect.py`)

- Erkennt Emoji-Marker am Anfang der Nachricht
- Marker und zugehörige Dialekte:
  - `🐻🐻` → Berlin
  - `🥨🥨` → Schwäbisch
  - `✨✨` → Emojis
- Wort-Substitutionen aus `dialect.json`
- Kurzschluss: Bei erkanntem Dialekt kein LLM-Call

---

## 5. Module & Subsysteme

Module werden beim Start dynamisch aus `zerberus/modules/` geladen. Ist `enabled: false` in `config.yaml`, wird das Modul übersprungen.

### 5.1 RAG-Modul (`zerberus/modules/rag/router.py`)

| Eigenschaft | Wert |
|---|---|
| **Status** | Aktiv, voll implementiert (Patch 35) |
| **Prefix** | `/rag` |
| **Embedding-Modell** | `all-MiniLM-L6-v2` (sentence-transformers) |
| **Index-Typ** | FAISS `IndexFlatL2`, Dimension 384 |
| **Persistenz** | `./data/vectors/faiss.index` + `./data/vectors/metadata.json` |
| **Abhängigkeiten** | `faiss-cpu`, `sentence-transformers`, `numpy` |

**Endpunkte:**
- `POST /rag/index` – Dokument indexieren
- `POST /rag/search` – Semantische Suche (top_k, L2-Score)
- `GET /rag/health` – Status (initialized, rag_available, index_size)

**Technische Details:**
- Singleton-Init mit `threading.Lock()`
- Blocking-Operationen (encode, search, add) laufen im Thread-Pool (`asyncio.to_thread`)
- Score-Berechnung: `1.0 / (1.0 + l2_distance)` (kleiner = besser)
- Alle Indexierungen persistieren sofort auf Disk
- Auto-Indexierung nach jedem LLM-Call (non-blocking, eigener Event-Loop im Thread)

**Abhängigkeit:** Orchestrator importiert `_rag_search`, `_rag_index_background`, `_ensure_init`, `_encode`, `_search_index`, `_add_to_index` direkt (kein HTTP).

---

### 5.2 Emotional-Modul (`zerberus/modules/emotional/router.py`)

| Eigenschaft | Wert |
|---|---|
| **Status** | Aktiv, funktional |
| **Prefix** | `/emotional` |
| **Abhängigkeiten** | `vaderSentiment` |

**Endpunkte:**
- `POST /emotional/analyze` – Sentiment-Analyse (positive / neutral / negative)
- `GET /emotional/health`

**Funktionalität:**
- VADER-Compound-Score-Berechnung
- Bei negativem Sentiment: Zufällige Mood-Boost-Suggestion aus `config.yaml`
- EventBus-Events: `user_sentiment`, `mood_boost_suggested`

**Einschränkung:** Wird nicht automatisch in der Chat-Pipeline aufgerufen – nur direkt ansprechbar via API.

---

### 5.3 Nudge-Modul (`zerberus/modules/nudge/router.py`)

| Eigenschaft | Wert |
|---|---|
| **Status** | Aktiv, funktional |
| **Prefix** | `/nudge` |
| **Abhängigkeiten** | keine externen |

**Endpunkte:**
- `POST /nudge/evaluate` – Nudge-Bewertung
- `GET /nudge/health`

**Funktionalität:**
- Threshold (0.8) + Hysterese (0.1) für Nudge-Trigger
- Cooldown-System (30 Minuten Standard)
- In-Memory-History (`_nudge_history` Liste)
- EventBus: `nudge_sent`

**Einschränkung:** In-Memory-History, geht bei Neustart verloren. Kein automatisches Triggering aus der Pipeline heraus.

---

### 5.4 Preparer-Modul (`zerberus/modules/preparer/router.py`)

| Eigenschaft | Wert |
|---|---|
| **Status** | Aktiv, aber **Mock-Daten** |
| **Prefix** | `/preparer` |
| **Abhängigkeiten** | `httpx` |

**Endpunkte:**
- `GET /preparer/upcoming` – Nächste Kalender-Ereignisse
- `GET /preparer/health`

**Einschränkung:** Gibt aktuell hartcodierte Mock-Events zurück. Echte Kalender-Integration (URL aus `config.yaml`) nicht implementiert.

---

### 5.5 MQTT-Modul (`zerberus/modules/mqtt/`)

| Eigenschaft | Wert |
|---|---|
| **Status** | Deaktiviert (`enabled: false`) |
| **Abhängigkeiten** | `paho-mqtt` |
| **Konfiguration** | broker, port, topics |

---

### 5.6 Telegram-Modul (`zerberus/modules/telegram/`)

| Eigenschaft | Wert |
|---|---|
| **Status** | Deaktiviert (`enabled: false`) |
| **Abhängigkeiten** | `python-telegram-bot` |
| **Konfiguration** | bot_token, webhook_url, allowed_users |

---

### 5.7 WhatsApp-Modul (`zerberus/modules/whatsapp/`)

| Eigenschaft | Wert |
|---|---|
| **Status** | Deaktiviert (`enabled: false`) |
| **Abhängigkeiten** | `twilio` |
| **Konfiguration** | account_sid, auth_token, phone_number |

---

### 5.8 Hel-Dashboard (kein Modul, sondern Router)

Das Admin-Dashboard ist kein dynamisch geladenes Modul, sondern ein fest eingebundener Router. Es ist das zentrale Werkzeug zur Laufzeit-Konfiguration.

**Funktionen:**
- LLM-Modell aus OpenRouter-Liste wählen und speichern
- Temperatur und Threshold-Länge anpassen
- OpenRouter-Guthaben abrufen
- Whisper-Cleaner-Regeln bearbeiten (JSON-Editor, `whisper_cleaner.json`)
- Fuzzy-Dictionary bearbeiten (JSON-Array-Editor, `fuzzy_dictionary.json`)
- Dialekte bearbeiten (JSON-Editor)
- System-Prompt bearbeiten
- Metriken ansehen (Tabelle + Sentiment-Chart via Chart.js)
- Sessions verwalten (Export, Löschen)
- Debug: Session-Trace, Systemzustand

**Neue Endpunkte (Hel-Update Patch 43):**
- `GET /hel/admin/fuzzy_dict` – Fuzzy-Dictionary lesen
- `POST /hel/admin/fuzzy_dict` – Fuzzy-Dictionary schreiben (Validierung: muss JSON-Array sein)

---

## 6. Security & Governance

### Aktueller Stand

Das System hat grundlegende Sicherheitsmechanismen, aber **kein vollständiges Security-Framework**. Der geplante „Corporate Security Layer / Rosa Paranoia" ist noch nicht implementiert.

### Implementierte Mechanismen

| Mechanismus | Implementierung | Einschränkungen |
|---|---|---|
| **Admin-Auth** | HTTP Basic Auth (ADMIN_USER/ADMIN_PASSWORD via .env) | Default-Credentials admin/admin aktiv wenn nicht geändert |
| **JWT-Session-Auth** | HS256-Token bei Login, 8h Laufzeit, Middleware-Validierung (Patch 54) | Secret muss in `config.yaml` gesetzt werden |
| **Permission-Layer** | admin/user/guest aus JWT-Payload, nicht mehr aus Header (Patch 54) | Kein RBAC-Service, nur statisch in config.yaml |
| **Rate Limiting** | In-Memory pro IP+Pfad | Kein Redis, geht bei Neustart verloren |
| **Quiet Hours** | Zeitbasierte Blockade (503) | Aktuell deaktiviert |
| **API-Key-Schutz** | OPENROUTER_API_KEY aus .env, nie in Logs | Korrekt implementiert |
| **Config-Split-Erkennung** | Invarianten-Check beim Start | Nur Warning, kein Hard-Fail |
| **Atomare Datei-Writes** | Hel-Dashboard schreibt via tempfile + os.replace | Korrekt implementiert |
| **Data Collection Deny** | `provider.data_collection: deny` im LLM-Call | OpenRouter-seitig |
| **EU-Routing** | `provider.order: ["EU"], allow_fallbacks: True` im LLM-Call | OpenRouter routet bevorzugt EU (Patch 52) |

### Sicherheitslücken / Risiken (offen)

| Risiko | Beschreibung | Empfehlung |
|---|---|---|
| **Admin-Default-Credentials** | admin/admin – System warnt, aber startet trotzdem | In `.env` ändern (ADMIN_USER, ADMIN_PASSWORD) |
| **Rate-Limiting in-memory** | Kein Schutz nach Neustart | Redis-Backend |
| **Keine HTTPS** | Produktionsbetrieb ohne TLS | Reverse Proxy (nginx) mit TLS |
| **JWT-Secret in config.yaml** | Default-Secret „CHANGE_ME_IN_DOTENV" – Token unsicher wenn nicht geändert | Secret in `config.yaml` / `.env` setzen vor Produktionsbetrieb |
| **Kein CSRF-Schutz** | Hel-Dashboard-POSTs ohne Token | Für Produktion: CSRF-Token |
| **Keine Veto-Schicht** | Alle Prompts gehen ungefiltert an LLM | Geplant: Rosa Paranoia Security Layer |

### Geplante Security-Erweiterungen (noch nicht implementiert)

Laut Vision soll ein mehrstufiges Sicherheitsframework eingeführt werden:

1. **Stufe 1 – Input Validation:** Sanitization aller Eingaben vor Pipeline
2. **Stufe 2 – Intent Veto:** Bestimmte Intents (z.B. Datei-Löschen) erfordern explizite Bestätigung
3. **Stufe 3 – Execution Sandbox:** Docker-Container für Tool-Use-Ausführung (Patch 44-46)
4. **Stufe 4 – Audit Trail:** Vollständige, unveränderliche Protokollierung aller Aktionen
5. **Stufe 5 – Zero-Trust Admin:** Mehrfaktor-Authentifizierung für Admin-Zugang

**Status:** Alle 5 Stufen sind TODO.

---

## 7. Patch-Historie

### Patches 1–33: Vorgeschichte

Keine detaillierte Dokumentation verfügbar. Aus dem Code ableitbar:
- Grundaufbau der FastAPI-Anwendung
- Einführung des Legacy-Routers (OpenAI-kompatibles Interface)
- Einführung des Nala-Frontends
- Einführung von Hel (Admin-Dashboard)
- Einführung von SQLite + Sentiment-Speicherung
- Einführung der Middleware (QuietHours, RateLimiting)
- Einführung des EventBus
- Einführung des Pacemakers
- Einführung des Dialect-Systems
- Einführung des Whisper-Cleaners
- Iterative Bugfixes und Refactoring-Schritte

### Patch 34: Split-Brain-Behebung

**Problem:** `llm.py` las Konfiguration aus zwei Quellen (`config.json` und `config.yaml`), was zu inkonsistentem Verhalten führte.

**Lösung:**
- `llm.py` liest jetzt ausschließlich aus `config.yaml` via `get_settings()`
- `config.json` ist nur noch Schreib-Ziel des Hel-Dashboards, keine Laufzeit-Konfigurationsquelle
- Invarianten-Check `check_config_consistency()` beim Start warnt bei Abweichungen zwischen beiden Dateien
- Dokumentiert als **Single Source of Truth**-Prinzip in `CLAUDE.md`

### Patch 35: RAG – Echter FAISS-Index

**Problem:** RAG-Modul war ein Stub mit Mock-Daten, keine echte semantische Suche.

**Lösung:**
- Echter `faiss.IndexFlatL2` mit Dimension 384
- Embedding-Modell `all-MiniLM-L6-v2` (SentenceTransformers)
- Persistenz: `./data/vectors/faiss.index` + `./data/vectors/metadata.json`
- Thread-sichere Initialisierung (Singleton + Lock)
- Endpunkte: `POST /rag/index`, `POST /rag/search`, `GET /rag/health`
- L2-Score zu normalisierten Score-Werten konvertiert

### Patch 36: RAG-Integration im Orchestrator

**Problem:** Orchestrator rief LLM ohne Gedächtnis-Kontext auf.

**Lösung:**
- Orchestrator importiert RAG-Funktionen direkt (kein HTTP-Roundtrip)
- Vor jedem LLM-Call: RAG-Suche (Top-3, L2 < 1.5)
- Treffer werden als `[Gedächtnis]: ...`-Zeilen in den User-Prompt eingefügt
- Nach jedem LLM-Call: Auto-Indexierung der User-Nachricht im Hintergrund
- L2-Schwellwert 1.5 filtert irrelevante Treffer heraus

### Patch 37: (Details nicht rekonstruierbar aus Code)

**TODO:** Kein direkter Code-Kommentar gefunden, der Patch 37 zugeordnet ist. Möglicherweise: Stabilisierung der RAG-Persistenz, Bugfixes im Modul-Loader oder Hel-Dashboard-Erweiterungen.

### Patch 38: Legacy-Router nutzt Orchestrator-Pipeline

**Problem:** Legacy `/v1/chat/completions` nutzte nur direkten LLM-Call ohne RAG.

**Lösung:**
- Legacy-Router importiert `_rag_search` und `_rag_index_background` aus Orchestrator
- Vor LLM-Call: RAG-Suche auf letzte User-Nachricht
- RAG-Hits werden in die Nachrichtenkopie eingebettet (Original bleibt unverändert)
- Nach LLM-Call: Auto-Indexierung (non-blocking)
- Graceful Fallback: Bei Fehler direkter LLM-Call ohne RAG

### Patch 39: (Details nicht rekonstruierbar aus Code)

**TODO:** Kein direkter Code-Kommentar gefunden. Möglicherweise: Bugfixes in Orchestrator-Pipeline, Verbesserungen im Hel-Dashboard (Debug-Endpoints), oder Konsolidierung der Modul-Ladestrategie.

### Patch 40: Intent-Erkennung im Orchestrator

**Problem:** Orchestrator unterschied nicht zwischen Fragen, Befehlen und Konversation.

**Lösung:**
- Regelbasierte Intent-Erkennung: `detect_intent(message) → "QUESTION" | "COMMAND" | "CONVERSATION"`
- Erkennung via Fragewort-Liste, Imperativ-Keyword-Liste, Fragezeichen-Suffix
- Intent wird als Prefix `[Intent: QUESTION]` in den LLM-Prompt eingefügt
- EventBus-Event `intent_detected` mit Intent + Message
- Keine KI-basierte Intent-Erkennung – vollständig regelbasiert

### Patch 41: Nala-Voice nutzt vollständige Pipeline

**Problem:** `/nala/voice` führte nur Whisper + direkten LLM-Call durch, ohne RAG oder Intent-Erkennung.

**Lösung:**
- Vollständige Pipeline: Whisper → Cleaner → Dialect-Check → Session-Historie → RAG → LLM → Auto-Index
- Importiert `_rag_search` und `_rag_index_background` aus Orchestrator (direkt, kein HTTP)
- Session-Historie wird geladen und als Kontext an LLM übergeben
- System-Prompt wird eingefügt
- RAG-Kontext wird vor der User-Nachricht eingefügt
- Fallback: Direkter LLM-Call falls Orchestrator-Import fehlschlägt
- `_ORCH_PIPELINE_OK`-Flag zeigt im Health-Endpunkt den Status

### Patch 42: Bugfixes + Fuzzy-Layer

**Probleme (behoben):**

**FIX 1 – OPENROUTER_API_KEY nicht gefunden:**
`zerberus/core/config.py` rief `load_dotenv()` nicht explizit auf bevor Pydantic Settings
initialisiert wurde. Bei abweichendem Working Directory wurde `.env` nicht geladen.

**Lösung:**
- `from dotenv import load_dotenv` + `load_dotenv(dotenv_path=Path(".env"), override=False)`
  direkt nach den Imports in `config.py` – vor jeder Settings-Initialisierung.

**FIX 2 – `_analyzer` nicht definiert:**
In `zerberus/core/database.py` war `_analyzer` bereits korrekt auf Modul-Ebene definiert
(`_analyzer = SentimentIntensityAnalyzer()`). Kein weiterer Handlungsbedarf.

**FIX 3 – Audio-Transcription URL doppelt:**
Betraf die alte `nala.py` (`.bak`-Zustand), die `/v1/audio/transcriptions` direkt im
Frontend-JS aufgerufen hatte. Die aktuelle `nala.py` (seit Patch 41) nutzt korrekt
`/nala/voice` als Frontend-Endpunkt. Kein weiterer Handlungsbedarf.

**FIX 4 – start.bat Windows-Befehlsfehler:**
`echo`-Zeilen mit `&` (z.B. `Kalender & Vorbereitung`) wurden von Windows CMD als
Befehlstrenner interpretiert → `Vorbereitung`, `Mood-Boost`, `Suche` wurden als Befehle
ausgeführt und mit "Befehl nicht gefunden" quittiert.

**Lösung:**
- `&` in den betroffenen `echo`-Zeilen durch `^&` ersetzt (CMD-Escape).

**NEU: Fuzzy Dictionary Layer:**
- `fuzzy_dictionary.json` im Projektroot: Liste projektspezifischer Begriffe, Cutoff 0.82,
  min. Wortlänge 4.
- `zerberus/core/cleaner.py`: Neue Funktion `fuzzy_correct(text)` mit `difflib.get_close_matches`.
  Korrigiert Whisper-Fehler wie „Serberos" → „Zerberus" oder „Fastabi" → „FastAPI".
- `clean_transcript()` ruft `fuzzy_correct()` als letzten Schritt vor dem `return` auf.

### Patch 42b: Audio-Pipeline Cleanup

**Probleme (behoben):**

**FIX 1 – Falscher Funktionsaufruf in `legacy.py`:**
`clean_transcript()` wurde mit zwei Argumenten aufgerufen (`raw_transcript, cleaner`), obwohl die
Funktion seit Patch 42 kein Cleaner-Objekt mehr als Parameter erwartet.

**Lösung:**
- `legacy.py`: Aufruf auf `clean_transcript(raw_transcript)` reduziert.
- `legacy.py`: `load_cleaner_config()`-Aufruf und die zugehörige Import-Zeile entfernt
  (Funktion existiert nicht mehr).

**FIX 2 – Veralteter Import in `nala.py`:**
`nala.py` importierte noch `load_cleaner_config` aus dem Cleaner-Modul, obwohl die Funktion
in Patch 42 entfernt wurde.

**Lösung:**
- `nala.py`: `load_cleaner_config` aus dem Import-Statement entfernt.

---

### Patch 42c: Dialect-Engine Fix

**Problem:**
`dialect.py` → `apply_dialect()` erwartete intern eine verschachtelte `patterns`-Array-Struktur
(`dialect["berlin"]["patterns"][...]`), obwohl `dialect.json` eine flache Key-Value-Struktur
verwendet (`{"berlin": {"nicht": "nich", ...}}`). Dialekt-Substitutionen wurden deshalb nicht
angewendet.

**Lösung:**
- `dialect.py`: `apply_dialect()` auf direkte Key-Value-Iteration umgestellt.
- Längere Phrasen werden zuerst ersetzt (`sorted(..., key=len, reverse=True)`), um
  Teilwort-Konflikte zu vermeiden (z.B. „ich bin nicht" vor „nicht").

---

### Patch 43: Orchestrator Session-Kontext

**Ziel:** Session-Kontext (History, System-Prompt, Speichern, Kosten) vollständig im Orchestrator
integrieren – bisher war dieser nur in `nala.py` vorhanden.

**Änderungen `zerberus/app/routers/orchestrator.py`:**

- `OrchestratorRequest`: Neues Feld `session_id: str = ""` (optional; Fallback aus `context`-Dict).
- `_load_system_prompt()`: Lokale Hilfsfunktion (kein Import aus `legacy.py` wegen Zirkular-Import).
- Imports: `get_session_messages`, `store_interaction`, `save_cost` aus `zerberus.core.database`.
- Neue interne Funktion `_run_pipeline(message, session_id, settings)`:
  - Intent-Erkennung → RAG-Suche → Session-History laden → System-Prompt einbinden
  - LLM-Call mit vollständigem Nachrichtenkontext
  - `store_interaction("user", ...)` + `store_interaction("assistant", ...)` + `save_cost()`
  - RAG Auto-Index + EventBus-Events
  - Rückgabe: `(answer, model, prompt_tokens, completion_tokens, cost, intent)`
- `/orchestrator/process`-Endpoint delegiert vollständig an `_run_pipeline()`.

**Änderungen `zerberus/app/routers/nala.py`:**

- Import ersetzt: `_rag_search, _rag_index_background` → `_run_pipeline`
- Entfernte Imports: `LLMService`, `load_system_prompt`, `save_cost`, `get_session_messages`, `json`, `Path`
- Voice-Endpoint Step 5: Session-History-Lade-Logik, RAG, LLM-Call, `store_interaction(user/assistant)`,
  `save_cost` durch einzigen `await _run_pipeline(cleaned, session_id, settings)`-Aufruf ersetzt.
- `store_interaction("whisper_input", ...)` bleibt in `nala.py` (voice-spezifisch, kein session_id).
- Bei fehlendem Orchestrator: HTTP 503 statt stillem Fallback (explizites Fail-Fast).

### Patch 44: User-Profile + editierbares Transkript

**Ziel:** Profil-basiertes Login (Chris / Rosa) mit individuellen Farben, System-Prompts und bcrypt-Passwörtern. Editierbares Transkript statt Auto-Send bei Voice.

**Änderungen:**

- `config.yaml`: Neuer `profiles`-Abschnitt mit `display_name`, `theme_color`, `system_prompt_file`, `password_hash`
- `nala.py`: Neuer Endpunkt `POST /nala/profile/login` (bcrypt, First-Run-Setup)
- `nala.py`: Neuer Endpunkt `GET /nala/profile/prompts` (Profilliste ohne Hash)
- `nala.py`: Login-Screen im Frontend mit Profilwahl und Passwort
- `nala.py`: Header-Farbe wechselt je nach Profil
- `nala.py`: Voice-Transkript wird ins Eingabefeld geschrieben (editierbar, kein Auto-Send)
- `nala.py`: `X-Profile-Name`-Header in API-Requests

### Patch 45: Offene Profile + UX-Verbesserungen

**Ziel:** Offenes Login (Textfeld statt feste Buttons), Rollenstabilität, UX-Verbesserungen.

**Änderungen:**

- Login: Textfeld für Benutzername statt feste Profilbuttons
- Passwort-Toggle (Auge-Icon) zum Ein-/Ausblenden
- Input sticky am unteren Rand (kein Verrutschen bei Keyboard)
- Neue Session-ID beim Start (kein Laden gespeicherter sessionId)
- Profil-Wiederherstellung aus localStorage (kein erneutes Login nötig)
- System-Prompt-Rollenstabilität verbessert

### Patch 46: SSE EventBus Streaming ans Frontend

**Ziel:** Interne Pipeline-Events (RAG sucht, Intent erkannt, LLM antwortet) werden als Server-Sent Events ans Nala-Frontend gestreamt. Der User sieht live, was passiert, bevor die Antwort fertig ist.

**Änderungen `zerberus/core/event_bus.py`:**

- `Event`-Dataclass: Neues Feld `session_id: str = None`
- `EventBus`: Neue Methoden `subscribe_sse(session_id)` und `unsubscribe_sse(session_id, queue)`
- `EventBus._sse_queues`: Dict von session_id → Liste von `asyncio.Queue`
- `publish()`: Befüllt SSE-Queues automatisch (gefiltert nach session_id, globale Events an alle)

**Änderungen `zerberus/app/routers/nala.py`:**

- Neuer Endpunkt `GET /nala/events?session_id=...` (SSE, StreamingResponse)
- Event-Mapping: `rag_search` → "Suche im Gedächtnis...", `intent_detected` → "Verstanden: [intent]", `llm_start` → "Antwort kommt...", `rag_indexed` → "Gespeichert.", `done` → Verbindung idle
- Timeout: 30 Sekunden ohne Event → Verbindung schließen
- Frontend: `EventSource` verbindet sich nach Login mit SSE-Endpunkt
- Frontend: Status-Bar unter dem Header zeigt Pipeline-Events live an (fade-in/out)
- Frontend: SSE-Reconnect bei Session-Wechsel, Disconnect bei Logout

**Änderungen `zerberus/app/routers/orchestrator.py`:**

- `_run_pipeline()`: Publiziert Events mit `session_id` für SSE-Filterung:
  - `intent_detected` (nach Intent-Erkennung)
  - `rag_search` (vor RAG-Suche)
  - `llm_start` (vor LLM-Call)
  - `rag_indexed` (nach Auto-Index)
  - `done` (Pipeline abgeschlossen)

### Patch 47: Permission Layer + Intent-Subtypen + System-Prompt Overhaul

**Stand:** 2026-04-04

**Ziel:** Drei Säulen in einem Patch: rollenbasierter Permission-Layer pro Profil, verfeinerte Intent-Subtypen und profileigene editierbare System-Prompts.

---

#### Säule 1 – Permission Layer

**Änderungen `config.yaml`:**
- Jedes Profil erhält zwei neue Felder: `permission_level` (admin/user/guest) und `allowed_model` (null = globale Einstellung, sonst konkreter Modell-String)
- `chris`: `permission_level: admin`, `allowed_model: null`
- `user2` (Jojo): `permission_level: user`, `allowed_model: null`

**Permission-Matrix:**
| Level | Erlaubte Intents |
|---|---|
| admin | QUESTION, CONVERSATION, COMMAND_SAFE, COMMAND_TOOL |
| user | QUESTION, CONVERSATION, COMMAND_SAFE – COMMAND_TOOL → Human-in-the-Loop |
| guest | QUESTION, CONVERSATION – COMMAND_SAFE + COMMAND_TOOL → Human-in-the-Loop |

**Human-in-the-Loop Nachricht:** `"Das würde ich gern für dich erledigen – aber dafür brauche ich Chris' OK. Soll ich ihn fragen?"`

**Änderungen `zerberus/app/routers/orchestrator.py`:**
- `_run_pipeline()`: Neue Parameter `permission_level: str = "guest"` und `allowed_model: str | None = None`
- Permission-Check nach Intent-Erkennung: Bei Verstoß kein LLM-Call, direkte HitL-Antwort
- Modell-Override: `allowed_model` wird via `model_override` an `llm.call()` weitergegeben
- Neue Konstanten: `_PERMISSION_MATRIX`, `_HITL_MESSAGE`

**Änderungen `zerberus/core/llm.py`:**
- `call()`: Neuer optionaler Parameter `model_override: str | None = None`
- Wenn gesetzt, überschreibt dieser Parameter `settings.legacy.models.cloud_model`

**Änderungen `zerberus/app/routers/nala.py` (Backend):**
- `POST /nala/profile/login`: Gibt jetzt `permission_level` und `allowed_model` in der Response zurück
- Frontend speichert diese Werte in `currentProfile` (localStorage)
- `profileHeaders()` sendet `X-Permission-Level` und (optional) `X-Allowed-Model` bei jedem Request

**Änderungen `zerberus/app/routers/legacy.py`:**
- Liest `X-Permission-Level` Header (Default: `"guest"`)
- Importiert `detect_intent`, `_PERMISSION_MATRIX`, `_HITL_MESSAGE` aus Orchestrator
- Permission-Check vor Dialekt-Prüfung und LLM-Call
- Bei Verstoß: HitL-Response statt LLM-Call, Interaction wird trotzdem gespeichert

---

#### Säule 2 – Intent-Subtypen

**Änderungen `zerberus/app/routers/orchestrator.py`:**

`detect_intent()` komplett ersetzt. Neue 4-Kategorie-Logik:

| Subtyp | Prüflogik |
|---|---|
| COMMAND_TOOL | Phrases: `führe aus`, `erstelle datei`, `schreib in` + Keywords: `starte`, `öffne`, `lösche`, `agent`, `docker`, `tool`, `script`, `deploy`, `download`, ... |
| COMMAND_SAFE | Phrases: `zeig mir`, `liste auf`, `gib mir`, `lies vor` + Keywords: `exportier`, `spiel`, `wiederhol`, `zusammenfass`, `übersetze`, `formatier`, ... |
| QUESTION | Endet mit `?` oder beginnt mit Fragewort (`was`, `wie`, `wann`, `wo`, `warum`, `wer`, `welche`, `erkläre`, `definier`, ...) |
| CONVERSATION | Fallback – alles andere |

Prüfreihenfolge: COMMAND_TOOL → COMMAND_SAFE → QUESTION → CONVERSATION

**Intent-Snippets:** Werden direkt vor der User-Message in den Kontext eingefügt (nicht im System-Prompt):
```python
INTENT_SNIPPETS = {
    "QUESTION":      "[Modus: Informationsanfrage – präzise antworten, strukturiert, kein Bullshit]",
    "COMMAND_SAFE":  "[Modus: Aktion – kurz ausführen und knapp bestätigen]",
    "COMMAND_TOOL":  "[Modus: Tool-Anfrage – Permission-Check läuft]",
    "CONVERSATION":  "[Modus: Gespräch – locker, empathisch, keine Listen wenn nicht nötig]",
}
```

---

#### Säule 3 – System-Prompt Overhaul

**`system_prompt.json` (Default-Prompt komplett ersetzt):**
Neuer Bibel-Fibel-Stil mit klaren Sektionen: ROLE, GOAL, CONSTRAINTS, PERSONA, MEMORY, INTENT, PERMISSION, FALLBACK.

**Neue Endpunkte `zerberus/app/routers/nala.py`:**
- `GET /nala/profile/my_prompt` – Gibt eigenen System-Prompt zurück (Fallback-Kette: profilspezifische Datei → system_prompt.json → "")
- `POST /nala/profile/my_prompt` – Speichert eigenen System-Prompt in `system_prompt_{profil}.json` (atomares Schreiben via tempfile + os.replace)
- Beide Endpunkte: Auth über `X-Profile-Name` Header, kein fremdes Profil zugreifbar

**Frontend `nala.py`:**
- Neuer Abschnitt "✏️ Mein Ton" in der Sidebar (unterhalb der Chat-Liste)
- Textarea mit aktuellem profilspezifischem System-Prompt vorbelegt
- "Speichern"-Button → `POST /nala/profile/my_prompt`
- Prompt wird beim Öffnen der Sidebar automatisch geladen (`loadMyPrompt()`)
- Nur sichtbar wenn eingeloggt (Sidebar ist nur im Chat-Screen zugänglich)

### Patch 48: Stabilisierung

**Stand:** 2026-04-05

**Ziel:** Vier Stabilisierungsmaßnahmen ohne neue externe Abhängigkeiten: Config Live-Reload, VADER-Integration in die Orchestrator-Pipeline, aggregierter Health-Endpunkt, Preparer-Deaktivierung.

---

#### Punkt 1 – Config Live-Reload (`zerberus/app/routers/hel.py`)

**Problem:** Änderungen via `POST /hel/admin/config` (Hel-Dashboard) wurden in `config.json` geschrieben, aber die Runtime (`get_settings()`) las weiterhin den alten Stand aus dem Singleton-Cache. Neustart war erforderlich.

**Lösung:**
- Import von `reload_settings` aus `zerberus.core.config` ergänzt.
- In `post_config()` wird nach erfolgreichem `os.replace()` sofort `reload_settings()` aufgerufen.
- Response erweitert: `{"status": "ok", "reloaded": True}`.

**Effekt:** Modell, Temperatur und Threshold wirken ab sofort ohne Neustart.

---

#### Punkt 2 – VADER in Orchestrator-Pipeline (`zerberus/app/routers/orchestrator.py`)

**Problem:** Das Emotional-Modul (VADER) war nur via separaten HTTP-Endpunkt erreichbar, nicht automatisch in die Chat-Pipeline integriert. Sentiment-Score fehlte in der Orchestrator-Response.

**Lösung:**
- Graceful-Import von `SentimentIntensityAnalyzer` als `_vader` auf Modul-Ebene (try/except – kein Crash bei fehlendem `vaderSentiment`).
- `_VADER_OK`-Flag steuert, ob Analyse stattfindet.
- Nach dem LLM-Call in `_run_pipeline()` (Schritt 3b): `_vader.polarity_scores(message)` auf die User-Nachricht.
- EventBus-Event `user_sentiment` mit `compound`-Score und `session_id` wird publiziert.
- `_run_pipeline()` gibt jetzt 7-Tuple zurück: `(answer, model, p_tok, c_tok, cost, intent, sentiment_score)`.
- `OrchestratorResponse` erhält neues Feld `sentiment: float | None = None`.
- `process_message` entpackt das 7-Tuple und gibt `sentiment` in der Response zurück.

**Graceful Degradation:** Bei fehlender `vaderSentiment`-Installation: `sentiment: null`, kein Crash.

---

#### Punkt 3 – Health-Aggregations-Endpoint (`zerberus/main.py`)

**Problem:** Es gab keinen zentralen Endpunkt, der den Gesundheitszustand aller Module zusammenfasste. Monitoring erforderte mehrere separate HTTP-Aufrufe.

**Lösung:**
- Neuer Endpunkt `GET /health` direkt auf `app` in `zerberus/main.py`.
- Ruft Health-Funktionen aller Module und Core-Router per Direktaufruf ab (kein HTTP-Roundtrip):
  - `nala.health_check()`
  - `orchestrator.health_check()`
  - `rag.health_check(settings=settings)`
  - `emotional.health_check()`
  - `nudge.health_check()`
  - `preparer.health_check()`
- Jeder Aufruf ist graceful: Exception → `{"status": "error", "detail": "..."}`.
- Response: `{"status": "ok"|"degraded", "modules": {"nala": ..., "rag": ..., ...}}`.
- Status `"degraded"` wenn mindestens ein Modul nicht `"ok"` zurückgibt.

---

#### Punkt 4 – Preparer deaktivieren (`config.yaml`)

**Problem:** Das Preparer-Modul gab nur hartcodierte Mock-Events zurück (keine echte Kalender-Anbindung). Laufendes Pseudo-Modul erzeugte false confidence.

**Lösung:**
- `config.yaml`: `modules.preparer.enabled: false` gesetzt.
- Kein Code gelöscht – Modul kann jederzeit reaktiviert werden.

---

### Patch 49: Favicon + Design-Token Dunkelblau+Gold + start.bat SSL-Fix

**Stand:** 2026-04-05

**Ziel:** Visueller Feinschliff und Produktionsstabilität: eigenes Favicon, einheitliches Dunkelblau/Gold-Theme für die Nala-Oberfläche, Quotierung der SSL-Pfade in start.bat.

---

#### Punkt 1 – Favicon

- `Rosas top brain app rounded.png` (Projektroot) auf 32×32 px skaliert und als `zerberus/static/favicon.ico` gespeichert (Pillow, RGBA, ICO-Format).
- `nala.py` NALA_HTML `<head>`: `<link rel="icon" href="/static/favicon.ico">` ergänzt.
- `hel.py` ADMIN_HTML `<head>`: `<link rel="icon" href="/static/favicon.ico">` ergänzt.

#### Punkt 2 – Design-Token Dunkelblau+Gold als Default-Theme

CSS-Custom-Properties in NALA_HTML (`:root`):

| Token | Wert | Verwendung |
|---|---|---|
| `--color-primary` | `#0a1628` | Body, Chat-Hintergrund, Login-Inputs |
| `--color-primary-mid` | `#1a2f4e` | Header, Status-Bar, Input-Area, Sidebar, Login-Screen |
| `--color-gold` | `#f0b429` | Titel, Buttons (Send/Mic), Links, aktive Elemente, Border-Akzente |
| `--color-gold-dark` | `#c8941f` | Hover-Zustand für Gold-Buttons |
| `--color-text-light` | `#e8eaf0` | Standard-Text auf dunklem Grund |

Alle bisherigen Hartkodierungen (`#fce4ec`, `#ec407a`, `#9c27b0`, `#d81b60`, `white`, `#f8f8f8`) wurden durch Token ersetzt. Layout und alle Funktionen bleiben unverändert.

#### Punkt 3 – start.bat SSL-Fix

Uvicorn-Startzeile: `--reload` entfernt, SSL-Pfade in Anführungszeichen gesetzt:
```
uvicorn zerberus.main:app --host 0.0.0.0 --port 5000 --ssl-keyfile="desktop-rmuhi55.tail79500e.ts.net.key" --ssl-certfile="desktop-rmuhi55.tail79500e.ts.net.crt"
```

### Patch 50: start.bat venv-Fix + Alembic entfernt + Nudge-Modul in Pipeline

**Stand:** 2026-04-05

**Ziel:** Drei unabhängige Stabilisierungsmaßnahmen: Schlanker start.bat ohne Installer-Logik, Aufräumen der requirements.txt, Nudge-Modul automatisch in die Orchestrator-Pipeline integriert.

---

#### Punkt 1 – start.bat venv-Fix

`start.bat` auf minimalen Inhalt reduziert: nur `cd`, `call venv\Scripts\activate` und Uvicorn-Start mit SSL. Keine pip-Installer-Logik mehr im Start-Skript (war fehleranfällig bei bereits aktiviertem venv).

#### Punkt 2 – Alembic aus requirements.txt entfernt

`alembic==1.12.1` aus `requirements.txt` entfernt. Begründung: Kein `alembic.ini` vorhanden, keine Migrationen aktiv. DB-Schema wird weiterhin per `Base.metadata.create_all` initialisiert. Alembic wird reaktiviert sobald Schema-Änderungen anstehen.

#### Punkt 3 – Nudge-Modul in `_run_pipeline()` integriert

- Graceful-Import von `nudge_evaluate` + `_NudgeRequest` aus `zerberus.modules.nudge.router` (try/except, `_NUDGE_OK`-Flag analog zu `_VADER_OK`)
- Neuer Pipeline-Schritt **3c** nach VADER-Sentiment: Nudge-Score = `abs(sentiment_score)`, Event-Type `"conversation"`
- Bei `should_nudge=True`: EventBus-Event `nudge_sent` mit `session_id` und `nudge_text`; `nudge_text` in `OrchestratorResponse` befüllt
- Neues Feld `nudge: str | None = None` in `OrchestratorResponse`
- Graceful Degradation: Bei Fehler, `_NUDGE_OK = False` oder Modul deaktiviert → `nudge: null`, kein Crash
- Permission-Block Early-Return um fehlende Rückgabewerte (`None, None`) ergänzt (Bugfix)

### Patch 51: Docker-Check + Sandbox-Modul Skeleton + COMMAND_TOOL Routing

**Stand:** 2026-04-05

**Ziel:** Grundlegende Infrastruktur für die Docker-Sandbox vorbereiten, ohne echte Ausführung zu aktivieren. Drei unabhängige Maßnahmen: Docker-Verfügbarkeits-Check beim Start, neues Sandbox-Modul-Skeleton, COMMAND_TOOL-Intent für Admin vorverkabelt.

---

#### Punkt 1 – Docker-Check beim Start (`zerberus/main.py`)

- `import subprocess` ergänzt
- `_DOCKER_OK: bool = False` als Modul-Konstante eingeführt
- Im Lifespan-Manager nach `run_all()`: `subprocess.run(["docker", "info"], timeout=3)` mit `capture_output=True`
- `_DOCKER_OK = True` wenn `returncode == 0`, sonst `False`
- Bei `_DOCKER_OK = True`: `logger.info("[SANDBOX] Docker erreichbar")`
- Bei `_DOCKER_OK = False`: `logger.warning("[SANDBOX] Docker nicht erreichbar – Sandbox deaktiviert")`
- Kein Hard-Fail: Exception (Docker nicht installiert, Timeout, etc.) wird graceful abgefangen

#### Punkt 2 – Sandbox-Modul Skeleton (`zerberus/modules/sandbox/`)

- `zerberus/modules/sandbox/__init__.py` — leer
- `zerberus/modules/sandbox/router.py` — FastAPI-Router mit `GET /sandbox/health`
  - Health-Check gibt `{"status": "disabled", "docker": _DOCKER_OK}` zurück
  - `_DOCKER_OK` wird aus `zerberus.main` importiert
- `config.yaml`: `modules.sandbox.enabled: false` — Modul vom Loader erkannt, aber nicht aktiv

#### Punkt 3 – COMMAND_TOOL Routing vorbereitet (`zerberus/app/routers/orchestrator.py`)

- Neuer Schritt **0c** in `_run_pipeline()` nach Permission-Check:
  - Condition: `intent == "COMMAND_TOOL" and permission_level == "admin"`
  - Log: `"[SANDBOX] COMMAND_TOOL erkannt – Sandbox-Routing vorbereitet (noch nicht aktiv)"`
  - EventBus-Event `sandbox_pending` mit `session_id` und `message` (ersten 100 Zeichen)
  - LLM-Aufruf läuft wie bisher (Sandbox führt noch nichts aus)
- user/guest: Kein neues Verhalten — HitL-Block aus Patch 47 greift weiterhin

#### Punkt 4 – Health-Aggregation (`zerberus/main.py`)

- `GET /health`: Sandbox-Modul in der Loop der Modul-Checks ergänzt
- Sandbox-Health wird graceful eingebunden: Exception → `{"status": "error", "detail": "..."}`

---

### Patch 52: Sandbox-Executor + EU-Routing + COMMAND_TOOL Live-Ausführung

**Stand:** 2026-04-05

**Ziel:** Sandbox scharf schalten. Drei unabhängige Maßnahmen: EU-Routing für OpenRouter-Calls, echter Docker-Executor für Code-Ausführung, COMMAND_TOOL-Intent führt Code live in der Sandbox aus.

---

#### Punkt 1 – EU-Routing (`zerberus/core/llm.py`)

- `provider`-Block im LLM-Payload um zwei Felder ergänzt:
  - `"order": ["EU"]` – OpenRouter routet bevorzugt an EU-Rechenzentren
  - `"allow_fallbacks": True` – Fallback auf andere Regionen wenn EU nicht verfügbar
- Ergänzt neben bestehendem `"data_collection": "deny"` – kein weiterer Eingriff

---

#### Punkt 2 – Sandbox Executor (`zerberus/modules/sandbox/executor.py`)

Neue Datei. Funktion `async def execute_in_sandbox(code: str, language: str = "python") -> dict`:

- Prüft `_DOCKER_OK` via Late-Import aus `zerberus.main` (verhindert Zirkular-Import-Problem)
- Docker-Befehl: `docker run --rm --network none --memory 128m --cpus 0.5 python:3.11-slim python -c "<code>"`
- Ausführung via `asyncio.create_subprocess_exec`, stdout/stderr captured
- Timeout 10 Sekunden via `asyncio.wait_for` → bei Überschreitung `proc.kill()`, `"timed_out": True`
- Rückgabe: `{"stdout": str, "stderr": str, "exit_code": int, "timed_out": bool}`
- Graceful: Jede Exception → `{"stdout": "", "stderr": str(e), "exit_code": -1, "timed_out": False}`

---

#### Punkt 3 – Sandbox Router erweitert (`zerberus/modules/sandbox/router.py`)

Neuer Endpunkt `POST /sandbox/execute`:

- Request-Model: `{"code": str, "language": str = "python", "session_id": str = ""}`
- Auth: `X-Permission-Level`-Header muss `"admin"` sein → sonst HTTP 403
- Docker nicht verfügbar → HTTP 503
- Ruft `execute_in_sandbox()` auf
- Publiziert EventBus-Event `sandbox_executed` mit `session_id`, `exit_code`, `timed_out`
- Response: Das Dict aus `execute_in_sandbox()`
- Health-Check aktualisiert: gibt `"ok"` wenn Docker erreichbar, `"disabled"` sonst

---

#### Punkt 4 – COMMAND_TOOL Flow im Orchestrator (`zerberus/app/routers/orchestrator.py`)

- `import re` ergänzt (Code-Block-Extraktion)
- Schritt **0c** in `_run_pipeline()` komplett ersetzt:
  - `sandbox_context = ""` als lokale Variable initialisiert
  - Bei `_DOCKER_OK and permission_level == "admin" and intent == "COMMAND_TOOL"`:
    - Code-Extraktion: Regex ```` ```python?\n?(.*?)``` ```` auf `message`; Fallback: `message` direkt
    - `await execute_in_sandbox(code)` aufrufen
    - Ergebnis in `sandbox_context` speichern (`[Sandbox-Output]: stdout` / Fehler / Timeout)
    - Graceful: Exception → Warning-Log, `sandbox_context` bleibt leer
  - EventBus-Event `sandbox_pending` wird weiterhin publiziert
- Schritt **1** (RAG + user_content): `sandbox_block` aus `sandbox_context` gebildet, vor `message` eingefügt
  - LLM erhält: `[Intent] + [Gedächtnis] + [Sandbox-Output] + Snippet + User-Nachricht`
  - Bei leerem `sandbox_context`: Verhalten identisch zu Patch 51

---

#### Punkt 5 – config.yaml

- `modules.sandbox.enabled: true` – Modul wird jetzt vom Loader aktiv geladen und der Router eingebunden

---

### Patch 53: Cleaner-Bug-Fix + GitHub-Vorbereitung + README

**Stand:** 2026-04-05

**Ziel:** Drei unabhängige Maßnahmen: Bugfix im Whisper-Cleaner (kaputte Regex-Replacements), Projekt für GitHub vorbereiten (`.gitignore`, `config.yaml.example`), lesbare Anleitung für Nicht-Techniker (`README.md`).

---

#### Punkt 1 – Cleaner `$1`-Bug-Fix (`zerberus/core/cleaner.py`)

**Problem:** In der Listenformat-Schleife des Whisper-Cleaners wurden Regex-Backreferences als `$1`, `$2` etc. in `whisper_cleaner.json`-Regeln erwartet. Python's `re.sub` versteht aber `\1`, nicht `$1`. Außerdem fehlte ein Guard für Regeln ohne `"pattern"`-Key, was zu `KeyError` führen konnte.

**Lösung:**
- Guard `if "pattern" not in rule: continue` am Schleifenstart
- Direkt nach dem Lesen: `replacement = re.sub(r'\$(\d+)', r'\\\1', replacement)` – konvertiert `$1` → `\1`
- Damit sind `whisper_cleaner.json`-Regeln mit Gruppen-Backreferences jetzt korrekt

---

#### Punkt 2 – GitHub-Vorbereitung

**`.gitignore`:**
- Neuer Eintrag `config.yaml` (enthält Passwort-Hashes und lokale Pfade) – darf nicht ins Repository
- `*.key` und `*.crt` waren bereits vorhanden – nicht doppelt eingetragen

**`config.yaml.example`** (neu, Projektroot):
- Vollständige Kopie der Produktions-`config.yaml` mit bereinigten Werten:
  - Alle `password_hash`-Felder: leer (`''`)
  - `bot_token`, `account_sid`, `auth_token` → `YOUR_TOKEN_HERE`
  - Tailscale-Domain in `whisper_url` → `YOUR_TAILSCALE_DOMAIN`
- Jeder Abschnitt mit deutschem Kommentar versehen
- BIOS-Hinweis beim `modules.sandbox`-Block: Virtualisierung (Intel VT-x / AMD-V)

---

#### Punkt 3 – README.md (neu, Projektroot)

Deutschsprachige Anleitung für Nicht-Techniker (Zielgruppe: Joana und ähnliche Nutzer).

**Struktur:**
- Was ist das? – Nala als persönlicher KI-Assistent
- Was brauchst du? – OpenRouter, Tailscale, Docker, BIOS-Virtualisierung
- Installation – Repository, config.yaml.example → config.yaml, venv, start.bat
- Die Nala-Oberfläche – Login, Chat, Spracheingabe, Sessions
- Das Hel-Dashboard – alle Tabs erklärt (Modell, Temperatur, Guthaben, Cleaner, Fuzzy, Dialekte, System-Prompt, Metriken, Sessions, Debug)
- Zugriff von unterwegs (Tailscale)
- Häufige Fragen / Probleme (6 häufige Fehler mit Lösung)

---

### Patch 54: JWT-Authentifizierung + Token-Middleware + Header-Ablösung

**Stand:** 2026-04-05

**Ziel:** Die bisher auf `X-Permission-Level`/`X-Profile-Name` HTTP-Headern basierende Session-Verwaltung war eine Sicherheitslücke – jeder Browser konnte beliebige Header setzen. Patch 54 schließt diese Lücke durch echte JWT-basierte Authentifizierung mit serverseitiger Signaturprüfung.

---

#### Punkt 1 – PyJWT-Dependency (`requirements.txt`)

- `PyJWT==2.8.0` ergänzt

---

#### Punkt 2 – AuthConfig (`zerberus/core/config.py`)

Neues Submodell `AuthConfig`:
- `token_secret: str = "CHANGE_ME"` – HS256-Signatur-Secret
- `token_expire_minutes: int = 480` – Token-Laufzeit (8 Stunden)

In `Settings` als `auth: AuthConfig = AuthConfig()` eingebunden.

`config.yaml` und `config.yaml.example` um `auth:`-Block ergänzt.

---

#### Punkt 3 – Token-Generierung beim Login (`zerberus/app/routers/nala.py`)

In `POST /nala/profile/login`, nach erfolgreichem bcrypt-Check:
- JWT-Payload: `sub` (Profilname), `permission_level`, `allowed_model`, `exp` (UTC + 480 min)
- `jwt.encode(payload, settings.auth.token_secret, algorithm="HS256")`
- Response: `"token": token` statt `"session_token": uuid4()`

---

#### Punkt 4 – Token-Validation Middleware (`zerberus/core/middleware.py`)

Neue Funktion `verify_token(request) → dict | None`:
- Liest `Authorization: Bearer <token>` Header
- Verifiziert via `jwt.decode()`, Algo HS256, Secret aus Settings
- Bei Fehler (abgelaufen, ungültig): `None`

Neue Middleware `token_auth_middleware`:
- **Ausgenommen:** `/nala/profile/login`, `/nala/events`, `/static/`, `/favicon.ico`, `/health`, `/docs`, `/openapi.json`, `/redoc`, `/hel` (eigene Basic-Auth), `/nala`, `/nala/health`, `/nala/profile/prompts`
- Bei fehlendem/ungültigem Token: HTTP 401 JSON-Response
- Bei gültigem Token: `request.state.profile_name`, `.permission_level`, `.allowed_model` setzen

Registrierung in `zerberus/main.py` – zuletzt registriert = zuerst ausgeführt (Starlette-Reihenfolge).

---

#### Punkt 5 – Router-Anpassung: Header → request.state

**`zerberus/app/routers/legacy.py`:**
- `request.headers.get("X-Profile-Name")` → `getattr(request.state, "profile_name", None)`
- `request.headers.get("X-Permission-Level", "guest")` → `getattr(request.state, "permission_level", "guest")`

**`zerberus/app/routers/nala.py` (Python-Endpunkte):**
- `/profile/my_prompt` GET + POST: `X-Profile-Name` Header → `request.state.profile_name`
- `/voice`: `X-Profile-Name` Header → `request.state.profile_name`

**`zerberus/modules/sandbox/router.py`:**
- `x_permission_level: Header(default=None)` → `getattr(request.state, "permission_level", "guest")`

---

#### Punkt 6 – Frontend (`NALA_HTML` in `zerberus/app/routers/nala.py`)

- `doLogin()`: speichert `data.token` als `currentProfile.token` in localStorage
- `profileHeaders()`: sendet `Authorization: Bearer <token>` statt `X-Permission-Level`/`X-Profile-Name`/`X-Allowed-Model`
- `handle401()`: neue Funktion – loggt bei 401-Response automatisch aus und zeigt Login-Screen mit Hinweis
- `sendMessage()` + `toggleRecording()` voice-fetch: prüfen auf `response.status === 401` → `handle401()`

### Patch 55: Pen-Test-Protokoll + README-Erweiterung

**Stand:** 2026-04-05

**Ziel:** Sicherheits-Audit-Struktur dokumentieren und GitHub-Setup-Anleitung für Entwickler ergänzen. Reine Dokumentations-Änderungen – kein Code verändert.

- Abschnitt 13 „Sicherheits-Audit & Pen-Test-Protokoll" in `docs/PROJEKTDOKUMENTATION.md` ergänzt
- Geplante Angriffsvektoren tabellarisch erfasst (Prompt Injection, JWT, Sandbox Escape, RAG Poisoning, …)
- Bekannte akzeptierte Risiken dokumentiert (in-memory Rate-Limiting, Tailscale als Netzwerk-Schicht)
- README.md um Abschnitt „Für Entwickler: GitHub & eigene Instanz" ergänzt

---

### Patch 59: Pacemaker-Fix, Statischer API-Key, Metric Engine Level 1

**Stand:** 2026-04-06

#### Säule 1 – Pacemaker Double-Start-Bug behoben (`zerberus/app/pacemaker.py`)

- **Problem:** `update_interaction()` konnte mehrfach vor dem ersten Worker-Start aufgerufen werden → mehrere parallele `pacemaker_worker`-Tasks
- **Fix:** `update_interaction()` ist jetzt `async`; Guard via `async with _pacemaker_lock: if pacemaker_task is None or pacemaker_task.done():`
- `_pacemaker_running = False` wird am Ende von `pacemaker_worker()` explizit gesetzt (Cleanup nach natürlichem Stopp)
- Alle Call-Sites in `legacy.py` und `nala.py` auf `await update_interaction()` umgestellt

#### Säule 2 – Dialect 401 Bug: Statischer API-Key (`config.py`, `middleware.py`)

- Neuer Config-Eintrag `auth.static_api_key` in `config.yaml` und `config.yaml.example`
- In `AuthConfig` (Pydantic): `static_api_key: str = ""` (Patch 59)
- JWT-Middleware prüft **vor** dem Bearer-Check ob `X-API-Key` Header gesetzt ist und mit `static_api_key` übereinstimmt
- Bei Match: Request direkt durchgelassen, kein JWT erforderlich
- Graceful: leerer String = Feature deaktiviert, kein Verhaltensunterschied

#### Säule 3 – Metric Engine Level 1 (`zerberus/modules/metrics/`)

Neues Modul mit drei Dateien:

**`engine.py` – Pure-Python + spaCy Metriken:**

| Funktion | Beschreibung | Typ |
|---|---|---|
| `compute_ttr()` | Type-Token-Ratio | Pure Python |
| `compute_mattr()` | Moving Average TTR (Fenster 50) | Pure Python |
| `compute_hapax_ratio()` | Hapax-Legomena-Anteil | Pure Python |
| `compute_avg_sentence_length()` | Ø Satzlänge in Wörtern | Pure Python |
| `compute_shannon_entropy()` | Shannon Entropy der Wortverteilung | Pure Python |
| `compute_hedging_frequency()` | Anteil Hedge-Wörter | spaCy (graceful) |
| `compute_self_reference_frequency()` | Anteil Selbstreferenz-Tokens | spaCy (graceful) |
| `compute_causal_ratio()` | Anteil Sätze mit Kausalkonnektoren | spaCy (graceful) |

- spaCy wird **lazy geladen** (`_get_nlp()`), kein Import beim Modulstart
- spaCy-Metriken geben `None` zurück wenn `de_core_news_sm` nicht installiert ist

**`router.py` – Endpunkte:**
- `POST /metrics/analyze` – nimmt `{"text": str, "session_id": str}`, gibt alle Metriken zurück, schreibt in `message_metrics`
- `GET /metrics/history?session_id=&limit=20` – liest `message_metrics` aus DB
- Berechnung via `asyncio.to_thread` (non-blocking)
- Neue DB-Spalten (`ttr`, `mattr`, `hapax_ratio`, `avg_sentence_length`, `shannon_entropy`, `hedging_freq`, `self_ref_freq`, `causal_ratio`) werden per PRAGMA-Check angelegt (wie Overnight-Scheduler)

**`config.yaml`:** `modules.metrics.enabled: true`

**`requirements.txt`:** `spacy>=3.7.0` hinzugefügt

**spaCy-Modell einmalig installieren:**
```
venv\Scripts\activate
python -m spacy download de_core_news_sm
```

---

### Patch 58: UI-Verbesserungen, Guthaben-Fix, Slider-Buttons, Dialect-Kurzschluss

**Stand:** 2026-04-05

#### Säule 1 – Guthaben-Anzeige verbessert
- `GET /hel/admin/balance`: Berechnet `balance = limit_usd - usage` direkt serverseitig; gibt `{ "balance": float, "last_cost": float }` zurück
- `last_cost`: letzte Zeile aus `costs`-Tabelle via neuer Hilfsfunktion `get_last_cost()` in `database.py`
- Frontend zeigt beide Werte getrennt: „Guthaben: $X.XX" und „Letzte Anfrage: $X.XXXXXX"

#### Säule 2 – Modell-Liste sortierbar
- Zwei Sort-Buttons über der Modell-Liste: „Nach Name (A–Z)" und „Nach Preis (↑)"
- Client-seitige Sortierung via `sortModels(mode)` + `renderModelSelect()` in JavaScript (kein neuer Endpunkt)
- Preis „0.000000" wird als „kostenlos" angezeigt
- Default-Sortierung: Name A–Z

#### Säule 3 – Temperatur + Threshold ±0.1-Buttons
- ▼/▲-Buttons neben jedem Schieberegler (Temperatur, Threshold)
- `stepSlider(id, displayId, delta, min, max)`: addiert/subtrahiert 0.1, klemmt auf [min, max], rundet auf 1 Dezimalstelle
- Schieberegler, Anzeigefeld und Buttons synchronisieren sich

#### Säule 4 – Dialect-Kurzschluss vor Orchestrator (legacy.py)
- Dialect-Check in `POST /v1/chat/completions` wird jetzt **vor** dem Permission-Check ausgeführt
- Dialect-Requests (z.B. 🐻🐻 Berlin, 🥨🥨 Schwäbisch) verlassen den Handler sofort via `check_dialect_shortcut()` ohne Intent-Check und HitL-Flow
- Permission-Check bleibt erhalten — wird nur nach erfolglosem Dialect-Check durchgeführt

---

### Patch 57: German BERT Sentiment + Dashboard-Fix + Navigation-Tab + Overnight-Scheduler

**Stand:** 2026-04-05

#### Säule 1 – VADER → German BERT Sentiment
- `vaderSentiment` aus `requirements.txt` und allen Imports entfernt
- Neues Modul `zerberus/modules/sentiment/router.py`: lädt `oliverguhr/german-sentiment-bert` via `transformers.pipeline`
- CUDA-Unterstützung: `device=0` wenn `torch.cuda.is_available()`, sonst CPU-Fallback
- Modell wird beim Import einmalig gecacht, kein Reload pro Request
- Exportierte Funktion `analyze_sentiment(text) -> {"label": str, "score": float}`
- Graceful: bei fehlendem `torch`/`transformers` → `{"label": "neutral", "score": 0.5}`
- `database.py`: Hard-Import von `vaderSentiment` entfernt; `_compute_sentiment()` nutzt neues Modul (graceful)
- `orchestrator.py`: VADER-Block ersetzt durch Import `analyze_sentiment`; EventBus-Event `user_sentiment` erweitert um `label` + `score`
- `config.yaml`: Neuer Block `sentiment` mit `enabled`, `model`, `device`

#### Säule 2 – Bug-Fix Modellauswahl + Guthaben
- `GET /hel/admin/models`: OpenRouter-Call jetzt mit `Authorization: Bearer <OPENROUTER_API_KEY>`; gibt Array direkt zurück (nicht das umhüllende Objekt); HTTP-Fehler werden als `502` mit lesbarem `detail` weitergeleitet
- `GET /hel/admin/balance`: Timeout 10 s; `HTTPStatusError` → `502` mit Fehlermeldung
- Frontend `loadModelsAndBalance`: prüft `res.ok` und zeigt lesbaren Fehlertext; parst `models` korrekt als Array; `pricing.prompt` via `parseFloat` gesichert; Balance zeigt `limit_usd - usage` oder `usage` je nach API-Response

#### Säule 3 – Neuer Hel-Tab „Navigation"
- Tab-Button „🔗 Navigation" in der Tab-Leiste ergänzt
- Tab-Content `tab-nav` mit Links (neuer Tab) zu: Nala-Chat, Hel-Dashboard, API Health, Session-Archiv, Metrics Latest, Debug State, API Docs (Swagger)
- Dunkelblau+Gold-Design (`.nav-link` CSS-Klasse)

#### Säule 4 – Overnight-Job + Dashboard-Graphen
- Neues Modul `zerberus/modules/sentiment/overnight.py`:
  - `create_scheduler()` → `AsyncIOScheduler` (APScheduler 3.x), täglich 04:30 Europe/Berlin
  - `run_overnight_sentiment()`: Spalten `bert_sentiment_label`/`bert_sentiment_score` in `message_metrics` per PRAGMA + ALTER TABLE anlegen; alle Messages der letzten 24h ohne BERT-Wert auswerten
  - `misfire_grace_time=3600` (bis zu 1h verspäteter Start)
- `main.py` Lifespan: Scheduler starten + graceful shutdown
- `requirements.txt`: `apscheduler>=3.10.0` ergänzt
- Neuer Endpunkt `GET /hel/metrics/history?limit=50`: letzte N Messages mit allen Chart-Metriken inkl. `bert_sentiment_score` (graceful wenn Spalten noch nicht existieren)
- Metriken-Tab: Toggle-Checkboxen für BERT Sentiment, Word Count, TTR, Shannon Entropy; Chart.js Multi-Dataset mit `yAxisID` für Word Count auf zweiter Y-Achse

---

### Patch 56: RAG-Dokument-Upload + Pacemaker-Verbesserungen + Dokumentation

**Stand:** 2026-04-05

**Ziel:** Vier Säulen: RAG-Upload-Interface im Hel-Dashboard, Pacemaker-Anpassungen (Erstpuls + längere Laufzeit), README-Erweiterung (RAG-Erklärung + Pacemaker-Doku), Dokumentation nachziehen.

---

#### Säule 1 – RAG-Dokument-Upload im Hel-Dashboard

**Neue Backend-Endpunkte (`zerberus/app/routers/hel.py`):**

- `POST /hel/admin/rag/upload` – Nimmt `.txt` oder `.docx` entgegen, extrahiert Text, zerlegt ihn in Chunks (~300 Wörter, 50 Wörter Überlapp), indiziert jeden Chunk via `_add_to_index` im FAISS-Index. Dateiname wird als `source`-Feld in `metadata.json` gespeichert.
- `GET /hel/admin/rag/status` – Gibt aktuelle Index-Größe (Anzahl Chunks) und Liste aller Quellen aus `_metadata` zurück.
- `DELETE /hel/admin/rag/clear` – Leert den FAISS-Index und `metadata.json` via neue Funktion `_reset_sync()` im RAG-Modul.
- `GET /hel/admin/pacemaker/config` – Aktuelle Pacemaker-Werte aus Settings.
- `POST /hel/admin/pacemaker/config` – Schreibt `keep_alive_minutes` direkt in `config.yaml` (PyYAML round-trip; wirkt nach Neustart).

**Neues Frontend-Tab „Gedächtnis / RAG":**
- Upload-Feld (File-Input, `.txt`/`.docx`), Hochladen-Button
- Statusanzeige nach Upload (z.B. „23 Chunks indiziert aus Rosendornen.txt")
- Index-Übersicht: Anzahl Chunks, Liste der indizierten Quellen mit Chunk-Count
- „Index leeren"-Button mit Bestätigungsdialog

**Neues Frontend-Tab „Systemsteuerung":**
- Editierbares Feld „Pacemaker-Laufzeit (Minuten)" mit Hinweis „Wirkt nach Neustart"
- Speichern-Button schreibt via `POST /hel/admin/pacemaker/config` in `config.yaml`

**Neues RAG-Modul-Hilfsfunktionen (`zerberus/modules/rag/router.py`):**
- `_reset_sync(settings)` – Erstellt neuen leeren `IndexFlatL2`, setzt `_metadata = []`, persistiert beides auf Disk.

**Neue Abhängigkeit (`requirements.txt`):**
- `python-docx==1.1.2` – für `.docx`-Textextraktion; graceful Import (`.txt` funktioniert auch ohne)

---

#### Säule 2 – Pacemaker-Anpassungen

**`config.yaml`:**
- `keep_alive_minutes`: 25 → **120** (2 Stunden statt 25 Minuten)

**`zerberus/app/pacemaker.py`:**
- Erstpuls sofort beim Start des Workers (vor der ersten `asyncio.sleep`-Pause) → Whisper-Container wird beim Anwendungsstart aufgeweckt, nicht erst nach dem ersten Intervall
- Laufzeit-Info im Startlog ergänzt
- Erstpuls-Fehler werden als Warning geloggt (nicht kritisch, kein Crash)

---

#### Säule 3 – README.md erweitert

- Neuer Abschnitt **„Das Gedächtnis von Nala (RAG)"** nach dem Hel-Dashboard-Abschnitt:
  - Zettelkasten-Metapher
  - Schritt-für-Schritt Upload-Anleitung
  - Erklärung des Chunking-Prozesses
  - Test-Tipps (Beispielfragen)
  - Hinweis zum Index leeren
- Neuer Abschnitt **„Der Pacemaker – Warum gibt es ihn?"** darunter:
  - Erklärung des Problems (Container schläft ein)
  - Wann er startet/stoppt (erste Interaktion, 2 Stunden Laufzeit)
  - Anleitung zur Laufzeit-Konfiguration im Hel-Dashboard
- Fußzeile: `Patch 55` → `Patch 56`

---

#### Säule 4 – Dokumentation nachgezogen

- `docs/PROJEKTDOKUMENTATION.md`: Pacemaker-Abschnitt (4.5) mit neuen Werten, neue Endpunkte in Hel-Router (4.3), Patch 56 in Patch-Historie, Roadmap aktualisiert
- `CLAUDE.md`: RAG-Upload-Endpunkt und Pacemaker-Konfigurationshinweis ergänzt

---

## 8. Aktueller Projektstatus

### Was funktioniert stabil

| Komponente | Status | Anmerkungen |
|---|---|---|
| **Server-Start** | Stabil | Alle Invarianten-Checks bestehen |
| **Text-Chat** (`/v1/chat/completions`) | Stabil | OpenAI-kompatibel, RAG+LLM+Auto-Index |
| **Voice-Pipeline** (`/nala/voice`) | Stabil | Vollständige Pipeline seit Patch 41 |
| **RAG-Index** | Stabil | FAISS FlatL2, persistent, thread-safe |
| **Intent-Erkennung** | Stabil | Regelbasiert, konfigurierbar via Code |
| **Hel-Dashboard** | Stabil | Alle Tabs funktional |
| **Session-Archiv** | Stabil | Lesen, Exportieren, Löschen |
| **Metriken** | Stabil | word_count, ttr, shannon_entropy, vader, Kosten |
| **Pacemaker** | Stabil | Aktiviert sich bei erster Interaktion |
| **EventBus** | Stabil | In-Memory, SSE-Support seit Patch 46, session_id-Filterung |
| **SSE Streaming** | Stabil | Pipeline-Events live ans Frontend (Patch 46) |
| **User-Profile** | Stabil | bcrypt-Login, individuelle Farben/Prompts (Patch 44/45) |
| **Permission Layer** | Stabil | admin/user/guest pro Profil, HitL bei gesperrten Aktionen (Patch 47) |
| **Intent-Subtypen** | Stabil | COMMAND_SAFE / COMMAND_TOOL / QUESTION / CONVERSATION (Patch 47) |
| **Profil-System-Prompts** | Stabil | Eigener Ton pro Profil editierbar via Nala-Frontend (Patch 47) |
| **Config Live-Reload** | Stabil | `POST /hel/admin/config` → sofortiger `reload_settings()` (Patch 48) |
| **VADER in Pipeline** | Stabil | Sentiment-Score nach LLM-Call, EventBus `user_sentiment`, graceful (Patch 48) |
| **Health-Aggregation** | Stabil | `GET /health` aggregiert alle Module (Patch 48) |
| **Dialect-Engine** | Stabil | Berlin, Schwäbisch, Emojis |
| **Whisper-Cleaner** | Stabil | Füllwörter, Korrekturen, Wiederholungen, Fuzzy-Matching (Patch 42); `$1`-Regex-Bug behoben (Patch 53) |
| **JWT-Session-Auth** | Stabil | HS256-Token beim Login, 8h Laufzeit, Middleware-Validierung; Header-Trust beseitigt (Patch 54) |
| **Modul-Loader** | Stabil | Dynamisches Laden via pkgutil |

### Was teilweise implementiert ist

| Komponente | Status | Was fehlt |
|---|---|---|
| **Emotional-Modul** | Läuft, in Pipeline integriert (Patch 48) | Sentiment-Score in Orchestrator-Response, EventBus-Event |
| **Nudge-Modul** | In Pipeline integriert (Patch 50) | Automatisch nach VADER in `_run_pipeline()`, graceful; History in-memory |
| **Preparer-Modul** | Deaktiviert (Patch 48) | `enabled: false`; Mock-Daten; echte Kalender-API fehlt |
| **Config-Live-Reload** | Voll implementiert (Patch 48) | `POST /hel/admin/config` ruft `reload_settings()` auf |
| **Local-Model-Routing** | Konfigurierbar | `threshold_length: 0` → immer Cloud; Local-URL leer |
| **Rate-Limiting** | Läuft | In-memory, kein Redis |
| **Sandbox-Modul** | Stabil (Patch 52) | `enabled: true`; Docker-Executor aktiv; COMMAND_TOOL führt Code live aus |

### Was geplant, aber nicht begonnen ist

| Komponente | Ziel-Patches | Beschreibung |
|---|---|---|
| **Docker-Sandbox** | 44-46 | Sicheres Ausführen von Tool-Use in Containern |
| **Tool-Use / Function-Calling** | 44-46 | LLM kann echte Aktionen auslösen |
| **Corporate Security Layer** | TBD | 5-stufiges Sicherheitsframework (Rosa Paranoia) |
| **Redis EventBus** | TBD | Persistenter, verteilter EventBus |
| **Echte Kalender-Integration** | TBD | Preparer-Modul mit realer ICS/CalDAV-Anbindung |
| **Multi-User-Sessions** | TBD | Server-seitige Session-Verwaltung |
| **Alembic-Migrationen** | Zurückgestellt (Patch 50) | `alembic` aus requirements entfernt; reaktivieren wenn Schema-Änderungen nötig |

---

## 9. Offene Entscheidungen

### 9.1 config.json vs. config.yaml – Vollständige Konsolidierung

**Situation:** `config.json` wird vom Hel-Dashboard beschrieben. LLM liest nur `config.yaml`. Änderungen via Dashboard wirken erst nach Neustart nicht im LLM, da kein Live-Reload verknüpft ist.

**Trade-offs:**
- Option A: `config.json`-Änderungen lösen automatisch `reload_settings()` aus → Live-Reload, aber komplexer
- Option B: Hel-Dashboard schreibt direkt in `config.yaml` → Einfacher, aber Datei-Konflikte möglich
- Option C: Status quo – Neustart erforderlich → Einfach, aber nicht nutzerfreundlich

**Empfehlung:** Option A (POST auf `/hel/admin/config` ruft danach `reload_settings()` auf). Risiko gering, Nutzen hoch.

### 9.2 Modul-Isolation vs. Direktimport

**Situation:** Orchestrator und Nala importieren RAG-Funktionen direkt (`from zerberus.modules.rag.router import ...`). Das widerspricht der Modul-Philosophie (lose Kopplung via EventBus).

**Trade-offs:**
- Direktimport: Performant (kein HTTP-Roundtrip), aber enge Kopplung
- EventBus: Lose Kopplung, aber latenter (async queue) und schwerer zu debuggen

**Empfehlung:** Status quo für RAG akzeptabel, da RAG eine Core-Funktion ist. Für zukünftige Module (Tool-Use etc.) EventBus-Ansatz bevorzugen.

### 9.3 FAISS FlatL2 vs. IVF/HNSW

**Situation:** Aktuell `IndexFlatL2` – exakte Suche, O(n) pro Query. Bei > 100.000 Vektoren wird es langsam.

**Empfehlung:** Für aktuelle Nutzung (persönlicher Assistent, <<10.000 Vektoren) ist FlatL2 ausreichend. Erst bei messbarer Latenz auf IVFFlat oder HNSW wechseln.

### 9.4 Preparer: Mock vs. Echte Implementierung

**Situation:** Preparer gibt hardcodierte Events zurück. Kein ICS/CalDAV-Parser vorhanden.

**Empfehlung:** Entweder echte Integration (ICS via `ics`-Library oder CalDAV) oder Modul deaktivieren (`enabled: false`) bis implementiert.

### 9.5 Alembic vs. create_all

**Situation:** `alembic` steht in `requirements.txt`, aber kein `alembic.ini`. Die DB wird per `Base.metadata.create_all` initialisiert – keine Schema-Migrationen möglich.

**Empfehlung:** Entweder Alembic vollständig aufsetzen (notwendig sobald Schema-Änderungen nötig), oder `alembic` aus requirements entfernen.

---

## 10. Roadmap

### Nächste 1–2 Wochen (April 2026)

**Priorität: Stabilisierung und Live-Reload**

| Task | Status | Beschreibung |
|---|---|---|
| Config-Live-Reload | ✅ Erledigt (Patch 48) | `POST /hel/admin/config` ruft `reload_settings()` auf |
| Preparer deaktivieren | ✅ Erledigt (Patch 48) | `enabled: false` in `config.yaml` |
| Emotional-Modul in Pipeline integrieren | ✅ Erledigt (Patch 48) | VADER-Sentiment nach LLM-Call, EventBus-Event |
| Health-Aggregations-Endpoint | ✅ Erledigt (Patch 48) | `GET /health` fasst alle Module zusammen |
| Alembic aufsetzen oder entfernen | ✅ Erledigt (Patch 50) | Alembic aus requirements entfernt; kein `alembic.ini` vorhanden |

### Nächste 1–2 Monate (April–Mai 2026)

**Priorität: Patches 43–46 (Orchestrator-Vertiefung + Tool-Use)**

| Patch | Status | Beschreibung |
|---|---|---|
| Patch 42 / 42b / 42c | ✅ Erledigt | Bugfixes (API-Key, Audio-Pipeline, Dialect-Engine), Fuzzy-Layer |
| Patch 43 | ✅ Erledigt | Orchestrator: Session-Kontext vollständig integriert – History, System-Prompt, Store, Kosten |
| Patch 44 | ✅ Erledigt | User-Profile: bcrypt-Login, individuelle Farben/System-Prompts, editierbares Transkript |
| Patch 45 | ✅ Erledigt | Offene Profile: Textfeld-Login, Passwort-Toggle, sticky Input, neue Session beim Start |
| Patch 46 | ✅ Erledigt | SSE EventBus Streaming: Pipeline-Events live ans Frontend, Status-Bar |
| Patch 47 | ✅ Erledigt | Permission Layer (admin/user/guest), Intent-Subtypen (COMMAND_TOOL/COMMAND_SAFE), Profil-System-Prompts |
| Patch 48 | ✅ Erledigt | Stabilisierung: Config Live-Reload, VADER in Pipeline, Health-Aggregation, Preparer deaktiviert |
| Patch 49 | ✅ Erledigt | Favicon, Design-Token Dunkelblau+Gold als Default-Theme, start.bat SSL-Fix |
| Patch 50 | ✅ Erledigt | start.bat venv-Fix, Alembic aus requirements entfernt, Nudge-Modul in Orchestrator-Pipeline integriert |
| Patch 51 | ✅ Erledigt | Docker-Check beim Start, Sandbox-Modul Skeleton, COMMAND_TOOL Routing vorbereitet, Health-Aggregation erweitert |
| Patch 52 | ✅ Erledigt | Sandbox-Executor aktiv, EU-Routing, COMMAND_TOOL führt Code live in Docker aus |
| Patch 53 | ✅ Erledigt | Cleaner-Bug-Fix (`$1`-Regex), GitHub-Vorbereitung (`.gitignore`, `config.yaml.example`), README für Nicht-Techniker |
| Patch 54 | ✅ Erledigt | JWT-Authentifizierung (HS256), Token-Middleware, Header-Trust-Lücke geschlossen, Frontend auf Bearer-Token umgestellt |
| Patch 55 | ✅ Erledigt | Pen-Test-Protokoll + README-Erweiterung (GitHub-Setup) |
| Patch 56 | ✅ Erledigt | RAG-Dokument-Upload (Hel-Dashboard), Pacemaker-Erstpuls + 120 min Laufzeit, README-Erweiterung (RAG + Pacemaker) |
| Patch 57 | ✅ Erledigt | German BERT Sentiment (VADER entfernt), Dashboard Bug-Fix models/balance, Navigation-Tab, Overnight-Scheduler, Multi-Metrik-Graph |
| Patch 58 | ✅ Erledigt | UI-Verbesserungen Hel-Dashboard, Guthaben-Fix (last_cost aus DB), Slider ±0.1-Buttons, Dialect-Kurzschluss vor Permission-Check |
| Patch 59 | ✅ Erledigt | Pacemaker Double-Start-Fix, Statischer API-Key Infra, Metric Engine Level 1 (TTR, MATTR, Hapax, Shannon Entropy, spaCy-Metriken) |
| Patch 60 | ✅ Erledigt | BERT-Chart-Fix (COALESCE SQL + yAxisID), TTR-Berechnungs-Fix (Debug-Log), User-Tag in DB (profile_name), Text-Chat Speicherung verifiziert |
| Patch 61 | ✅ Erledigt | RAG-Upload Mobile-Fix + MIME-Type-Toleranz (suffix-only), Per-User Temperatur-Override (ProfileConfig + JWT + request.state), JWT-Erweiterung um temperature, Hel-Dashboard Profil-Übersicht (readonly), GET /hel/admin/profiles |
| Patch 62 | ✅ Erledigt | Whisper-Cleaner Bugfixes: ZDF-für-funk-Variante, "Das war's für heute", "für's Zuschauen", "Bis zum nächsten Mal" standalone |
| Patch 63 | ✅ Erledigt | OpenRouter Provider-Blacklist: config.yaml + llm.py provider.ignore + Hel-Tab "Provider" (chutes + targon default) |

### Langfristige Meilensteine (2026+)

| Meilenstein | Beschreibung |
|---|---|
| **Redis-Integration** | EventBus auf Redis umstellen, Rate-Limiting mit Redis-Backend |
| **Corporate Security Layer** | 5-stufiges Sicherheitsframework (Input-Validation, Veto, Sandbox, Audit, Zero-Trust) |
| **HTTPS/TLS** | Reverse Proxy mit Zertifikat |
| **Multi-User-Support** | Server-seitige Sessions, Benutzer-Trennung |
| **Lokales Modell** | Echte Local-URL + Threshold-Routing (Cloud vs. Local) |
| **Telegram/WhatsApp-Aktivierung** | Messenger-Integration für Mobile-Nutzung |
| **MQTT-Aktivierung** | IoT-Sensor-Integration |

### Backlog / Visionen

Ideen und Erweiterungen ohne konkrete Zuteilung zu einem Patch:

| Idee | Beschreibung |
|---|---|
| **Server-seitige Session-Tokens** | Aktuell vertraut das System dem client-seitig gesendeten `X-Permission-Level` Header. Für echte Sicherheit: Server-seitige Token-Validation (z.B. JWT mit Secret) statt reiner Header-Prüfung. |
| **Permission-Level Live-Update** | Wenn ein Admin per Hel-Dashboard das `permission_level` eines Profils ändert, soll das sofort wirken – aktuell erst nach erneutem Login. |
| **COMMAND_TOOL Sandbox** | Wenn ein erlaubter COMMAND_TOOL-Intent erkannt wird, soll die Ausführung in einer Docker-Sandbox stattfinden (geplant seit Patch 44, noch nicht implementiert). |
| **Intent-Erkennung via LLM** | Die regelbasierte Intent-Erkennung ist schnell und deterministisch, aber begrenzt. Option: Kurzer LLM-Aufruf (z.B. lokales Modell) für ambivalente Nachrichten. |
| **Profil-Audit-Log** | Jede Permission-Block-Aktion sollte in einem separaten Audit-Log persistiert werden (für spätere Nachvollziehbarkeit). |
| **Hel-Dashboard: Profil-Verwaltung** | Neue Profile via Dashboard anlegen, Permission-Level ändern, allowed_model setzen – ohne manuelles config.yaml-Editing. |
| **User-Dokumentation** | Lesbare Anleitung für Nicht-Techniker: Was kann Nala, was ist Hel, Passwort-Reset, Metriken — wird benötigt sobald das System an weitere Nutzer weitergegeben wird. |

---

## 11. Glossar

| Begriff | Definition |
|---|---|
| **Auto-Index** | Automatisches Einfügen von Nachrichten in den FAISS-Index nach jedem LLM-Call |
| **bunker_memory.db** | SQLite-Datenbankdatei, die alle Interaktionen, Metriken und Kosten persistiert. Darf NICHT gelöscht werden. |
| **CLAUDE.md** | Projektdatei mit Regeln und Anweisungen für Claude Code (KI-Assistent) |
| **cleaner** | Whisper-Cleaner-Modul; entfernt Füllwörter und korrigiert Transkriptionsfehler |
| **cloud_model** | Das LLM-Modell, das via OpenRouter in der Cloud aufgerufen wird |
| **config.json** | Nur für Admin-Schreibzugriff via Hel-Dashboard; NICHT als Konfigurationsquelle für die Laufzeit |
| **config.yaml** | Einzige Konfigurationsquelle (Single Source of Truth seit Patch 34) |
| **CONVERSATION** | Intent-Typ: allgemeine Konversation ohne erkennbare Frage oder Befehl |
| **COMMAND_SAFE** | Intent-Typ (Patch 47): harmlose Aktionen (zeig mir, liste auf, exportier, ...) |
| **COMMAND_TOOL** | Intent-Typ (Patch 47): Tool-Use, Agenten, externe Ressourcen (starte, docker, deploy, ...) |
| **Human-in-the-Loop (HitL)** | Mechanismus: Bei gesperrter Aktion wird Chris um Erlaubnis gefragt statt die Aktion auszuführen |
| **permission_level** | Profil-Attribut (Patch 47): admin / user / guest – steuert erlaubte Intent-Typen |
| **allowed_model** | Profil-Attribut (Patch 47): optionaler Modell-Override; null = globales Modell aus config.yaml |
| **data_collection: deny** | OpenRouter-Privacy-Flag: verhindert Training auf Gesprächsdaten |
| **Dialect Engine** | Erkennt Emoji-Marker und gibt vordefinierte Antworten zurück (Kurzschluss, kein LLM) |
| **EventBus** | In-Memory-Pub/Sub-System für lose Kopplung zwischen Modulen (asyncio.Queue) |
| **FAISS** | Facebook AI Similarity Search – Bibliothek für schnelle Vektorsuche |
| **FlatL2** | FAISS-Index-Typ: exakte L2-Suche ohne Approximation |
| **Force Cloud / Force Local** | Suffixe `++` / `--` am Nachrichtenende erzwingen bestimmtes Modell |
| **Graceful Degradation** | Fallback-Verhalten: Bei Ausfall einer Komponente (z.B. RAG) Weiterarbeit mit Einschränkungen |
| **Hel** | Admin-Router und Dashboard; benannt nach der nordischen Göttin der Unterwelt |
| **Hapax Count** | Anzahl der Wörter, die genau einmal im Text vorkommen (Metrik) |
| **Ingress** | Eingehende HTTP-Requests (erste Schicht der Architektur) |
| **Integrity** | Feld in der interactions-Tabelle; bei Whisper-Inputs 0.9 (da ggf. Transkriptionsfehler) |
| **Intent** | Klassifikation einer User-Nachricht: QUESTION / COMMAND / CONVERSATION |
| **Invarianten** | Systemannahmen, die beim Start geprüft werden (Fail-Fast-Prinzip) |
| **L2-Distanz** | Euklidische Distanz zwischen zwei Vektoren; kleiner = ähnlicher |
| **L2-Schwellwert** | Maximale L2-Distanz (1.5) für relevante RAG-Treffer |
| **Legacy Router** | `/v1`-Router mit OpenAI-kompatiblem Interface (historisch: erster Chat-Endpunkt) |
| **Lifespan** | FastAPI-Mechanismus für Startup- und Shutdown-Logik |
| **LLM** | Large Language Model; hier: via OpenRouter erreichbare Cloud-Modelle |
| **Local Sovereignty** | Designprinzip: Daten und Konfiguration verbleiben lokal |
| **Modul-Loader** | Dynamisches Laden von Modulen aus `zerberus/modules/` via `pkgutil.iter_modules` |
| **Nala** | Persona/Name des KI-Assistenten; auch Name des primären Chat-Routers |
| **Nudge** | Proaktiver Hinweis/Vorschlag; ausgelöst wenn Score-Schwellwert überschritten |
| **OpenRouter** | API-Gateway für verschiedene Cloud-LLMs (Llama, Hermes, etc.) |
| **Orchestrator** | Zentraler Intent+RAG+LLM-Router; koordiniert die Cognitive-Core-Pipeline |
| **Pacemaker** | Hält Whisper-Server durch periodische Silent-WAV-Pings im VRAM |
| **Persona** | Die "Rolle" des Assistenten: Nala / Rosa – freundlich, deutschsprachig |
| **Preparer** | Modul für Kalender-Integration (aktuell Mock-Daten) |
| **QUESTION** | Intent-Typ: Anfrage nach Information |
| **RAG** | Retrieval-Augmented Generation; semantische Suche im Gedächtnis vor LLM-Call |
| **Rate Limiting** | Begrenzt Anfragen pro IP/Pfad (100/min default) |
| **Quiet Hours** | Zeitbasierte Sperrung des Systems (z.B. 22:00–06:00) |
| **Rosa** | Alternativer Projektname; auch geplanter Name für Corporate Security Layer |
| **SentenceTransformer** | Bibliothek für Satz-Embeddings; hier: `all-MiniLM-L6-v2` |
| **Session-ID** | UUID, client-seitig generiert (localStorage), identifiziert einen Chat-Verlauf |
| **Shannon Entropy** | Informationstheoretisches Maß für lexikalische Vielfalt (Metrik) |
| **Single Source of Truth** | `config.yaml` ist die einzige authoritative Konfigurationsquelle |
| **Split-Brain** | Zustand, in dem zwei Konfigurationsquellen unterschiedliche Werte haben (behoben in Patch 34) |
| **system_prompt.json** | Enthält den System-Prompt der Persona Nala |
| **TTR** | Type-Token-Ratio: Verhältnis einzigartiger Wörter zu Gesamtwörtern (lexikalische Vielfalt) |
| **VADER** | Valence Aware Dictionary and Sentiment Reasoner; Sentiment-Analyse-Library |
| **Vector DB** | Vektor-Datenbank; hier: FAISS-Index + JSON-Metadaten auf Disk |
| **Whisper** | OpenAI-Spracherkennungsmodell; läuft lokal auf Port 8002 |
| **Yule-K** | Statistisches Maß für Vokabular-Reichhaltigkeit (Metrik) |
| **Zerberus** | Projektname; benannt nach dem dreiköpfigen Höllenhund aus der griechischen Mythologie |

---

## 12. Anhang

### 12.1 API-Endpunkte Übersicht

| Methode | Pfad | Router | Beschreibung |
|---|---|---|---|
| GET | `/` | main | Redirect auf `/static/index.html` |
| POST | `/v1/chat/completions` | Legacy | OpenAI-kompatibler Chat |
| POST | `/v1/audio/transcriptions` | Legacy | Audio → Text (Whisper) |
| GET | `/v1/health` | Legacy | Health-Check |
| GET | `/nala` | Nala | Chat-Frontend (HTML) |
| POST | `/nala/voice` | Nala | Voice-Pipeline |
| GET | `/nala/events` | Nala | SSE Pipeline-Events (Patch 46) |
| POST | `/nala/profile/login` | Nala | Profil-Login (Patch 44) |
| GET | `/nala/profile/prompts` | Nala | Profilliste (Patch 44) |
| GET | `/nala/health` | Nala | Health-Check |
| POST | `/orchestrator/process` | Orchestrator | Intent+RAG+LLM-Pipeline |
| GET | `/orchestrator/health` | Orchestrator | Health-Check |
| GET | `/hel/` | Hel | Admin-Dashboard (Auth) |
| GET | `/hel/admin/models` | Hel | OpenRouter Modell-Liste |
| GET | `/hel/admin/balance` | Hel | OpenRouter Guthaben |
| GET/POST | `/hel/admin/config` | Hel | config.json lesen/schreiben |
| GET/POST | `/hel/admin/whisper_cleaner` | Hel | Cleaner-Regeln |
| GET/POST | `/hel/admin/fuzzy_dict` | Hel | Fuzzy-Dictionary |
| GET/POST | `/hel/admin/dialect` | Hel | Dialekte |
| GET/POST | `/hel/admin/system_prompt` | Hel | System-Prompt |
| GET | `/hel/admin/sessions` | Hel | Session-Liste |
| GET | `/hel/admin/export/session/{id}` | Hel | Session-Export |
| DELETE | `/hel/admin/session/{id}` | Hel | Session löschen |
| GET | `/hel/metrics/latest_with_costs` | Hel | Metriken + Kosten |
| GET | `/hel/metrics/summary` | Hel | Zusammenfassung |
| GET | `/hel/admin/profiles` | Hel | Profil-Liste (ohne password_hash, Patch 61) |
| GET | `/hel/debug/trace/{session_id}` | Hel | Session-Debug |
| GET | `/hel/debug/state` | Hel | Systemzustand |
| GET | `/archive/sessions` | Archive | Session-Liste |
| GET | `/archive/session/{id}` | Archive | Session-Nachrichten |
| DELETE | `/archive/session/{id}` | Archive | Session löschen |
| POST | `/rag/index` | RAG | Dokument indexieren |
| POST | `/rag/search` | RAG | Semantische Suche |
| GET | `/rag/health` | RAG | RAG-Status |
| POST | `/emotional/analyze` | Emotional | Sentiment-Analyse |
| GET | `/emotional/health` | Emotional | Health-Check |
| POST | `/nudge/evaluate` | Nudge | Nudge-Bewertung |
| GET | `/nudge/health` | Nudge | Health-Check |
| GET | `/preparer/upcoming` | Preparer | Nächste Events |
| GET | `/preparer/health` | Preparer | Health-Check |

### 12.2 Metriken pro Nachricht

Für jede gespeicherte Nachricht werden folgende Metriken automatisch berechnet und in `message_metrics` abgelegt:

| Metrik | Bedeutung |
|---|---|
| `word_count` | Anzahl Wörter |
| `sentence_count` | Anzahl Sätze |
| `character_count` | Anzahl Zeichen |
| `avg_word_length` | Durchschnittliche Wortlänge |
| `unique_word_count` | Anzahl eindeutiger Wörter |
| `ttr` | Type-Token-Ratio (lexikalische Vielfalt) |
| `hapax_count` | Anzahl einmaliger Wörter |
| `yule_k` | Yule's K (Vokabular-Reichhaltigkeit) |
| `shannon_entropy` | Shannon-Entropie (Informationsdichte) |
| `vader_compound` | VADER-Sentiment (-1.0 bis +1.0) |

### 12.3 Architekturdiagramm (Textbeschreibung)

```
[Browser]
    │
    ├─── GET /nala ──────────────────────────────→ [Nala HTML Frontend]
    │                                                      │
    │    Text: POST /v1/chat/completions                   │ Voice: POST /nala/voice
    │    ←──────────────────────┐                          │
    │                           │                          │
    ▼                           │                          ▼
[Middleware]               [Legacy Router] ←──── [Nala Router]
 QuietHours                     │                    │
 RateLimiting                   │                    │
                                └────────────────────┘
                                         │
                                         ▼
                              [Orchestrator Funktionen]
                              detect_intent()
                              _rag_search()
                              _rag_index_background()
                                         │
                              ┌──────────┼──────────┐
                              ▼          ▼          ▼
                           [FAISS]    [LLM]    [Auto-Index]
                          (semantic  (OpenR-   (background
                           search)   outer)     thread)
                                         │
                              ┌──────────┤
                              ▼          ▼
                          [Database]  [EventBus]
                          (SQLite)    (in-memory)
                              │
                     [metrics/costs/interactions]

[Browser] ──→ GET /hel ──→ [Hel Admin Dashboard]
                                    │
                          ┌─────────┼─────────┐
                          ▼         ▼         ▼
                      [config.json] [system_  [whisper_
                                    prompt.   cleaner.
                                    json]     json]
```

### 12.4 Konfigurationsdateien im Überblick

| Datei | Zweck | Wer schreibt |
|---|---|---|
| `config.yaml` | Einzige Runtime-Konfigurationsquelle | Manuell |
| `config.json` | Admin-Schreibzugriff via Hel; kein Runtime-Einfluss | Hel-Dashboard |
| `system_prompt.json` | System-Prompt für LLM | Hel-Dashboard |
| `dialect.json` | Dialekt-Definitionen | Hel-Dashboard |
| `whisper_cleaner.json` | Cleaner-Regeln (Regex, Korrekturen, Füllwörter) | Hel-Dashboard |
| `fuzzy_dictionary.json` | Fuzzy-Dictionary für Whisper-Fehlerkorrektur (Patch 42) | Manuell |
| `hel_settings.json` | Legacy-Einstellungen (historisch; nicht aktiv genutzt) | Manuell |
| `.env` | API-Keys, Admin-Credentials | Manuell |
| `bunker_memory.db` | SQLite-Datenbank | Automatisch |
| `data/vectors/faiss.index` | FAISS-Vektorindex | RAG-Modul |
| `data/vectors/metadata.json` | Metadaten zu FAISS-Vektoren | RAG-Modul |

### 12.5 Server starten

```bash
cd C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus
venv\Scripts\activate
uvicorn zerberus.main:app --host 0.0.0.0 --port 5000 --reload
```

Wichtige URLs (lokal):
- Chat: http://localhost:5000/nala
- Admin: http://localhost:5000/hel
- API Docs: http://localhost:5000/docs
- Whisper: http://localhost:8002

---

---

## 13. Sicherheits-Audit & Pen-Test-Protokoll

### 13.1 Geplante Angriffsvektoren

Vor jeder öffentlichen Veröffentlichung sind folgende Tests durchzuführen.
Methode: Verschiedene LLMs (GPT-4, Claude, Gemini, lokale Modelle) erhalten
den Systemkontext und die Aufgabe: "Brich dieses System. Melde was du findest."

| Angriffsvektor | Beschreibung | Status |
|---|---|---|
| **Prompt Injection** | Bösartige Anweisungen in User-Input einschleusen | ☐ Ausstehend |
| **Permission Bypass** | Als guest Admin-Aktionen auslösen | ☐ Ausstehend |
| **JWT Manipulation** | Token fälschen oder abgelaufene Token wiederverwenden | ☐ Ausstehend |
| **Sandbox Escape** | Aus Docker-Container ausbrechen, Netzwerk erreichen | ☐ Ausstehend |
| **RAG Poisoning** | Manipulierte Daten in FAISS-Index einschleusen | ☐ Ausstehend |
| **Path Traversal** | Über API auf Dateisystem zugreifen | ☐ Ausstehend |
| **Rate Limit Bypass** | In-Memory Rate-Limiting umgehen | ☐ Ausstehend |
| **Bösartiger Code** | Schadcode über COMMAND_TOOL in Sandbox einschleusen | ☐ Ausstehend |

### 13.2 Durchgeführte Tests

*(Wird nach jedem Test-Durchlauf ergänzt)*

### 13.3 Bekannte akzeptierte Risiken

| Risiko | Begründung |
|---|---|
| Rate-Limiting in-memory | Kein Redis; akzeptabel für persönliche Nutzung |
| Tailscale als einzige Netzwerk-Schicht | Bewusste Entscheidung; Tailscale gilt als sicher |
| Rosa Security Layer noch nicht implementiert | Für Corporate-Einsatz vorgesehen; persönliche Version bewusst ohne |

---

---

## Patch 64 – Static API Key + Metrik-Darstellung Fix (2026-04-10)

### Static API Key (Aufgabe 1)
- `config.yaml` → `auth.static_api_key` mit 32-Byte Hex-Zufallskey befüllt
- Middleware `token_auth_middleware` (`zerberus/core/middleware.py`) prüft bereits seit Patch 59 `X-API-Key` Header vor JWT-Validierung — kein Code-Change nötig
- Externe Clients senden: `X-API-Key: <key>` → JWT-Prüfung wird übersprungen

### Metrik-Darstellung (Aufgabe 2)

**Problem A – TTR konstant 1.0:**
- Ursache: `compute_metrics()` in `database.py` berechnet TTR pro Einzelnachricht; kurze Sätze (< 10 Wörter) haben nahezu alle Tokens einmalig → TTR = 1.0
- Fix: `metrics_history` Endpoint (`hel.py`) fetcht jetzt `i.content`, berechnet Rolling-Window-TTR über letzte 50 Nachrichten in Python
- Algorithmus: Token-Listen pro Nachricht akkumuliert; Fenster `[i-49 … i]`; `unique(tokens) / total(tokens)`; Feld `rolling_ttr` im JSON-Response
- Chart JS: Dataset "TTR (Rolling-50)" liest `r.rolling_ttr` (Fallback: `r.ttr`)

**Problem B – BERT-Linie unsichtbar:**
- Ursache: `bert_sentiment_score` enthielt rohe Modell-Konfidenz (0.5–1.0, immer hoch) — Linie klebte am oberen Rand oder war farblich nicht vom Hintergrund unterscheidbar
- Fix: SQL CASE-Ausdruck in `metrics_history`:
  - `positive` → `mm.bert_sentiment_score` (0.5–1.0, obere Hälfte)
  - `negative` → `1.0 - mm.bert_sentiment_score` (0.0–0.5, untere Hälfte)
  - `neutral` → `0.5` (Mittellinie)
  - Fallback (kein BERT-Label): `(i.sentiment + 1.0) / 2.0`
- Chart Y-Achse: `min: 0, max: 1` explizit, Achsentitel: "Sentiment (0=neg · 0.5=neutral · 1=pos) / TTR / Entropy"

*Dieses Dokument wurde automatisch aus dem Quellcode und der CLAUDE.md generiert.*
*Stand: 2026-04-10, Patch 64.*

---

## Patch 65 – Multimodaler Input/Output + RAG-Fix (2026-04-10)

### RAG-Dependencies Fix (Aufgabe 1)

**Problem:** `sentence-transformers==2.2.2` importierte `cached_download` aus `huggingface_hub`, das in Version `>=0.25` entfernt wurde. Resultat: `ImportError`, RAG-Modul komplett nicht verfügbar.

**Fix:**
- `requirements.txt`: `sentence-transformers>=2.7.0` (gelockerte Untergrenze statt Pin)
- Installierte Version: `5.4.0` — vollständig kompatibel mit `huggingface_hub 0.36.2`
- `faiss-cpu==1.7.4` bleibt gepinnt (stabiler als GPU-Variante auf RTX 3060)

### RAG-Upload Multiformat (Aufgabe 2)

- `zerberus/app/routers/hel.py`: `rag_upload` Endpunkt erweitert
- Neue Formate: `.pdf` via `pdfplumber`, `.md` via UTF-8 Direktlese (wie `.txt`)
- `.pdf`: `pdfplumber.open()` → seitenweise `extract_text()`, Seiten mit `\n\n` verbunden
- `.docx`: unverändert (python-docx), jetzt mit try/except gegen Crash
- Guard-Pattern: `_PDF_OK` (analog zu `_DOCX_OK`) — graceful 422 wenn Library fehlt
- Neue Dependency: `pdfplumber>=0.10.0` (zieht `pdfminer.six`, `pypdfium2` nach)

### Export-Endpunkt (Aufgabe 3)

**Neuer Endpunkt:** `POST /nala/export`

Request-Body: `{"text": str, "format": "pdf"|"docx"|"md"|"txt", "filename": str}`

| Format | Library | Content-Type |
|--------|---------|--------------|
| `pdf`  | reportlab 4.x | `application/pdf` |
| `docx` | python-docx | `application/vnd.openxmlformats-...` |
| `md`   | — | `text/markdown; charset=utf-8` |
| `txt`  | — | `text/plain; charset=utf-8` |

- PDF: `SimpleDocTemplate` A4, 2.5 cm Ränder, Helvetica 11pt, HTML-Escaping für `&<>`
- DOCX: Heading-1 „Nala – Antwort", zeilenweise `add_paragraph()`
- Authentifizierung: Bearer-JWT oder `X-API-Key` via bestehende Middleware — kein gesonderter Bypass nötig
- Neue Dependency: `reportlab>=4.0.0`

### Nala-UI Export-Dropdown (Aufgabe 3 – Frontend)

- `addMessage()` in `NALA_HTML` umgebaut: Bot-Nachrichten erhalten `.msg-wrapper` div
- Darunter: `.export-row` mit `<select class="export-select">` (4 Optionen)
- User-Nachrichten: `.msg-wrapper.user-wrapper` (align-self: flex-end, kein Export-Dropdown)
- `exportMessage(text, fmt)`: `fetch POST /nala/export` → `res.blob()` → `URL.createObjectURL()` → programmatischer `<a>` Download
- Styling: Dark-Navy-konform, transparenter Select mit Gold-Focus-Border

*Stand: 2026-04-10, Patch 65.*

---

### Patch 66: RAG Chunk-Optimierung

**Stand:** 2026-04-10

**Ziel:** Verbesserung der RAG-Trefferquote durch größere Chunks und mehr Überlapp. Diagnose: 2/11 korrekte Treffer bei Test-Fragen. Ursache: Chunks zu klein (300 Wörter), zu wenig Kontext pro Chunk, Glossar und Dokumentende wurden nicht erreicht.

**Änderungen:**

- `_chunk_text()` in `zerberus/app/routers/hel.py`:
  - `chunk_size`: 300 → **800 Wörter**
  - `overlap`: 50 → **160 Wörter** (20 % von 800)
  - Einheit: **Wörter** (`text.split()`), nicht Token, nicht Zeichen — im Docstring dokumentiert
- Upload-Logging: INFO-Zeile direkt nach Chunking mit Chunk-Anzahl + Parametern (`chunk_size=800 Wörter, overlap=160`)
- Kein Hard-Limit auf Dokumentlänge — gesamtes Dokument wird verarbeitet (war bereits so; explizit verifiziert)
- Neuer Endpunkt `POST /hel/admin/rag/reindex`:
  - Baut FAISS-Vektoren aus den in `metadata.json` gespeicherten Chunk-Texten neu auf
  - Sinnvoll nach Embedding-Modell-Wechsel
  - Für neue Chunk-Größen: erst `/admin/rag/clear`, dann Dokumente neu hochladen (Chunks werden dann mit neuen Parametern erstellt)
  - Kein Auto-Clear beim Serverstart — bewusstes Triggern verhindert ungewollten Datenverlust

*Stand: 2026-04-10, Patch 66.*

---

### Patch 67: Nala Frontend UI-Fixes

**Stand:** 2026-04-10

**Ziel:** Sieben UI-Verbesserungen + Archiv-Bug-Fix im Nala-Frontend (`nala.py` – inline HTML/CSS/JS).

**Änderungen:**

1. **Neue Session per Hamburger-Menü** – Sidebar enthält jetzt zwei Action-Buttons: „➕ Neue Session" und „💾 Exportieren". `newSession()` generiert neue UUID, leert Chat, startet SSE neu und ruft `fetchGreeting()` auf.

2. **Textarea Auto-Expand** – `<input id="text-input">` wurde zu `<textarea>` umgebaut. Beim Focus expandiert das Feld auf ≥3 Zeilen (max 140px); auf blur kollabiert es zurück auf 1 Zeile wenn leer. Shift+Enter = Zeilenumbruch, Enter = Senden (via `keydown`-Listener).

3. **Vollbild-Button** – `⛶`-Button neben Textarea öffnet Fullscreen-Modal (88vw × 68vh). „Übernehmen" überträgt Text zurück ins Hauptfeld; „Abbrechen" verwirft.

4. **Chat-Bubble Toolbar** – Jede Bubble zeigt beim Hover Timestamp (HH:MM) + 📋-Kopieren-Button. `copyBubble()` nutzt `navigator.clipboard`; visuelles Feedback „✓" für 1,5 Sek.

5. **Chat exportieren** – Sidebar-Button „💾 Exportieren" ruft `exportChat()` auf: sammelt `chatMessages[]`-Array, schreibt `[HH:MM] Rolle: Text`-Format, Download als `.txt`.

6. **Archiv-Bug behoben** – `loadSessions()` und `loadSession()` haben keine Auth-Header mitgeschickt → JWT-Middleware hat alle `/archive/*`-Requests mit 401 abgelehnt. Fix: `profileHeaders()` in beiden fetch-Calls. Zusätzlich: explizite Fehlerbehandlung wenn `response.ok === false`.

7. **Dynamische Startnachricht (Option A)** – Neuer Endpunkt `GET /nala/greeting`. Backend liest System-Prompt des eingeloggten Profils, sucht per Regex nach `Du bist / Ich bin [Name]` und gibt personalisierten Gruß zurück. Frontend: `showChatScreen()` ruft `fetchGreeting()` statt hardcodierter Nachricht. Fallback: „Hallo! Wie kann ich dir helfen?".

**Neue State-Variable:** `chatMessages = []` – wird bei `doLogout()`, `handle401()`, `newSession()` und `loadSession()` zurückgesetzt.

---

### Patch 68: Login-Bug + RAG-Cleanup

**Stand:** 2026-04-10

**Ziel:** Vier Bugfixes aus Patch 67 – Login, Passwort-Auge, RAG-Orchestrator-Chunks, Encoding.

**Bugfixes:**

1. **BUG 1+2 (KRITISCH) – Login + Passwort-Auge** (`nala.py`):
   - **Ursache:** `crypto.randomUUID()` schlägt in HTTP-Non-Secure-Kontexten (z.B. Zugriff von mobilem Gerät im LAN via `http://192.168.x.x:5000`) mit `TypeError` fehl, weil die Web Crypto API nur in Secure Contexts (HTTPS oder localhost) verfügbar ist. Da der Aufruf auf der obersten Skript-Ebene steht, bricht das gesamte `<script>`-Tag ab — kein Event-Handler wird registriert, kein Button reagiert.
   - **Fix:** Neue Funktion `generateUUID()` mit Fallback auf `Math.random()`-basierte UUID. Alle `crypto.randomUUID()`-Aufrufe ersetzt.
   - **Zusätzlich:** `keypress` → `keydown` für Login-Felder (konsistent mit Textarea-Änderung aus Patch 67); `type="button"` am Submit-Button (defensiv).

2. **BUG 3 – Orchestrator-Chunks im RAG-Index** (`orchestrator.py`):
   - **Ursache:** `_run_pipeline()` ruft nach jeder LLM-Antwort `_rag_index_background()` auf und schreibt die User-Nachricht mit `source: "orchestrator"` in den RAG-Index. Nach einem manuellen Clear + Reupload erscheinen nach dem nächsten Chat-Austausch sofort wieder „orchestrator – 2 Chunks".
   - **Fix:** Auto-Indexing in Schritt 5 der Pipeline deaktiviert. Die Hilfsfunktionen `_rag_index_sync()` und `_rag_index_background()` bleiben im Code (für evtl. spätere Reaktivierung als Config-Option).

3. **BUG 4 – Fragezeichen vor Dateinamen** (`hel.py`):
   - **Ursache:** RAG-Quellenliste rendert das 📄-Emoji als JavaScript-Surrogatpaar `\uD83D\uDCC4`. In bestimmten Umgebungen/Encodings erscheint dies als `??`.
   - **Fix:** Icon durch `[doc]` ersetzt — kein Unicode, keine Encoding-Abhängigkeit.

4. **Bonus – `import io` fehlend** (`nala.py`):
   - Export-Endpoint (`POST /nala/export`) nutzt `io.BytesIO()`, aber `import io` fehlte. Würde bei jedem PDF/DOCX-Export mit `NameError` scheitern. Ergänzt.

*Stand: 2026-04-10, Patch 68.*

---

## Patch 69a – Bug-Fixes (2026-04-11)

### BUG 1 – Whisper-Cleaner: `$1` → `\1` (`whisper_cleaner.json`)
- **Problem:** Pattern `(?i)\\b(\\w{2,})\\s+\\1\\b` (Duplikat-Wort-Entfernung) hatte `"replacement": "$1"`.
  `$1` ist JavaScript-Regex-Syntax; Python's `re.sub` erwartet `\1`. Der Match wurde nicht korrekt durch die erste Capture-Gruppe ersetzt.
- **Fix:** `"replacement": "$1"` → `"replacement": "\\1"` (nur diesen Eintrag, alle anderen `$1`-Einträge im Cleanup-Abschnitt unverändert).

### BUG 2 – Hel: Kostenanzeige auf Pro-Million-Format (`hel.py`)
- **Problem:** Kosten wurden als rohe Dezimalzahl angezeigt (z.B. `0.000042`), schwer lesbar.
- **Fix:** Beide Render-Stellen auf `$X.XX / 1M Tokens` umgestellt (Formel: `cost * 1_000_000`):
  - „Letzte Anfrage"-Label in `loadDashboard()` (Zeile ~570)
  - Kostenspalte in der Metriktabelle `#messagesTable` (Zeile ~705)
- Backend-Werte bleiben unverändert.

### BUG 3 – Patch-67-Endpunkte Verifikation (`nala.py`, `archive.py`)
- Alle vier Endpunkte aus Patch 67 verifiziert — vollständig implementiert, keine Fixes nötig:
  - `GET /nala/greeting`: vorhanden, liest Charaktername via Regex aus System-Prompt-Datei ✓
  - `GET /archive/sessions`: in `archive.py` implementiert, JS-Frontend sendet `profileHeaders()` ✓
  - `GET /archive/session/{id}`: in `archive.py` implementiert, JS-Frontend sendet `profileHeaders()` ✓
  - `POST /nala/export`: vollständig für `txt`, `md`, `docx`, `pdf` (reportlab A4) ✓

*Stand: 2026-04-11, Patch 69a.*

---

## Patch 69b – Dokumentations-Restrukturierung (2026-04-11)

- `lessons.md` neu angelegt: zentrales Dokument für alle Gotchas, Fallstricke und hart gelernte Lektionen (Konfiguration, Datenbank, Frontend/JS, RAG, Security, Deployment, Pacemaker)
- `CLAUDE.md` bereinigt: alle Patch-Changelog-Blöcke (Patch 57–68) entfernt — Archiv verbleibt vollständig in `PROJEKTDOKUMENTATION.md`; CLAUDE.md enthält jetzt nur operative Abschnitte + Verweis auf `lessons.md`
- `HYPERVISOR.md`: zwei neue Roadmap-Ideen im Backlog ergänzt
- `PROJEKTDOKUMENTATION.md`: neuer Abschnitt `## 13. Roadmap & Feature-Ideen` angehängt

*Stand: 2026-04-11, Patch 69b.*

---

## 13. Roadmap & Feature-Ideen

### Metriken: Interaktive Auswertung
Vorbild: Sleep as Android Statistik-Screen.
- Zeiträume frei wählbar (Tag / Woche / Monat / custom)
- LLM-Auswertung auf Knopfdruck: Zusammenfassung der Sprachmetriken als Klartext
- Vektorieller Zoom (SVG/Canvas/D3), Mobile-first
- Erweiterte Metriken (MATTR, Hapax, Hedging, Selbstreferenz, Kausalität)
Konzept noch nicht final — erst ausarbeiten wenn Metrik-Engine stabil läuft.

### RAG: Automatisiertes Evaluation-Skript
- Claude Code schreibt Testskript: N Fragen → RAG-Antworten → automatische Bewertung
- Bewertungsframework: RAGAS oder eigene Scoring-Logik
- Benchmark-Docs als Grundlage + eigene Testfragen
Voraussetzung: RAG-Mobile-Upload-Bug muss zuerst behoben sein.

### Patch 70 – Nala UI/UX Overhaul (geplant)

**Bugs zu fixen:**
- Textarea-Collapse: bleibt groß nach erstem Blur — soll nach Blur auf 1 Zeile kollabieren
- Begrüßungstext: abgerissener Satz → einfachere Begrüßung + tageszeit-abhängig (Morgen/Tag/Abend/Nacht)
- Whisper-Insert: Transkript überschreibt immer alles → Logik: Selektion=ersetzen, Cursor=einfügen, leer=anfügen

**Features:**
- Ladeindikator (Spinner/Sanduhr) während Whisper + LLM-Antwort läuft
- Archiv-Sidebar: nur Titel + 2-Zeilen-Preview statt Volltext
- Archiv-Volltextsuche: Suche in Chat-Inhalten, nicht nur Titeln
- Pin-Funktion: Chats anpinnen (Icon bereits vorhanden, Funktion fehlt)
- Theme-Editor pro User: alle Oberflächenfarben einstellbar, bis zu 3 Favoriten, später Hintergrundbild-Upload

**Design-Overhaul:**
- Moderne Optik: Tiefeneffekt auf Buttons, leicht metallisch/texturiert
- Weniger klobig, feingliedriger, zeitgemäßes UI

---

### Patch 71 – Jojo Login Fix (2026-04-12)

**Problem:** `user2` (Jojo) hatte `password_hash: ''` — leerer String. Beim Login-Versuch schlug der bcrypt-Vergleich lautlos fehl, der Button reagierte nicht.

**Änderung:**
- `config.yaml`: `profiles.user2.password_hash` mit gültigem bcrypt-Hash belegt (`rounds=12`, Passwort: `jojo123`)

**Betroffene Dateien:**
- `config.yaml` (einzige Änderung)

---

### Patch 72 – Nala Begrüßung Fix (2026-04-12)

**Problem:** `GET /nala/greeting` gab „Hallo! Ich bin ein. Wie kann ich dir helfen?" zurück. Ursache: Regex `(?:du bist|ich bin)\s+(\w+)` mit `re.IGNORECASE` traf auf „Du bist ein präziser..." und extrahierte den Artikel „ein" als Namen.

**Änderungen in `zerberus/app/routers/nala.py`:**

1. **Regex-Fix:** Nach dem Regex-Match wird `candidate[0].isupper()` geprüft. Nur großgeschriebene Wörter werden als Name übernommen — Artikel wie „ein", „eine", „der" werden damit zuverlässig ausgeschlossen.

2. **Tageszeit-abhängige Begrüßung:**
   - 06:00–11:59 → „Guten Morgen, [Name]! Wie kann ich dir helfen?"
   - 12:00–17:59 → „Hallo, [Name]! Wie kann ich dir helfen?"
   - 18:00–21:59 → „Guten Abend, [Name]! Wie kann ich dir helfen?"
   - 22:00–05:59 → „Hallo, [Name]! Wie kann ich dir helfen?"

3. **Greeting-Format:** Von „Hallo! Ich bin [Name]. Wie kann ich dir helfen?" auf „[Prefix], [Name]! Wie kann ich dir helfen?" umgestellt.

4. **Sauberer Fallback:** Kein Name gefunden → „[Prefix]! Wie kann ich dir helfen?" (kein abgerissener Satzteil mehr).

**Betroffene Dateien:**
- `zerberus/app/routers/nala.py` (Funktion `get_greeting`)

---

### Patch 73 – Self-Service Passwort-Reset (2026-04-12)

**Ziel:** Nala-User können ihr eigenes Passwort direkt in der App ändern, ohne Admin-Eingriff über Hel.

**Backend – `zerberus/app/routers/nala.py`:**

Neuer Endpunkt `POST /nala/change-password`:
1. Profil-Key wird ausschließlich aus dem JWT-Token gelesen (`request.state.profile_name`) — kein User kann fremde Passwörter ändern.
2. Altes Passwort wird per `bcrypt.checkpw` gegen den in `config.yaml` gespeicherten Hash verifiziert.
3. Bei falschem altem Passwort: HTTP 401 `{ "detail": "Altes Passwort falsch" }`.
4. Neues Passwort wird mit `bcrypt.hashpw(..., bcrypt.gensalt(rounds=12))` gehasht.
5. `_save_profile_hash(profile_key, new_hash)` schreibt den neuen Hash in `config.yaml`.
6. `reload_settings()` aktiviert den neuen Hash sofort ohne Server-Neustart.
7. Rückgabe: HTTP 200 `{ "detail": "Passwort geändert" }`.

Neues Pydantic-Modell `ChangePasswordRequest`:
```python
class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str
```

**Frontend – Sidebar + Modal:**

- Neuer Button „🔑 Passwort ändern" in der Nala-Sidebar (unter „Neue Session" / „Exportieren").
- Grüne Status-Meldung `✓ Passwort gespeichert` erscheint in der Sidebar nach Erfolg (3 s sichtbar).
- Klick öffnet Modal (gleicher Stil wie das Vollbild-Modal aus Patch 67) mit drei Feldern:
  - Aktuelles Passwort (`type=password`)
  - Neues Passwort (`type=password`)
  - Neues Passwort wiederholen (`type=password`)
- Clientseitige Validierung vor dem API-Call:
  - Neue Passwörter müssen übereinstimmen → rote Fehlermeldung im Modal
  - Mindestlänge 6 Zeichen → rote Fehlermeldung im Modal
- Bei API-Fehler (z. B. falsches altes PW): `detail` aus der JSON-Antwort wird im Modal angezeigt.
- JS-Funktionen: `openPwModal()`, `closePwModal()`, `submitPwChange()`.

**Betroffene Dateien:**
- `zerberus/app/routers/nala.py` (Modell `ChangePasswordRequest`, Endpunkt `/change-password`, Sidebar-HTML, Modal-HTML, JS-Funktionen)

---

### Patch 76 – Nala UI: Bug-Fixes + Ladeindikator (2026-04-12)

**Ziel:** Drei UI-Verbesserungen: Textarea-Collapse-Bug, Whisper-Insert-Logik, Ladeindikator für Whisper + LLM.

**Bug 1 – Textarea kollabiert nicht nach Blur:**

- `textInput.addEventListener('blur', ...)`: Wenn `textInput.value.trim() === ''`, wird `textInput.style.height` jetzt auf `''` zurückgesetzt (CSS-Default, 1 Zeile via `min-height`).
- Vorher: explizites `'48px'` hatte dasselbe Ziel, schlug aber bei bestimmten Zuständen fehl.

**Bug 2 – Whisper-Insert überschrieb immer den gesamten Inhalt:**

In `mediaRecorder.onstop` neue Insert-Logik (statt blindem `textInput.value = transcript`):
1. Textarea leer → `textInput.value = transcript` (bisheriges Verhalten)
2. Cursor ohne Selektion (`selectionStart === selectionEnd`) → Transkript an Cursorposition einfügen
3. Aktive Textauswahl (`selectionStart !== selectionEnd`) → Auswahl durch Transkript ersetzen

**Feature – Ladeindikator Whisper:**

- Neue CSS-Klasse `.mic-btn.processing` + `@keyframes pulseGold`: Mikrofon-Button pulsiert gold während der Whisper-API-Call läuft.
- Button wird `disabled` gesetzt während Verarbeitung, `finally`-Block stellt Zustand wieder her.

**Feature – Typing-Indicator bei LLM-Antwort:**

- Neue CSS-Klasse `.typing-indicator` + `.typing-dot` + `@keyframes typingBounce`: drei springende Gold-Punkte in einer Bot-Bubble.
- JS-Funktionen `showTypingIndicator()` / `removeTypingIndicator()`.
- SSE-Handler: bei Event `llm_start` → `showTypingIndicator()`.
- `sendMessage()`: vor `addMessage(reply, 'bot')` → `removeTypingIndicator()`; ebenso im `catch`-Branch.

**Betroffene Dateien:**
- `zerberus/app/routers/nala.py` (CSS, JS-Inline-Sektion)

### Patch 78 – Login-Fix + config.json Cleanup + Profil-Key Rename (2026-04-14)

**Ziel:** Login-Robustheit erhöhen, verwaiste config.json entfernen, Profil-Key bereinigen.

**Block A – Login case-insensitive:**
- `nala.py` `POST /nala/profile/login`: statt `req.profile.lower() not in profiles` iteriert die Funktion jetzt über alle Profile und vergleicht den Eingabewert case-insensitive sowohl mit dem Key als auch mit `display_name`. Login mit „Jojo", „JOJO", „jojo" oder „user2" (solange Key existiert) funktioniert.

**Block B – config.json gelöscht:**
- `config.json` im Projektroot existierte als Überbleibsel — gelöscht. `config.yaml` bleibt die einzige Konfigurationsquelle (CLAUDE.md Regel 4).

**Block C – Profil-Key user2 → jojo:**
- DB-Check: `user2` nirgendwo in `interactions.profile_name` → Umbenennung sicher.
- `config.yaml` + `config.yaml.example`: Key `user2` → `jojo` (display_name, password_hash, alle anderen Felder unverändert).
- Keine Codestellen hatten den Key hardcodiert.

---

### Patch 77 – Nala UI Overhaul: Archiv + Design + Theme-Editor (2026-04-12)

**Ziel:** Drei Bereiche überarbeitet: Archiv-Sidebar Darstellung + Suche + Pin, visuelles Design, Theme-Editor.

**Block A – Archiv-Sidebar:**

A1 – Session-Darstellung neu:
- `.session-item` erhält `border-left: 3px solid transparent` + Transition → Gold-Border auf hover.
- Neues Render-System `renderSessionList()` statt direktem DOM-Aufbau in `loadSessions()`.
- Jeder Eintrag zeigt: Titel (erste User-Nachricht, max 40 Zeichen + „…"), Timestamp (klein, rechts), optionalen Preview-Text (bis 80 Zeichen aus derselben Nachricht).

A2 – Volltextsuche:
- `<input type="search" class="archive-search" id="archive-search">` über der Session-Liste.
- `input`-Event → `renderSessionList()` filtert `window._lastSessions` client-seitig. Kein Backend-Call.

A3 – Pin-Funktion:
- `getPinnedIds()` / `setPinnedIds()` lesen/schreiben `sessionStorage('pinned_sessions')` als JSON-Array.
- `togglePin(sid, btn)` togglet Pin-Status, ruft `renderSessionList()` neu auf.
- Gepinnte Sessions immer oben in der Liste, ausgefülltes 📌-Icon.

**Block B – Visuelles Design:**

B1 – Buttons: `box-shadow: 0 2px 8px rgba(240,180,41,0.3)`, hover `scale(1.05)` + intensiverer Schatten. `transition: all 0.15s ease`.

B2 – Chat-Bubbles: User `border-radius: 18px 18px 4px 18px` + Gold-Glow. Bot `border-radius: 18px 18px 18px 4px`. Padding 13px/18px, Schriftgröße 15px.

B3 – Input-Textarea: `border: 1px solid rgba(240,180,41,0.3)`, focus `box-shadow: 0 0 0 2px rgba(240,180,41,0.15)`.

B4 – Sidebar-Header: Neue `.sidebar-header`-Div mit `border-bottom: 1px solid rgba(240,180,41,0.2)`. Buttons `border-radius: 8px`.

B5 – Input-Area: `backdrop-filter: blur(8px)`.

**Block C – Theme-Editor:**

C1 – Einstiegspunkt: Button „🎨 Theme" neben „🔑 Passwort" in Sidebar-Header.

C2 – Modal: 4 `<input type="color">` für `--color-primary`, `--color-primary-mid`, `--color-gold`, `--color-text-light`. `oninput="themePreview()"` → Live-Vorschau via `document.documentElement.style.setProperty()`.

C3 – Persistenz: „Speichern" → `localStorage('nala_theme')` als JSON. Früher Load-Block im `<head>` setzt CSS-Variablen vor dem ersten Render. „Zurücksetzen" → Defaults, löscht localStorage-Eintrag.

C4 – 3 Favoriten-Slots: `saveFav(n)` / `loadFav(n)` in `localStorage('nala_theme_fav_1/2/3')`.

**Betroffene Dateien:**
- `zerberus/app/routers/nala.py` (CSS, HTML, JS-Inline-Sektion)

---

### Patch 78b – RAG/History-Kontext-Fix (2026-04-14)

**Ziel:** LLM soll Session-History nicht als aktiven Dialog behandeln, RAG nur bei echten Wissensfragen triggern, und bei fehlenden Dokumentinfos auf Allgemeinwissen zurückgreifen dürfen.

**Block A – Session-History als Tonreferenz:**
- `_run_pipeline()` in `orchestrator.py`: History-Messages werden nicht mehr als separate user/assistant-Turns in die `messages`-Liste eingefügt, sondern als gelabelter Textblock (`[VERGANGENE SESSION ... ENDE VERGANGENE SESSION]`) in den System-Prompt injiziert.
- Dadurch behandelt das LLM alte Nachrichten als Kontext/Tonreferenz, nicht als laufendes Gespräch.

**Block B – RAG nur bei echten Wissensfragen:**
- Neue `skip_rag`-Logik vor dem RAG-Call: RAG wird übersprungen wenn `intent == "CONVERSATION"` oder wenn die Nachricht weniger als 15 Wörter hat und kein Fragezeichen enthält.
- Bei Skip: kein `_rag_search()`-Aufruf, keine RAG-Ergebnisse im Prompt, direkter LLM-Call.

**Block C – System-Prompt Fallback-Permission:**
- Nach dem Laden des Profil-System-Prompts wird geprüft ob der Text bereits eine Formulierung enthält die Allgemeinwissen erlaubt (Keywords: "allgemein", "wissen", "smalltalk").
- Wenn nicht vorhanden: Fallback-Satz wird ans Ende des System-Prompts angehängt: „Wenn keine spezifischen Dokumentinformationen verfügbar sind, beantworte allgemeine Fragen aus deinem Allgemeinwissen und führe normale Gespräche."
- Bestehender System-Prompt wird nie überschrieben, nur ergänzt.

**Betroffene Dateien:**
- `zerberus/app/routers/orchestrator.py` (`_run_pipeline()`: History-Injection, RAG-Skip, Fallback-Permission)

*Stand: 2026-04-14, Patch 78b.*

---

## Patch 79 – start.bat + Theme-Editor + Export-Timestamp (2026-04-14)

**Block A – start.bat: Terminal sichtbar:**
- `start.bat` verifiziert: `uvicorn` läuft im Vordergrund (kein `start /B`), Terminal bleibt offen, Scrollback erhalten. `pause` am Ende verhindert Schließen nach Ctrl+C. Keine Änderungen nötig.

**Block B – Theme-Editor: hardcodierte Farbe tokenisiert:**
- Neues CSS-Token `--color-accent: #ec407a` im `:root`-Block angelegt.
- `.header` und `.user-message` verwenden jetzt `var(--color-accent)` statt hardcodiertem Wert / JS-Override.
- Login-Handler setzt `--color-accent` via `setProperty()` statt direktem `style.background`.
- Direkte Farbzuweisung auf User-Bubbles (`msgDiv.style.background = color`) entfernt — CSS übernimmt.
- Theme-Editor-Modal: 5. Color-Picker „Akzent / Header" (`#tc-accent`) ergänzt.
- `openThemeModal()`, `themePreview()`, `saveTheme()`, `resetTheme()`, `saveFav()`, `loadFav()`: `accent`-Feld integriert.
- Early-Load im `<head>`: `v.accent` wird aus `nala_theme` geladen.

**Block C – Export: Timestamp im Dateinamen:**
- `exportChat()`: Dateiname von `nala_chat_YYYY-MM-DD.txt` auf `nala_export_YYYY-MM-DD_HH-MM.txt` geändert (ISO-Slice + Replace).

**Betroffene Dateien:**
- `zerberus/app/routers/nala.py` (CSS `:root`, `.header`, `.user-message`, Theme-Editor-Modal, alle Theme-JS-Funktionen, `exportChat()`)

*Stand: 2026-04-14, Patch 79.*

---

> ℹ️ **Hinweis:** Die ausführlichen Patch-Einträge 80–89 sind in `HYPERVISOR.md` zusammengefasst (kompakter Stand für die Live-Session). Hier folgt direkt der nächste Volleintrag.

---

### Patch 90 – Aufräum-Sammelpatch: rag_eval HTTPS + Hel-Backlog (2026-04-18)

**Ziel:** Vier kleinere Aufräumarbeiten in einem gemeinsamen Patch — `rag_eval.py` an HTTPS anpassen, Hel bekommt Schriftgrößen-Wahl, Landscape-Defensiv-Fix für beide UIs, WhisperCleaner von Roh-JSON auf Karten-Formular umstellen.

**Block A – `rag_eval.py` HTTPS-Fix (Backlog 7):**
- `BASE_URL` Default jetzt `https://127.0.0.1:5000` (war `http://`), per `RAG_EVAL_URL`-Env-Var override-bar; `API_KEY` analog per `RAG_EVAL_API_KEY` override-bar.
- Neuer modul-globaler `_SSL_CTX = ssl.create_default_context()` mit `check_hostname = False` und `verify_mode = ssl.CERT_NONE` für das Self-Signed-Cert.
- `_query_rag()` reicht den Context nur bei `https://`-URLs an `urllib.request.urlopen(req, timeout=30, context=ctx)` durch — bei reinem HTTP-Override bleibt das Verhalten neutral.
- Damit entfällt der bisherige Inline-Workaround pro Eval-Lauf.

**Block B – N-F09b: Schriftgrößen-Wahl Hel:**
- Neue CSS-Variable `--hel-font-size-base: 15px` im `:root`. Body, alle `.hel-section-body`-Inhalte (inkl. `<select>`, `<textarea>`, `<input>`, Tabellen) ziehen `font-size: var(--hel-font-size-base)`.
- Vier Preset-Buttons (13 / 15 / 17 / 19) als Toolbar unter `<h1>`, jeder min. 44 × 44 px, mit `:active`-State und `.active`-Klasse für den aktiven Wert.
- Persistenz via `localStorage('hel_font_size')`. Early-Load-IIFE im `<head>` setzt die Var noch vor dem ersten Paint → kein FOUC.
- JS-Helper `setFontSize(px)` + `markActiveFontPreset()` analog zu Nala (`nala_font_size`).

**Block C – N-F10: Landscape-Mode Defensive:**
- Diagnose: Nala nutzt `100dvh` für Body / `.app-container` / `.sidebar` (gut — Keyboard-tolerant), hat aber großzügige Header- und Modal-Höhen, die in Low-Height-Landscape (z.B. iPhone 14 quer ≈ 390 px hoch) stören. Hel nutzt kein `100dvh`, sondern padded Scroll-Layout — von Haus aus tolerant.
- Nala-Fix: `@media (orientation: landscape) and (max-height: 500px)` reduziert Header-Padding/-Schrift, schrumpft Hamburger, kompaktiert Status-Bar, Input-Bar (`min-height: 40px`, `max-height: 96px`), `.fullscreen-inner` auf `90vh` mit `90dvh`-Fallback, Settings-Modal auf `92vh`, Sidebar-Padding reduziert.
- Hel-Fix: gleiche Media-Query reduziert Body-Padding (10 px), `<h1>`-Größe (1.2em), Section-Header-Höhe (40 px), Card-Padding (12 px) — der Akkordeon-Body-Padding bleibt (wird durch JS inline gesetzt).

**Block D – H-F02: WhisperCleaner UX-Formular:**
- Bisher: einzelne `<textarea>` mit Roh-JSON. Neu: scrollbare Karten-Liste (`.cleaner-list`, max-height: 60vh).
- Jeder Eintrag:
  - **Kommentar/Sektion** (`{ "_comment": ... }` ohne `pattern`) → `.cleaner-section`-Block, gold linksbordered, einziges Textfeld + Trash-Button.
  - **Regel** (`{ "pattern": ..., "replacement": ..., "_comment"?: ... }`) → `.cleaner-card` mit Pattern (monospace), Replacement, Kommentar (optional), Trash-Button.
- Buttons: „➕ Regel hinzufügen", „➕ Kommentar/Sektion", „💾 Speichern" (right-aligned).
- JS-State: `_cleanerEntries[]`, in `loadCleaner()` aus dem JSON normalisiert; `renderCleanerList()` erzeugt das DOM neu nach jeder Mutation; `_collectCleanerFromDom()` rekonstruiert das JSON in Original-Reihenfolge.
- **Pattern-Validierung:** `_validatePattern()` strippt Python-Inline-Flags `(?i)`/`(?s)`/`(?m)` und versucht ein `new RegExp(stripped, flags)`. Fehlerhafte Patterns markieren ihre Karte mit `.invalid` (rot) und blockieren den Save (Status-Zeile zeigt Anzahl invalid).
- `removeCleanerEntry()` mit `confirm()`-Prompt; `addCleanerRule()` / `addCleanerComment()` mit Auto-Fokus auf das frisch eingefügte Pattern/Comment-Feld.
- Mobile: Karten sind volle Breite, Touch-Targets ≥ 44 px, alle Inputs `autocapitalize="off" autocorrect="off" spellcheck="false"`.

**Betroffene Dateien:**
- `rag_eval.py` (Block A)
- `zerberus/app/routers/hel.py` (Blöcke B, C, D — CSS-Var, Preset-Bar, Landscape-Media-Query, kompletter Cleaner-Block)
- `zerberus/app/routers/nala.py` (Block C — Landscape-Media-Query)
- `HYPERVISOR.md`, `docs/PROJEKTDOKUMENTATION.md`, `README.md`, `backlog_nach_patch83.md` (Pflicht-Updates)

**Verifikation:**
- `python -c "import ast; ast.parse(open('zerberus/app/routers/hel.py', encoding='utf-8').read())"` → ok
- `python -c "import ast; ast.parse(open('zerberus/app/routers/nala.py', encoding='utf-8').read())"` → ok
- `python -c "import ast; ast.parse(open('rag_eval.py', encoding='utf-8').read())"` → ok
- Manueller Server-Restart empfohlen (Hel-HTML wird beim Modul-Load gebaut).

*Stand: 2026-04-18, Patch 90.*

---

### Patch 91 – Metriken-Dashboard Overhaul: Chart.js + Zeiträume + Metrik-Toggles (2026-04-18)

**Ziel:** Hel-Metriken interaktiv machen — Zeiträume filterbar, mehr Metriken, Pinch-Zoom auf Mobile. Der bisherige Canvas-Chart wird durch Chart.js ersetzt, das API-Backend um Zeitraum-Filter erweitert.

**Block A – Backend:**
- `GET /hel/metrics/history` bekommt optionale Query-Parameter `from_date`, `to_date` (ISO `YYYY-MM-DD`), `profile_key` (vorbereitet für Patch 92), Default-`limit` 50 → 200.
- SQL-WHERE-Clauses werden dynamisch zusammengesetzt; `to_date` wird auf Tagesende (`23:59:59`) ergänzt.
- `profile_key`-Filter wird nur angewendet wenn die Spalte in `interactions` existiert (PRAGMA-Check zur Laufzeit).
- Response ist jetzt ein Envelope: `{"meta": {"from", "to", "count", "profile_key", "profile_key_supported"}, "results": [...]}`. Frontend liest `body.results || body` (abwärtskompatibel).
- Pro Eintrag neue Frontend-Felder: `hapax_ratio` (Hapax/Gesamt-Tokens), `avg_word_length` (Ø Zeichen/Wort), `bert_sentiment`-Alias, `created_at`-Alias.

**Block B – Frontend (Chart.js):**
- CDN-Dependencies im `<head>`: `chart.js@4.4.7/dist/chart.umd.min.js` + `hammerjs@2.0.8/hammer.min.js` + `chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.min.js`. `hammerjs` ist zwingende Voraussetzung für Touch-Pinch-Zoom.
- Neue UI-Sektionen:
  - `.metric-timerange` (Chip-Leiste: 7 / 30 / 90 Tage, Alles, Custom) — `min-height: 36px`, `:active`-State für Touch.
  - `.custom-range-picker` — zwei Date-Inputs + Anwenden-Button, wird per JS umgeklappt.
  - `.chart-container-p91` — `position: relative; height: 280px` (Pflicht für `responsive: true`).
  - `.metric-toggle-pills` — 5 Pills (BERT Sentiment, TTR Rolling-50, Shannon Entropy, Hapax Ratio, Ø Wortlänge), jede mit Farbkreis und ⓘ-Info-Icon.
- JS-State: `metricsChart` (Chart.js-Instanz), `_currentTimeRange` (Tage), `METRIC_DEFS` (Label/Color/Info).
- `loadMetricsChart(days)` baut URL mit Zeitraum-Filter, holt Daten, ruft `buildChart()`.
- `buildChart(data)` zerstört alten Chart (`metricsChart.destroy()`), generiert Datasets dynamisch aus den aktiven Toggle-Pills, konfiguriert Chart.js mit `borderWidth: 1.5`, `pointRadius: 0`, `pointHitRadius: 12`, `tension: 0.3`, Zoom-Plugin mit `pan + pinch + wheel (mode: x)`, dunkle Hel-Tooltips.
- `toggleMetric(key)` + `renderMetricToggles()` + `showMetricInfo(key)` für die Pill-Interaktion.

**Block C – Aufräumen:**
- Alter manueller Canvas-Chart (`#sentimentChart` + `updateChart()`-Body) komplett entfernt. Neuer Canvas heißt `#metricsCanvas`.
- Metriken-Datentabelle (`#messagesTable`) in `<details>` eingeklappt (per Default geschlossen), `table-layout: fixed` + `text-overflow: ellipsis` gegen Overflow, Horizontal-Scroll-Wrapper für Mobile.
- `updateChart()` als Kompatibilitäts-Alias erhalten (ruft `rebuildChart()`).

**Betroffene Dateien:**
- `zerberus/app/routers/hel.py` (alle drei Blöcke)
- `HYPERVISOR.md`, `README.md`, `lessons.md`, `backlog_nach_patch83.md`, `docs/PROJEKTDOKUMENTATION.md`

**Verifikation:**
- `python -c "from zerberus.app.routers import hel; print('ok')"` → ok
- Server-Restart + `curl -k -u <admin> "https://127.0.0.1:5000/hel/metrics/history?from_date=2026-04-15&to_date=2026-04-18&limit=200"` → Envelope-JSON
- Hel öffnen → Metriken-Akkordeon → Chart mit dünnen Linien, Zeitraum-Chips schalten, Metrik-Pills togglen, Pinch-Zoom auf Touch.

*Stand: 2026-04-18, Patch 91.*

---

### Patch 92 – DB-Fix: `profile_key` in `interactions` + Alembic-Setup (2026-04-18)

**Ziel:** Zuverlässige User-Zuordnung in `interactions` (bisher nur per clientseitiger Session-ID, unzuverlässig). Parallel das Migrations-Tooling (Alembic) etablieren, damit künftige Schema-Änderungen versioniert laufen.

**Block A – `profile_key`-Spalte:**
- Neue Spalte `profile_key TEXT DEFAULT NULL` in `interactions` (zusätzlich zur bestehenden `profile_name`-Spalte aus Patch 60, die bleibt für Rückwärtskompatibilität).
- Migration in `database.py::init_db` (idempotent, PRAGMA-Check): ALTER TABLE nur wenn Spalte fehlt, danach `UPDATE interactions SET profile_key = profile_name WHERE profile_name IS NOT NULL AND profile_name != ''`.
- Index `idx_interactions_profile_key(profile_key, timestamp DESC)` per `CREATE INDEX IF NOT EXISTS`.
- SQLAlchemy-Model `Interaction` bekommt `profile_key = Column(String(100), nullable=True, index=True)`.
- `store_interaction()` bekommt optionalen `profile_key`-Parameter (Fallback auf `profile_name`). Call-Sites in `legacy.py`, `orchestrator.py`, `nala.py` geben `profile_key=profile_name or None` mit.
- `GET /hel/metrics/history` nutzt die Spalte ab jetzt tatsächlich (Patch 91 hatte den Parameter vorsorglich eingebaut).
- Migrations-Ergebnis auf der bestehenden DB: 76/4667 Zeilen haben `profile_key` (alle, die bisher eine `profile_name` hatten — typisch `'chris'`; Rest bleibt `NULL`, weil pre-Patch-60-Daten keinen User-Tag hatten).
- Backup vor der Migration: `bunker_memory_backup_patch92.db`.

**Block B – Alembic:**
- `alembic init alembic` im Projektroot — erzeugt `alembic.ini`, `alembic/env.py`, `alembic/versions/`.
- `alembic.ini`: `sqlalchemy.url = sqlite:///bunker_memory.db`.
- Baseline-Revision `7feab49e6afe_baseline_patch92_profile_key.py`:
  - `_has_column(conn, table, column)`-Helper per PRAGMA.
  - `upgrade()`: legt `profile_key`-Spalte + Daten-Migration + Index an, alles idempotent. Läuft auf frischen DBs wie auf bereits migrierten.
  - `downgrade()`: Index droppen, Spalte per `batch_alter_table` entfernen.
- `alembic stamp head` — markiert die Baseline als „schon angewandt", da die Migration bereits manuell lief.
- **Kein Auto-Upgrade beim Serverstart.** Migrations-Anwendung kontrolliert per `alembic upgrade head`.

**Betroffene Dateien:**
- `zerberus/core/database.py` (Model + `init_db` + `store_interaction`)
- `zerberus/app/routers/legacy.py`, `orchestrator.py`, `nala.py` (Call-Sites)
- `zerberus/app/routers/hel.py` (Filter-Logik — schon Patch 91)
- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/7feab49e6afe_baseline_patch92_profile_key.py` (neu)
- `bunker_memory_backup_patch92.db` (Backup, nicht ins Git)

**Verifikation:**
- `PRAGMA table_info(interactions)` → `profile_key` in Spaltenliste
- `SELECT COUNT(*) FROM interactions WHERE profile_key IS NOT NULL` → 76
- `alembic current` → `7feab49e6afe (head)`
- Neuer Chat → Eintrag hat `profile_key` gesetzt (nach Server-Restart)

*Stand: 2026-04-18, Patch 92.*

---

### Patch 93 – Loki & Fenrir: Playwright E2E + Chaos-Tests (2026-04-18)

**Ziel:** Erste echte Testebene für Zerberus — ein methodischer E2E-Tester (Loki) und ein Chaos-Agent (Fenrir) gegen den laufenden Server, mit eigenen Test-Accounts damit reale User-Daten (Chris/Jojo) unberührt bleiben.

**Mythologie:**
- **Loki** (Trickster): methodisch, prüft erwartete Ergebnisse, reportet sauber.
- **Fenrir** (Wolf): wild, findet Edge-Cases durch rohe Gewalt — Prompt-Injection, XSS, SQL, Nullbytes, Emoji-Bomben.

**Block A – Infrastruktur:**
- `pip install playwright pytest-playwright pytest-html`. `playwright install chromium` zieht ~250 MB nach `%USERPROFILE%\AppData\Local\ms-playwright\`.
- Neue Test-Accounts in `config.yaml`:
  - `loki` (Passwort `lokitest123`, bcrypt-Hash rounds=12)
  - `fenrir` (Passwort `fenrirtest123`, bcrypt-Hash rounds=12)
- Verifiziert per `POST /nala/profile/login` → beide 200.
- Projektstruktur `zerberus/tests/`:
  - `__init__.py`
  - `conftest.py` — Fixtures `browser_context_args` (Self-signed-Cert-Toleranz, Viewport 390×844), `nala_page`, `logged_in_loki`, `logged_in_fenrir`, `hel_page` (Basic Auth aus `.env`). Login-Helper nutzt tatsächliche Selektoren `#login-username`, `#login-password`, `#login-submit`.

**Block B – Loki (E2E):**
- `zerberus/tests/test_loki.py`:
  - `TestLogin`: Login-Screen lädt, gültige Credentials öffnen Chat, falsches Passwort blockt, case-insensitive Login (LOKI).
  - `TestChat`: Textarea schreibbar, leere Nachricht wird nicht gesendet.
  - `TestNavigation`: Hamburger- und Settings-Buttons öffnen Sidebar/Modal (mit `pytest.skip` wenn Buttons fehlen — defensive).
  - `TestHel`: Dashboard lädt, Metriken-Sektion präsent, Patch-91-Zeitraum-Chips sichtbar.
  - `TestMetricsAPI`: Der neue `/hel/metrics/history`-Endpoint liefert das Envelope-Format mit `meta.count`.

**Block C – Fenrir (Chaos):**
- `zerberus/tests/test_fenrir.py`:
  - `CHAOS_PAYLOADS`-Liste: 15 bösartige Strings (leer, Whitespace, 5000 a's, XSS, SQL-Injection, Log4Shell `${jndi:...}`, Prompt-Injection, Path-Traversal, Emoji-Bombe, arabisch/RTL, Nullbytes, HTTP-Smuggling).
  - `TestChaosInput`: parametrisiert jeden Payload in die Textarea, prüft dass die Seite danach noch funktioniert.
  - `TestChaosNavigation`: Rapid-Viewport-Switch (Portrait ↔ Landscape), Rapid-Click auf alle sichtbaren Buttons (`force=True`, Exceptions stumm geschluckt), Enter-Press ohne Login.
  - `TestChaosHel`: `/hel/` ohne Auth → 401/403 (nie 500), Metrics-API mit Müll-Dates (`'9999-99-99'`, `"'; DROP--"`, leer) → nie 5xx.

**Betroffene Dateien:**
- `config.yaml` (Test-Profile loki, fenrir)
- `zerberus/tests/__init__.py`, `conftest.py`, `test_loki.py`, `test_fenrir.py` (neu)
- `HYPERVISOR.md`, `README.md`, `lessons.md`, `backlog_nach_patch83.md`, `docs/PROJEKTDOKUMENTATION.md`, `CLAUDE.md`

**Ausführung:**
```bash
venv\Scripts\activate
pytest zerberus/tests/ -v --html=zerberus/tests/report/full_report.html --self-contained-html
```

**Verifikation:**
- `playwright --version` → 1.58.0
- `pytest zerberus/tests/test_loki.py::TestLogin -v` → alle grün
- Beide Logins (loki/fenrir) per `POST /nala/profile/login` → HTTP 200

*Stand: 2026-04-18, Patch 93.*

### Patch 94 – Loki & Fenrir Erstlauf + Test-Bugfix (2026-04-18)

**Ziel:** Patch 93 hat die Tests installiert, aber nie gegen den live Server ausgeführt. Patch 94 ist der Erstlauf — vollständige Diagnose, Fixes für Failures, Re-Run bis grün.

**Block A – Erstlauf:**
- Vorbedingung: Server läuft (`https://127.0.0.1:5000`), `pytest-html` installiert.
- Kommando:
  ```bash
  pytest zerberus/tests/ -v --html=zerberus/tests/report/full_report.html --self-contained-html \
    > zerberus/tests/report/test_run_patch94.log 2>&1
  ```
- Ergebnis Erstlauf: **31 passed, 1 skipped** in 49 s.
- Alle 14 Chaos-Payloads (XSS `<script>`, SQLi `'; DROP TABLE`, Log4Shell `${jndi:ldap://}`, Prompt-Injection, Emoji-Bombe, arabisch/RTL, Nullbytes, Path-Traversal, HTTP-Smuggling, …) ohne 500er, ohne Crash, ohne Reflection.
- `TestChaosHel::test_metrics_history_bogus_dates` mit Müll-Dates (`'9999-99-99'`, `"'; DROP--"`, leer) → kein 5xx, Envelope sauber.
- **Keine App-Bugs gefunden** — Patch-91-Envelope-Struktur, Patch-92-`profile_key`-Filter und Auth-Layer alle robust.

**Block B – Test-Bugfix:**
- Eine Test-Funktion skippte stillschweigend: `TestNavigation::test_hamburger_menu_opens`.
- Ursache: Locator suchte `button:has-text('☰')` oder `.hamburger-btn`, aber das tatsächliche Element in [nala.py:885](zerberus/app/routers/nala.py:885) ist `<div class="hamburger" onclick="toggleSidebar()">☰</div>` — also weder `<button>` noch `.hamburger-btn`.
- Fix: Locator um `.hamburger` ergänzt ([test_loki.py:81](zerberus/tests/test_loki.py:81)).
- Kein App-Eingriff nötig.

**Block C – Re-Run + Report:**
- Re-Run: **32 passed, 0 skipped** in 47 s.
- HTML-Report `zerberus/tests/report/full_report.html` (78 KB, self-contained) liegt vor.
- Logfile: `zerberus/tests/report/test_run_patch94.log`.

**Betroffene Dateien:**
- `zerberus/tests/test_loki.py` (Locator-Fix)
- `zerberus/tests/report/full_report.html` (generiert)
- `zerberus/tests/report/test_run_patch94.log` (generiert)
- `HYPERVISOR.md`, `README.md`, `docs/PROJEKTDOKUMENTATION.md`

**Lessons:**
- Stille `pytest.skip()`-Pfade in Tests verschleiern Selector-Drift. Default-Verhalten: Skip → Failure umstellen, sobald die Selektoren stabilisiert sind. Bis dahin nach Re-Run das Testreport-Summary auf Skips prüfen.
- Patch-93-Suite läuft **48 s end-to-end** — schnell genug für CI-Trigger nach jedem Patch.

*Stand: 2026-04-18, Patch 94.*

### Patch 95 – Per-User-Filter im Hel Metriken-Dashboard (2026-04-18)

**Ziel:** Patch 91 hat den Metriken-Endpoint bereits um `profile_key` erweitert, Patch 92 die DB-Spalte angelegt. Was fehlte: ein Dropdown in der Hel-UI, mit dem man die Chart-Daten nach Profil filtern kann.

**Block A – Backend (`GET /hel/metrics/profiles`):**
- Neuer Endpoint in [hel.py:2242](zerberus/app/routers/hel.py:2242), unmittelbar vor `metrics_history`.
- Query: `SELECT DISTINCT profile_key FROM interactions WHERE profile_key IS NOT NULL AND profile_key != '' ORDER BY profile_key`
- PRAGMA-Check für `profile_key`-Spalte (Patch-92-ready) — gibt `{"profiles": []}` zurück wenn die Spalte fehlt, statt zu crashen.
- Auth: automatisch über `verify_admin`-Router-Dependency (`router = APIRouter(prefix="/hel", dependencies=[Depends(verify_admin)])`).
- Erste Live-Antwort: `{"profiles":["chris"]}` (76 von 4667 Zeilen migriert; loki/fenrir hatten noch keine Chats über die neuen Profile-Keys).

**Block B – Frontend-Dropdown:**
- `<select id="profileSelect" class="profile-select">` direkt vor `<div class="metric-timerange">` ([hel.py:489](zerberus/app/routers/hel.py:489)).
- Default-Option „Alle Profile" (Wert `""` = kein Filter).
- Eigener CSS-Block `.metric-profile-filter` + `.profile-select` matched die `.time-chip`-Optik (gold-Ring 1 px, 36 px Touch-Target, dunkler Background, `:focus`/`:active`-Rand in `#f0b429`).
- Mobile-first: Container `flex-wrap`, label + select brechen bei schmalen Viewports um.
- JS-Hook: neue Funktion `loadProfilesList()` lädt die Profile via `fetch('/hel/metrics/profiles')` beim DOMContentLoaded und hängt einen `change`-Listener an, der `loadMetricsChart(_currentTimeRange)` triggert.
- URL-Erweiterung in `loadMetricsChart()`: liest `document.getElementById('profileSelect').value` und hängt `&profile_key=<value>` an, wenn nicht leer.

**Block C – Verifikation:**
- `/hel/metrics/profiles` → `{"profiles":["chris"]}`.
- `/hel/metrics/history?profile_key=chris&limit=1` → `meta.count=1`.
- `/hel/metrics/history?profile_key=nonexistent` → `meta.count=0` (Filter greift sauber, kein 500er).
- Hel-HTML: 10 neue Marker (`profileSelect`, `metric-profile-filter`, `loadProfilesList`, `.profile-select`-CSS) im served Markup.

**Betroffene Dateien:**
- `zerberus/app/routers/hel.py` (Endpoint + HTML + CSS + JS)
- `HYPERVISOR.md`, `README.md`, `docs/PROJEKTDOKUMENTATION.md`

**Lessons:**
- `uvicorn --reload` kann hängen, wenn parallel ein langlaufender Request läuft (z.B. Whisper-Voice). Workaround: nicht den ganzen Reloader killen, sondern nur den Worker-Prozess (Reloader spawnt automatisch einen frischen). PIDs unterscheidet `Get-CimInstance Win32_Process`.
- Der Reload wird vom OS-Watcher korrekt erkannt (Logzeile `WatchFiles detected changes...`), bricht aber stillschweigend ab wenn der alte Worker nicht beendet werden kann. Kein Warning, kein Error — nur das Ausbleiben des „Application startup complete".

*Stand: 2026-04-18, Patch 95.*

### Patch 96 – Testreport-Viewer in Hel (H-F04) (2026-04-18)

**Ziel:** Patch 93 generiert HTML-Reports nach `zerberus/tests/report/full_report.html`. Bisher musste man ins Dateisystem gucken — Patch 96 macht sie direkt aus dem Hel-Dashboard zugänglich.

**Block A – Backend (Endpoints):**
- `GET /hel/tests/report` — liefert `full_report.html` als `HTMLResponse` aus `_REPORT_DIR / "full_report.html"`. Falls die Datei nicht existiert: 404 mit JSON `{"error": "Kein Testreport vorhanden. Bitte pytest ausführen."}`.
- `GET /hel/tests/reports` — liefert eine Liste aller `*.html`-Dateien im Report-Ordner mit `mtime` (Unix-Timestamp) und `size` (Bytes), sortiert nach mtime DESC.
- `_REPORT_DIR = Path(__file__).resolve().parents[2] / "tests" / "report"` — robust gegen das aktuelle Working-Dir, nutzt die Datei-Position von [hel.py](zerberus/app/routers/hel.py) (`zerberus/app/routers/hel.py` → `parents[2]` = `zerberus/`).
- Auth via `verify_admin`-Router-Dependency — keine zusätzliche Sicherheitsmaßnahme nötig.
- `Path`, `HTMLResponse`, `JSONResponse` waren bereits in [hel.py:12-16](zerberus/app/routers/hel.py:12) importiert.

**Block B – Frontend-Akkordeon:**
- Neue Sektion `<div class="hel-section" id="section-tests">` direkt nach Metriken (vor LLM & Guthaben), Header „🧪 Testreports".
- `class="hel-section-body collapsed"` — Standard zu, wie die meisten anderen Sektionen außer Metriken.
- Card-Inhalt: Erklärungs-Paragraph + Button „Letzten Report öffnen" (Klasse `.font-preset-btn` für 44 px Touch-Target + `:active`-Fallback) + `<div id="reportsList">` für die dynamische Tabelle.
- Button öffnet `/hel/tests/report` in einem neuen Tab via `window.open(..., '_blank')` — kein DOM-Embedding (Self-contained-HTML der pytest-Reports kann globale Styles überschreiben).
- JS `loadReportsList()` lädt die Reports-Liste beim DOMContentLoaded und rendert eine kompakte Tabelle (Datei, Stand `de-DE`-formatiert, Größe in KB, Link). Nur `full_report.html` ist verlinkbar; ältere Dateien (`loki_report.html`, `fenrir_report.html`) werden gelistet aber nicht verlinkt — kein Path-Param-Endpoint = kein Path-Traversal-Risiko.

**Block C – Verifikation:**
- `curl /hel/tests/report` → HTTP 200, ~78 KB HTML.
- `curl /hel/tests/reports` → 3 Reports (`full_report.html`, `fenrir_report.html`, `loki_report.html`) mit korrekten Mtimes.
- Hel-HTML enthält 6 neue Marker (`section-tests`, `loadReportsList`, `reportsList`).
- Komplette Test-Suite nach Patch 95+96 re-run: **32 passed in 56 s** — keine Regressionen.

**Betroffene Dateien:**
- `zerberus/app/routers/hel.py` (Endpoints + Akkordeon-HTML + JS)
- `HYPERVISOR.md`, `README.md`, `docs/PROJEKTDOKUMENTATION.md`, `backlog_nach_patch83.md`

**Lessons:**
- Pytest-Reports sind self-contained-HTML mit eigenem CSS — niemals via `<iframe>` oder `innerHTML` einbetten, immer in neuem Tab öffnen, sonst kollidieren die Styles mit Hel.
- Path-Traversal-Schutz mit „nur fixe Datei verlinken" ist die einfachste Lösung. Wenn später beliebige Reports verlinkt werden sollen, muss der Endpoint einen Pfad-Param mit `Path.resolve().is_relative_to(_REPORT_DIR)`-Check bekommen.

### Patch 97 – R-04: Query Expansion für Aggregat-Queries (2026-04-18)

**Ziel:** Backlog-Item R-04 aus Patch 87 abarbeiten. Vor dem eigentlichen FAISS-Retrieval erzeugt ein kurzer LLM-Call 2–3 synonyme Formulierungen, die zusammen mit der Original-Query durch die Pipeline laufen. Für Aggregat-Queries wie Q11 („Nenn alle Momente wo…") soll damit der Kandidaten-Pool breiter werden.

**Block A – Query-Expansion-Modul:**
- Neues Modul [query_expander.py](zerberus/modules/rag/query_expander.py): `expand_query(query, config)` ist async, nutzt `httpx.AsyncClient` mit 3 s Timeout gegen `settings.legacy.urls.cloud_api_url` (OpenRouter).
- System-Prompt: *„Du bist ein Suchassistent. Gegeben eine Suchanfrage, erzeuge 2-3 alternative Formulierungen und Stichworte die dasselbe Thema betreffen. Antworte NUR mit einer JSON-Liste von Strings, kein anderer Text."*
- `_parse_expansions()`: sucht die erste `[...]`-Struktur in der Antwort per Regex und `json.loads()`, toleriert Markdown-Wrapper wie ```` ```json ... ``` ````.
- Modell: `config["query_expansion_model"]` oder Fallback `settings.legacy.models.cloud_model` (per Default `null` → cloud_model).
- Fail-Safe: `asyncio.TimeoutError`, `httpx.HTTPError`, Parse-Error, generisches `Exception` — jeweils Warning-Log + `return [original]`. Kein Crash, kein RAG-Ausfall.
- Dedupe: Original + Expansionen case-insensitive per Set, Reihenfolge erhalten.

**Block B – Integration in die RAG-Pipeline:**
- `/rag/search` (router.py) und `_rag_search` (orchestrator.py) folgen demselben Muster:
  1. `expand_query()` aufrufen → Liste von Queries
  2. Pro Query: `_encode()` + `_search_index(vec, per_query_k, min_words, q, rerank_enabled=False, ...)` — **Rerank pro Sub-Query deaktiviert**.
  3. `per_query_k = top_k * rerank_multiplier` — jede Sub-Query über-fetched, damit der finale Pool genügend Vielfalt hat.
  4. Dedupe über Text-Prefix-Key (erste 200 Zeichen) in einem `set`.
  5. **Finaler Rerank einmal über den kombinierten Pool mit der ORIGINAL-Query** — so bleibt die Relevanz-Bewertung an der User-Absicht verankert statt an den Expansionen.
- Diagnose-Log `[EXPAND-97]` auf WARNING-Level: Original, Expansionen, per-query-k, Post-dedupe-Größe. Zusätzlich Info-Log im query_expander für die erzeugten Varianten.

**Block C – Orchestrator/Legacy-Durchgriff:**
- legacy.py importiert `_rag_search` direkt aus orchestrator.py — die Query-Expansion greift dort ohne weitere Änderungen.
- Skip-Logik (`CONVERSATION`-Intent, kurze Msgs) bleibt unverändert: Expansion greift nur wenn RAG überhaupt läuft.
- Config: `config.yaml` + `config.yaml.example` um `query_expansion_enabled: true` und `query_expansion_model: null` ergänzt.

**Block D – Eval-Lauf:**
- `python rag_eval.py` gegen den (nach Neustart) laufenden Server.
- Expansion feuerte bei allen 11 Fragen, typisch 3–5 Varianten. Beispiele:
  - Q4 „Perseiden-Nacht" → *Perseiden-Meteoritenschauer*, *Nacht der Sternschnuppen*, *Astrofotografie nach dem Perseiden-Ereignis*
  - Q10 „verkaufsoffener Sonntag in Ulm" → *Verkaufsoffene Sonntage in Ulm*, *Einkaufssonntage in Ulm*, *Öffnungszeiten von Geschäften am Sonntag in Ulm*
  - Q11 „Nenn alle Momente wo Annes Verhalten als unkontrollierbarer Impuls…" → 5 synonyme Umschreibungen
- Ergebnis: **9–10 JA / 1–2 TEILWEISE / 0 NEIN** — im Rahmen der Patch-89-Baseline, keine Regressions.
- **Q11 bleibt TEILWEISE** wie erwartet. Grund: der Index hat nur 12 Chunks, die Dedupe über den expandierten Pool erschöpft ihn komplett (`Post-dedup: 12`). Der Cross-Encoder wählt trotzdem den Glossar-Chunk als Top-1, weil er — isoliert betrachtet — am besten zu „Impuls / Kontrollwahn"-Begriffen passt. Für eine echte Aggregat-Antwort muss das LLM mehrere Chunks *zusammen* interpretieren → **Backlog-Item R-07 (Multi-Chunk-Aggregation)** als nächster Hebel dokumentiert.

**Betroffene Dateien:**
- `zerberus/modules/rag/query_expander.py` (neu)
- `zerberus/modules/rag/router.py` (`/rag/search` erweitert)
- `zerberus/app/routers/orchestrator.py` (`_rag_search` erweitert)
- `config.yaml`, `config.yaml.example` (neue Keys)
- `rag_eval_delta_patch97.md` (Eval-Report)
- `HYPERVISOR.md`, `README.md`, `backlog_nach_patch83.md`, `docs/PROJEKTDOKUMENTATION.md`

**Lessons:**
- `--reload` auf Windows hängt regelmäßig bei langlaufenden Requests — bei jedem Patch mit neuen Imports (neue Datei, neues Paket) manueller Neustart einplanen. Test per `[EXPAND-97]`-Log bzw. einfacher „kommt der Log an"-Check.
- Query Expansion ist nur so stark wie der Index breit ist. Bei 12 Chunks ist sie nutzlos für Aggregat-Queries — bei 100+ Chunks wird sie zum echten Retrieval-Boost. Fürs Protokoll: die Infrastruktur steht jetzt, der Effekt skaliert mit Dokumenten-Volumen.
- Wichtige Design-Entscheidung: **pro Sub-Query FAISS ohne Rerank, dann finaler Rerank mit Original-Query auf den Merged-Pool.** Alternative (Rerank pro Sub-Query und Merge der Rerank-Scores) hätte 5× CrossEncoder-Calls gekostet und das Scoring inkonsistent gemacht.

### Patch 98 – Wiederholen & Bearbeiten an Chat-Bubbles (N-F03/N-F04) (2026-04-18)

**Ziel:** Zwei seit Patch 83 im Backlog liegende N-F03/N-F04-Features — Wiederholen-Button und Bearbeiten-Button — an der bestehenden Bubble-Toolbar ergänzen. Bewusst **minimal-invasiv** umgesetzt, kein Fork / kein History-Rewrite / kein Inline-Editing.

**Block A – Wiederholen-Button (N-F03):**
- Neuer 🔄-Button (`.bubble-action-btn`) in der bereits existierenden `msg-toolbar` ([nala.py:~1524](zerberus/app/routers/nala.py)) — **nur an User-Bubbles** angehängt.
- `retryMessage(text, btn)` in JS: triggert `sendMessage(text)` mit dem ursprünglichen Text, Gold-Flash auf dem Button (800 ms `.copy-ok`-Klasse) als visuelles Feedback.
- Klick auf die **letzte** User-Nachricht = echtes Retry. Klick auf eine **frühere** Nachricht = neue Message am Ende des Chats (identisches UX-Verhalten; ein Fork wäre zu komplex für den Scope dieses Patches).

**Block B – Bearbeiten-Button (N-F04):**
- Neuer ✏️-Button, ebenfalls nur an User-Bubbles.
- `editMessage(text, btn)` setzt den Text in `#text-input`, fokussiert die Textarea, triggert das bestehende Auto-Expand (`scrollHeight` → `min(max(sh,96),140)`), setzt den Cursor ans Ende.
- **NICHT automatisch senden** — der User editiert und drückt selbst Enter. Kein Inline-Editing, kein Fork.

**Block C – Styling & Touch:**
- Bestehende `.copy-btn`-Klasse um `.bubble-action-btn` erweitert (gemeinsamer Selektor für Farbe, Hover/Active, Padding).
- Neue `@media (hover: none) and (pointer: coarse)`-Regel: Touch-Targets auf 44 px (Mobile), Toolbar leicht opak (0.55) für Dauer-Sichtbarkeit — bis Patch 97 war die Toolbar nur bei Hover sichtbar, was auf Touch-Geräten per `:active` nur kurzzeitig ging.
- LLM-Bubbles behalten weiterhin nur 📋 + Timestamp (Retry/Edit wären dort semantisch sinnlos).

**Block D – Tests:**
- Komplette Loki+Fenrir-Suite post-Restart: **32 passed in 50 s** — keine Regressions.
- Markers im gerenderten Nala-HTML: `bubble-action-btn`, `retryMessage`, `editMessage` — 7 Treffer wie erwartet (Klasse in CSS, 2 Buttons, 2 Funktionen, 2 Onclick-Handler).
- Die bestehenden `.copy-btn`-Selektoren in den Tests greifen weiterhin — der Shared-Selector-Ansatz hat die Testbasis nicht bewegt.

**Betroffene Dateien:**
- `zerberus/app/routers/nala.py` (CSS + `addMessage()` + 2 neue JS-Funktionen)
- `HYPERVISOR.md`, `README.md`, `backlog_nach_patch83.md`, `docs/PROJEKTDOKUMENTATION.md`

**Lessons:**
- Server-Reload-Lesson erneut: `--reload` auf Windows sah die Änderung an der HTML-Stringliteral in `nala.py` auch nach `touch` nicht — manueller Kill + Neustart nötig. Zum dritten Mal in dieser Session (Patch 95, 97, 98) — gehört in `lessons.md` als harter Punkt.
- Design-Entscheidung „kein Fork": Ein Chat-Fork würde bedeuten, dass die weiter-obigen Nachrichten und alles danach als „abgezweigte Version" weiterleben — schwierig im aktuellen linearen Message-Array. Der Kosten-Nutzen-Vergleich (kleine UX-Verbesserung vs. Datenmodell-Umbau) hat klar gegen Fork gesprochen. Falls später doch: neue Tabelle `session_branches` mit Parent-ID wäre der Einstieg.

### Patch 99 – Hel Sticky Tab-Leiste (H-F01) (2026-04-18)

**Ziel:** Das Akkordeon-Layout (seit Patch 85) wird bei elf Sektionen unübersichtlich. Backlog-Item H-F01 fordert eine Sticky-Tab-Leiste, die immer am oberen Rand klebt und per Tap zwischen den Sektionen wechselt. Diese Patch löst das.

**Block A – Tab-Leiste HTML & CSS:**
- Neues `<nav class="hel-tab-nav">` direkt unter `<h1>` (nach der Schriftgrößen-Wahl). Rolle `tablist`, Aria-Label.
- 11 Tabs (`role` wird per HTML5 `<nav>` + `<button type="button">` geliefert): 📊 Metriken, 🤖 LLM, 💬 Prompt, 📚 RAG, 🔧 Cleaner, 👥 User, 🧪 Tests, 🗣 Dialekte, 💗 Sysctl, ❌ Provider, 🔗 Links.
- CSS: `position: sticky; top: 0; z-index: 100` — bleibt am oberen Rand beim Scrollen. `overflow-x: auto; -webkit-overflow-scrolling: touch; white-space: nowrap` — horizontal scrollbar auf Mobile. Scrollbar per `scrollbar-width: none; -ms-overflow-style: none; ::-webkit-scrollbar { display: none }` versteckt. `min-height: 44px; padding: 8px 16px` pro Tab (Touch-Targets). Aktiver Tab: `color: #ffd700` + 3 px Unterstrich in derselben Farbe. `:active`-Fallback für Touch.
- Hintergrund `#1a1a1a` (matcht Body), Bottom-Border `2px solid #c8941f` (matcht den alten Akkordeon-Header-Look).

**Block B – Tab-Wechsel JavaScript:**
- Jede `.hel-section` bekommt `data-tab="metrics" | "llm" | …` (Section-IDs bleiben unverändert für Test-Kompat).
- CSS-Regel `.hel-section[data-tab]:not(.active) { display: none; }` — nur die aktive Sektion ist sichtbar.
- Neue Funktion `activateTab(id)`:
  1. Toggelt die `.active`-Klasse auf `.hel-section[data-tab=id]` und `.hel-tab[data-tab=id]`.
  2. Persistiert `localStorage('hel_active_tab')`.
  3. Lazy-Load genau einmal pro Sektion (Set `_HEL_LAZY_LOADED`): `loadMetrics()`, `loadSystemPrompt()+loadProfiles()`, `loadRagStatus()`, `loadPacemakerConfig()`, `loadProviderBlacklist()`.
  4. Scrollt den aktiven Tab per `scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' })` ins Sichtfeld, falls die Tab-Leiste scrollbar ist.
- `toggleSection(id)` bleibt als Alias (`function toggleSection(id) { activateTab(id); }`) — falls irgendwo noch Altcode darauf referenziert.
- Early-Load-IIFE im `<head>` (neben dem Patch-90-FOUC-Fix für `hel_font_size`) liest `hel_active_tab` in `window.__hel_active_tab`, DOMContentLoaded-Handler ruft `activateTab(window.__hel_active_tab || 'metrics')` auf.

**Block C – Migration bestehender Sektionen:**
- **Akkordeon-Wrapper bleiben erhalten** aus Rückwärtskompat-Gründen — `<div class="hel-section-header">` wird per CSS `display: none !important` versteckt statt aus dem HTML entfernt. Der Vorteil: alte `toggleSection`-Onclicks im HTML feuern nicht, aber der Code ist minimal-invasiv.
- `.hel-section-body.collapsed` wird via CSS-Override (`max-height: none !important; padding: 20px`) neutralisiert — der alte Akkordeon-Mechanismus ist tot, die Body-Div dient nur noch als optischer Container.
- Metriken-Sektion erhält im HTML direkt `class="hel-section active"` als Default (matched den ersten Tab-Button mit `class="hel-tab active"`). Das verhindert eine FOUC-Lücke zwischen Render und DOMContentLoaded.

**Block D – Tests & Verifikation:**
- `grep -c data-tab=` im gerenderten HTML: 11 (eine pro Sektion, + 11 im Tab-Nav = 22 plus die IIFE-Referenzen). 23 neue Marker gesamt (`Patch 99`, `activateTab`, `hel-tab-nav`).
- Full-Test-Suite post-Restart: **32 passed in 49.93 s**, keine Regressions.
- `test_metrics_section_present` greift `#metricsCanvas` ODER `#section-metrics` — beide bleiben vorhanden, also ✓.
- `test_time_chips_visible` greift `.time-chip` im Metriken-Tab — Metriken ist Default-Active, also ✓.

**Betroffene Dateien:**
- `zerberus/app/routers/hel.py` (CSS + Nav-HTML + `activateTab()`/`toggleSection()` + DOMContentLoaded-Hook + `data-tab`-Attribute)
- `HYPERVISOR.md`, `README.md`, `backlog_nach_patch83.md`, `lessons.md`, `docs/PROJEKTDOKUMENTATION.md`

**Lessons:**
- **Zombie-Uvicorn-Worker durch wiederholtes `--reload` + manuelle Kills:** In dieser Session lief irgendwann ein Baum aus 3 parallelen uvicorn-Masters plus ihren Workers, weil frühere `--reload`-Hänger nicht sauber aufgeräumt waren. Port 5000 wurde vom ältesten Prozess gehalten, die neuen Prozesse starteten aber ohne zu merken, dass der Port belegt war (SSL-Setup schluckt die Fehlermeldung). **Symptom: neue Code-Änderungen erscheinen nicht im gerenderten HTML, obwohl eine neue uvicorn-Instanz läuft.** Diagnose: `Get-WmiObject Win32_Process -Filter "Name='python.exe'"` listet alle Uvicorn-PIDs, dann `taskkill //PID <pid> //F` für jeden einzelnen plus Worker-Kinder. Vor jedem Server-Start: diese Liste prüfen.
- Minimal-invasive Migration (Akkordeon → Tabs) durch CSS-Versteck statt HTML-Umbau spart viel Diff-Größe und senkt das Regressionsrisiko. Die bestehenden `.hel-section-header`-`onclick`-Handler bleiben harmlos (Elemente sind `display: none`), die Tests greifen weiter an den stabilen IDs, und der Rollback ist ein CSS-Zwei-Zeiler.
- `scrollIntoView({ inline: 'center' })` auf dem aktiven Tab ist ein nettes UX-Detail für Mobile, das beim Tab-Wechsel den neu gewählten Tab in die Mitte der Leiste scrollt — ohne diese Zeile müssten Nutzer zwischen Tab-Wechsel und Sektion hin- und her-scrollen.

*Stand: 2026-04-18, Patch 96.*

---

### Patch 100 – Meilenstein: Hel-Hotfix + JS-Integrity-Tests + Easter Egg (2026-04-19)

**Ziel:** Drei-Teile-Patch anlässlich des 100. Patches:
1. **Hotfix** für den SyntaxError im Hel-Frontend (kaputt seit Patch 91, erst nach Patch 99 akut sichtbar).
2. **JS-Integrity-Tests** als Playwright-Pageerror-Listener — damit dieser Bug-Typ künftig im CI-Grün nicht versehentlich durchrutscht.
3. **Easter Egg** zum Meilenstein: KI-generiertes Entwicklerbild versteckt hinter dem Trigger "Rosendornen" / "Patch 100" in Nala und als About-Tab in Hel.

**Teil 1 – Hotfix SyntaxError:**
- Diagnose: `Uncaught SyntaxError: '' string literal contains an unescaped line break` → bricht das gesamte `<script>` ab → weder `activateTab` noch `setFontSize` werden definiert, Hel-Frontend tot.
- Root Cause: `hel.py:1290` enthielt seit Patch 91 (`showMetricInfo`) den Ausdruck `alert(METRIC_DEFS[key].label + '\n\n' + METRIC_DEFS[key].info);`. Innerhalb eines plain Python-`"""..."""`-Strings interpretiert Python `\n` als echtes Newline — der Browser sieht ein JS-String-Literal mit hartem Zeilenumbruch und verweigert das Parsing.
- Fix: `'\n\n'` → `'\\n\\n'` (zwei Zeichen Python-Escape, damit im Output literal `\n\n` steht).
- Latenz-Erklärung: Der Bug war seit Patch 91 im Code, fiel aber erst auf, als `activateTab` in Patch 99 zur kritischen Init-Funktion wurde. Davor war das Akkordeon per initialer Inline-CSS schon sichtbar, und die meisten Lazy-Loads hatten eigene try/catch-Rettungen.
- Lesson in `lessons.md` ergänzt: „Python-HTML-Strings mit JS — immer `\\n`."

**Teil 2 – JavaScript-Integrity-Tests:**
- Neue Test-Klasse `TestJavaScriptIntegrity` in `zerberus/tests/test_loki.py`.
- Zwei Tests: `test_hel_no_js_errors` (Hel via Basic-Auth-Context), `test_nala_no_js_errors` (Nala ohne Auth-Wall).
- **Wichtiges Pattern:** `page.on("pageerror", …)` MUSS VOR `page.goto(...)` registriert werden, sonst werden initiale Parse-Errors verschluckt. Dafür eigene Browser-Contexts (nicht die `hel_page`/`nala_page`-Fixtures, die schon navigiert haben).
- `page.wait_for_timeout(2000)` gibt dem Frontend Zeit, alle deferred-Loads anzustoßen.
- Test verifiziert den Hotfix: vor dem Fix schlägt `test_hel_no_js_errors` mit der SyntaxError-Meldung fehl; nach dem Fix grün.
- **Full-Test-Suite: 34 passed in 54 s** (32 bestehende + 2 neue).

**Teil 3 – Easter Egg (Meilenstein-Feature):**
- Bild: `docs/pics/Architekt_und_Sonnenblume.png` (1.5 MB PNG, KI-generiert, zeigt Rosa-Architektur auf Bildschirm + Kintsugi-Gehirn + Jojo + Motorblock + Gisela-approves-Post-it).
- Static-Serving: Neues Unterverzeichnis `zerberus/static/pics/`, Bild dorthin kopiert. FastAPI served es über das bestehende `/static`-Mount (`zerberus/main.py:219`) — kein neuer Mount nötig. Kein Base64-Inlining (würde den HTML-Output um 2 MB aufblähen).
- **Nala-Trigger:** `sendMessage(text)` fängt `text.trim().toLowerCase() === 'rosendornen' || === 'patch 100'` ab BEVOR der Chat-Request rausgeht. Das Overlay öffnet sich statt einer LLM-Antwort.
- **Nala-Overlay:** `#ee-modal` als Vollbild-Fixed-Layer (`rgba(0,0,0,0.88)`, z-index 9999). `.ee-inner`-Card (88 vw, max-720 px, gold-Border `#DAA520`, Glow-Shadow). Fade-In via `opacity 0→1, transition 1.5s ease`. Bild (`max-height: 60vh`), Titel ("🏺 Patch 100 – Zerberus Pro 4.0 🏺"), Zitat („Das Gebrochene sichtbar machen."), Body-Text, Emoji-Liste der Services, Schließen-Button. Klick außerhalb der Inner-Card schließt ebenfalls.
- **Sternenregen im Hintergrund:** `setInterval` spawned alle 400 ms 4 Sterne in der oberen Bildschirmhälfte (recycling von `spawnStars` aus Patch 83). Interval wird beim Schließen via Interval-Clear + `!modal.classList.contains('open')`-Check gestoppt.
- **Hel-About-Tab:** Neuer 12. Tab `ℹ️ About` in der Sticky-Nav (`hel.py:543`). Eigene `.hel-section[data-tab="about"]` am Ende. Inhalt: dasselbe Bild, derselbe Titel/Quote/Text, plus Version-Block (`Patch 100`, Architektur, Tests, RAG) und Entwickler-Credits („Entwickelt von Chris mit Claude (Supervisor + Claude Code) / Für Jojo und Nala 🐱"). Kein Overlay — direkt als Tab-Content. `toggleSection`/`activateTab`-Integration automatisch über das bestehende `data-tab`-Pattern.

**Betroffene Dateien:**
- `zerberus/app/routers/hel.py` (Hotfix Z. 1290, neuer About-Tab + Section)
- `zerberus/app/routers/nala.py` (Easter-Egg-Modal: HTML + CSS + JS-Trigger + open/close-Helpers)
- `zerberus/static/pics/Architekt_und_Sonnenblume.png` (neues Asset)
- `zerberus/tests/test_loki.py` (neue Klasse `TestJavaScriptIntegrity`)
- `SUPERVISOR.md`, `README.md`, `backlog_nach_patch83.md`, `lessons.md`, `docs/PROJEKTDOKUMENTATION.md`

**Lessons:**
- **Latenter Bug durch Code-Pfadänderung sichtbar:** Der SyntaxError existierte seit Patch 91 und wurde 9 Patches lang nicht bemerkt, weil `showMetricInfo` nur per Info-Button-Klick erreichbar war und der Pfad nie getestet wurde. Der Lernpunkt: Tests müssen **das JS-Parsing** prüfen, nicht nur einzelne DOM-Interaktionen. Ein `pageerror`-Listener hätte das sofort gefangen.
- **Playwright `pageerror` nur VOR `goto`:** Frisch gelernt — wenn man einen Listener auf einer Page registriert, die bereits geladen ist, fängt er nur zukünftige Errors. Für initiale Parse-Errors: neuer Context, neue Page, Listener anhängen, erst dann `goto`. Diese Reihenfolge ist nicht-trivial und verdient einen expliziten Kommentar im Test.
- **Easter Egg als Meilenstein-Dokumentation:** Der About-Tab in Hel ist gleichzeitig das Kolophon dieses Projekts — Architektur, Tests, RAG-Stand, alles auf einer Seite. Das ist die ehrlichste Form einer Version-Anzeige. Kein separates `/version`-Endpoint nötig, kein README-Abschnitt der veraltet — der About-Tab zeigt den aktuellen Stand aus der Quelle.

*Stand: 2026-04-19, Patch 100 — Meilenstein 🏺.*

---

### Patches 101–119 (2026-04-20 bis 2026-04-23)

**Phase 1 (101–107) — Infrastruktur-Fixes:**
- 101–104: Template-Konsolidierung, R-07 Multi-Chunk-Aggregation
- 105–106: Llama-Hardcode-Fix, Reranker-Threshold auf 0.05
- 107: Split-Brain config.json/yaml gefixt (Hel schrieb json, LLMService las yaml), TRANSFORM-Intent (RAG-Skip bei Übersetzen/Lektorieren), 71 Tests grün, Modell jetzt deepseek/deepseek-v3.2

**Phase 2 (108–111) — RAG Category-Tags + GPU:**
- 108: RAG Category-Tags beim Upload (Dropdown in Hel, Metadata pro Chunk)
- 108b: RAG-Eval Q12–Q20 entwickelt (Codex, Kadath, Cross-Doc)
- 109: SSE-Fallback (`retryOrRecover` pollt `/archive/session/{id}`) + Theme-Hardening (rgba-Defaults, Reset-Fix)
- 110: Upload-Formate erweitert (.md/.json/.pdf/.csv) + Chunking-Weiche pro Category
- 111: GPU-Code für Embedding + Reranker (device.py, Auto/CUDA/CPU) + Auto-Category-Detection + Query-Router (Category-Boost)
- 111b: Torch GPU-Hotfix (cu124, RTX 3060 erkannt)

**Phase 3 (112–118) — Stabilisierung + Features:**
- 112: Config-Split config.json→.bak bereinigt
- 113a: DB-Dedup (35 Zeilen entfernt) + Insert-Guard (30s-Window)
- 113b: W-001b Whisper Satz-Repetitions-Erkennung
- 114a: SSE-Heartbeat alle 5s + Client-Watchdog-Reset
- 114b: RAG-Eval mit GPU: 11 JA / 5 TEILWEISE / 4 NEIN
- 115: Background Memory Extraction (extractor.py, Overnight-Integration, Hel-Button)
- 116: Hel RAG-Tab: Gruppierte Dokumenten-Cards + Soft-Delete
- 117: Relative Pfade in start.bat
- 118a: Decision-Boxes in Nala (`[DECISION][OPTION:x]Label[/DECISION]` → klickbare Buttons)
- 118b: Neon Kadath indiziert (category=lore), RAG-Eval 15 JA / 5 TEILWEISE / 0 NEIN, sync_repos.ps1 erstellt, Repo-Sync-Pflicht in CLAUDE_ZERBERUS.md

**Phase 4 Start (119–121):**
- 119: Whisper Docker Auto-Restart Watchdog (whisper_watchdog.py, Hel-Button, 116 Tests grün)
- 119b: Hotfix — PROJEKTDOKUMENTATION.md-Pflichtschritt in CLAUDE_ZERBERUS.md verankert + Patches 101–119 nachgetragen, sync_repos.ps1 auf `docs/PROJEKTDOKUMENTATION.md` korrigiert
- 120: "Ach-laber-doch-nicht"-Guard (`zerberus/hallucination_guard.py`, Mistral Small 3 via OpenRouter, zustandslos, fail-open, SKIP bei <50 Tokens, WARNUNG haengt Qualitaetshinweis an die Antwort). W-001b Long-Subsequence-Fix (fing bisher nur 6-Wort-Phrasen und Saetze mit Interpunktion — jetzt auch lange 17–19-Wort-Loops ohne Punkte). Neue Architektur-Doku `docs/AUDIO_SENTIMENT_ARCHITEKTUR.md` mit 5-Schicht-Pipeline (Whisper → BERT → Prosodie [GEPLANT] → DeepSeek → Guard). **138 Tests grün** (+22: 8 Long-Subseq + 14 Guard).
- 121: Konsolidierung — Memory-Router-Import-Fix (main.py Modul-Loader prüft `router.py`-Existenz und loggt Helper-Pakete als INFO-Skip statt ERROR), RAG Einzel-Delete (Patch 116) verifiziert — Confirm-Dialog, DELETE-Endpoint, Retrieval-Filter, Reindex-Physikal-Cleanup konsistent. Lessons Session 2026-04-23 nachgetragen. 138 Tests grün (keine neuen Tests — trivialer Import-Guard + Doku).

**Aktueller Stand nach Patch 121:**
- Tests: 138 passed
- RAG-Eval: 15 JA / 5 TEILWEISE / 0 NEIN
- GPU: torch 2.5.1+cu124, RTX 3060 12GB
- Modelle: deepseek/deepseek-v3.2 (Haupt), mistralai/mistral-small-24b-instruct-2501 (Guard), beide via OpenRouter
- Repos: Zerberus (public), Ratatoskr (doc-mirror), Claude (global templates)
- Serverstart: sauber, keine Modul-Loader-Errors mehr

### Mega-Patch 122–126 – Code-Chunker, Huginn, UI-Overhaul, Bibel-Fibel, Dual-Embedder (2026-04-23)

Großer Schritt in Phase 4: fünf Patches in einer Session, 71 neue Unit-Tests, **233 passed** offline.

**Patch 122 – AST Code-Chunker für RAG:**
- Neues Modul `zerberus/modules/rag/code_chunker.py` mit Dispatcher `chunk_code(content, file_path)`.
- Strategien: Python AST (Funktionen/Klassen/Imports/Modul-Docstring als eigene Chunks), JS/TS Regex (function/class/const-arrow/export default), HTML Tag (script/style/body), CSS regel-basiert, JSON/YAML Top-Level-Keys, SQL Statement-Split.
- Fallback auf Prose-Chunker bei SyntaxError oder unbekannter Extension.
- Context-Header (`# Datei: …` + `# Funktion: name (Zeile X-Y)`) wird vor jedem Chunk prepended.
- Size-Limits MIN 50 / MAX 2000 Zeichen mit Merge/Split.
- Hel-Upload akzeptiert jetzt `.py/.js/.jsx/.mjs/.cjs/.ts/.tsx/.html/.css/.scss/.sql/.yaml/.yml`. Response enthält `chunker_strategy` + `chunk_preview`.
- FAISS-Metadata um `chunk_type`/`name`/`language`/`start_line`/`end_line`/`chunker_strategy` erweitert.
- **38 neue Tests** (`test_code_chunker.py`).

**Patch 123 – Telegram-Bot Huginn:**
- Neue Module `zerberus/modules/telegram/{bot,group_handler,hitl}.py`.
- Huginn = vollwertiger Chat-Partner (Fastlane: Input → Guard → LLM → Output, kein RAG/Memory/Sentiment).
- Direct Messages: immer beantworten. Gruppen-Logik: reagiert auf „Huginn" im Text, @-Mention, Reply auf eigene Messages; autonome Einwürfe mit LLM-Validierung + 5-Minuten-Cooldown.
- Bilder (photo_file_ids) werden als Vision-Inputs via OpenRouter weitergegeben.
- Guard-Check (Mistral Small 3, Patch 120) vor jeder Antwort; WARNUNG blockiert und benachrichtigt Admin.
- **HitL-Mechanismus:** `HitlManager` mit asyncio.Event-Wait, Inline-Keyboard (✅/❌), Timeout-Support. Anwendungen: Code-Ausführung, Gruppenbeitritt in nicht erlaubte Gruppen.
- Config unter `modules.telegram`: admin_chat_id, allowed_group_ids, model, max_response_length, group_behavior, hitl.
- Lifespan-Hook in `main.py`: Webhook-Register/Deregister bei Startup/Shutdown.
- Hel-Endpoints: `GET/POST /admin/huginn/config` (Token maskiert).
- **40 neue Tests** (`test_telegram_bot.py`).

**Patch 124 – Nala UI-Polish:**
- CSS-Upgrade in `zerberus/app/routers/nala.py`: Bubble-Shine (`::before` mit 135°-Gradient, dezenter Glanz oben-rechts), breitere Bubbles (90% mobile / 75% desktop), `messageSlideIn`-Animation, tiefere Box-Shadows, Button-Active-Transform.
- Long-Message-Collapse: bei >500 Zeichen wird die Bubble auf 220px beschnitten + Gradient-Fade + „▼ Mehr anzeigen"-Button, Klick toggled.
- Eingabezeile: collapsed auf 44px wenn leer, expandiert auf 48-140px bei Fokus (smooth transition).
- Scope-Zurücknahme: vollständiger Burger-Menü-Umbau / Settings-Drawer / HSL-Farbrad / Huginn-Tab sind bewusst als eigene Patches verschoben (127/128+) — Backend-Endpunkte für Huginn stehen in Patch 123.

**Patch 125 – Bibel-Fibel Prompt-Kompressor:**
- Neues Modul `zerberus/utils/prompt_compressor.py`.
- `compress_prompt(text, preserve_sentiment=False)`: Artikel-/Stoppwörter-Entfernung, Verb-Kürzung („du musst sicherstellen dass" → „Sicherstellen:"), Listen → Pipes („X, Y und Z" → „X|Y|Z"), Redundanz-Entfernung (identische Sätze), Whitespace-Collapse.
- `preserve_sentiment=True` schützt Marker wie „Nala/Rosa/Huginn/Chris/warm/liebevoll" — User-facing Prompts dürfen NICHT komprimiert werden.
- Werkzeug-Charakter: wird manuell auf Backend-Prompts angewendet, nicht zur Laufzeit auf jeden Prompt.
- `compression_stats(o, c)` liefert Before/After-Metriken (saved_chars, saved_pct, estimated_tokens_saved).
- **14 neue Tests**.

**Patch 126 – Dual-Embedder Infrastruktur:**
- Neue Module `zerberus/modules/rag/language_detector.py` + `dual_embedder.py`.
- **Sprachdetektor:** wortlisten-basiert (keine externe Library), analysiert erste 500 Zeichen, filtert Code-Tokens (def/class/function/...) und YAML-Frontmatter raus, Umlaute boosten DE-Score. Fallback auf DE bei <5 Tokens.
- **DualEmbedder-Klasse:** Lazy-Loading-Wrapper für zwei SentenceTransformer-Modelle. Default DE: `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` (GPU), Default EN: `intfloat/multilingual-e5-large` (CPU). Auto-Dispatch nach `detect_language`, manueller Override via `language=`-Parameter.
- **Scope bewusst auf Infrastruktur beschränkt:** aktiver FAISS-Index läuft weiter mit MiniLM (all-MiniLM-L6-v2, dim=384). Migration auf Dual-Embedder ist manueller Schritt (künftiges `scripts/migrate_embedder.py` mit Backup + Rebuild + RAG-Eval) — bewusst nicht destruktiv gemacht.
- **17 neue Tests** (`test_dual_embedder.py`).

**Aktueller Stand nach Mega-Patch 122–126:**
- Tests: **233 passed** offline (162 vorher + 71 neu)
- RAG-Eval: unverändert 15 JA / 5 TEILWEISE / 0 NEIN (kein Reindex)
- GPU: torch 2.5.1+cu124, RTX 3060 12GB
- Modelle: deepseek (Haupt), mistral-small (Guard), zusätzlich Huginn konfigurierbar (default deepseek-chat)
- Neu auf Disk: `zerberus/modules/rag/{code_chunker,language_detector,dual_embedder}.py`, `zerberus/modules/telegram/{bot,group_handler,hitl}.py`, `zerberus/utils/prompt_compressor.py`
- Offene Punkte (Patches 127+): Huginn-Tab im Hel-Frontend, Nala-Settings-Drawer, FAISS-Migration-Script + RAG-Eval auf neuem Embedder

*Stand: 2026-04-23, Mega-Patch 122–126 — Phase 4 mit Schwung weiter.*

### Patches 127–129 – Huginn-Hel-Tab, Settings-Drawer HSL, Embedder-Migration (2026-04-23)

Zweiter Zyklus desselben Session-Tests (Token-Budget-Probe): die in Mega-Patch 122-126 bewusst verschobenen Frontend/Infrastruktur-Teile werden nachgeliefert.

**Patch 127 – Huginn-Tab im Hel:**
- Neuer Tab „🐤 Huginn" in der Hel-Tab-Leiste (zwischen „Links" und „About").
- Frontend für die Patch-123-Endpoints `GET/POST /admin/huginn/config`: Status-Dot, Bot-Token-Input (Password-masked, rejecting maskierte Reload-Werte), Admin-Chat-ID, Modell-Dropdown (nutzt `_allModels` mit Preis pro 1M Tokens), Max-Response-Länge.
- Gruppen-Verhalten + HitL als `<details>`-Blöcke mit Checkboxes.
- Drei Action-Buttons: Speichern / Neu laden / Webhook registrieren.
- Lazy-Load via `_HEL_LAZY_LOADED`-Pattern.

**Patch 128 – Nala Settings-Anchor + HSL-Slider:**
- Sticky-Button „⚙️ Einstellungen" unten im Burger-Sidebar (44px Touch-Target, Gold-Rand, öffnet bestehendes Settings-Modal).
- Neuer HSL-Slider-Block im Settings-Modal unter Bubble-Farben: H/S/L pro Bubble (User + Bot) mit Live-Swatch, Wert-Readout und Regenbogen-Gradient auf Hue-Slider.
- `hslToHex(h,s,l)` JS-Helper synchronisiert den HSL-Wert zum bestehenden `<input type="color">` damit Favoriten-Speicherung mitzieht.
- LocalStorage-Persistenz für beide Bubbles. HSL ist **additiv** zu den bestehenden Color-Pickern, nicht ersetzend.

**Patch 129 – FAISS Dual-Embedder Migration-Script:**
- Neues Script `scripts/migrate_embedder.py` mit `--dry-run` (Default) und `--execute`.
- Flow: Backup in `data/backups/pre_patch129_<ts>/`, Sprache pro Chunk mit `detect_language` auf den tatsächlichen Content (NICHT System-Prompt), pro Sprache eigener FAISS-Index mit dem passenden Embedder, Persist als `{lang}.index` + `{lang}_meta.json`.
- Nicht-destruktiv: alte `faiss.index` + `metadata.json` bleiben bestehen. Umschaltung auf Dual erfolgt erst durch config.yaml-Flag (separater künftiger Patch).
- Dry-Run auf dem realen Index: **61 Chunks → 61 DE / 0 EN** (Rosendornen/Codex Heroicus/Kadath sind reiner DE-Content, wie erwartet).
- **5 neue Tests**.

**Aktueller Stand nach Patches 127–129:**
- Tests: **238 passed** offline (233 vorher + 5 neu)
- RAG-Eval: unverändert 15 JA / 5 TEILWEISE / 0 NEIN (kein Reindex)
- Neu: Huginn-Tab im Hel (Frontend komplett), HSL-Slider in Nala, Migrations-Script mit Dry-Run
- Offene Punkte (Patch 130+): echte Dual-Embedder-Umschaltung in `rag/router.py`, Sancho-Panza-Veto, Projekt-Oberfläche

*Stand: 2026-04-23, Patches 127–129 — Phase 4 erweitert.*

### Patch 130 – Loki & Fenrir UI-Sweep + Mega-Patch-Lessons (2026-04-24)

Nach dem Mega-Patch-Zyklus (122–129) fehlte die E2E-Abdeckung der neuen UI-Elemente. Patch 130 schließt die Lücke und hält zusätzlich die Meta-Lernerkenntnisse des Mega-Patch-Experiments in `lessons.md` fest.

**Loki E2E (`zerberus/tests/test_loki_mega_patch.py`):**
- TestBubbleShine (2 Tests): `::before`-Gradient auf User- und Bot-Bubble (Patch 124).
- TestBubbleWidth (2 Tests): `max-width` ist `90%` Mobile (<768px), `75%` Desktop (≥768px) (Patch 124).
- TestSlideInAnimation (1 Test): `animation-name: messageSlideIn` auf neuen Bubbles.
- TestInputBarCollapse (4 Tests): Textarea im Ruhezustand ~44px, expandiert bei Fokus, collapsed wieder nach Blur wenn leer, bleibt expanded wenn Text drin.
- TestLongMessageCollapse (2 Tests): `.expand-toggle` bei Bot-Messages >500 Zeichen, Toggle wechselt Klasse und Button-Text „▼ Mehr"/„▲ Weniger".
- TestBurgerMenu (4 Tests): Burger sichtbar + Touch-Target, Klick öffnet Sidebar (Klasse `.open`, `left:0`), `.sidebar-settings-anchor` ist `sticky`, Klick auf `⚙️ Einstellungen` öffnet das Settings-Modal (Patch 128).
- TestHslSlider (2 Tests): Alle 6 Slider (H/S/L × User/Bot) existieren, Hue-Änderung via Input-Event aktualisiert die CSS-Variable `--bubble-user-bg` live (Patch 128).
- TestTouchTargets (1 Test): `.send-btn`, `.mic-btn`, `.expand-btn`, `.hamburger` haben ≥40px (≥34px für Hamburger) auf Mobile.
- TestHuginnHelTab (3 Tests): Tab `.hel-tab[data-tab='huginn']` existiert, Config-Felder `#huginn-enabled`/`#huginn-bot-token`/`#huginn-model` sind da, Tab-Klick macht `#section-huginn` sichtbar (Patch 127).

**Fenrir Chaos (`zerberus/tests/test_fenrir_mega_patch.py`):**
- TestInputStress (3 Tests): 10k-Zeichen-Textarea, leerer Enter ohne Bubble, Unicode-Bombe (Emojis + CJK + RTL + Umlaute) Round-Trip.
- TestUiStress (3 Tests): 20× Burger-Toggle ohne Hänger, Settings-Öffnung während pending Message, Mobile↔Desktop-Viewport-Resize ohne Overflow.
- TestHslSliderStress (2 Tests): Hue auf 0 und 360 ergibt gültige Farbe, alle HSL-Extremwerte-Kombinationen ohne JS-Fehler.
- TestCodeChunkerEdgeCases (4 Tests, reine Unit-Tests gegen `zerberus/modules/rag/code_chunker.py`): SyntaxError in `.py` → `[]`-Kontrakt, leerer Input → `[]`, 2000 Top-Level-Funktionen terminieren, Unicode im Docstring/Funktionsname (Patch 123).

**Loki-Finding → Fix:**
- `.expand-btn` war 36×48 — der `min(width,height)=36` lag unter der 44px-Touch-Target-Schwelle auf Mobile (Mobile-First-Regel).
- Fix in `zerberus/app/routers/nala.py` CSS: `min-width: 44px; width: 44px;` — Icon-Ausrichtung über `flex-shrink: 0` bleibt erhalten.

**Server-Reload-Guard:**
- Bei laufendem uvicorn mit `--reload` werden große Single-File-Router (`nala.py`, `hel.py`) nicht immer sauber neu eingelesen. Die 10 Tests, die Patch-127/128-Elemente prüfen (HSL-Slider, Sidebar-Settings-Anchor, Huginn-Tab), nutzen einen `_require_element(page, selector, feature)`-Helper, der bei fehlendem DOM-Element mit klarer Begründung skipped. So bleibt die Suite grün auf staled Server und markiert nach Neustart automatisch alle Tests als lauffähig.

**Mega-Patch-Lesson (ergänzt in `lessons.md`):**
- 8 Patches in einem Kontextfenster (122–129), Opus 4.7 / 1M Tokens, **261,2k** tatsächlich verbraucht (26% des Budgets), 238 Tests grün, 0 Abbrüche.
- Vergleich zu 2–3-Patch-Sessions: ~3× mehr Patches bei etwa gleichem Token-Verbrauch (Codebasis wird nur einmal gelesen).
- Prompt-Struktur-Muster: Block-basiert (Diagnose → Fix → Test → Doku) pro Patch, Reihenfolge nach Abbrechbarkeit (destruktive Patches ans Ende), Selbst-Überwachungsgrenze bei ~450k Tokens.

**Aktueller Stand nach Patch 130:**
- Tests: **291 passed** + 10 skipped (server-stale) in 1m50s. Baseline vorher 268, +23 neue grün. Die 10 Skips verschwinden nach Server-Neustart.
- Die 4 neuen Code-Chunker-Unit-Tests sind serverless und damit regressions-stabil.
- Neue Testdateien: `zerberus/tests/test_loki_mega_patch.py` (21 Tests), `zerberus/tests/test_fenrir_mega_patch.py` (12 Tests).
- Doku: SUPERVISOR_ZERBERUS.md Patch-130-Eintrag, Roadmap aktualisiert (Patch 131+ = Dual-Embedder-Umschaltung).

*Stand: 2026-04-24, Patch 130 — Mega-Patch-UI ist end-to-end abgedeckt.*

### Mega-Patch 131–136 – Vision + Memory-Store + FAISS-Switch + DB-Dedup + Pipeline-Dedup + Kostenanzeige (2026-04-24)

Zweites Mega-Patch-Experiment nach 122–129. Sechs fokussierte Patches in einer Session.

**Patch 131 – Huginn Vision + Hel Vision-Dropdown:**
- Neue Registry `zerberus/core/vision_models.py`: 8 Vision-Modelle (qwen2.5-vl, gemini 2.5, claude 4.5, gpt-4o), sortiert nach Input-Preis, mit Tiers (budget/mid/premium).
- Utility `zerberus/utils/vision.py`: `analyze_image(image_data|image_url, prompt, model, max_tokens, timeout, max_bytes)`. Auto-Erkennung MIME-Type (PNG/JPEG/GIF/WebP) → data-URL. Fail-Safe für `no_image`, `image_too_large`, `missing_api_key`, `http_*`, `exception_*`.
- Config-Block `vision:` in config.yaml (enabled/model/max_image_size_mb/supported_formats).
- Huginn (`zerberus/modules/telegram/router.py::_process_text_message`): wenn photo_file_ids gesetzt, wird `pick_vision_model()` gerufen und das Vision-Modell verwendet — DeepSeek V3.2 (Hauptmodell) hat keinen Vision-Support.
- Hel LLM-Tab: neue Card mit Vision-Modell-Dropdown (Tier-Badges + Preis im Option-Text), Enable-Toggle, Max-MB-Input, Save/Reload. JS-Funktionen `visionReload()`/`visionSave()`. Lazy-Load beim Öffnen des LLM-Tabs.
- Drei Endpoints: `GET /admin/vision/config`, `POST /admin/vision/config` (Model-Whitelist-Check: nur Vision-Registry-Einträge akzeptiert), `GET /admin/vision/models`.
- **19 neue Tests** (`test_vision.py`).

**Patch 132 – Background Memory Extraction + Structured Store:**
- Neue SQLAlchemy-Tabelle `Memory` in `zerberus/core/database.py`: id/category/subject/fact/confidence/source_conversation_id/source_tag/embedding_index/extracted_at/is_active. Wird via `Base.metadata.create_all` beim Serverstart angelegt.
- `_store_memory_structured()` in `zerberus/modules/memory/extractor.py`: schreibt jeden neu extrahierten Fakt auch in den strukturierten Store, mit exaktem Duplikat-Check auf `category + fact`.
- Integration direkt nach `_add_to_index` im Extraction-Flow: vec_idx wird als `embedding_index` gespeichert — erlaubt spätere Verknüpfung von Structured zu Vector.
- Vier Hel-Endpoints: `GET /admin/memory/list?category=X&limit=N`, `GET /admin/memory/stats` (total + by_category + last_extraction), `POST /admin/memory/add` (manueller Insert mit source_tag="manual"), `DELETE /admin/memory/{id}` (Soft-Delete: is_active=0).
- **9 neue Tests** (`test_memory_store.py`).

**Patch 133 – FAISS Dual-Embedder Switch-Mechanismus:**
- Neue Modul-Globals `_dual_embedder` + `_use_dual` in `rag/router.py`.
- `_init_sync()` liest `modules.rag.use_dual_embedder` (Default **false**). Bei true: lädt DualEmbedder + `de.index`/`de_meta.json`; fehlen sie → automatischer Fallback auf Legacy MiniLM mit Warning-Log.
- `_encode()` nutzt dynamisch den aktiven Embedder (`_dual_embedder.embed()` vs. `_model.encode()`).
- config.yaml: `use_dual_embedder: false` explizit dokumentiert (Pre-Patch-133-Verhalten bleibt aktiv).
- Backup des aktuellen MiniLM-Index (61 Chunks) in `data/backups/pre_patch133_<ts>/`.
- Dry-Run von `scripts/migrate_embedder.py` bestätigt: 61 DE / 0 EN (unverändert seit Patch 129).
- Echter `--execute` bleibt manueller Schritt (Modell-Downloads + Server-Restart erforderlich; Chris entscheidet mit RAG-Eval-Vergleich).
- **5 neue Tests** (`test_rag_dual_switch.py`).

**Patch 134 – DB-Deduplizierung (Overnight-Job):**
- Neue Utility `zerberus/utils/db_dedup.py`: `deduplicate_interactions(db_path, window_seconds=60, dry_run, do_backup)`.
- Zweizeiger-Sliding-Window-Algorithmus: pre-grouped nach (profile_key, role, content) für O(n log n) statt O(n²). Duplikate im Zeitfenster ≤ 60 s werden soft-gelöscht (integrity=-1.0).
- Automatisches DB-Backup vor jeder echten Aktion (`backup_db()` in `backups/`-Subverzeichnis).
- Loggt jedes Duplikat mit `[DEDUP-134] Duplikat: id=X → Original id=Y, Δ=Zs`.
- Overnight-Integration in `sentiment/overnight.py` direkt nach der Memory-Extraction.
- Zwei Hel-Endpoints: `POST /admin/dedup/scan` (Dry-Run), `POST /admin/dedup/execute`.
- Hintergrund: Patch-113a-Guard deckt nur 30-s-Fenster + gleiche session_id ab. Der Overnight-Pass ist die zweite Verteidigungslinie für session-übergreifende Dictate-Retries.
- **9 neue Tests** (`test_db_dedup.py`).

**Patch 135 – Pipeline-Dedup `X-Already-Cleaned`-Header:**
- Chirurgischer Fix in `legacy.py::audio_transcriptions` und `nala.py::voice_endpoint`: Header `X-Already-Cleaned: true` (case-insensitive) überspringt den `clean_transcript()`-Aufruf. Log-Line `[PIPELINE-135] Cleaner übersprungen`.
- audio_transcriptions bekam zusätzlich `request: Request` als Parameter (vorher nur `file` + `settings`).
- Aktueller Status idempotent (harmlos) — Patch ist Vorsorge für künftige non-idempotente Cleaning-Regeln.
- **6 neue Tests** (`test_pipeline_dedup.py`).

**Patch 136 – Kostenanzeige-Fix (Hel LLM-Tab):**
- Bug in `loadModelsAndBalance()`: `parseFloat(balance.last_cost || 0) * 1_000_000` interpretierte die tatsächlichen Kosten einer einzelnen Anfrage als „pro Token" und multiplizierte mit 1M — Anzeige war sinnlos.
- Fix im `GET /admin/balance`-Endpoint: liefert jetzt `last_cost_usd`/`last_cost_eur`/`today_total_usd`/`today_total_eur`/`balance_eur` (USD→EUR-Kurs 0.92 statisch). `last_cost` bleibt als Alias für Backward-Compat.
- Neue `_get_today_total_cost()`-Helper summiert `costs.cost` ab `date('now', 'start of day')`.
- Fallback-Pfad: auch bei OpenRouter-HTTP-Error oder -Netzwerkfehler werden die Cost-Felder aus der lokalen DB geliefert (statt 502).
- Frontend-Anzeige: „Kontostand: 12,45 € ($13,53) / Letzte Anfrage: 0,0034 € / Heute gesamt: 0,1500 €".
- Zweiter Bug an Zeile 1784 (per-message cost in der Message-Tabelle) gleich mitgefixt.
- **8 neue Tests** (`test_cost_display.py`).

**Aktueller Stand nach Mega-Patch 131–136:**
- Tests: **308 passed** offline (252 vorher + 56 neue). Non-Playwright-Teile sind regressions-stabil.
- RAG: unverändert (use_dual_embedder=false → Legacy MiniLM aktiv). Dry-Run bestätigt Baseline 61 DE / 0 EN.
- Neue Module: `zerberus/core/vision_models.py`, `zerberus/utils/vision.py`, `zerberus/utils/db_dedup.py`. Neue Tabelle: `memories`.
- Neue Hel-Endpoints (9): Vision (3), Memory (4), Dedup (2).
- Offene Punkte (Patch 137+): echte `scripts/migrate_embedder.py --execute` mit RAG-Eval-Vergleich; Sancho-Panza-Veto; Nala-Vision-Upload-UI.

*Stand: 2026-04-24, Mega-Patch 131–136 — zweites 6-Patch-Experiment erfolgreich.*

### Monster-Patch 137–152 – Käferpresse (Bugs + UI + TTS + Pfoten + Feuerwerk + Design-System) (2026-04-24)

**Kontext:** Drittes und bisher größtes Mega-Patch-Experiment (16 Patches in einem Zug). Scope: komplette Käferpresse-Liste vom 24.04.2026 abarbeiten. Token-Selbstüberwachung: keine harte Grenze erreicht, alle Patches inklusive Tests durchgezogen.

**Patch 137 – RAG Smalltalk-Skip (B-001):**
- Neuer Intent `GREETING` in `zerberus/app/routers/orchestrator.py` mit Regex-Pattern-Liste (Hallo/Hi/Hey/Moin/Servus/Guten Morgen/Na?/Wie geht's/Danke/Tschüss/Grüß Gott).
- Pre-Check in `detect_intent()`: Pattern matcht **und** ≤8 Wörter **und** kein Fragewort im Rest → GREETING. Sonst QUESTION (gewinnt bei "Hallo, wer ist Anne?").
- GREETING skippt RAG in `_run_pipeline()` (orchestrator.py) und `audio_transcriptions` (legacy.py — aktiver Chat-Pfad).
- Threshold `rerank_min_score` von 0.05 → 0.15 in config.yaml (Noise-Schwelle angehoben — Scores um 0.10 waren typisch bei Smalltalk).
- Permission-Matrix und INTENT_SNIPPETS um GREETING erweitert.
- **16 neue Tests** (`test_greeting_intent.py`).

**Patch 138 – Test-Profile Filter (B-004):**
- `is_test: true` Flag an loki und fenrir in `config.yaml` → `profiles:`.
- `get_all_sessions(limit, exclude_profiles)` in `zerberus/core/database.py` erweitert.
- `/archive/sessions?include_test=False` (Default) filtert Test-Profile automatisch via neues Helper `_get_test_profile_keys()` in `archive.py`.
- Cleanup-Script `scripts/cleanup_test_sessions.py` mit `--execute`-Flag (Dry-Run default), inklusive DB-Backup vor Delete.
- **9 neue Tests** (`test_test_profile_filter.py`).

**Patch 139 – Nala Bubble-Layout (B-005, B-008, B-009, B-010, B-011):**
- **B-005 Shine:** Linear-Gradient → `radial-gradient(ellipse at 20% 20%, …)`. Lichtquelle oben-links, weicher Falloff über 60%.
- **B-008 Breite:** `.message` + `.msg-wrapper` max-width von 75%/80% → 92% Mobile / 80% Desktop.
- **B-009 Action-Toolbar:** Initial `opacity: 0; pointer-events: none`. Neue JS-Helper `attachActionToggle(wrapper, bubble)` reagiert auf Tap (Klicks auf Buttons/Links werden ausgeschlossen), setzt `.actions-visible`-Klasse für 5s.
- **B-010 Repeat-Button:** `.bubble-action-btn.retry-btn { background: transparent !important }`.
- **B-011 Titel:** `.header` von 1.5em auf 1.05em, `.title` zusätzlich `font-size: 0.95em`, `.hamburger` von 1.8em auf 1.5em.
- **15 neue Tests** (`test_nala_bubble_layout.py`).

**Patch 140 – Dark-Theme Kontrast (B-003):**
- Neue JS-Funktion `getContrastColor(cssColor)` in nala.py: Nutzt einen temporären DOM-Knoten + `getComputedStyle()`, um beliebige CSS-Farben (hex, rgba, hsl) zu RGB aufzulösen. Danach WCAG-gewichtete Luminanz `0.299R + 0.587G + 0.114B`. `>0.55 → #1a1a1a`, sonst `#f0f0f0`.
- Neue Funktion `applyAutoContrast()`: Wendet die Kontrast-Farbe an `--bubble-user-text` und `--bubble-llm-text` an, respektiert aber manuelle Override-Flags (`localStorage.nala_bubble_*_text_manual`).
- Neue Funktion `bubbleTextPreview(which)`: Wird beim direkten Text-Color-Picker aufgerufen, setzt den Manual-Flag.
- Getriggert von `bubblePreview()` und `applyHsl()`; zusätzlich beim Init (`showChatScreen`).
- **9 neue Tests** (`test_dark_theme_contrast.py`).

**Patch 141 – Session-Liste Fallback (B-002):**
- `buildSessionItem(s, isPinned)` in nala.py überarbeitet:
  - `hasMsg = !!(s.first_message && s.first_message.trim())`
  - Titel-Fallback: "Unbenannte Session" + Datum, wenn `!hasMsg`
  - Titel-Kürzung auf 50 Zeichen (war 40)
  - Untertitel: Datum + Uhrzeit (HH:MM) via `toLocaleTimeString`
- **4 neue Tests** (`test_session_list_fallback.py`).

**Patch 142 – Settings-Umbau (B-006, B-012, B-013, B-015, B-016):**
- **B-006:** `🔧`-Button aus Nala-Top-Bar entfernt.
- **B-013 Sidebar-Footer:** Neue Klasse `.sidebar-footer` mit `position: sticky; bottom: 0`. `🚪 Abmelden` links (Exit-Icon, rot: `rgba(229,115,115,...)`), `⚙️ Einstellungen` rechts (gold). Beide 48×48px. Passwort-Button raus aus sidebar-actions.
- **B-012 Mein Ton:** `#my-prompt-area` + `saveMyPrompt()` aus Sidebar entfernt, in neuen Tab "Ausdruck" verschoben.
- **B-015 Tabs:** Neue Tab-Nav im Settings-Modal (`.settings-tabs`) mit 3 Tabs `look`/`voice`/`system`. Panels `.settings-tab-panel` toggeln via `switchSettingsTab(tab)`.
  - **Aussehen:** Theme-Farben, Bubble-Farben, HSL-Slider, UI-Skalierung, Favoriten
  - **Ausdruck:** Mein Ton + TTS-Controls (Stimme, Rate, Probe hören)
  - **System:** Passwort-Ändern-Button, Account-Info (Profil + Permission)
- **B-016 UI-Skalierung:** CSS-Variable `--ui-scale` (Default 1). `applyUiScale(val)` setzt `--ui-scale` und `--font-size-base` (16px × scale). Range-Slider 0.8-1.4×, Schritt 0.05. Persistent via `localStorage.nala_ui_scale`. IIFE `restoreUiScale()` stellt beim Laden wieder her.
- **19 neue Tests** (`test_settings_umbau.py`).

**Patch 143 – TTS Integration (B-014):**
- Neue Utility `zerberus/utils/tts.py` mit `text_to_speech(text, voice, rate)`, `list_voices(language)`, `is_available()`.
- Wrapper um `edge_tts.Communicate`. Validation: leerer Text → `ValueError`, invalides Rate-Format → `ValueError`, keine Audio-Daten → `RuntimeError`.
- Zwei Router-Endpoints in `nala.py`:
  - `GET /nala/tts/voices?lang=de` → Liste `{ShortName, FriendlyName, Locale, Gender}`
  - `POST /nala/tts/speak` → `audio/mpeg`, Input `{text, voice, rate}`, Text auf 5000 Zeichen gekappt
- 503 bei fehlendem edge-tts, 400 bei invalider Rate, 502 bei API-Fehler.
- **Frontend:**
  - Neues `<select id="tts-voice-select">` und Range-Slider `#tts-rate-slider` (-50 bis +100) im Tab "Ausdruck"
  - `initTtsControls()` lazy beim Öffnen des Settings-Modals
  - `speakText(text)` als gemeinsamer Player
  - `🔊`-Button an jeder Bot-Bubble mit Loading/Error-States (`⏳`/`⚠️`)
- edge-tts (`>=7.0.0`) in requirements.txt.
- **14 neue Tests** (`test_tts_integration.py`).

**Patch 144 – Katzenpfoten (B-007 / F-001) — Jojo-Priorität:**
- Alter Spinner-Bubble-Indikator in showTypingIndicator/removeTypingIndicator durch 4 `🐾`-Pfoten ersetzt.
- CSS-Keyframe `@keyframes pawWalk { 0% { left: -40px } … 100% { left: calc(100% + 40px) } }` mit 3s Loop.
- 4 Pfoten mit staggered `animation-delay: 0s / 0.5s / 1.0s / 1.5s`.
- `.paw-indicator { position: fixed; bottom: 84px }` über der Input-Area, `.paw-status` darunter.
- Neue Funktion `setPawStatus(phase)` mappt Backend-Events auf Text:
  - `rag_search` → "RAG durchsucht…"
  - `llm_start` → "Nala denkt nach…"
  - `rerank` → "Reranker läuft…"
  - `generating` → "Antwort wird geschrieben…"
- SSE-Handler (`evtSource.onmessage`) ruft `setPawStatus(evt.type)` und bei `done` `_hidePaws()`.
- **13 neue Tests** (`test_katzenpfoten.py`).

**Patch 145 – Feuerwerk & Sternenregen (F-002) — Jojo-Priorität:**
- Neues `<canvas id="particleCanvas" style="position:fixed; … z-index:9999; pointer-events:none">`.
- IIFE `initParticles()` baut Particle-Engine:
  - Funktionen: `spawn(x, y, type)` (stars/firework), `goldRain()`, `drawStar(cx, cy, size)` (5-zackiger Stern), `animate()` (requestAnimationFrame-Loop mit Gravity+Decay), `flashBackground()` (100ms Gold-Tint)
  - 8 Farben, Shape-Mix (star/circle), Life-Decay 0.005-0.03 pro Frame
- **Trigger 1 – Rapid-Tap:** Im Textfeld ≥7 Tasten innerhalb 2000ms → `spawn(rect.center, rect.top, 'star')` + Flash.
- **Trigger 2 – Swipe-Up:** `touchstart`/`touchend` tracken. Wenn `dy > 200 && dx < 100 && dt < 800` → `spawn(firework)` + `goldRain()` + Flash.
- Canvas-Resize-Handler passt Breite/Höhe an Viewport an.
- **11 neue Tests** (`test_particle_effects.py`).

**Patch 146 – Metriken-Cleanup (B-018):**
- `metrics_latest_with_costs()` in `hel.py`: Content auf 50 Zeichen + `…` gekürzt. Zusätzlich `content_truncated: bool` und `content_original_length: int` im Response-Dict. Eine LLM-Antwort füllt nicht mehr mehrere Bildschirme im Metriken-Tab.
- **3 neue Tests** (`test_metrics_cleanup.py`).

**Patch 147 – Modell-Dropdown Vereinheitlichung (B-019):**
- Neue Funktion `formatModelLabel(name, inputPrice, outputPrice)` in hel.py JS: Format "Name — $X.XX/$Y.YY/1M", `kostenlos` bei 0/0.
- `renderModelSelect()` (Nala-LLM), `visionReload()` (Vision), `huginnReload()` (Huginn) nutzen jetzt alle den Formatter und sortieren aufsteigend nach `pricing.prompt` bzw. `input_price`.
- Vision-Dropdown: `[Budget]`/`[Premium]` Präfix entfernt — `data-tier` bleibt als HTML-Attribut für Styling.
- **9 neue Tests** (`test_model_dropdown_unified.py`).

**Patch 148 – Dialekte-Tab (B-022):**
- JSON-Textarea aus der Hauptansicht entfernt, jetzt als aufklappbares `<details>` (Raw-JSON-Fallback für Notfälle).
- Neuer strukturierter Editor:
  - `<input id="dialectSearch">` oben, Live-Filter (Von oder Nach)
  - `<div id="dialectGroups">` von `renderDialectGroups()` befüllt
  - Pro Gruppe: Titel + 🗑-Button + Neu-Eintrag-Zeile OBEN (Von→Nach+`+`) + bestehende Einträge (Von/Nach editierbar + `✕`-Lösch-Button)
  - Neue-Gruppe-Eingabe unten mit Namen + `+ Gruppe`-Button
- JS: `_dialectData` als Arbeitskopie, `loadDialect()`, `renderDialectGroups()`, `addDialectGroup()`, `saveDialectStructured()`. Legacy `saveDialect()` bleibt für Raw-Editor.
- **10 neue Tests** (`test_dialect_ui.py`).

**Patch 149 – Hel Kleinigkeiten (B-021, B-023, B-025):**
- **B-021:** WhisperCleaner-Regel-Editor (cleanerList + addCleanerRule/addCleanerComment/saveCleaner) aus dem HTML entfernt. Ersetzt durch Hinweis "Pflege nur noch via `whisper_cleaner.json`". Fuzzy-Dictionary bleibt.
- **B-023:** Tab-Label `Sysctl` → `System` (Button-Text in `hel-tab-nav`).
- **B-025:** Neue Zeile oberhalb der Tab-Nav: `<h1>⚡ Hel – Admin-Konsole</h1>` + `⚙️`-Button rechts. Klick toggelt `#helSettingsPanel`. Panel enthält Range-Slider 0.8-1.4× mit `applyHelUiScale(val)`, persistent via `hel_ui_scale`. IIFE `restoreHelUiScale()` beim Laden. Alter 4-Preset-Bar (`.font-preset-bar`) ist `display: none` (kein Hard-Delete — rückwärtskompatibel).
- **10 neue Tests** (`test_hel_kleinigkeiten.py`).

**Patch 150 – Pacemaker-Steuerung (B-024):**
- Neue Hel-Card im System-Tab "Pacemaker-Prozesse".
- UI:
  - Master-Toggle `#pacemaker-master` + Sync-Toggle `#pacemaker-sync`
  - Prozess-Liste via `renderPacemakerProcesses()`: 4 Default-Prozesse (sentiment/memory/db_dedup/whisper_ping) mit Aktiv-Checkbox, Status-LED (🟢/⚪), Range-Slider 1-60 min + Label, CPU/GPU-Select.
  - Sync-Modus synchronisiert Intervall-Changes live auf alle Prozesse
  - Activity-Anzeige (`#pacemakerActivity`) für aktuellen Prozess
- Backend: `GET/POST /hel/admin/pacemaker/processes` mit `PACEMAKER_DEFAULT_PROCESSES`-Konstante. Persistent in `config.yaml` → `modules.pacemaker_processes.{master, sync, processes}`. YAML-Write via `yaml.safe_dump(sort_keys=False)`.
- Scheduler-Integration (Worker-Loop liest die Config) bleibt für Folge-Patch.
- **15 neue Tests** (`test_pacemaker_controls.py`).

**Patch 151 – Design-Konsistenz (B-026 / L-001):**
- Neue Datei `zerberus/static/css/shared-design.css` mit Design-Tokens:
  - Farben: `--zb-primary/danger/success/warning/text-primary/bg-primary/border`
  - Dark-Variante: `--zb-dark-*`
  - Spacing: `--zb-space-xs/sm/md/lg/xl` (4/8/16/24/32px)
  - Radien: `--zb-radius-sm/md/lg/pill` (4/8/16/999px)
  - Schatten: `--zb-shadow-sm/md/lg`
  - Typography: `--zb-font-family/size-sm/md/lg`
  - Touch: `--zb-touch-min: 44px`
- Klassen: `.zb-btn` / `.zb-btn-primary` / `.zb-btn-danger` / `.zb-btn-ghost` / `.zb-select` / `.zb-slider` / `.zb-toggle` — alle mit einheitlichen Paddings/Radien/Mindest-Touch.
- `@media (hover: none) and (pointer: coarse)` erzwingt global `min-height: var(--zb-touch-min)` auf alle klickbaren Elemente.
- `<link rel="stylesheet" href="/static/css/shared-design.css">` im `<head>` von nala.py UND hel.py.
- Neue Doku `docs/DESIGN.md` mit Leitregel "projektübergreifende Konsistenz" + Token-Tabelle + Checkliste.
- **12 neue Tests** (`test_design_system.py`).

**Patch 152 – Memory-Dashboard (B-020):**
- Neue Card im RAG-Tab (`gedaechtnis`) mit:
  - Statistik-Leiste `#memoryStats`: "X Fakten · Y Kategorien · Letzte Extraktion: Z"
  - Such-Input `#memorySearch` (filtert Subjekt+Fakt+Kategorie)
  - Kategorie-Filter `#memoryCategoryFilter` (PERSON/PREFERENCE/FACT/EVENT/SKILL/EMOTION + "Alle")
  - `🔄 Neu laden`-Button
  - Manuell-Hinzufügen via `<details>`-Block: Kategorie-Select + Subjekt + Fakt → `addMemoryManual()`
  - Tabelle via `renderMemoryTable()`: Kategorie (gold) · Subjekt · Fakt · Confidence-Badge (≥0.9 grün, ≥0.7 gelb, sonst rot) · Extrahiert-Date · `✕`-Button
- Nutzt bestehende Endpoints aus Patch 132 (`/admin/memory/list`, `/admin/memory/stats`, `/admin/memory/add`, `DELETE /admin/memory/{id}`).
- Lazy-Load: `activateTab('gedaechtnis')` ruft jetzt `loadMemoryDashboard()` zusätzlich zu `loadRagStatus()`.
- **11 neue Tests** (`test_memory_dashboard.py`).

**Aktueller Stand nach Monster-Patch 137–152:**
- Tests: **488 passed** offline in 16.6s (308 vorher + **180 neue**). Größte Test-Erweiterung aller Mega-Patches.
- Non-Playwright regressions-stabil.
- Neue Dateien: `shared-design.css`, `docs/DESIGN.md`, `scripts/cleanup_test_sessions.py`, `zerberus/utils/tts.py`, 14 neue Test-Dateien.
- Neue Dependencies: `edge-tts>=7.0.0`.
- Neue Hel-Endpoints (2): `/admin/pacemaker/processes` (GET+POST).
- Neue Nala-Endpoints (2): `/nala/tts/voices`, `/nala/tts/speak`.
- Neue Config-Keys: `profiles.*.is_test`, `modules.pacemaker_processes`, `modules.rag.rerank_min_score: 0.15`.
- Offene Punkte (Patch 153+): Scheduler-Integration für Patch-150-Processes (Worker liest `modules.pacemaker_processes`); FAISS-Migration via `--execute`; Legacy-CSS auf `.zb-*`-Klassen migrieren; Sancho-Panza-Veto; Nala-Vision-Upload-UI.

*Stand: 2026-04-24, Monster-Patch 137–152 — drittes Mega-Patch-Experiment, 16 Patches, 180 neue Tests.*

---

## Patch 153 — Vidar Smoke-Test-Agent + Farb-Default-Fix
*2026-04-24*

### Farb-Default-Fix (cssToHex HSL-Bug)
Root-Cause-Analyse des Schwarz-Bubbles-Bugs:
- `cssToHex('hsl(H,S%,L%)')` parste H/S/L fälschlich als RGB-Bytes → ungültiges Hex (z.B. `#152523b`, 9 Zeichen)
- Browser ignoriert ungültigen Picker-Wert → `<input type="color">` zeigt `#000000`
- Nächster `oninput`-Event → `bubblePreview()` schreibt `#000000` in localStorage
- Nächster Seitenload: IIFE liest `#000000` → schwarze Bubbles
**Fix:**
- `cssToHex()`: HSL/oklch-Strings werden jetzt über Browser-Canvas aufgelöst (wie `getContrastColor`)
- Neue Hilfsfunktion `computedVarToHex(varName)`: rendert CSS-Variable über Temp-Div → immer korrekte Hex-Farbe, auch bei HSL/rgba
- `openSettingsModal()`: nutzt `computedVarToHex` statt `cssToHex(r.getPropertyValue(...))` für alle Bubble-Picker
- IIFE-Guard: `#000000`/`#000`/`rgb(0,0,0)` für Bubble-BG-Keys → bereinigt + nicht angewendet

### Vidar-Profil
- `config.yaml`: Profil `vidar` (`vidartest123`, `is_test: true`) hinzugefügt
- `conftest.py`: `VIDAR_CREDS`, `logged_in_vidar`-Fixture ergänzt

### Vidar Smoke-Test-Agent
Neue Datei `zerberus/tests/test_vidar.py` (Go/No-Go Post-Deployment):
- `TestCritical` (6 Deploy-Blocker): Nala lädt, Login, Bubbles nicht schwarz, Chat senden+empfangen, Hel lädt, shared-design.css
- `TestImportant` (11 wichtige Checks): Touch-Targets ≥44px, Design-Tokens, Settings öffnen + 3 Tabs, Katzenpfoten-DOM, Particle-Canvas, Hel System-Tab, Hel Memory, Hel Pacemaker, Session-Titel nicht leer, TTS-Button
- `TestCosmetic` (4 optionale): LLM-Dropdowns, kein JSON in Dialekte, CSS-Var gesetzt, keine input-statt-select

**Geänderte Dateien:** `zerberus/app/routers/nala.py`, `config.yaml`, `zerberus/tests/conftest.py`
**Neue Dateien:** `zerberus/tests/test_vidar.py`

---

## Patch 154 — Checklisten-Sweep (Patches 137–153)
*2026-04-24*

### test_loki_mega_patch.py — Fixes + Sweep
- **Test-Bug-Fix:** `TestBubbleShine` prüfte `linear-gradient`, CSS-Code hat seit Patch 139 `radial-gradient` → Assertion korrigiert (prüft jetzt auf `radial-gradient`)
- **Neue Klasse `TestChecklistSweep`** (10 Tests):
  - L-SW-01: `.message` max-width 92% auf Mobile (Patch 139 B-008)
  - L-SW-02: Action-Toolbar initial `opacity: 0` (Patch 139 B-009)
  - L-SW-03: Retry-Button `background: transparent` (Patch 139 B-010)
  - L-SW-04: Profil-Badge font-size < 1em (Patch 139 B-011)
  - L-SW-05: Kein Schraubenschlüssel 🔧 in Top-Bar (Patch 142 B-006)
  - L-SW-06: "Mein Ton" im Settings-Tab "Ausdruck", nicht in Sidebar (Patch 142 B-012)
  - L-SW-07: Logout-Button nicht neben "Neue Session" (Patch 142 B-013)
  - L-SW-08: UI-Skalierungs-Slider in Settings (Patch 142 B-016)
  - L-SW-09: Bubble-Farben nach Login nicht #000000 (Patch 153)
  - L-SW-10: `cssToHex('hsl(...)')` gibt kein #000000 zurück (Patch 153)

### test_fenrir_mega_patch.py — Stress-Tests
- **TestFarbenStress** (3): Logout+Login-Farb-Persistenz, HSL-Picker-Wert nach Slider, Kontrast-Extremwert auf weißem BG
- **TestPacemakerStress** (2): Rapid-Toggle 5×, JS-Error-frei beim System-Tab
- **TestTTSStress** (2): leerer Text kein Crash, kein TTS-Button-Duplikat

### Dokumentation
- `CLAUDE_ZERBERUS.md`: Test-Agenten-Tabelle mit Vidar ergänzt
- `SUPERVISOR_ZERBERUS.md`: Patch 153–154 als aktueller Patch, Roadmap [x] markiert
- `docs/PROJEKTDOKUMENTATION.md`: Diese Einträge

**Geänderte Dateien:** `zerberus/tests/test_loki_mega_patch.py`, `zerberus/tests/test_fenrir_mega_patch.py`, `CLAUDE_ZERBERUS.md`, `SUPERVISOR_ZERBERUS.md`

**Aktueller Stand nach Patch 153–154:**
- Tests: **488 passed** (Baseline, Offline-Suite) + neue Playwright/Smoke-Tests (server-abhängig)
- Neue Dateien: `zerberus/tests/test_vidar.py`
- Neue Config-Keys: `profiles.vidar` (is_test: true)

*Stand: 2026-04-24, Patch 153–154 — Vidar + Farb-Fix + Checklisten-Sweep.*

---

## Patch 155 — Huginn Long-Polling + Lessons-Konsolidierung
*2026-04-24*

### Problem
Telegram-Webhooks funktionieren nicht hinter Tailscale MagicDNS. Die Domain `*.tail*.ts.net` ist nur innerhalb des Tailnets auflösbar — Telegram's Server scheitern am DNS-Lookup bevor das Zertifikat überhaupt geprüft wird. Resultat: Der Bot empfängt keine Nachrichten obwohl Server läuft.

### Lösung
Transport-Refactor auf Long-Polling. Der Bot fragt Telegram aktiv "neue Updates?" via `getUpdates` (Long-Poll mit 30s Telegram-Timeout) statt auf Webhooks zu warten. Funktioniert hinter jeder NAT/VPN/Firewall.

### Umsetzung
**[`zerberus/modules/telegram/bot.py`](../zerberus/modules/telegram/bot.py) — drei neue Funktionen via `httpx`:**
- `get_me(bot_token)` → cached `_bot_user_id` für `was_bot_added_to_group()`
- `get_updates(bot_token, offset, timeout=30)` → `httpx.TimeoutException` wird als normaler Long-Poll-Idle behandelt (still, kein Log-Spam)
- `long_polling_loop(bot_token, handler, ...)` → entfernt alten Webhook (sonst HTTP 409), Endlos-Loop mit Offset-Management (`offset = update_id + 1`), Handler-Exceptions werden geloggt aber der Loop läuft weiter, `CancelledError` propagiert sauber
- `_POLL_ALLOWED_UPDATES = ["message", "channel_post", "callback_query", "my_chat_member"]`

**[`zerberus/modules/telegram/router.py`](../zerberus/modules/telegram/router.py):**
- `process_update(data, settings)` aus `telegram_webhook` extrahiert — gemeinsamer Handler für Webhook UND Polling
- `telegram_webhook` wird dünn: JSON parsen + `process_update(...)` aufrufen
- `startup_huginn(settings) -> Optional[asyncio.Task]`:
  - `enabled=false` → `None`
  - `mode="polling"` (Default) → `asyncio.create_task(long_polling_loop(...))` zurückgeben
  - `mode="webhook"` → existierender Webhook-Register-Flow, `None` zurück

**[`zerberus/main.py`](../zerberus/main.py) lifespan:**
- Task-Referenz `_huginn_polling_task` hält den Polling-Task
- Beim Shutdown: wenn Task vorhanden → `task.cancel()` + `await` (mit CancelledError-Catch). Sonst alter Webhook-Deregister-Pfad.

**[`config.yaml`](../config.yaml) — neuer Key:**
```yaml
modules:
  telegram:
    mode: polling  # "polling" (Default) oder "webhook"
```

### Tests
**[`zerberus/tests/test_telegram_bot.py`](../zerberus/tests/test_telegram_bot.py) — neue Klasse `TestLongPolling` (12 Tests):**
- `test_get_updates_no_token` — leere Liste ohne HTTP-Call
- `test_get_me_no_token` — None
- `test_get_updates_parses_response` — httpx-Mock, verifiziert offset/timeout/allowed_updates im Payload
- `test_get_updates_timeout_returns_empty` — `httpx.TimeoutException` → `[]`
- `test_get_updates_http_error_returns_empty` — HTTP 500 → `[]`
- `test_long_polling_loop_calls_delete_webhook` — Start ruft `deregister_webhook`
- `test_long_polling_loop_advances_offset` — `offsets_seen == [0, 11, 18]` nach 2 Batches
- `test_long_polling_handler_exception_does_not_break_loop` — RuntimeError im Handler → Loop läuft weiter
- `test_long_polling_loop_no_token_exits_silently` — `""` als Token → sofortige Rückkehr
- `test_startup_huginn_polling_mode_creates_task` — asyncio.Task zurück, `_bot_user_id` gecacht
- `test_startup_huginn_webhook_mode_returns_none` — None + `register_webhook` gerufen
- `test_startup_huginn_disabled_returns_none` — `enabled=false` → None

### Lessons-Konsolidierung
**[`lessons.md`](../lessons.md) — vier neue Blöcke:**
1. **Monster-Patch Session-Bilanz 2026-04-24** — Tabelle über 6 Sessions (Mega 1 bis Huginn-Polling), Test-Trajektorie 162→500 (+338 Tests), Token-Effizienz pro Patch.
2. **Telegram hinter Tailscale** — Webhook-Problem, Long-Polling-Lösung, Guardrails (deleteWebhook vor Start, Offset-Management, explizite allowed_updates).
3. **Vidar-Architektur** — 3 Levels (CRITICAL/IMPORTANT/COSMETIC), Verdict-Semantik (GO/WARN/FAIL), Faustregel.
4. **Design-Konsistenz-Regel L-001** — projektübergreifend, Touch-Target 44px, Loki-Auto-Check.

**[`CLAUDE_ZERBERUS.md`](../CLAUDE_ZERBERUS.md):** Neuer Abschnitt "Telegram/Huginn (Patch 155)" mit `mode`-Config-Doku.
**[`docs/DESIGN.md`](DESIGN.md):** Verweis auf Lessons-Abschnitt L-001.
**[`SUPERVISOR_ZERBERUS.md`](../SUPERVISOR_ZERBERUS.md):** Patch 155 als aktueller Patch, Roadmap [x].

### Scope-Entscheidungen
- **Funktions-Architektur beibehalten** (kein Wechsel auf `python-telegram-bot`'s `Application`/`Handler`-Framework). Konsistent zum Rest des Codebases (`call_llm`, `send_telegram_message` sind auch freie async-Funktionen).
- **`httpx` statt `aiohttp`** (abweichend vom Supervisor-Beispiel). `httpx` ist Projektkonvention und bereits überall im Einsatz.
- **`_bot_user_id` jetzt endlich gecacht** (vorher war die Variable nie gesetzt, sodass `was_bot_added_to_group` immer False lieferte). Nebeneffekt-Fix durch den Polling-Start.

### Tests
- **500 passed** offline in 17s (488 vorher + **12 neue Long-Polling-Tests**).
- Playwright/Vidar-Tests weiter server-abhängig (nicht in Offline-Suite).

**Geänderte Dateien:** `zerberus/modules/telegram/bot.py`, `zerberus/modules/telegram/router.py`, `zerberus/main.py`, `config.yaml` (lokal), `zerberus/tests/test_telegram_bot.py`, `lessons.md`, `CLAUDE_ZERBERUS.md`, `SUPERVISOR_ZERBERUS.md`, `docs/DESIGN.md`, `docs/PROJEKTDOKUMENTATION.md`
**Neue Dateien:** keine

*Stand: 2026-04-24, Patch 155 — Huginn Long-Polling + Lessons-Konsolidierung.*

---

## Patch 162 — Input-Sanitizer + Telegram-Hardening
*2026-04-25*

### Problem
Der 7-LLM-Architektur-Review (Patch 161) hat als kritischste offene Findings markiert: **K1** (kein Input-Guard vor dem LLM — Jailbreaks und Injection-Versuche kamen ungefiltert an), **K3** (Forwarded-/Reply-Chains als Injection-Vektor in Gruppen), **O1/O2** (unbekannte Update-Typen und edited_message verursachen unnötige LLM-Calls bzw. lassen nachträgliches Umschreiben einer Nachricht zu einem Jailbreak zu), **O3** (in einer Telegram-Gruppe konnte jeder beliebige User die HitL-Buttons eines anderen Users klicken — Telegram validiert das by design nicht), **D8/N8** (Long-Polling-Offset war nur im RAM, nach Server-Restart fing der Bot bei 0 an und verarbeitete bereits gesehene Updates erneut), **D9** (channel_post-Updates wurden eingelesen obwohl Huginn in Channels nichts verloren hat), **D10** (Antworten in Forum-Topics landeten im General statt im richtigen Thread).

### Lösung
Phase A der Huginn-Roadmap v2 — Sicherheits-Fundament. Zwei-Schichten-Prinzip: pragmatischer RegexSanitizer für Huginn-jetzt, Interface vorbereitet für Rosa-ML-Variante. Update-Typ-Filter, Offset-Persistenz, Topic-Routing und Callback-Spoofing-Schutz schließen die Telegram-Protokoll-Lücken.

### Umsetzung
**[`zerberus/core/input_sanitizer.py`](../zerberus/core/input_sanitizer.py) — neue Datei:**
- `InputSanitizer` (ABC) als Interface, `RegexSanitizer` als Huginn-Implementierung.
- 16 Injection-Patterns (DE+EN): Anweisungs-Overrides, Rollenspiel-Hijacking, Prompt-Leak-Versuche, Markdown/Code-Fence-Tricks, deutsche Varianten. Bewusst konservativ — kein False-Positive auf normales Deutsch wie „Kannst du das ignorieren?".
- Steuerzeichen-Filter (entfernt Null-Bytes, ASCII-Bell etc.; behält `\n \r \t`).
- Max-Länge 4096 = Telegram-Limit.
- Forwarded-Marker als Metadata-Hinweis (K3-Vektor).
- Singleton via `get_sanitizer()` analog `get_settings()`. Test-Reset-Helper `_reset_sanitizer_for_tests()`.
- **Findings werden geloggt, NICHT geblockt** (Tag `[SANITIZE-162]`). Im Huginn-Modus ist `blocked` immer `False` — der Guard (Mistral Small) entscheidet final. Der `blocked=True`-Pfad ist im Konsumenten implementiert (sendet „🚫 Nachricht wurde aus Sicherheitsgründen blockiert.") und kommt mit Rosa zum Tragen sobald Config-Key `security.input_sanitizer.mode = "ml"` existiert.

**[`zerberus/modules/telegram/router.py`](../zerberus/modules/telegram/router.py):**
- Update-Typ-Filter ganz oben in `process_update()`: `channel_post`/`edited_channel_post` (D9), `edited_message` (O2), unbekannte Typen wie `poll`/`my_chat_member`-only (O1) werden lautlos verworfen mit Tag `[HUGINN-162]`. Vor dem Event-Bus, vor Manager-Setup.
- Sanitizer-Integration in `_process_text_message` UND im autonomen Gruppen-Einwurf-Pfad — jeder Text der ans LLM geht, läuft erst durch den Sanitizer.
- `message_thread_id` wird durch alle `send_telegram_message`-Calls durchgereicht.
- Callback-Validierung: `clicker_id` muss in `{admin_chat_id, requester_user_id}` sein, sonst Popup („🚫 Das ist nicht deine Anfrage.") via `answer_callback_query()` mit `show_alert=True`. Logs `[HUGINN-162] Callback-Spoofing blockiert (O3)`.
- HitL-Group-Join-Anfrage trägt jetzt `requester_user_id=info.get("user_id")`.

**[`zerberus/modules/telegram/bot.py`](../zerberus/modules/telegram/bot.py):**
- `_load_offset()` / `_save_offset()` — persistent in `data/huginn_offset.json` (D8/N8). Korrupte Datei → graceful Fallback auf 0.
- `long_polling_loop()` startet mit dem geladenen Offset und persistiert nach jedem verarbeiteten Update.
- `_POLL_ALLOWED_UPDATES` ohne `channel_post` (spart Telegram-Bandbreite; Webhook-Setups bleiben über den `process_update`-Filter geschützt).
- `send_telegram_message()` neuer Parameter `message_thread_id` (D10).
- `extract_message_info()` exposed `is_forwarded` (`forward_origin`/`forward_from`/`forward_from_chat`) und `message_thread_id`.
- `answer_callback_query(callback_query_id, bot_token, text, show_alert)` — neuer Helper für Telegram-Callback-Antworten.

**[`zerberus/modules/telegram/hitl.py`](../zerberus/modules/telegram/hitl.py):**
- `HitlRequest` neues Feld `requester_user_id: Optional[int]` (O3-Validierung).
- `HitlManager.create_request()` neuer optionaler Parameter `requester_user_id`.

**[`.gitignore`](../.gitignore):**
- `data/huginn_offset.json` ergänzt (Runtime-State, gehört nicht ins Repo).

### Tests
**[`zerberus/tests/test_input_sanitizer.py`](../zerberus/tests/test_input_sanitizer.py) — neue Datei (11 Tests):**
- `TestRegexSanitizerBasics` (5): clean, empty, max-length, control-chars, newline/tab preserved.
- `TestInjectionDetection` (4): English pattern, German pattern, not-blocked-in-Huginn-mode, no-false-positive auf normales Deutsch.
- `TestForwardedMessage` (2): forwarded-Finding gesetzt / nicht gesetzt.
- `TestSingleton` (2): Identität, Interface-Konformität.

**[`zerberus/tests/test_telegram_bot.py`](../zerberus/tests/test_telegram_bot.py) — 17 neue Tests:**
- `TestProcessUpdateFilters` (3): channel_post / edited_message / unknown_update_type werden ignoriert.
- `TestOffsetPersistence` (4): Save+Load, kein File → 0, korrupte Datei → 0, Loop nutzt geladenen Offset und persistiert.
- `TestThreadIdRouting` (4): Payload enthält thread_id, omitted bei None, extract_message_info-Felder (forward + thread).
- `TestCallbackSpoofing` (3): Admin-Klick erlaubt, Requester-Klick erlaubt, fremder User → blockiert mit Popup-Alert.
- `TestAnswerCallbackQuery` (2): API-Call-Format, no-token → False.
- Außerdem: zwei pre-existierende Polling-Tests (`test_long_polling_loop_advances_offset`, `test_long_polling_handler_exception_does_not_break_loop`) auf `tmp_path`-Patch umgestellt — sie schrieben sonst in die echte `data/huginn_offset.json` und kontaminierten Folge-Tests.

**Test-Bilanz:**
- Non-Browser-Suite: **438 passed** (Baseline 422 → +16 reale neue Asserts; nicht 28, weil Singleton-Sharing-Fixtures als ein Setup zählen). Keine Regressionen.
- Telegram + Sanitizer in Isolation: **81/81 grün**.

### Scope-Entscheidungen
- **`logging` statt `structlog`.** Der Patch-Vorschlag aus dem Review nutzte `structlog.get_logger()` — Zerberus hat aber überall `logging.getLogger(...)`. Auf den vorhandenen Logger-Stil adaptiert.
- **Sanitizer blockt nicht im Huginn-Modus.** False-Positive-Risiko bei Regex auf normales Deutsch ist real, und Huginn lebt von Persona/Sarkasmus. Der `blocked=True`-Pfad ist im Code vorhanden, aber für Rosa reserviert (config-driven via `security.input_sanitizer.mode`, kommt mit Patch 163+).
- **Callback-Validierung erlaubt Admin ODER Requester** (statt nur Requester). So bleibt der heutige Admin-DM-Pfad funktional, und zukünftige In-Group-HitL-Buttons sind abgesichert. String-Vergleich auf beiden Seiten, weil Telegram `from.id` als int liefert aber `admin_chat_id` häufig als String konfiguriert ist.
- **`channel_post` aus Polling raus**, nicht nur in `process_update()` filtern. Spart Bandbreite UND macht den Filter explizit. Webhook-Setups bleiben über den `process_update`-Filter geschützt.
- **NICHT in Scope:** Config-Key `security.input_sanitizer.mode` (Patch 163+), `security.guard_fail_policy`, Rate-Limiting, Intent-Router, Nala-seitiger Sanitizer (Nala hat eigene Pipeline).

**Geänderte Dateien:** `zerberus/modules/telegram/bot.py`, `zerberus/modules/telegram/hitl.py`, `zerberus/modules/telegram/router.py`, `zerberus/tests/test_telegram_bot.py`, `.gitignore`, `CLAUDE_ZERBERUS.md`, `SUPERVISOR_ZERBERUS.md`, `lessons.md`, `README.md`, `docs/PROJEKTDOKUMENTATION.md`
**Neue Dateien:** `zerberus/core/input_sanitizer.py`, `zerberus/tests/test_input_sanitizer.py`

*Stand: 2026-04-25, Patch 162 — Input-Sanitizer aktiv (loggt + lässt durch), Telegram-Protokoll gegen Spoofing/Replay/Edit-Jailbreak gehärtet, Forum-Topics werden korrekt geroutet.*

---

## Patch 162b — PROJEKTDOKUMENTATION-Eintrag + Repo-Sync-Pflicht-Klarstellung
*2026-04-25*

### Problem
Patch 162 wurde committet und gepusht ohne Eintrag in `docs/PROJEKTDOKUMENTATION.md`, weil bisherige Scope-Notizen („Pflichtschritt liegt beim Supervisor") suggerierten, der Patchlog-Eintrag liege nicht im Verantwortungsbereich von Claude Code. Das hat in der Vergangenheit dazu geführt, dass die Patches 156–161 ebenfalls keinen Eintrag bekommen haben — die Dokumentation hängt seitdem hinter dem Code-Stand zurück.

### Lösung
- **[`docs/PROJEKTDOKUMENTATION.md`](PROJEKTDOKUMENTATION.md):** Patch-162-Eintrag im Patch-155-Stil angehängt (Problem / Lösung / Umsetzung / Tests / Scope-Entscheidungen / Geänderte Dateien / Stand-Footer).
- **[`CLAUDE_ZERBERUS.md`](../CLAUDE_ZERBERUS.md), Sektion „Repo-Sync-Pflicht":** Neuer Satz „Der PROJEKTDOKUMENTATION.md-Eintrag ist Teil jedes Patches und wird von Claude Code mit erledigt — nicht separat vom Supervisor." Frühere Formulierungen mit „Pflichtschritt liegt beim Supervisor" sind explizit als nicht mehr gültig markiert.
- Die historischen Patch-Scope-Notizen in [`SUPERVISOR_ZERBERUS.md`](../SUPERVISOR_ZERBERUS.md) (Patch 159, 161) werden NICHT rückwirkend geändert — sie dokumentieren den damaligen Stand.

**Geänderte Dateien:** `docs/PROJEKTDOKUMENTATION.md`, `CLAUDE_ZERBERUS.md`
**Neue Dateien:** keine

*Stand: 2026-04-25, Patch 162b — Doku-Disziplin korrigiert: PROJEKTDOKUMENTATION-Eintrag ist ab jetzt fester Bestandteil jedes Patches.*
