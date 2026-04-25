# CLAUDE_ZERBERUS.md – Zerberus Pro 4.0

## Globale Wissensbasis
- Repo: https://github.com/Bmad82/Claude (PUBLIC|keine Secrets/Keys/IPs/interne URLs)
- lessons/ nur bei Bedarf prüfen|nicht rituell bei jedem Patch
- Nach Abschluss: universelle Erkenntnisse dort eintragen

## Token-Effizienz
- Datei bereits im Kontext → NICHT nochmal lesen|nur lesen wenn (a) nicht sichtbar ODER (b) direkt vor Write
- Doku-Updates am Patch-Ende|ein Read→Write-Zyklus pro Datei
- CLAUDE_ZERBERUS.md + lessons.md: Bibel-Fibel-Format (Pipes|Stichpunkte|ArtikelWeg)
- SUPERVISOR/PROJEKTDOKU/README/Patch-Prompts: Prosa (menschliche Leser)
- Neue Einträge in CLAUDE_ZERBERUS.md + lessons.md IMMER im komprimierten Format schreiben

## Pflicht nach jedem Patch
- SUPERVISOR_ZERBERUS.md aktualisieren: Nummer|Datum|3-5 Zeilen Inhalt
- Offene Items: erledigte raus|neue rein
- Architektur-Warnungen: nur bei Änderung
- SUPERVISOR_ZERBERUS.md = einzige Datei für Supervisor-Claude beim Session-Start
- PROJEKTDOKUMENTATION.md = vollständiges Archiv, nur bei Bedarf konsultiert
- Vollständige Doku: `docs/PROJEKTDOKUMENTATION.md`

## Auto-Test-Policy (P165)
- GRUNDSATZ: Alles was Coda testen kann → Coda testet|Mensch nur für Untestbares
- CODA TESTET:
  - Unit/Integration-Tests (pytest)|API-Calls gegen echte Services (OpenRouter/Telegram/Whisper)
  - System-Prompt-Validation (LLM-Output-Format)|Config-Konsistenz (YAML-Keys vs Code)
  - Doku-Konsistenz (Patch-Nummern|Datei-Referenzen|tote Links|README-Footer)
  - Regressions-Sweep nach jedem Patch|Import/AST-Checks|Log-Tag-Konsistenz
  - Live-Validation-Scripts in `scripts/` (wie `validate_intent_router.py`)
- MENSCH TESTET (nicht delegierbar):
  - UI-Rendering auf echtem Gerät (iPhone/Android)|Touch-Feedback|visuelles Layout
  - Telegram-Gruppen-Dynamik mit echten Usern (Forwards|Edits|Multi-User)
  - Whisper mit echtem Mikrofon + Umgebungsgeräusche
  - UX-Gefühl ("fühlt sich richtig an")
- NACH JEDEM PATCH: Coda führt `pytest zerberus/tests/ -v --tb=short` aus|bei Failures: fixen BEVOR Commit
- LIVE-VALIDATION: Bei neuen Features die externe APIs nutzen → Validation-Script in `scripts/` anlegen + ausführen
- DOKU-CHECKER: `scripts/check_docs_consistency.py` (P165) prüft Patch-Nummer-Sync|Tote Links|Log-Tag-Konsistenz|Imports|Settings-Keys|nach jedem Patch laufen lassen, additiv zu pytest
- RETROAKTIV: Code-Stellen ohne Tests gefunden → Tests nachrüsten (kein separater Patch nötig)

## Projektpfad
```
C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus
```

## Server starten
```bash
cd C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus
venv\Scripts\activate
uvicorn zerberus.main:app --host 0.0.0.0 --port 5000 --reload
```

## Regeln

