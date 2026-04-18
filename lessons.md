# lessons.md – Zerberus Pro 4.0
*Gelernte Lektionen. Nach jeder Korrektur ergänzen.*

## Konfiguration
- config.yaml ist Single Source of Truth — config.json nie als Konfig-Quelle verwenden (Split-Brain Patch 34)
- Module mit `enabled: false` in config.yaml nicht anfassen — auch nicht "kurz testen"
- Pacemaker-Änderungen in config.yaml wirken erst nach Neustart (kein Live-Reload)

## Datenbank
- bunker_memory.db niemals manuell editieren oder löschen
- interactions-Tabelle hat keine User-Spalte — User-Trennung nur per Session-ID (unzuverlässig, vor Metrik-Auswertung klären)
- Alembic noch nicht eingerichtet — DB-Schemaänderungen per PRAGMA/ALTER TABLE, sorgfältig dokumentieren

## Frontend / JavaScript
- '\n' in Python-f-Strings/HTML-Strings wird als echtes Newline gerendert und bricht JS-String-Literale → immer '\\n' verwenden (Patch 69c)
- crypto.randomUUID() schlägt in HTTP-Non-Secure-Kontexten (LAN, kein HTTPS) fehl → generateUUID()-Fallback verwenden (Patch 68)
- $1 ist JS-Regex-Syntax — Python re.sub braucht \1 als Backreference (Patch 69a)
- keypress ist deprecated → keydown verwenden
- Buttons in Login-Formularen brauchen type="button" sonst lösen sie versehentlich form-submit aus
- System-Prompt-Wirkung testen: "Ich bin ein Computerprogramm" kam nicht aus dem Code sondern aus dem fehlenden Persona-Prompt — LLM fällt ohne klare Persona in generisches Verhalten zurück (Patch 81)
- Wenn man Python-Regex-Patterns (z.B. aus `whisper_cleaner.json`) im Browser validieren will: Python-Inline-Flags wie `(?i)` / `(?m)` / `(?s)` werden von JS `RegExp` als ungültige Gruppen abgelehnt. Vor `new RegExp(stripped, flags)` den Prefix mit Regex `/^\(\?([imsx]+)\)/` matchen, ihn rausschneiden und die Buchstaben in den `flags`-String übernehmen. Reicht für Syntax-Smoke-Test; echte Pattern-Semantik (z.B. Backref-Gruppen) wird damit nicht 1:1 abgedeckt (Patch 90)

