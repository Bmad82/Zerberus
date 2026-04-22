# lessons.md – Zerberus Pro 4.0 (projektspezifisch)
*Gelernte Lektionen. Nach jeder Korrektur ergänzen.*

Universelle Erkenntnisse: https://github.com/Bmad82/Claude/lessons/

## Konfiguration
- config.yaml ist Single Source of Truth — config.json nie als Konfig-Quelle verwenden (Split-Brain Patch 34)
- Module mit `enabled: false` in config.yaml nicht anfassen — auch nicht "kurz testen"
- Pacemaker-Änderungen in config.yaml wirken erst nach Neustart (kein Live-Reload)

## Datenbank
- bunker_memory.db niemals manuell editieren oder löschen
- interactions-Tabelle hat keine User-Spalte — User-Trennung nur per Session-ID (unzuverlässig, vor Metrik-Auswertung klären)

## RAG
- Orchestrator Auto-Indexing deaktiviert lassen — erzeugt sonst unerwünschte "source: orchestrator"-Chunks nach manuellem Clear (Patch 68)
- RAG-Upload Mobile: MIME-Type-Check schlägt mobil fehl → nur Dateiendung prüfen (Patch 61)
- chunk_size=800 Wörter, overlap=160 Wörter — Einheit ist Wörter, nicht Token (Patch 66)
- Code-Pfad prüfen: Patch 78b implementierte RAG-Skip-Logik nur in orchestrator.py, aber das Nala-Frontend nutzt legacy.py → Fix griff nicht. IMMER prüfen welcher Router den aktiven Traffic führt (Patch 80b)
- RAG-Skip-Logik: OR-basierter Skip (`intent==CONVERSATION OR (kurz AND kein ?)`) ist zu aggressiv — skipped auch QUESTION-Intent ohne "?". MUSS AND-verknüpft sein: nur skippen wenn CONVERSATION UND kurz UND kein ? (Patch 85)
- Orchestrator Auto-Index erzeugt "source: orchestrator"-Chunks die echte Dokument-Chunks verdrängen → nach jedem RAG-Clear prüfen ob nur gewünschte Quellen im Index sind
- Kurze "Residual-Tail"-Chunks (<50 Wörter) aus dem 800/160-Word-Chunking kapern bei normalisierten MiniLM-Embeddings systematisch Rang 1 gegen inhaltsreiche Chunks. Bei Retrieval-Problemen immer zuerst Chunk-Längen-Verteilung prüfen (Patch 87)
- Embedding-Modell `paraphrase-multilingual-MiniLM-L12-v2` findet deutsche Eigennamen/Redewendungen selbst bei wörtlicher Präsenz nicht zuverlässig in Top-5 — Cross-Encoder-Reranker oder stärkeres Modell nötig (Patch 87)
- `rag_eval.py` hardcoded auf `http://127.0.0.1:5000` — Server läuft auf HTTPS seit Patch 82. Script anpassen oder Eval inline mit SSL-Skip (Patch 87)
- Min-Chunk-Schwelle (Patch 88, `min_chunk_words=120`) in Chunking UND Retrieval: Chunking-Merge sorgt für saubere Quelldaten, Retrieval-Filter ist Sicherheitsnetz für Alt-Indizes. Reihenfolge der Fixes: erst Chunking säubern, dann Reranker, erst danach Modell-Wechsel (Patch 88)
- `_search_index()` in `rag/router.py` (NICHT `_rag_search`) ist die FAISS-Search-Funktion. Im Orchestrator wird sie als `rag_search` importiert und dort in `_rag_search()` gewrappt — Patch-Prompts oft missverständlich, immer den tatsächlichen Funktionsnamen prüfen (Patch 88)
- Cross-Encoder-Reranker als zweite Retrieval-Stufe: **BAAI/bge-reranker-v2-m3** (Apache 2.0). Pattern: FAISS over-fetched `top_k * rerank_multiplier`, Cross-Encoder scored Query+Chunk-Paare neu. Bei aktivem Rerank L2-Threshold-Filter überspringen. Fail-Safe-Fallback auf FAISS-Order bei Reranker-Exception (Patch 89)
- **Eval-Delta Reranker: 4/11 JA → 10/11 JA** (Patch 89). Q4 (Perseiden-Nacht) und Q10 (Verkaufsoffener Sonntag in Ulm) geheilt.