1. Erst lesen, dann schreiben|keine blinden Überschreibungen
2. `bunker_memory.db` niemals löschen/ändern
3. `.env` niemals leaken/loggen
4. `config.yaml` = Single Source of Truth|`config.json` NICHT als Konfig-Quelle
5. Module mit `enabled: false` nicht anfassen
6. Dateinamen `CLAUDE_ZERBERUS.md`/`SUPERVISOR_ZERBERUS.md` FINAL|nicht umbenennen/verschieben/durch alte Namen ersetzen|gilt auch für Ratatoskr-Kopien
7. Mobile-first (iOS Safari + Android Chrome)|`:active` statt nur `:hover`|Touch-Target ≥44px|`keydown` statt `keypress`|`type="button"` auf Non-Submit-Buttons
8. `/v1/`-Endpoints (`/v1/chat/completions`, `/v1/audio/transcriptions`) IMMER auth-frei|Dictate-App kann keine Custom-Headers|Bypass: `_JWT_EXCLUDED_PREFIXES` in [`middleware.py`](zerberus/core/middleware.py)|NIEMALS entfernen (Hotfix 103a)

### HTTP-Header-Konventionen

| Header | Verwendung | Seit |
|---|---|---|
| `X-Session-ID` | Session für `/v1/chat/completions` + `/nala/voice`|Default `legacy-default`/`nala-default` | Früh |
| `X-Already-Cleaned: true` | Skip `clean_transcript()` in `/v1/audio/transcriptions`+`/nala/voice`|Dictate-App|Case-insensitive | P135 |

### Regel 9 — User-Entscheidungen als klickbare Box
- Aktionen mit User-Input (Datei löschen, Index leeren, Settings) → IMMER klickbare Entscheidungsbox in Nala-UI
- Format: Buttons + Klare Optionen + "Soll ich das für dich übernehmen?"
- Kein Freitext-Dialog bei binärer/ternärer Entscheidung

## RAG-Upload
- Endpoint: `POST /hel/admin/rag/upload` (`.txt`/`.md`/`.docx`/`.pdf`)
- Chunks: `chunk_size=800 Wörter`|`overlap=160 Wörter` (20%)|Einheit Wörter, NICHT Token
- Status: `GET /hel/admin/rag/status`
- Clear: `DELETE /hel/admin/rag/clear` → `faiss.index`+`metadata.json` reset
- Reindex: `POST /hel/admin/rag/reindex` → re-embed aller Chunks
- Helper: `_reset_sync(settings)` in `zerberus/modules/rag/router.py`

## Statischer API-Key
- `config.yaml` → `auth.static_api_key`|wenn gesetzt akzeptiert JWT-Middleware `X-API-Key` als Bearer-Alternative

## Telegram/Huginn (P155+158+162)

- `config.yaml` → `modules.telegram.mode`:
  - `polling` (Default): Long-Polling via `getUpdates`|funktioniert hinter Tailscale/NAT|Shutdown cancelt Task
  - `webhook`: nur mit öffentl. HTTPS-URL|braucht `webhook_url`|Shutdown deregistriert
- Background-Task in `lifespan` via `startup_huginn()`|`main.py` hält Referenz, cancelt bei Shutdown
- Update-Handler: `zerberus.modules.telegram.router.process_update(data, settings)`|Webhook+Polling rufen ihn
- `modules.telegram.system_prompt` (P158): Huginn-Persona (Default zynischer Rabe)|editierbar in Hel→Huginn-Tab|Default: [`DEFAULT_HUGINN_PROMPT`](zerberus/modules/telegram/bot.py)|3-Wege-Resolver `_resolve_huginn_prompt(settings)`: Key fehlt→Default|leer→leer (Opt-Out)|sonst Config
- BotFather GroupPrivacy=AUS|sonst kein `respond_to_name`/`autonomous_interjection`|Nach Toggle: Bot aus Gruppe entfernen+neu hinzufügen (TG cached pro Beitritt)|→ lessons.md
- **Update-Typ-Filter (P162):** `process_update()` verwirft ganz oben `channel_post`/`edited_channel_post`/`edited_message`/unbekannte Typen|`_POLL_ALLOWED_UPDATES` in [`bot.py`](zerberus/modules/telegram/bot.py) listet durchgereichte Typen|Bei neuen Typen: erst entscheiden ob Huginn verarbeitet, dann Filter+`_KNOWN_UPDATE_TYPES` ergänzen
- **Offset-Persistenz (P162):** `data/huginn_offset.json` speichert letzten `update_id`|Boot lädt via `_load_offset()`|gegen Doppelverarbeitung nach Restart|Tests müssen `OFFSET_FILE` per `monkeypatch.setattr(bot_module, "OFFSET_FILE", tmp_path/"off.json")` umlenken
- **Forum-Topics / `message_thread_id` (P162, D10):** `extract_message_info()` exposed `message_thread_id`+`is_forwarded`|Alle `send_telegram_message`-Calls in `router.py` reichen `message_thread_id=info.get("message_thread_id")` durch|sonst Antwort im General statt Topic|Telegram ignoriert bei None → nur truthy ins Payload