## RAG
- Orchestrator Auto-Indexing deaktiviert lassen — erzeugt sonst unerwünschte "source: orchestrator"-Chunks nach manuellem Clear (Patch 68)
- RAG-Upload Mobile: MIME-Type-Check schlägt mobil fehl → nur Dateiendung prüfen (Patch 61)
- chunk_size=800 Wörter, overlap=160 Wörter — Einheit ist Wörter, nicht Token (Patch 66)
- Code-Pfad prüfen: Patch 78b implementierte RAG-Skip-Logik nur in orchestrator.py, aber das Nala-Frontend nutzt legacy.py → Fix griff nicht. IMMER prüfen welcher Router den aktiven Traffic führt (Patch 80b)
- RAG-Skip-Logik: OR-basierter Skip (`intent==CONVERSATION OR (kurz AND kein ?)`) ist zu aggressiv — skipped auch QUESTION-Intent ohne "?". MUSS AND-verknüpft sein: nur skippen wenn CONVERSATION UND kurz UND kein ? (Patch 85)
- Orchestrator Auto-Index erzeugt "source: orchestrator"-Chunks die echte Dokument-Chunks verdrängen → nach jedem RAG-Clear prüfen ob nur gewünschte Quellen im Index sind
- Kurze "Residual-Tail"-Chunks (<50 Wörter) aus dem 800/160-Word-Chunking kapern bei normalisierten MiniLM-Embeddings systematisch Rang 1 gegen inhaltsreiche Chunks — der 16-Wort-Titel-Chunk dominiert regelmäßig Glossar-Queries. Bei Retrieval-Problemen immer zuerst Chunk-Längen-Verteilung prüfen (Patch 87)
- Embedding-Modell `paraphrase-multilingual-MiniLM-L12-v2` findet deutsche Eigennamen/Redewendungen ("Perseiden-Nacht", "Verkaufsoffener Sonntag in Ulm") selbst bei wörtlicher Präsenz im Dokument nicht zuverlässig in Top-5 — Cross-Encoder-Reranker oder stärkeres Modell (multilingual-e5-large, bge-m3) nötig für robuste Retrieval-Qualität (Patch 87)
- `rag_eval.py` hardcoded auf `http://127.0.0.1:5000` — Server läuft auf HTTPS seit Patch 82. Script muss pro Lauf angepasst werden oder Eval inline über curl/Python mit SSL-Skip gefahren werden (Patch 87)
- Min-Chunk-Schwelle (Patch 88, `min_chunk_words=120`) in Chunking UND Retrieval ist stabiler als nur eine Seite: Chunking-Merge sorgt für saubere Quelldaten, Retrieval-Filter ist Sicherheitsnetz für Alt-Indizes ohne re-build. Lesson aus Patch 88: das Entfernen kurzer „Räuber-Chunks" verbessert die Chunk-Hygiene messbar (18→12 Chunks, kein <120w-Chunk mehr in Top-5), aber **heilt nicht automatisch schlechte Retrieval-Scores** — wenn das Embedding-Modell den richtigen Chunk gar nicht erst kennt (Q4 Perseiden, Q10 Ulm), bleibt das Top-1-Ergebnis schlecht. Reihenfolge der Fixes: erst Chunking säubern, dann Reranker, erst danach Modell-Wechsel (Patch 88)
- `_search_index()` in `rag/router.py` (NICHT `_rag_search`) ist die FAISS-Search-Funktion. Im Orchestrator wird sie als `rag_search` importiert und dort in `_rag_search()` gewrappt — Patch-Prompts oft missverständlich, immer den tatsächlichen Funktionsnamen prüfen (Patch 88)
- Server-Restart bei Code-Änderungen: `start.bat` lief bis Patch 88 OHNE `--reload` — Code-Änderungen wurden nicht aufgenommen, ohne dass es laut auffiel. `--reload`-Flag in start.bat ergänzt; bei jedem Patch im Eval-Pfad Index löschen + neu indizieren UND prüfen ob neue Response-Felder/Logs erscheinen, sonst läuft alter Code (Patch 88)
- Cross-Encoder-Reranker als zweite Retrieval-Stufe heilt deutsche Eigennamen-Queries zuverlässig, wo der Bi-Encoder (MiniLM, 384-dim) kapituliert: **4/11 JA → 10/11 JA** auf identischem Index, ohne Re-Embed. Q4 (Perseiden-Nacht) und Q10 (Verkaufsoffener Sonntag in Ulm) — beide vorher harte NEINs — landen mit Rerank-Scores 0.942 bzw. 0.929 auf Rang 1. Gewähltes Modell: **BAAI/bge-reranker-v2-m3** (MIRACL-trainiert inkl. Deutsch, saubere `sentence_transformers.CrossEncoder`-Integration ohne `trust_remote_code`, Apache 2.0, 568 M Params). Pattern: FAISS over-fetched `top_k * rerank_multiplier` (z.B. 5×4=20), Cross-Encoder scored Query+Chunk-Paare mit voller Token-Attention und sortiert neu. Bei aktivem Rerank den L2-Threshold-Filter überspringen — Reranker hat eigene Relevanz-Metrik. Fail-Safe-Fallback auf FAISS-Order bei Reranker-Exception ist Pflicht (Cold-Start-DNS-Fehler beim Modell-Download ist real passiert) (Patch 89)
- Uvicorn `--reload` picked new imports NICHT immer auf: Nach Hinzufügen von `zerberus/modules/rag/reranker.py` und Änderung des Import-Graphen in `router.py` lief alter Code weiter, trotz --reload-Flag. Symptom: Response ohne `rerank_score`-Feld, ungewöhnlich niedrige Latenz (< 200 ms). Ein manueller Kill+Restart ist nötig, wenn neue Module eingebunden werden — `--reload` reicht nur für Änderungen an bereits geladenen Dateien (Patch 89)