## Security / Auth
- JWT blockiert externe Clients (Dictate, SillyTavern) vollständig → static_api_key in config.yaml als Workaround (Patch 59/64)
- .env niemals in Logs ausgeben oder committen
- Rosa Security Layer: Dateien im Projektordner sind nur Vorbereitung — Verschlüsselung ist NICHT aktiv
- Bei Profil-Key-Umbenennung (z.B. user2 → jojo) immer password_hash explizit prüfen und im Log bestätigen lassen — fehlende Hashes erzeugen lautlose Login-Fehler (Patch 83)
- **/v1/-Endpoints MÜSSEN auth-frei bleiben** — Dictate-App (Android-Tastatur) kann keine Custom-Headers (X-API-Key, JWT) setzen. Bei JEDEM Patch der die Auth-Middleware anfasst: `/v1/chat/completions` und `/v1/audio/transcriptions` ohne Auth testen. Bypass liegt in `_JWT_EXCLUDED_PREFIXES` (Prefix `/v1/`) in `zerberus/core/middleware.py`. Regression ist hier ein Dauerbrenner (Hotfix 103a).

## Deployment (Zerberus-spezifisch)
- start.bat muss `call venv\Scripts\activate` vor uvicorn enthalten
- spaCy-Modell einmalig manuell installieren: `python -m spacy download de_core_news_sm`

## Pacemaker
- Pacemaker Double-Start: update_interaction() muss async sein, alle Call-Sites brauchen await (Patch 59)
- Lock-Guard prüfen ob Task bereits läuft bevor neuer gestartet wird

## Architektur / Bekannte Schulden
- interactions-Tabelle ohne User-Spalte: Metriken per User erst nach DB-Fix (Alembic) vertrauenswürdig
- Rosa Security Layer ist NICHT aktiv — nur Dateivorbereitung vorhanden
- JWT + static_api_key: externe Clients (Dictate, SillyTavern) brauchen X-API-Key Header
- RAG Lazy-Load-Guard in _encode(): _model war None bei erstem Upload-Call → immer auf None prüfen vor Modell-Nutzung

## Pipeline / Routing
- Das Nala-Frontend routet über /v1/chat/completions (legacy.py), NICHT über den Orchestrator. Fixes die nur in orchestrator.py landen wirken nicht auf den Haupt-Chat-Pfad.
- Bei jedem Pipeline-Fix: alle drei Pfade prüfen — legacy.py, orchestrator.py, nala.py /voice
- Änderungen in legacy.py betreffen auch externe Clients (Dictate, SillyTavern) — immer prüfen ob /v1/audio/transcriptions und der Static-API-Key-Bypass intakt sind (Patch 82)
- Dictate-Tastatur (Android) schickt bereits transkribierte+bereinigte Texte an /v1/chat/completions. legacy.py wendet den Whisper-Cleaner erneut an — das ist doppelt, aber aktuell harmlos (Cleaner-Regeln sind idempotent). Falls künftig nicht-idempotente Regeln hinzukommen, braucht der /v1/-Endpoint einen Skip-Flag oder Header (z.B. X-Already-Cleaned: true). Backlog-Item, kein akuter Fix. (Patch 107)
- Intent-Klassen TRANSFORM (Übersetzen/Lektorieren/Zusammenfassen/…) skippen RAG komplett. Der User liefert den Bearbeitungstext mit — RAG-Treffer wären Lärm, Cross-Encoder-Rerank kostet ~47 s auf CPU für nichts. Pattern match NUR am Nachrichtenanfang (sonst kapert "übersetze" mitten im Text jede Frage). Muster in `_TRANSFORM_PATTERNS` in orchestrator.py (Patch 106).