## Intent-Router (P164)
- Architektur: Intent kommt vom Haupt-LLM via JSON-Header in der Antwort|kein Regex (Whisper-Fehler) + kein extra Classifier-Call (Latenz)
- Format: `{"intent":"CHAT|CODE|FILE|SEARCH|IMAGE|ADMIN","effort":1-5,"needs_hitl":bool}` als allererste Zeile|optional in ```json-Fence|Body folgt
- Enum [`core/intent.py`](zerberus/core/intent.py): 6 Kern-Intents aktiv|Rosa-Future (EXECUTE/MEMORY/RAG/SCHEDULE/TRANSLATE/SUMMARIZE/CREATIVE/SYSTEM/MULTI) als Kommentar reserviert|`HuginnIntent.from_str()` Fallback auf CHAT bei None/Empty/Unknown
- Parser [`core/intent_parser.py`](zerberus/core/intent_parser.py): Brace-Counter (statt naivem `[^}]+`)|robuste Defaults bei kaputtem JSON, fehlendem Header, unbekanntem Intent, effort außerhalb 1-5
- `INTENT_INSTRUCTION` + `build_huginn_system_prompt(persona)` in [`bot.py`](zerberus/modules/telegram/bot.py)|wird in `_process_text_message` und im autonomen Gruppen-Einwurf an Persona angehängt|Persona darf leer sein, Intent-Block bleibt
- Router parsing: Body=`parsed.body` (ohne JSON-Header)|Guard sieht Body, User sieht Body|Edge: nur Header ohne Body → Roh-Antwort als Fallback (Log-Warnung)
- HitL-Policy [`core/hitl_policy.py`](zerberus/core/hitl_policy.py): NEVER_HITL={CHAT,SEARCH,IMAGE} überstimmt LLM-`needs_hitl=true` (K5)|BUTTON_REQUIRED={CODE,FILE,ADMIN} braucht ✅/❌-Inline-Keyboard|ADMIN erzwingt HitL auch bei `needs_hitl=false` (K6 — jailbroken-LLM-Schutz)|aktuell P164: Decision wird geloggt + Admin-DM-Hinweis|echter Button-Flow folgt Phase D
- K6-Regel: HitL-Bestätigung NIE per natürlicher Sprache („ja, mach kaputt" kein gültiger GO)|nur Inline-Keyboard
- Effort-Score: nur geloggt (Bucket low/mid/high)|aktive Routing-Entscheidung kommt mit Phase C (Aufwands-Kalibrierung)
- Gruppen-Einwurf-Filter (D3/D4/O6): autonome Antworten nur bei {CHAT,SEARCH,IMAGE}|CODE/FILE/ADMIN unterdrückt mit `skipped="autonomous_intent_blocked"`
- Logging-Tags: `[INTENT-164]` (Parsing+Routing), `[EFFORT-164]` (Effort-Logging), `[HITL-POLICY-164]` (Policy-Decisions)
- Test-Pattern: `parse_llm_response(raw)` direkt testen (16 Parser-Tests)|Policy `.evaluate(parsed)` direkt testen (11 Policy-Tests)|Integration via `_process_text_message`-Mock (Guard, Send, LLM gemockt)|6 Integration-Tests fuer Gruppen-Filter + Header-Strip

## Rate-Limiting + Graceful Degradation (P163)
- [`core/rate_limiter.py`](zerberus/core/rate_limiter.py): Interface `RateLimiter` (Rosa-Skelett für Redis) + `InMemoryRateLimiter` (Huginn-jetzt)|Singleton via `get_rate_limiter()`|Default 10 msg/min/User|Cooldown 60s
- Integration in [`process_update()`](zerberus/modules/telegram/router.py) GANZ oben (nach Update-Typ-Filter, vor Event-Bus)|nur für `message`-Updates|Callback-Queries ausgenommen (Admin-HitL-Klicks)
- `RateLimitResult.first_rejection`-Flag: genau EIN „Sachte, Keule"-Reply pro Cooldown-Periode|Folge-Nachrichten still ignorieren|sonst spammt der Bot selbst|Test-Reset via `_reset_rate_limiter_for_tests()` (Modul-Singleton)
- `cleanup()` entfernt Buckets nach 5min Inaktivität (Memory-Leak-Schutz)
- Guard-Fail-Policy `security.guard_fail_policy` ∈ {`allow`,`block`,`degrade`}|Default `allow` (Huginn — Antwort durchlassen + Log-Warnung)|`block` (Rosa — „⚠️ Sicherheitsprüfung nicht verfügbar.")|`degrade` reserviert (Future Ollama)|Helper `_resolve_guard_fail_policy(settings)` via `getattr(settings, "security", None)` (Pydantic `extra="allow"`)
- Trigger: Guard-Verdict `ERROR` (Guard-Call selbst raised nicht, returnt `{"verdict":"ERROR"}`)|beide Pfade respektieren Policy: `_process_text_message` + autonomer Gruppen-Einwurf
- OpenRouter-Retry `_call_llm_with_retry()` wrappt `call_llm`|`call_llm` raised NICHT → Error-String prüfen via `_is_retryable_llm_error`|Retryable: `429`/`503`/„rate"|Backoff 2s/4s/8s|max 3 Versuche|400/401/etc. SOFORT zurück (kein Retry-Sinn)
- Erschöpfung: DM → Fallback „Meine Kristallkugel ist gerade trüb. Versucht's später nochmal. 🔮"|autonom → still überspringen (niemand hat gefragt)
- Ausgangs-Throttle [`bot.py::send_telegram_message_throttled`](zerberus/modules/telegram/bot.py): 15 msg/min/Chat (konservativ unter TG ~20/min/Gruppe)|wartet via `asyncio.sleep` statt drop|Modul-Singleton `_outgoing_timestamps`|Test-Reset via `_reset_outgoing_throttle_for_tests()`|aktuell nur autonomer Gruppen-Einwurf|DMs bei `send_telegram_message` direkt
- Config-Keys VORBEREITET nicht aktiv: `limits.per_user_rpm`/`limits.cooldown_seconds` in `config.yaml`|aktives Reading mit Phase-B-Config-Refactor|jetzige Defaults im Code (max_rpm=10, cooldown=60)|`security.guard_fail_policy` IST aktiv gelesen
- Logging-Tags: `[RATELIMIT-163]` (Rate-Limiter), `[HUGINN-163]` (Router/Bot — Throttle/Retry/Guard-Fail/LLM-Unavailable)

## Input-Sanitizer (P162)
- [`input_sanitizer.py`](zerberus/core/input_sanitizer.py): Interface `InputSanitizer` (Rosa-Skelett) + `RegexSanitizer` (Huginn-jetzt)|Singleton via `get_sanitizer()`
- VOR jedem LLM-Call: in [`_process_text_message()`](zerberus/modules/telegram/router.py) für DMs + autonomer Gruppen-Einwurf|Neue LLM-Pfade: `get_sanitizer().sanitize(text, metadata={...})` davor
- Findings geloggt, NICHT geblockt (Tag `[SANITIZE-162]`)|Huginn-Modus: `blocked=False`|Guard (Mistral Small) entscheidet final|`blocked=True`-Pfad im Konsumenten („🚫 Nachricht blockiert.")|aktiv mit Rosa wenn `security.input_sanitizer.mode = "ml"`
- Patterns konservativ|Lieber ein Pattern weniger als False-Positive auf Deutsch („Kannst du das ignorieren?" darf NICHT triggern)|Neue Patterns: erst gegen [`test_input_sanitizer.py::TestInjectionDetection::test_sanitize_normal_german_no_false_positive`](zerberus/tests/test_input_sanitizer.py)
- Metadata: `user_id`/`chat_type`/`is_forwarded`/`is_reply`|`is_forwarded=True` → Finding `FORWARDED_MESSAGE` (K3-Vektor: Chat-Übernahme via Reply-Chain)|Future: ML-Sanitizer kann je Chat-Typ/Reply unterschiedlich strikt sein

## Callback-Spoofing-Schutz (P162, O3)
- `HitlRequest.requester_user_id: Optional[int]`|`process_update()` validiert bei `callback_query`: clicker_id ∈ `{admin_chat_id, requester_user_id}`|sonst Popup („🚫 Nicht deine Anfrage.") via [`answer_callback_query()`](zerberus/modules/telegram/bot.py) mit `show_alert=True`
- Neue HitL-Pfade (Code-Exec, File-Ops): `requester_user_id=info.get("user_id")` an `create_request()`|sonst nur-Admin-Fallback (DM-only ok, In-Group-Buttons offen)
- String-Vergleich: TG liefert `from.id` als int|`admin_chat_id` oft String|`str(...)` auf beiden Seiten

## Guard-Kontext (P158)
- [`check_response()`](zerberus/hallucination_guard.py) akzeptiert optionalen `caller_context: str`|String beschreibt Antwortenden|im Guard-System-Prompt als `[Kontext des Antwortenden]`-Block + harter Satz „Referenzen auf diese Elemente sind KEINE Halluzinationen."
- Huginn-Calls ([`telegram/router.py`](zerberus/modules/telegram/router.py)): Raben-Persona via `_build_huginn_guard_context(persona)` inkl. 300-Zeichen-Auszug
- Nala-Calls ([`legacy.py`](zerberus/app/routers/legacy.py)): Zerberus-Selbstreferenz („Referenzen auf Zerberus/Chris/Nala/Hel/Huginn keine Halluzinationen")
- WARNUNG = KEIN Block|Antwort geht IMMER an User|Admin (Chris) bekommt bei WARNUNG DM mit Chat-ID+Grund|BLOCK/TOXIC würden Antwort unterdrücken — gibt's aktuell nicht (nur OK/WARNUNG/SKIP/ERROR)
- Neue Frontends: eigenen `caller_context` definieren|leer = Pre-158-Verhalten

## Settings-Cache (P156)
- `get_settings()` = Singleton in `core/config.py`|YAML-Write MUSS Cache invalidieren|sonst stale Wert + verzögerte UI-Werte nach Save
- Decorator: `@invalidates_settings` (sync+async)|Kontextmanager: `with settings_writer():` (granular)
- Migriert: 7 YAML-Writer in [`hel.py`](zerberus/app/routers/hel.py)+[`nala.py`](zerberus/app/routers/nala.py) (P156-Sweep)
- Neue YAML-Writer-Endpoints: IMMER `@invalidates_settings`|Test-Pattern: POST→GET im selben Test mit tmp-cwd-Fixture + `_settings = None`-Reset|→ [`test_huginn_config_endpoint.py`](zerberus/tests/test_huginn_config_endpoint.py)

## Datenbank-Migrationen (Alembic, seit P92)
- Config: `alembic.ini` im Root|Revisionen unter `alembic/versions/`
- Manueller Aufruf|KEIN Auto-Upgrade beim Serverstart
- Apply: `alembic upgrade head`|Neue Revision: `alembic revision -m "..."` + manuell upgrade/downgrade editieren
- IMMER idempotent (`PRAGMA table_info`-Check, `IF NOT EXISTS`)
- Vor Schema-Änderung: Backup `cp bunker_memory.db bunker_memory_backup_patch{N}.db`

## Tests (seit P93)
- Playwright in `zerberus/tests/`|Loki=E2E|Fenrir=Chaos|Vidar=Smoke
- Test-Accounts `loki`/`fenrir`/`vidar` Pflicht in `config.yaml` (P93, P153)
- Run: `pytest zerberus/tests/ -v --html=zerberus/tests/report/full_report.html --self-contained-html`
- Server muss laufen (`https://127.0.0.1:5000`)

