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
- Session-Ende ODER nach 5. Patch: `powershell -ExecutionPolicy Bypass -File sync_repos.ps1`|sync Ratatoskr (SUPERVISOR/CLAUDE/PROJEKTDOKU/lessons/backlog/README) + Claude-Repo (lessons.md→`lessons/zerberus_lessons.md`)|zieht Commit-Msg aus letztem Zerberus-Commit|pusht nur bei Änderungen
- NIEMALS Ratatoskr/Claude-Repo manuell editieren|nur via `sync_repos.ps1`|direkte Commits werden überschrieben
- Universelle Lessons können direkt in `C:\Users\chris\Python\Claude\lessons\` (z.B. `sqlite-db.md`)|Sync-Script fasst sie nicht an (schreibt nur `zerberus_lessons.md`)