## Security / Auth
- JWT blockiert externe Clients (Dictate, SillyTavern) vollständig → static_api_key in config.yaml als Workaround (Patch 59/64)
- .env niemals in Logs ausgeben oder committen
- Rosa Security Layer: Dateien im Projektordner sind nur Vorbereitung — Verschlüsselung ist NICHT aktiv
- Bei Profil-Key-Umbenennung (z.B. user2 → jojo) immer password_hash explizit prüfen und im Log bestätigen lassen — fehlende Hashes erzeugen lautlose Login-Fehler (Patch 83)

## Deployment
- Emoji-Zeichen (z.B. 📄 = \uD83D\uDCC4 Surrogate-Pair) in Python-Strings können Encoding-Fehler auf Windows erzeugen → durch ASCII-Alternativen ersetzen (Patch 68)
- start.bat muss `call venv\Scripts\activate` vor uvicorn enthalten
- spaCy-Modell einmalig manuell installieren: `python -m spacy download de_core_news_sm`

## Pacemaker
- Pacemaker Double-Start: update_interaction() muss async sein, alle Call-Sites brauchen await (Patch 59)
- Lock-Guard prüfen ob Task bereits läuft bevor neuer gestartet wird

## Workflow / Claude Code
- Vor jeder Claude-Code-Session: git commit als Checkpoint (YOLO-Modus ohne Backup ist Russisch Roulette)
- Max 2-3 Patches pro Session — ab Patch 4+ leidet die Kontextqualität spürbar
- CLAUDE.md und HYPERVISOR.md sind heilig — Claude Code darf sie ergänzen, niemals überschreiben
- Bei unerwartetem Verhalten: erst HYPERVISOR.md lesen, dann debuggen
- Patch-Prompts im Hypervisor-Fenster planen, Claude Code nur ausführen lassen — kein Mischen

## Architektur / Bekannte Schulden
- interactions-Tabelle ohne User-Spalte: Metriken per User erst nach DB-Fix (Alembic) vertrauenswürdig
- Rosa Security Layer ist NICHT aktiv — nur Dateivorbereitung vorhanden
- JWT + static_api_key: externe Clients (Dictate, SillyTavern) brauchen X-API-Key Header
- RAG Lazy-Load-Guard in _encode(): _model war None bei erstem Upload-Call → immer auf None prüfen vor Modell-Nutzung

## Mobile / Zugriff
- Tailscale + HTTPS Pflicht für mobile Nutzung (crypto.randomUUID() versagt auf HTTP)
- :hover funktioniert auf Touch nicht — immer :active zusätzlich setzen. Genereller :hover → :active Sweep nötig bei jedem UI-Patch. Touch-Geräte kennen kein Hover.
- Mindest-Touch-Target: 44px — kleinere Buttons werden mobil regelmäßig verfehlt
- backdrop-filter ohne Fallback bricht auf älteren Mobile-Browsern
- Landscape ≠ Portrait nur in der Höhe: bei `@media (orientation: landscape) and (max-height: 500px)` greifen extra Regeln (Header schrumpfen, Modals von 88vh auf 90vh+dvh ziehen, Input-Bar kompaktieren). `100dvh` ist gegen Keyboard-Overlap robust, aber Header und Modals fressen sonst die halbe Höhe. Hel mit padded-scroll-Body ist von Haus aus tolerant — `100dvh`-Layouts (Nala) brauchen den Fix (Patch 90).

## Pipeline / Routing
- Das Nala-Frontend routet über /v1/chat/completions (legacy.py), NICHT über den Orchestrator. Fixes die nur in orchestrator.py landen wirken nicht auf den Haupt-Chat-Pfad.
- Bei jedem Pipeline-Fix: alle drei Pfade prüfen — legacy.py, orchestrator.py, nala.py /voice
- Änderungen in legacy.py betreffen auch externe Clients (Dictate, SillyTavern) — immer prüfen ob /v1/audio/transcriptions und der Static-API-Key-Bypass intakt sind (Patch 82)