### Test-Agenten
| Agent | Datei | Fokus | Passwort |
|-------|-------|-------|---------|
| Loki | `test_loki.py`, `test_loki_mega_patch.py` | E2E Happy-Path | `lokitest123` |
| Fenrir | `test_fenrir.py`, `test_fenrir_mega_patch.py` | Chaos/Edge/Stress | `fenrirtest123` |
| Vidar | `test_vidar.py` | Smoke (Go/No-Go) | `vidartest123` |

## Weiterführende Doku
- Projektspezifische Lessons: `lessons.md`
- Globale Lessons: https://github.com/Bmad82/Claude/lessons/
- Patch-Archiv: `docs/PROJEKTDOKUMENTATION.md`

## ⚠️ Dateinamen-Konvention
- Projektspezifisch: `CLAUDE_ZERBERUS.md` (diese Datei)
- Supervisor: `SUPERVISOR_ZERBERUS.md`
- Patch-Prompts: IMMER vollen Dateinamen mit Projektsuffix
- NIEMALS mit globaler CLAUDE.md verwechseln/zusammenführen

## Supervisor-Patch-Prompts
- Vom Supervisor (claude.ai Chat) als `.md`-Datei|nie inline Chat-Text|Claude Code via Copy-Paste

## Repo-Sync-Pflicht
- Nach jedem Patch: `git add -A; git commit -m "Patch XX – [Titel]"; git push` (nur Zerberus)
- Nach jedem Patch: PROJEKTDOKUMENTATION.md anhängen (am Ende, bestehende nicht ändern):
  - Patch-Nr+Titel|Datum (ISO)|Was geändert (1-3 Sätze)|Dateien neu/geändert|Teststand (X grün)
