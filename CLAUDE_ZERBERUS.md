# CLAUDE_ZERBERUS.md – Zerberus Pro 4.0

## Globale Wissensbasis
Repository: https://github.com/Bmad82/Claude
- Vor Arbeitsbeginn: `lessons/`-Ordner auf relevante Einträge prüfen
- Nach Abschluss: Neue universelle Erkenntnisse dort eintragen

⚠️ **Das globale Repo ist PUBLIC.** Keine Secrets, Keys, IPs, interne URLs.

## Pflicht nach jedem Patch
Nach Abschluss jedes Patches SUPERVISOR_ZERBERUS.md aktualisieren:
- Aktueller Patch: Nummer, Datum, 3-5 Zeilen was gemacht wurde
- Offene Items: Liste aktuell halten (erledigte raus, neue rein)
- Architektur-Warnungen: nur wenn sich etwas geändert hat
SUPERVISOR_ZERBERUS.md ist die einzige Datei die Supervisor-Claude beim Session-Start liest.
PROJEKTDOKUMENTATION.md bleibt das vollständige Archiv — wird nur bei Bedarf konsultiert.

**Vollständige Projektdokumentation:** `docs/PROJEKTDOKUMENTATION.md`

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

1. Immer erst lesen, dann schreiben – keine blinden Überschreibungen
2. `bunker_memory.db` niemals löschen oder verändern
3. `.env` niemals nach außen leaken oder in Logs ausgeben
4. `config.yaml` ist die einzige Konfigurationsquelle – `config.json` nicht als Konfig-Quelle verwenden
5. Module mit `enabled: false` in `config.yaml` nicht anfassen
6. Dateinamen `CLAUDE_ZERBERUS.md` und `SUPERVISOR_ZERBERUS.md` sind FINAL — nicht umbenennen, nicht verschieben, nicht durch alte Namen ersetzen. Gilt auch für die Kopien in Ratatoskr.
7. Mobile-first: Nala/Hel/Admin-UIs werden primär auf iOS Safari + Android Chrome genutzt. Jede UI-Änderung muss mobile-kompatibel sein (`:active` statt nur `:hover`, Mindest-Touch-Target 44px, `keydown` statt `keypress`, `type="button"` auf allen Form-Buttons die kein submit sind).
8. `/v1/`-Endpoints (`/v1/chat/completions`, `/v1/audio/transcriptions`) sind **IMMER auth-frei**. Die Dictate-App (Android-Tastatur) kann keine Custom-Headers (X-API-Key, JWT) setzen. Diese Ausnahme darf **NIEMALS** entfernt werden, auch nicht bei Auth-Refactoring. Der Bypass liegt in [`zerberus/core/middleware.py`](zerberus/core/middleware.py) in `_JWT_EXCLUDED_PREFIXES` als `/v1/`-Prefix. Hintergrund: Hotfix 103a.

### HTTP-Header-Konventionen (Zerberus-spezifisch)

| Header | Verwendung | Seit |
|---|---|---|
| `X-Session-ID` | Session-ID für `/v1/chat/completions` und `/nala/voice`. Default `legacy-default` / `nala-default`. | Früh |
| `X-Already-Cleaned: true` | Überspringt `clean_transcript()` in `/v1/audio/transcriptions` und `/nala/voice`. Für die Dictate-App, die bereits gecleanten Text sendet. Case-insensitive. | Patch 135 |

### Regel 9 — User-Entscheidungen als klickbare Box

Bei Aktionen die User-Input erfordern (z.B. Datei löschen, Index leeren, Einstellungen ändern), 
IMMER eine klickbare Entscheidungsbox in der Nala-UI anbieten statt nur Text-Rückfrage.  
Format: Buttons mit klaren Optionen + eine Option "Soll ich das für dich übernehmen?"  
Kein reiner Freitext-Dialog, wenn eine binäre/ternäre Entscheidung ausreicht.

## RAG-Upload

- Endpunkt: `POST /hel/admin/rag/upload` (`.txt` / `.md` / `.docx` / `.pdf`)
- **Chunk-Parameter:** `chunk_size=800 Wörter`, `overlap=160 Wörter` (20 %) — Einheit: **Wörter**, nicht Token
- Index-Status: `GET /hel/admin/rag/status`
- Index leeren: `DELETE /hel/admin/rag/clear` — setzt `faiss.index` + `metadata.json` zurück
- Index neu aufbauen: `POST /hel/admin/rag/reindex` — re-embeddet alle gespeicherten Chunk-Texte
- Hilfsfunktion: `_reset_sync(settings)` in `zerberus/modules/rag/router.py`

## Statischer API-Key