## Frontend-Charts
- Chart.js CDN-Integration: chart.umd.min.js allein reicht NICHT für Touch-Pinch-Zoom. Das `chartjs-plugin-zoom` benötigt zwingend `hammerjs` (Touch-Gesten-Library) — Reihenfolge im `<head>`: chart.umd → hammer → zoom-plugin. Ohne hammerjs failed das Plugin silent, nur `wheel`-Zoom funktioniert (Patch 91).
- Chart.js `new Chart()` mehrfach auf dasselbe Canvas → Memory-Leak. IMMER `metricsChart.destroy()` vor dem Neu-Rendern, UND Referenz auf `null` setzen (Patch 91).
- Für responsives Chart-Layout braucht der Container eine feste Höhe (`position: relative; height: 280px`) — ohne explizite Höhe rendert Chart.js mit `responsive: true` entweder 0-Pixel oder bläht sich unkontrolliert auf (Patch 91).
- Dünne Linien (`borderWidth: 1.5`) + `pointRadius: 0` + `pointHitRadius: 12` ist der sauberste Look für viele parallel laufende Metriken: keine Punkte-Wolke, aber großzügige Touch-Zone für Tooltips (Patch 91).

## DB-Migrationen
- DB-Migrationen IMMER idempotent schreiben: `PRAGMA table_info`-Check vor `ALTER TABLE`, `CREATE INDEX IF NOT EXISTS`. Das erlaubt parallele Startup-Hook-Migration UND Alembic-Revisionen ohne Konflikt (Patch 92).
- Vor jeder Schema-Änderung an `bunker_memory.db`: manuelles Backup (`cp bunker_memory.db bunker_memory_backup_patch{N}.db`). Die DB ist heilig und darf nicht durch eine fehlerhafte Migration sterben (Patch 92).
- Alembic-Baseline-Revision NACH manueller Spalten-Migration erstellen + `alembic stamp head` — sonst versucht `alembic upgrade head` beim nächsten Lauf, die Spalte nochmal anzulegen. Idempotente Migrationen (`_has_column`-Check) verzeihen das, aber sauberer ist stamp (Patch 92).
- Wenn der Server bereits läuft und `init_db` die neue Spalte beim Start hätte anlegen sollen: die laufende Session hat noch das alte Schema. Entweder manuell `ALTER TABLE` + Server-Restart, oder Server-Restart allein (dann greift init_db). Nur Code-Änderung reicht nicht (Patch 92).

## Testing / Playwright
- Self-signed HTTPS-Certs (Tailscale, lokale Entwicklung): Playwright-Browser-Context braucht `ignore_https_errors: True` — sonst scheitert `page.goto()` mit SSL-Fehler. Setzen via `browser_context_args`-Fixture in `conftest.py` (Patch 93).
- Locator-Strategie: Echte IDs der App nehmen, keine generischen Fallback-Selektoren erfinden. Nala hat `#login-username`, `#login-password`, `#login-submit`, `#text-input` — vor dem Schreiben der Tests einmal in die Ziel-Datei reingrepen. Spart später Debug-Zeit (Patch 93).
- Test-Accounts in `config.yaml` statisch anlegen — NICHT zur Laufzeit per Fixture, sonst spült ein Server-Neustart die Hashes weg. bcrypt-Hashes einmalig per Python generieren und persistieren (Patch 93).
- Chaos-Tests mit `@pytest.mark.parametrize` für Payload-Listen — sauber, Fehlschläge zeigen genau welches Pattern gebrochen hat. `force=True` bei `.click()` in Chaos-Tests, damit andere Overlays nicht blockieren. Exceptions beim Klicken in Schleifen stumm schlucken — Modals dürfen öffnen (Patch 93).
- `playwright install chromium` zieht ~250 MB nach `%USERPROFILE%\AppData\Local\ms-playwright\` — einmaliger Download, dann offline nutzbar. Version beim Upgrade prüfen (`playwright --version`) (Patch 93).
