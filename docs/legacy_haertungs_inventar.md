# Legacy-Härtungs-Inventar

*Block A des Patch 166 — reine Analyse, kein Code-Change.*
*Stand: 2026-04-26*

## Quelle

- `legacy/Nala_Weiche.py` — 1090 Zeilen, einzige Datei im `legacy/`-Ordner.
- Stand vor dem Zerberus-Rewrite ("Nala-Weiche" — alter Routing-Layer).

## Methodik

Suche nach defensiven Mustern in der Legacy-Datei (Try/Except, Längen-Checks,
Timeouts, Fallbacks, Locks, Rate-Limits, Auth-Checks). Pro Härtung Abgleich
gegen den aktuellen `zerberus/`-Code via Grep + Patch-Doku-Recherche.

Status-Werte:
- **✅ Übernommen** — Härtung existiert äquivalent oder verbessert in `zerberus/`,
  mit Patch-Nummer + Pfad-Referenz.
- **❌ Fehlt** — Härtung existiert in Legacy, im aktuellen Code nicht. Empfehlung:
  übernehmen / nicht relevant / abwägen.
- **🔄 Obsolet** — Härtung wurde durch ein neueres Modul oder Pattern ersetzt.

## Ergebnis

| # | Härtung | Legacy-Datei:Zeile | Status im aktuellen Code |
|---|---------|--------------------|--------------------------|
| 1 | OPENROUTER_KEY Env-Check beim Startup | `Nala_Weiche.py:40-42` | ✅ Übernommen — `core/config.py` (Pydantic-Validation, `OpenRouterConfig`) + `.env`-Fallback. Defensiver als Hard-Block. |
| 2 | Pacemaker `asyncio.Lock()` gegen Double-Start | `Nala_Weiche.py:67, 106-125` | ✅ Übernommen (P59) — `app/pacemaker.py::_pacemaker_lock`, gleiches Pattern. |
| 3 | Pacemaker Idle-Timeout (30 Min) | `Nala_Weiche.py:63, 104-108` | ✅ Übernommen — `app/pacemaker.py::KEEP_ALIVE_SECONDS = 1800`, Timestamp-Vergleich identisch. |
| 4 | Pacemaker Heartbeat httpx-Timeout (10 s) | `Nala_Weiche.py:112` | ✅ Übernommen — `app/pacemaker.py` mit Config-Timeout. |
| 5 | Pacemaker Exception-Handler (fail-soft) | `Nala_Weiche.py:109-116` | ✅ Übernommen — Try/Except + Logging in `app/pacemaker.py`, Worker bleibt am Leben. |
| 6 | Punctuation-Modell Lazy-Load + Fallback | `Nala_Weiche.py:159-166` | ✅ Übernommen — `core/cleaner.py` mit `if _punctuation_model is None`-Pattern + Try/Except. |
| 7 | Punctuation-Executor Exception → Original-Text | `Nala_Weiche.py:175-179` | ✅ Übernommen — `core/cleaner.py::apply_punctuation_async()`, Graceful Fallback. |
| 8 | ConfigManager File-Mtime-Cache + Reload | `Nala_Weiche.py:188-198` | 🔄 Obsolet — ersetzt durch P156 `@invalidates_settings`-Decorator + Singleton-Reload in `core/config.py`. Mtime-Check ist unnötig, weil Schreiber den Cache aktiv invalidieren. |
| 9 | DB Transaction `BEGIN IMMEDIATE` + Rollback bei Insert-Fehler | `Nala_Weiche.py:269-299` | ✅ Übernommen — `core/database.py::store_interaction()` mit `async with`-Context-Manager (SQLAlchemy macht Rollback automatisch). Plus P113a Dedup-Guard (30 s-Window). |
| 10 | Whisper-Cleaner Regex Try/Except pro Regel | `Nala_Weiche.py:372-377` | ✅ Übernommen — `core/cleaner.py::apply_whisper_cleaner()` mit Rule-Loop + Exception-Handling. Idempotent (P107). |
| 11 | Dialekt Lookup-Guard (`if dialect_name not in rules`) | `Nala_Weiche.py:381-382` | ✅ Übernommen — `core/dialect.py::apply_dialect()` mit identischem Early-Return. Plus P103-Wortgrenzen-Matching (`(?<!\w)`/`(?!\w)`). |
| 12 | LLM-Call httpx-Timeout (120 s) | `Nala_Weiche.py:446` | ✅ Übernommen (P160) — `core/llm.py` mit Config-basiertem Timeout (`settings.whisper.request_timeout_seconds` analog). |
| 13 | LLM HTTP 401 explizit abfangen + sprechende Meldung | `Nala_Weiche.py:448-449` | ✅ Übernommen — `core/llm.py` Status-Check + Fallback-Antwort. |
| 14 | LLM Exception → "Miau"-Fallback (Persona-konsistent) | `Nala_Weiche.py:445-460` | ✅ Übernommen + verbessert — `core/llm.py` mit Graceful Fallback; P163 fügt OpenRouter-Retry-Wrapper (`_call_llm_with_retry` mit Backoff 2/4/8 s) und „Kristallkugel ist trüb"-Fallback hinzu. |
| 15 | Audio-Dauer-Check `<0.5 s` via Wave-Header | `Nala_Weiche.py:463-474` | 🔄 Obsolet — ersetzt durch P160 `utils/whisper_client.py::WhisperSilenceGuard` mit **Bytes-Größen-Check** (`min_audio_bytes=4096`). Schneller, robuster (kein Wave-Parser nötig). |
| 16 | Audio-File-Type-Check (`.wav`-Endung) | `Nala_Weiche.py:464-465` | 🔄 Obsolet — ersetzt durch reinen Byte-Größen-Check (akzeptiert beliebige Audio-Container). |
| 17 | Audio-Header-Parse Try/Except → 999.0 | `Nala_Weiche.py:466-474` | 🔄 Obsolet — ersetzt durch P160 Bytes-Check (kein Header-Parsing mehr). |
| 18 | `/voice`-Endpoint Audio-Dauer-Guard | `Nala_Weiche.py:830-831` | ✅ Übernommen (P160) — `utils/whisper_client.py::transcribe()` raised `WhisperSilenceGuard`; beide Endpoints (`legacy.py` + `nala.py`) liefern endpoint-spezifisches Silence-Response. |
| 19 | `/voice`-Whisper-Call Timeout (600 s) | `Nala_Weiche.py:832` | ✅ Übernommen + verbessert (P160) — Hardcoded → Config-driven (`settings.whisper.request_timeout_seconds=120`, `connect=10`). |
| 20 | `/voice`-Whisper Try/Except → Error-JSON | `Nala_Weiche.py:827-838` | ✅ Übernommen (P160) — Endpoint-Router fängt `httpx.ReadTimeout` (Retry+Backoff via `whisper_client.transcribe`), 500 nur nach Retry-Erschöpfung. |
| 21 | `X-Session-ID`-Header-Extract mit Fallback | `Nala_Weiche.py:825, 860, 909` | ✅ Übernommen — alle Router lesen `X-Session-ID` mit Default `legacy-default`/`nala-default`. CLAUDE-Regel dokumentiert. |
| 22 | `/v1/audio/transcriptions` Dauer-Guard | `Nala_Weiche.py:888-890` | ✅ Übernommen (P160) — gleicher zentraler `transcribe()`-Helper wie `/voice`. |
| 23 | `/v1/chat/completions` Pydantic-Parse Try/Except → HTTP 400 | `Nala_Weiche.py:904-908` | ✅ Übernommen — Router nutzt FastAPI-Pydantic-Validation, 422 statt 400 (FastAPI-Standard). |
| 24 | User-Message Empty-Check → HTTP 400 | `Nala_Weiche.py:916-917` | ✅ Übernommen — Validation auf Pydantic-Layer + Endpoint-Guard. |
| 25 | Sentiment-Smoothing `asyncio.Lock` + EMA (Alpha=0.2) | `Nala_Weiche.py:845-855` | ✅ Übernommen — `modules/sentiment/router.py::_stimmung_lock` + Exponential Moving Average. Verhindert Race-Conditions bei parallelem `/voice` + `/chat`. |
| 26 | Pydantic Request-Validation (`ChatCompletionRequest.parse_raw()`) | `Nala_Weiche.py:905` | ✅ Übernommen + erweitert (P162) — Input-Sanitizer (`core/input_sanitizer.py::RegexSanitizer`) prüft zusätzlich auf Injection-Patterns als zweite Schicht. |
| 27 | Lifespan-Cleanup `task.cancel()` + `CancelledError`-Handler | `Nala_Weiche.py:139-142` | ✅ Übernommen — `zerberus/main.py` Lifespan mit identischem Pattern für Pacemaker, Whisper-Watchdog (P119), Huginn-Polling-Task (P155). |