## Konfiguration (Fortsetzung)
- Hel-Admin-UI `/hel/admin/config` arbeitet seit Patch 105 auf config.yaml als Single Source of Truth. Vorher Split-Brain: UI schrieb `llm.cloud_model` nach config.json, LLMService las `legacy.models.cloud_model` aus config.yaml → UI-Auswahl wirkte nie. `_yaml_replace_scalar` (hel.py) macht eine line-basierte In-Place-Ersetzung, damit die handgepflegten Kommentare in config.yaml erhalten bleiben (yaml.safe_dump würde alles neu serialisieren). config.json darf nicht mehr als Authoritative-Quelle verwendet werden — Debug-Endpunkte dürfen es anzeigen, aber nichts darf von dort lesen/schreiben.

## RAG (Fortsetzung)
- Reranker-Minimum-Score (`modules.rag.rerank_min_score`, Default 0.05) filtert nach dem Cross-Encoder-Pass: liegt der Top-Score darunter, ist KEIN Chunk wirklich relevant → RAG-Kontext wird komplett verworfen (z.B. bei Übersetzungs-Requests). Fix greift in `_rag_search()`, automatisch für legacy.py und orchestrator.py. Logging `[THRESHOLD-105]` (Patch 105).

## Dialekt-Weiche (Patch 103)
- **Marker-Länge:** Emoji-Marker im `detect_dialect_marker` sind historisch ×5 (Teclado/Dictate-Pattern). Zwischenzeitliche ×2-Variante in `zerberus/core/dialect.py` produzierte 400/500, weil der `stripped[len(marker):]`-Offset nur 2 Emojis abschnitt und drei Emojis im rest-Text übrig blieben. IMMER ×5 als Invariante behalten.
- **Wortgrenzen-Matching Pflicht:** `apply_dialect` muss `re.sub(r'(?<!\w)KEY(?!\w)', ...)` nutzen, nicht `str.replace()`. Sonst matcht `ich` in `nich` und erzeugt `nick`. Case-sensitive bleibt (dialect.json hat `Ich` und `ich` als separate Keys). Multi-Wort-Keys (`haben wir`) funktionieren dank Length-sortiertem Durchlauf weiter.

## Frontend / JS in Python-Strings
- **Python-HTML-Strings mit JS:** `'\n'` in einem Python-String der HTML/JS enthält wird als echtes Newline gerendert und bricht JS-String-Literale (`SyntaxError: unterminated string literal`). Innerhalb `ADMIN_HTML = """..."""`/`NALA_HTML = """..."""` immer `'\\n'` (bzw. `\\r`, `\\t`) schreiben. Betrifft jeden String in nala.py und hel.py der JavaScript-Code enthält. Erstmals Patch 69c (`exportChat`), erneut sichtbar Patch 100 (`showMetricInfo` — seit Patch 91 latent).
- **JS-Syntax immer mit `node --check` verifizieren:** Nach jedem Patch der das `<script>`-Block in hel.py/nala.py ändert, HTML aus dem Router rendern, `<script>`-Blöcke extrahieren, einzeln durch `node --check` jagen. Der Test `TestJavaScriptIntegrity` (Patch 100) fängt den Fehler im Playwright-Browser — `node --check` ist die schnelle Pre-Commit-Variante.
- DOM-/API-Level-Tests (Playwright ohne `pageerror`-Listener) fangen JS-Parse-Errors NICHT. `page.on("pageerror", ...)` MUSS VOR `page.goto()` registriert werden, sonst werden initiale Script-Errors verschluckt.