- `config.yaml` → `auth.static_api_key` — wenn gesetzt, akzeptiert die JWT-Middleware den `X-API-Key` Header als Alternative zu Bearer

## Telegram/Huginn (Patch 155 + 158)

- `config.yaml` → `modules.telegram.mode`:
  - **`polling`** (Default): Long-Polling via `getUpdates`. Funktioniert hinter Tailscale/NAT ohne öffentliche URL. Beim Shutdown wird der Polling-Task cancelt.
  - **`webhook`**: Nur für Setups mit öffentlicher HTTPS-URL. Braucht `webhook_url`. Beim Shutdown wird der Webhook bei Telegram deregistriert.
- Background-Task wird in `lifespan` via `startup_huginn()` gestartet, der die Task zurückgibt. `main.py` hält die Referenz und cancelt beim Shutdown.
- Gemeinsamer Update-Handler: `zerberus.modules.telegram.router.process_update(data, settings)` — sowohl Webhook-Endpoint als auch Polling-Loop rufen ihn auf.
- `config.yaml` → `modules.telegram.system_prompt` (Patch 158): Huginn-Persona (Default: zynischer Rabe). Editierbar in Hel → Huginn-Tab als Textarea. Default-Konstante: [`DEFAULT_HUGINN_PROMPT`](zerberus/modules/telegram/bot.py). 3-Wege-Resolver `_resolve_huginn_prompt(settings)` in [`router.py`](zerberus/modules/telegram/router.py): Key fehlt → Default, leerer String → leer bleibt leer (Opt-Out), sonst → Config-String.
- **BotFather "Group Privacy"** muss AUS sein, damit der Bot in Gruppen Nachrichten ohne `@`-Mention sieht (nötig für `respond_to_name` und `autonomous_interjection`). Nach Umschalten: Bot aus Gruppe entfernen + neu hinzufügen (Telegram cached die Privacy-Stufe pro Gruppen-Beitritt). Siehe `lessons.md` → "Telegram Group Privacy".

## Guard-Kontext (Patch 158)

- [`check_response()`](zerberus/hallucination_guard.py) akzeptiert einen optionalen `caller_context: str`. Der String beschreibt wer antwortet und wird in den Guard-System-Prompt als `[Kontext des Antwortenden]`-Block eingefügt, gefolgt vom harten Satz „Referenzen auf diese Elemente sind KEINE Halluzinationen."
- **Huginn-Calls** ([`telegram/router.py`](zerberus/modules/telegram/router.py)): Raben-Persona-Kontext via `_build_huginn_guard_context(persona)` — inklusive eines 300-Zeichen-Auszugs der aktuell konfigurierten Persona.
- **Nala-Calls** ([`legacy.py`](zerberus/app/routers/legacy.py)): Zerberus-Selbstreferenz-Kontext ("Referenzen auf Zerberus, Chris, Nala, Hel, Huginn sind keine Halluzinationen").
- **Verdict WARNUNG ist KEIN Block.** Die Antwort geht immer an den User durch; der Admin (Chris) bekommt bei WARNUNG einen DM mit Chat-ID + Grund. Nur echte Sicherheitsklassen (BLOCK/TOXIC) würden die Antwort unterdrücken — die gibt es aktuell nicht, der Guard liefert nur OK/WARNUNG/SKIP/ERROR.
- **Bei neuen Frontends:** Immer eigenen `caller_context` definieren. Leer lassen ist legitim (dann verhält sich der Guard wie vor Patch 158), aber bei Persona-Bots oder Self-Referencing-Assistants sonst false-positive-anfällig.

## Settings-Cache (Patch 156)

- `get_settings()` aus [`zerberus/core/config.py`](zerberus/core/config.py) ist ein Modul-globaler Singleton. Wer `config.yaml` schreibt, MUSS den Cache invalidieren — sonst liefert der nächste `get_settings()`-Aufruf den alten Wert und das UI zeigt verzögerte Werte nach Save.
- **Decorator** (Standardweg): `@invalidates_settings` auf den POST-Handler. Ruft nach dem Funktionsaufruf `reload_settings()` auf, funktioniert für sync + async.
- **Kontextmanager** (granularer): `with settings_writer():` um den YAML-Write-Block — für Fälle wo der Handler mehrere Pfade hat und nur einer schreibt.
- **Migriert:** 7 YAML-Writer in [`hel.py`](zerberus/app/routers/hel.py) und [`nala.py`](zerberus/app/routers/nala.py) sind auf den Decorator umgestellt (Patch 156-Sweep).
- **Für neue YAML-Writer-Endpoints:** Immer `@invalidates_settings` verwenden. Test-Pattern: POST → GET im selben Test mit tmp-cwd-Fixture + `_settings = None`-Reset (siehe [`test_huginn_config_endpoint.py`](zerberus/tests/test_huginn_config_endpoint.py)).