## Zusammenfassung

| Kategorie | Anzahl |
|-----------|-------:|
| ✅ Übernommen | 23 |
| 🔄 Obsolet (ersetzt durch neueres Modul/Pattern) | 4 |
| ❌ Fehlt — übernahmewürdig | **0** |

**Zusätzliche Härtungen, die das aktuelle System ÜBER die Legacy hinaus
liefert** (keine Lücke, sondern Fortschritt):

- **P162 Input-Sanitizer** (`core/input_sanitizer.py`) — Injection-Pattern-Erkennung
  vor jedem LLM-Call, in Legacy nicht vorhanden.
- **P163 Per-User-Rate-Limiter** (`core/rate_limiter.py`) — 10 msg/min/User mit
  Cooldown 60 s, in Legacy nicht vorhanden (Telegram-spezifisch, aber
  Pattern-Skelett für künftige Nala-Variante vorhanden).
- **P158 Guard-Kontext** (`hallucination_guard.py::caller_context`) — Persona-
  Selbstreferenzen werden nicht mehr als Halluzination gewertet.
- **P164 HitL-Policy** (`core/hitl_policy.py`) — destruktive Aktionen brauchen
  Inline-Keyboard-Bestätigung, NEVER_HITL überstimmt LLM-Flag.
- **P162 Update-Typ-Filter** (`router.py::process_update`) — `edited_message`/
  `channel_post`/unbekannte Update-Typen werden lautlos verworfen.
- **P162 Callback-Spoofing-Schutz** (`router.py`) — Inline-Button-Klicks in
  Gruppen werden gegen `requester_user_id` validiert.
- **P162 Offset-Persistenz** (`bot.py::_load_offset/_save_offset`) — Telegram-
  Update-Replay nach Restart wird verhindert.
- **P109 SSE Heartbeat + Frontend-Timeout-Recovery** — Backend sendet alle 5 s
  Heartbeat, Frontend prüft DB vor Retry → keine Doppel-Calls.
- **P113a DB-Dedup-Guard** (`store_interaction`) — 30 s-Insert-Guard verhindert
  Doppel-Inserts bei Timeout-Retry + Parallel-Pfaden.

## Empfehlung

**Keine Action-Items.** Der Zerberus-Rewrite hat alle Legacy-Härtungen
übernommen oder durch neuere Patterns ersetzt; zusätzlich existieren ~9
Härtungen (P158/P162/P163/P164/P109/P113a) als Zugewinn gegenüber Legacy.

Der `legacy/`-Ordner kann als historische Referenz erhalten bleiben — das
Inventar dient als Beweis, dass beim Rewrite nichts unbemerkt verloren ging.