## Chunking-Weiche pro Category (Patch 110)
- **Pro-Category-Profile statt globale Defaults:** `CHUNK_CONFIGS` in [hel.py](zerberus/app/routers/hel.py) mapped jede Category auf `{chunk_size, overlap, min_chunk_words, split}`. `narrative`/`lore` behalten 800/160/120 aus Patch 75 — die sind mit dem bge-reranker-v2-m3 aus Patch 89 validiert (10/11 JA im Eval). Kleinere Chunks (z.B. 300/60 für `reference`) brauchen entsprechend niedrigere `min_chunk_words` (50 statt 120), sonst mergt der Residual-Pass alles zu einem Einzel-Chunk — getestet im Unit-Test `test_override_parameters_produce_expected_chunk_count`.
- **Split-Strategien:** `"chapter"` (Prolog/Akt/Epilog/Glossar, `_CHAPTER_RE`), `"markdown"` (`# … ######`, `_MD_HEADER_RE`, für `technical`), `"none"` (nur Wortfenster, für `reference`). Die beiden Regex-Patterns lassen sich parametrisch wählen, der Rest der Chunk-Logik bleibt gleich.
- **Rückwärtskompat:** `config.yaml modules.rag.min_chunk_words` (Patch 88) überschreibt weiterhin den Category-Default, falls gesetzt — so bleibt Chris' globaler Tuning-Hebel intakt.
- **JSON + CSV als Klartext indizieren:** `.json` → `json.dumps(data, indent=2, ensure_ascii=False)` liefert strukturerhaltenden Klartext für das Embedding. `.csv` → Header-Zeile, dann pro Daten-Zeile `"Header1: Wert1; Header2: Wert2"` (leere Zellen ausgelassen). Kein neues Dependency nötig, beides stdlib.

## SSE / Streaming Resilience (Patch 109)
- **Frontend-Timeout ≠ Backend-Timeout:** Der 45-s-Frontend-Timeout bricht den `fetch()` ab, das Backend arbeitet trotzdem weiter und speichert die fertige Antwort via `store_interaction()` in die DB. Ein naiver Retry-Button, der einfach `sendMessage(text)` nochmal aufruft, produziert **doppelte LLM-Calls** (doppelte OpenRouter-Kosten + Verwirrung in der Session-Historie).
- **Fix-Pattern:** Retry-Button prüft erst per REST-Endpoint (`/archive/session/{id}`) ob die DB inzwischen eine Antwort zum gleichen User-Text enthält. Nur wenn keine späte Antwort gefunden wird, läuft ein echter Retry. Kein neuer Endpoint nötig — das Archive-Endpoint existiert bereits für Session-Load. `fetchLateAnswer(sid, userText)` + `retryOrRecover(retryText, retrySid, cleanupFn)` in [nala.py](zerberus/app/routers/nala.py) kapseln die Logik (Patch 109).
- **Signatur-Hinweis:** Retry-Handler (`setTypingState`, `showErrorBubble`) müssen die `reqSessionId` als dritten Parameter durchreichen — nicht `sessionId` zur Click-Zeit verwenden, weil der User inzwischen die Session gewechselt haben kann.

## Theme-Defaults (Patch 109)
- **Anti-Invariante „nie schwarz auf schwarz":** Bubble-Background-Defaults in `:root` müssen **lesbare Werte** haben, selbst ohne gesetztes Theme oder Favorit. `rgba(…, 0.85–0.88)`-Werte mit den Theme-Hex-Farben als Basis geben optische Tiefe ohne den Kontrast zu brechen. User-Bubble `rgba(236, 64, 122, 0.88)` + LLM-Bubble `rgba(26, 47, 78, 0.85)` entsprechen Chris's Purple/Gold-Scheme.
- **Reset muss vollständig sein:** `resetTheme()` darf nicht nur die 5 Theme-Farben zurücksetzen — sonst bleiben alte Bubble-Overrides (z.B. schwarzer Hintergrund) in `localStorage` aktiv und übersteuern die rgba-Defaults. Lösung: `resetTheme()` ruft `resetAllBubbles()` + `resetFontSize()` mit auf.

## Dateinamen-Konvention (Patch 100)
- Projektspezifische CLAUDE.md IMMER als `CLAUDE_[PROJEKTNAME].md` benennen
- Gleiches für Supervisor-Briefing: `SUPERVISOR_[PROJEKTNAME].md`
- In Patch-Prompts IMMER den vollen Dateinamen verwenden
- Hintergrund: Patch 100 — Claude Code hat die projektspezifische Datei mit der globalen verwechselt