## Datenbank-Migrationen (Alembic, seit Patch 92)

- Alembic-Config: `alembic.ini` im Projektroot, Revisionen unter `alembic/versions/`
- Manueller Aufruf — **kein Auto-Upgrade beim Serverstart**
- Anwenden: `alembic upgrade head`
- Neue Revision: `alembic revision -m "beschreibung"`, dann manuell in upgrade/downgrade editieren
- Migrationen IMMER idempotent schreiben (`PRAGMA table_info`-Check, `IF NOT EXISTS`)
- Vor jeder Schema-Änderung: DB-Backup (`cp bunker_memory.db bunker_memory_backup_patch{N}.db`)

## Tests (seit Patch 93)

- Playwright-Tests in `zerberus/tests/` (Loki = E2E, Fenrir = Chaos, Vidar = Smoke)
- Test-Accounts `loki`/`fenrir`/`vidar` sind Pflicht in `config.yaml` (siehe Patches 93, 153)
- Ausführen: `pytest zerberus/tests/ -v --html=zerberus/tests/report/full_report.html --self-contained-html`
- Server muss laufen für die Tests (`https://127.0.0.1:5000`)

### Test-Agenten
| Agent | Datei | Fokus | Passwort |
|-------|-------|-------|---------|
| **Loki** | `test_loki.py`, `test_loki_mega_patch.py` | E2E Happy-Path, Feature-Verifikation | `lokitest123` |
| **Fenrir** | `test_fenrir.py`, `test_fenrir_mega_patch.py` | Chaos, Edge-Cases, Stress | `fenrirtest123` |
| **Vidar** | `test_vidar.py` | Post-Deployment Smoke-Test (Go/No-Go) | `vidartest123` |

## Weiterführende Doku

- **Fallstricke & Lektionen (projektspezifisch):** `lessons.md`
- **Globale Lessons:** https://github.com/Bmad82/Claude/lessons/
- **Vollständiges Patch-Archiv:** `docs/PROJEKTDOKUMENTATION.md`

## ⚠️ Dateinamen-Konvention
Projektspezifische Anweisungen: `CLAUDE_ZERBERUS.md` (diese Datei)
Supervisor-Briefing: `SUPERVISOR_ZERBERUS.md`
Patch-Prompts referenzieren IMMER den vollen Dateinamen mit Projektsuffix.
NIEMALS mit der globalen CLAUDE.md verwechseln oder zusammenführen.

## Supervisor-Patch-Prompts
Patch-Prompts werden vom Supervisor (claude.ai Chat) immer als `.md`-Datei generiert — nie als inline Chat-Text. Claude Code erhält den Inhalt per Copy-Paste aus der Datei.

## Repo-Sync-Pflicht

- **Nach jedem Patch:** `git add -A; git commit -m "Patch XX – [Titel]"; git push` (nur Zerberus).
- **Nach jedem Patch:** Neuen Eintrag in `docs/PROJEKTDOKUMENTATION.md` anhängen (am Ende des bestehenden Patchlogs, nicht vorhandene Einträge verändern). Der Eintrag enthält:
  - Patch-Nummer + Titel
  - Datum (ISO, z.B. `2026-04-23`)
  - Was geändert wurde (1–3 Sätze)
  - Welche Dateien neu/geändert
  - Aktuellen Teststand (X Tests grün)
- **Am Ende jeder Claude-Code-Session ODER nach jedem 5. Patch:** `powershell -ExecutionPolicy Bypass -File sync_repos.ps1` ausführen. Das Script synchronisiert Ratatoskr (SUPERVISOR/CLAUDE/PROJEKTDOKUMENTATION/lessons/backlog/README) und das Claude-Repo (nur lessons.md, als `lessons/zerberus_lessons.md`) automatisch, zieht die Commit-Message aus dem letzten Zerberus-Commit und pusht nur, wenn es Änderungen gibt.
- **Niemals** Ratatoskr oder das Claude-Repo manuell editieren — immer nur via `sync_repos.ps1` aus Zerberus kopieren. Direkte Commits dort werden beim nächsten Sync überschrieben.
- Universelle, projektübergreifende Lessons können zusätzlich direkt in `C:\Users\chris\Python\Claude\lessons\` unter einem generischen Namen abgelegt werden (z.B. `sqlite-db.md`, `frontend-js.md`) — das Sync-Script fasst sie nicht an, weil es nur `zerberus_lessons.md` schreibt.
