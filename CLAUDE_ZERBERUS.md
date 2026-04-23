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

## Datenbank-Migrationen (Alembic, seit Patch 92)

- Alembic-Config: `alembic.ini` im Projektroot, Revisionen unter `alembic/versions/`
- Manueller Aufruf — **kein Auto-Upgrade beim Serverstart**
- Anwenden: `alembic upgrade head`
- Neue Revision: `alembic revision -m "beschreibung"`, dann manuell in upgrade/downgrade editieren
- Migrationen IMMER idempotent schreiben (`PRAGMA table_info`-Check, `IF NOT EXISTS`)
- Vor jeder Schema-Änderung: DB-Backup (`cp bunker_memory.db bunker_memory_backup_patch{N}.db`)

## Tests (seit Patch 93)

- Playwright-Tests in `zerberus/tests/` (Loki = E2E, Fenrir = Chaos)
- Test-Accounts `loki`/`fenrir` sind Pflicht in `config.yaml` (siehe Patch 93)
- Ausführen: `pytest zerberus/tests/ -v --html=zerberus/tests/report/full_report.html --self-contained-html`
- Server muss laufen für die Tests (`https://127.0.0.1:5000`)

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
