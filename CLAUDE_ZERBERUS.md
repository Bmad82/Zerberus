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

## Ratatoskr-Sync (nach JEDEM Patch)
Folgende Dateien nach `C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr\` kopieren und pushen:
- `CLAUDE_ZERBERUS.md`
- `SUPERVISOR_ZERBERUS.md` (wird vom Supervisor separat aktualisiert — nur kopieren wenn im Zerberus-Repo vorhanden)
- `lessons.md`
- `PROJEKTDOKUMENTATION.md`
- `backlog_nach_patch83.md`
- `README.md`

```powershell
$src = "C:\Users\chris\Python\Rosa\Nala_Rosa\Zerberus"
$dst = "C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr"
Copy-Item "$src\CLAUDE_ZERBERUS.md" "$dst\" -Force
Copy-Item "$src\lessons.md" "$dst\" -Force
Copy-Item "$src\PROJEKTDOKUMENTATION.md" "$dst\" -Force
Copy-Item "$src\backlog_nach_patch83.md" "$dst\" -Force
Copy-Item "$src\README.md" "$dst\" -Force
cd $dst
git add -A; git commit -m "Sync nach Patch [NR]"; git push
```

Universelle Lessons zusätzlich nach `C:\Users\chris\Python\Claude\lessons\` spiegeln (nur wenn die Erkenntnis projektübergreifend gilt).
