# CLAUDE.md – Zerberus Pro 4.0 / Rosa

## Pflicht nach jedem Patch
Nach Abschluss jedes Patches HYPERVISOR.md aktualisieren:
- Aktueller Patch: Nummer, Datum, 3-5 Zeilen was gemacht wurde
- Offene Items: Liste aktuell halten (erledigte raus, neue rein)
- Architektur-Warnungen: nur wenn sich etwas geändert hat
HYPERVISOR.md ist die einzige Datei die Hypervisor-Claude beim Session-Start liest.
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

## Weiterführende Doku

- **Fallstricke & Lektionen:** `lessons.md` — Gotchas, gelernte Fehler, Plattform-Eigenheiten
- **Vollständiges Patch-Archiv:** `docs/PROJEKTDOKUMENTATION.md`