- PROJEKTDOKUMENTATION.md-Eintrag = Teil jedes Patches, von Claude Code erledigt|alte „liegt beim Supervisor"-Notizen ungültig
- **Nach jedem `git push`: `sync_repos.ps1` ausführen** (P164 — Sync ist LETZTER Schritt jedes Patches, Patch gilt erst als abgeschlossen wenn alle 3 Repos synchron)
- Falls `sync_repos.ps1` Fehler wirft: Chris informieren, NICHT stillschweigend überspringen
- Falls Umgebung kein PowerShell hat: explizit melden „⚠️ sync_repos.ps1 nicht ausgeführt — bitte manuell nachholen"|nicht „vergessen"
- Session-Ende ODER nach 5. Patch ist KEINE Ausrede mehr|sync nach JEDEM push (Coda-Setup pusht zuverlässig, vergisst aber Sync)
- `powershell -ExecutionPolicy Bypass -File sync_repos.ps1`|sync Ratatoskr (SUPERVISOR/CLAUDE/PROJEKTDOKU/lessons/backlog/README) + Claude-Repo (lessons.md→`lessons/zerberus_lessons.md`)|zieht Commit-Msg aus letztem Zerberus-Commit|pusht nur bei Änderungen
- NIEMALS Ratatoskr/Claude-Repo manuell editieren|nur via `sync_repos.ps1`|direkte Commits werden überschrieben
- Universelle Lessons können direkt in `C:\Users\chris\Python\Claude\lessons\` (z.B. `sqlite-db.md`)|Sync-Script fasst sie nicht an (schreibt nur `zerberus_lessons.md`)
