# CLAUDE.md – Zerberus Pro 4.0 / Rosa

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

## RAG-Upload (Patch 56)

- Endpunkt: `POST /hel/admin/rag/upload` (`.txt` / `.docx`, Chunking ~300 Wörter / 50 Überlapp)
- Index-Status: `GET /hel/admin/rag/status`
- Index leeren: `DELETE /hel/admin/rag/clear` — setzt `faiss.index` + `metadata.json` zurück
- Neue Hilfsfunktion im RAG-Modul: `_reset_sync(settings)` in `zerberus/modules/rag/router.py`

## Sentiment-Modul (Patch 57)

- Modul: `zerberus/modules/sentiment/router.py`
- Exportierte Funktion: `analyze_sentiment(text: str) -> {"label": str, "score": float}`
- Modell: `oliverguhr/german-sentiment-bert` via `transformers.pipeline`, CUDA wenn verfügbar
- Graceful: bei fehlendem `torch`/`transformers` → `{"label": "neutral", "score": 0.5}`
- `database.py` nutzt `_compute_sentiment()` statt direktem `vaderSentiment`-Import

## Overnight-Scheduler (Patch 57)

- Modul: `zerberus/modules/sentiment/overnight.py`
- `create_scheduler()` → `AsyncIOScheduler` (APScheduler), täglich 04:30 Europe/Berlin
- Schreibt `bert_sentiment_label` + `bert_sentiment_score` in `message_metrics` (ALTER TABLE per PRAGMA-Check)
- Scheduler wird in `zerberus/main.py` Lifespan gestartet/gestoppt (graceful)
- Neuer Endpunkt: `GET /hel/metrics/history?limit=50`

## Dialect-Kurzschluss (Patch 58)

- In `zerberus/app/routers/legacy.py` (POST `/v1/chat/completions`) wird der Dialect-Check **vor** dem Permission-Check ausgeführt
- Dialect-Trigger (🐻🐻 Berlin, 🥨🥨 Schwäbisch, ✨✨ Emojis) verlassen den Handler sofort via `check_dialect_shortcut()` ohne Intent-Klassifikation und HitL-Flow
- Der Permission-Check bleibt erhalten und läuft danach für alle nicht-Dialect-Requests

## Pacemaker-Konfiguration (Patch 56)

- Laufzeit (`keep_alive_minutes`) im Hel-Dashboard unter „Systemsteuerung" editierbar
- Schreibt via `POST /hel/admin/pacemaker/config` direkt in `config.yaml` (PyYAML round-trip)
- Wirkt **erst nach Neustart** — kein Live-Reload
- Aktueller Default: 120 Minuten
- Pacemaker schickt beim allerersten Start sofort einen Erstpuls (kein Warten auf Intervall)
