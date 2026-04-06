# Zerberus Pro 4.0 – Projektdokumentation

**Stand:** 2026-04-05
**Version:** 4.0 (Patch 58 – UI-Verbesserungen, Guthaben-Fix, Slider-Buttons, Dialect-Kurzschluss)
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
| `interactions` | id, session_id, timestamp, role, content, sentiment, word_count, integrity | Alle Nachrichten (user, assistant, whisper_input) |
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
| Patch 59 | 🔜 Nächster Meilenstein | GitHub privat + Kumpels-Beta |

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

*Dieses Dokument wurde automatisch aus dem Quellcode und der CLAUDE.md generiert.*
*Stand: 2026-04-05, Patch 54.*
