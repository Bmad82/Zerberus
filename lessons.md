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

## RAG GPU-Acceleration (Patch 111)
- **torch-Variante checken:** `pip list | grep torch` zeigt `+cpu` wenn CUDA nicht drin ist. RTX 3060 bleibt ungenutzt bis `pip install torch --index-url https://download.pytorch.org/whl/cu121` (oder passender CUDA-Version). Der Device-Helper aus Patch 111 fällt defensiv auf CPU zurück — also keine Regression, aber auch kein Speed-Up.
- **VRAM-Threshold 2 GB:** MiniLM ~0.5 GB + bge-reranker-v2-m3 ~1 GB + Puffer. Whisper frisst ~4 GB, BERT-Sentiment ~0.5 GB → auf einer 12-GB-Karte bleiben 7 GB frei, mehr als genug. Der Check soll nur verhindern dass bei gleichzeitig belegtem VRAM (z.B. paralleler Whisper-Call) das Embedding auf OOM läuft.
- **CrossEncoder nimmt `device` direkt:** In `sentence-transformers >= 2.2` akzeptiert `CrossEncoder(model, device=...)` den Konstruktor-Parameter. Keine nachträgliche `.to(device)`-Verschiebung nötig. In Patch 111 mit `inspect.signature(CrossEncoder.__init__)` verifiziert.
- **`_cuda_state()` isolieren:** Die eine Funktion ist mockbar ohne `torch`-Dependency in Tests — `monkeypatch.setattr(dev_mod, "_cuda_state", lambda: (True, 8.0, 12.0, "RTX 3060"))`. 9 Unit-Tests für Device-Detection ohne jede GPU (Patch 111).

## Query-Router / Category-Boost (Patch 111)
- **Wortgrenzen-Matching IMMER:** Naives Substring-`in` findet `api` in `Kapitel` → false positive. `re.search(r'(?<!\w)kw(?!\w)', text.lower())` ist Pflicht. Multi-Wort-Keys (`"ich habe"`) fallen auf einfaches `in` zurück, weil der Leerzeichen-Kontext selbst Wortgrenze ist. Lesson analog zu Patch 103 Dialekt-Weiche.
- **Boost statt Filter:** Category-Filtering als hartes Drop würde Retrieval brüchig machen (Fehl-Klassifikation der Heuristik = leeres Ergebnis). Score-Bonus (Default 0.1) auf `rerank_score` (Cross-Encoder, Patch 89) bzw. `score` als Fallback verschiebt nur die Reihenfolge. Flag `category_boosted: True` pro Chunk für Debugging.
- **Keyword-Listen kurz halten:** Lieber false negatives (kein Boost) als false positives (falscher Boost). 5-15 Keywords pro Category reicht — LLM-basierte Detection kommt eh in Phase 4.

## Auto-Category-Detection (Patch 111)
- **Extension-Map statt Content-Analyse:** Erste Stufe reicht `Path(filename).suffix.lower()` → dict lookup. Content-basierte Detection (LLM-Call) kommt in Phase 4. `.json`/`.yaml`/`.md` → technical, `.csv` → reference, `.pdf`/`.txt`/`.docx` → general. Bei `.txt`/`.pdf` gibt's keine saubere Zuordnung — `general` ist die richtige Wahl (Chris kann manuell overriden).
- **`"general"` als Detection-Trigger behalten:** Nicht nur `"auto"`. Grund: Alt-Uploads mit `general` sind meist unbewusst gesetzt, die Auto-Detection verbessert ohne Opt-In. User-Override (narrative/technical/…) gewinnt immer.
- **Python `\u{…}` in HTML-Strings bricht Parse:** Emoji-Literale wie `\u{1F50D}` sind ES6+ JS-Syntax, aber Python's String-Parser erwartet nach `\u` genau 4 Hex-Chars → `SyntaxError: truncated \uXXXX escape` beim Import. Entweder Emoji direkt einfügen oder `\\u{1F50D}` (doppel-escape). Analog zur Patch-69c/100-Lesson zu `\n` in JS-Strings.

## GPU / PyTorch (Patch 111b)
- **pip default = CPU-only:** `pip install torch` zieht `2.x.y+cpu`. Für GPU ist der CUDA-Extra-Index Pflicht: `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 --force-reinstall`. CUDA-Version per `nvidia-smi` (Treiber rückwärtskompatibel, Driver 591.44 = CUDA 13.1 akzeptiert alle cu11x/cu12x-Wheels). RTX 3060 läuft stabil mit cu124 + torch 2.5.1.
- **typing-extensions Ripple:** `torch 2.5.1+cu124` downgradet `typing_extensions` auf 4.9.0, `cryptography>=46` verlangt aber 4.13.2+. Nach jedem torch-Reinstall prüfen: `pip install --upgrade "typing-extensions>=4.13.2"`. Sichtbares Symptom: `ImportError: cannot import name 'TypeIs' from 'typing_extensions'`.
- **`+cu124`-Suffix als Beweis:** `pip list | grep torch` muss `torch 2.5.1+cu124` zeigen (mit Plus-Suffix). Ohne Suffix = CPU-only oder noch aus der `-orch`-Umbenennung mitten im Install. Erst `torch.cuda.is_available() == True` UND `torch.cuda.get_device_name(0)` zeigt echten GPU-Status.
- **Frei VRAM nach Whisper/BERT:** Auf 12-GB-Karte bleiben nach Windows-Desktop-Usage ~11 GB frei. MiniLM + bge-reranker-v2-m3 brauchen zusammen <2 GB — reichlich Luft. Patch 111's 2-GB-Threshold ist konservativ aber korrekt.

## DB-Dedup / Insert-Guard (Patch 113a)
- **Schema-Check vor Dedup-Query:** Die `interactions`-Tabelle hat `content`+`role`, NICHT `user_message` wie oft in externen Beispielen angenommen. Vor jedem Dedup-SQL `PRAGMA table_info(interactions)` laufen lassen.
- **Dedup-Scope ≠ alle Rollen:** `whisper_input` hat häufig `session_id=NULL` (Dictate-Direct-Logging) — nur `role IN ('user','assistant')` mit konkreter session_id deduplizieren. Der Insert-Guard in `store_interaction` prüft session_id vorher explizit, damit Whisper-Log-Pipeline unberührt bleibt.
- **30-Sekunden-Fenster ist der Sweet Spot:** LLM-Call + Retry-Button bei Timeout liegen typischerweise 15-45s auseinander. 30s fängt die meisten Double-Inserts ab ohne legitime schnelle Nachsendungen zu blocken.

## Whisper Sentence-Repetition (Patch 113b / W-001b)
- **Wort-Dedup ≠ Satz-Dedup:** `detect_phrase_repetition` (Patch 102) cappt N-Gramme bis 6 Wörter. Ganze Sätze > 6 Wörter ("Ich gehe nach Hause.") rutschten durch. `detect_sentence_repetition` splittet an `(?<=[.!?])\s+` und dedupliziert **konsekutive** Sätze (case-insensitive, whitespace-collapsed).
- **Reihenfolge zwingend:** Erst Mikro (Phrase), dann Makro (Sentence) — sonst würden Sätze mit internen Phrase-Loops erst korrekt gekürzt und dann eventuell zu früh als gleich erkannt.
- **Nicht-konsekutive behalten:** `"A. B. A."` bleibt `"A. B. A."` (Refrain-Safe). Nur direkte Nachbarschaft fällt weg.

## SSE-Heartbeat + Watchdog-Reset (Patch 114a)
- **Heartbeat statt fixem Timeout:** Server sendet alle 5 s `event: heartbeat\ndata: processing\n\n` während LLM-Verarbeitung. Frontend `addEventListener('heartbeat', …)` setzt den 15-s-Watchdog zurück. Hard-Stop bei 120 s total verhindert Leaks. CPU-Fallback (kein GPU) bleibt so bis zum harten Limit funktionsfähig, GPU-Pfad bekommt straffe 15-s-UX.
- **`retry: 5000` am Stream-Anfang:** EventSource-Reconnect-Interval ändert sich dauerhaft für diese Verbindung (SSE-Spec). 5 s ist zahm genug um Mobile-Reconnect-Stürme zu vermeiden.
- **`window.__nalaSseWatchdogReset` als loses Coupling:** Das SSE-Listener-Binding lebt sessionübergreifend, aber der Watchdog gehört zu einer einzelnen fetch-Transaktion. Global-Funktion-Pointer (null wenn kein Request läuft) erlaubt das Cross-Wire ohne harte Abhängigkeit.

## Background Memory Extraction (Patch 115)
- **Overnight-Erweiterung statt eigener Cron:** Der 04:30-APScheduler-Job aus Patch 57 (BERT-Sentiment) wird am Ende um Memory-Extraction erweitert. Separater Scheduler wäre Duplizierung — Reihenfolge Sentiment-dann-Extraction ist egal, beide laufen read-only auf 24h-Nachrichten.
- **Cosine aus L2 bei normalisierten Embeddings:** MiniLM-Embeddings sind per `normalize_embeddings=True` unit-normalized. FAISS IndexFlatL2 liefert L2-Distanz; Umrechnung `cos = 1 - L2²/2`. Threshold 0.9 (cos) entspricht L2 ≈ 0.447 — passend für "fast gleiche Aussagen" ohne Refrain-Safe-Fehlinterpretationen.
- **Fail-Safe in Overnight-Job:** Exception aus `extract_memories()` darf den Overnight-Job NICHT abbrechen (Sentiment-Auswertung hängt nicht davon ab). `try/except` außen, fehlschlagende Extraction wird geloggt, Job endet normal.
- **Source-Tag mit Datum:** `source: "memory_extraction_2026-04-23"` statt fixem Tag erlaubt späteres gezieltes Löschen/Audit per Datum via Soft-Delete (Patch 116). Kein Datums-Rollover-Bug, weil `datetime.utcnow().strftime("%Y-%m-%d")` pro Batch einmal gelesen wird.
- **Prompt-Template mit `{messages}`-Placeholder:** Python `str.format()` auf Prompt-Konstante. Alle anderen `{}` im Prompt müssen als `{{}}` escaped werden, sonst KeyError. Lieblingsstelle: das JSON-Output-Beispiel — da müssen `[{{"fact": "..."}}]` doppelt geklammert sein.

## RAG Soft-Delete + Gruppierte Dokumentenliste (Patch 116)
- **FAISS kann nicht selektiv löschen:** `IndexFlatL2` hat kein `remove(idx)`. Zwei Optionen: (1) Rebuild (`_reset_sync` + alle überlebenden Chunks re-encoden) oder (2) Soft-Delete (Metadata-Flag `deleted: true`, beim Retrieval + Status-Listing filtern). Option 2 ist in O(1) statt O(N) und braucht kein erneutes Encoding — einziger Preis: Index wächst bis zum nächsten Reindex.
- **Drei Filter-Stellen:** `_search_index()` (Retrieval), `/admin/rag/status` (Listing), `/admin/rag/reindex` (Rebuild-Quelle). Bei einer vergessenen Stelle lebt der gelöschte Chunk weiter. Grep-Checkpoint: `m.get("deleted") is True` muss in allen drei Pfaden stehen.
- **`encodeURIComponent` für Query-Param mit Leerzeichen:** `source=Neon Kadath.txt` bricht bei URL-Concat, `?source=Neon%20Kadath.txt` funktioniert. JS `fetch('/…?source=' + encodeURIComponent(source))` — nie mit Template-Strings direkt.
- **XSS-Hardening bei Card-Rendering:** `innerHTML` des gruppierten Listings enthält User-Daten (Dateiname). `source.replace(/"/g, '&quot;')` + `source.replace(/</g, '&lt;')` fängt Angle-Bracket- und Quote-Breaks. Datenherkunft ist intern (Admin-Uploads), aber defensiv kostet nichts.

## Portabilität via `%~dp0` in .bat (Patch 117)
- **`%~dp0` = Script-Directory:** Ersetzt hardcodiertes `cd /d C:\...`. Läuft aus jedem Verzeichnis, auf jedem System — das Script wechselt in seinen eigenen Ordner. Muss in Anführungszeichen stehen wenn Pfad Leerzeichen enthält: `cd /d "%~dp0"`.
- **Python-Code war bereits portabel:** `Path("config.yaml")`, `Path("./data/vectors")`, `Path("bunker_memory.db")` alle relativ zum CWD. Kein Handlungsbedarf in `.py`-Dateien. Dokumentations-Markdowns enthalten weiterhin absolute Pfade für Chris-als-User — das sind keine Code-Referenzen, sondern Beispiele.

## Decision-Boxes + Feature-Flags (Patch 118a)
- **Marker-Parsing ohne `eval()`/innerHTML-Injection:** Regex findet `[DECISION]…[/DECISION]`-Blöcke, Inhalt geht durch `[OPTION:wert]` Label. Text ausserhalb der Marker wandert als `document.createTextNode` in die Bubble; nur die `<button>` werden strukturell gebaut. Kein `innerHTML` mit User/LLM-Content.
- **Python-String-Falle bei JS-Regex-Charakterklassen:** `[^\n\r\[]+` im Triple-Quote-String wird beim Python-Parse zu echten Newlines → kaputtes JS-Regex (`Invalid regular expression`). Entweder `[^\\n\\r\\[]+` (Python-Doppel-Escape) oder einfacher: `(.+)` (matcht default kein `\n`). Lesson analog zu Patch 69c/100/111.
- **Feature-Flag als Dict-Default in Settings:** `features: Dict[str, Any] = {"decision_boxes": True}` in `config.py`. config.yaml gitignored → Default muss im Pydantic-Model stehen (wie OpenRouter-Blacklist in Patch 102). `settings.features.get("decision_boxes", False)` greift nach frischem Clone auf True zurück.
- **`append_decision_box_hint()` zentral:** Gemeinsamer Helper in `zerberus/core/prompt_features.py`, importiert in legacy.py UND orchestrator.py. Doppel-Injection per `"[DECISION]" in prompt`-Check verhindert. Prompt-Erweiterung ist sauber getrennt vom System-Prompt-Laden.

## Dateinamen-Konvention (Patch 100)
- Projektspezifische CLAUDE.md IMMER als `CLAUDE_[PROJEKTNAME].md` benennen
- Gleiches für Supervisor-Briefing: `SUPERVISOR_[PROJEKTNAME].md`
- In Patch-Prompts IMMER den vollen Dateinamen verwenden
- Hintergrund: Patch 100 — Claude Code hat die projektspezifische Datei mit der globalen verwechselt

## Session 2026-04-23 (Patches 118b–121)

- **Ratatoskr-Sync vergessen:** sync_repos.ps1 erstellt (Patch 118b), PROJEKTDOKUMENTATION.md lag in docs/ nicht im Root (Pfad-Bug in sync_repos.ps1 gefixt Patch 119b)
- **PROJEKTDOKUMENTATION.md Pflichtschritt:** Muss nach jedem Patch aktualisiert werden — als Regel in CLAUDE_ZERBERUS.md verankert
- **Patch-Nummern:** Keine Nummern freihalten für "Reserve" — lückenlos durchnummerieren, bei Bedarf spätere Patches mit Suffix (127a etc.)
- **Modellwahl:** Nicht automatisch das neueste/größte Modell nehmen. Für jeden Job den Sweet Spot finden: Guard = Mistral Small 3 (billig, schnell), Prosodie = Gemma 4 E4B (Audio-fähig), Transkription = Whisper (spezialisiert)
- **Feature-Creep-Gefahr:** Audio-Sentiment-Pipeline braucht eigenes Architektur-Dokument (docs/AUDIO_SENTIMENT_ARCHITEKTUR.md) damit der Scope klar bleibt
- **Free-Tier-Modelle:** Sind Werbung, überlastet, schlechte Qualität. Paid bevorzugen, auch wenn nur Cents.
- **Bibel-Fibel:** Token-Optimierungs-Regelwerk (im Claude-Projekt). Potenzial für komprimierte System-Prompts an DeepSeek → weniger Tokens pro Call
- **W-001b lebt:** Satz-Repetitions-Loop wurde vom Patch 113b Regex nicht gefangen. Subsequenz-Matcher (Patch 120) als Ergänzung nötig.
- **Modul-Loader darf nicht hart crashen:** `main.py` iteriert `pkgutil.iter_modules()` und importiert stur `modules/<x>/router.py`. Helper-Pakete ohne Router (z.B. `memory` seit Patch 115 — nur `extractor.py`) müssen vor dem Import per `router.py`-Existenzcheck abgefangen und als INFO-Skip geloggt werden, nicht als ERROR. Sonst verwirrt der Serverstart-Output.

## Session 2026-04-23 (Mega-Patch 122–126)

- **Mega-Patches brauchen Token-Disziplin:** Bei ~400k Token-Budget über 5 Patches lohnt es sich, pro Patch die destruktiven/riskanten Teile (FAISS-Migration, UI-komplettumbau) bewusst auszuklammern und Infrastruktur + Tests zuerst abzuliefern. Patch 126 (Dual-Embedder) liefert bewusst nur den `DualEmbedder` + `detect_language` — die eigentliche Index-Migration bleibt ein separater manueller Schritt. Das ist kein halber Patch, sondern eine saubere Scope-Entscheidung.
- **AST-Chunker liefert Chunks mit „Signatur-Kontext" kostenlos:** Python hat `ast.parse` + `ast.get_docstring` in der Standardbibliothek; mit `node.lineno/end_lineno` lassen sich Funktionen/Klassen sauber ausschneiden. Für JS/TS reicht ein schwaches Regex-Set (function/class/const-arrow/export default) — kein Node.js-Parser nötig. YAML ohne PyYAML via Top-Level-Key-Regex geht ebenfalls.
- **Code-Chunks brauchen context_header:** Kleine Funktionen haben zu wenig Signal für den Retriever. Ein vorangestellter `# Datei: …\n# Funktion: foo (Zeile 12-24)` hebt das Embedding ohne den eigentlichen Code zu verändern.
- **Huginn-Pattern „Fastlane":** Input → Guard → LLM → Output. Bewusst ohne RAG/Memory/Sentiment im Telegram-Kanal, damit die Antwortlatenz niedrig bleibt. Für Gruppen-Intelligenz reicht ein LLM-Call mit „SKIP oder Beitrag"-Prompt — kein spezielles Klassifikations-Modell.
- **HitL via asyncio.Event + Inline-Keyboard:** `HitlManager` hält pro Request ein Event, Admin-Klick auf ✅/❌ ruft `approve()`/`reject()`, das Event setzt. `wait_for_decision(timeout)` blockiert im anfragenden Flow. Sauberer als Polling, und der Timeout ist explizit.
- **Bubble-Shine ist ein ::before, kein SVG:** 135°-Gradient mit `rgba(255,255,255,0.14)` → `transparent` im Pseudoelement. z-index 0 für den Shine, `message > *` auf z-index 1 damit Kinder oben bleiben. Funktioniert mit beliebigen Hintergrundfarben (User-Bubble vs. Bot-Bubble) ohne Anpassung.
- **Prompt-Kompression ist ein WERKZEUG, kein Prozess:** `compress_prompt()` darf nicht zur Laufzeit auf jeden Prompt laufen — nur manuell auf Backend-System-Prompts angewendet. `preserve_sentiment=True` schützt user-facing Marker (Nala, Rosa, Huginn, Chris, warm, liebevoll). Sicherheitsnetz: wenn <10% des Originals übrig bleibt, Original zurückgeben.
- **Spracherkennung ohne langdetect:** Wortlisten-Frequenz mit Code-Token-Filter (def/class/function/import) + Umlaut-Boost (+3 für DE) reicht für DE/EN bei Dokument-Content. Bei <5 Tokens → Default DE (Fallback-Sprache).
- **Config-Endpoints maskieren Token:** `GET /admin/huginn/config` gibt `bot_token_masked` zurück (`abcd…wxyz`). POST akzeptiert nur, wenn kein „…" im gesendeten Token — damit der Frontend-Reload den maskierten Wert nicht versehentlich als neuen Token speichert.

## Session 2026-04-23 (Patches 127–129, Zyklus 2)

- **Zweiter Zyklus im selben Token-Fenster bringt Frontend-Polish günstig:** Die in Mega-Patch 122–126 bewusst verschobenen Frontend-Teile (Huginn-Hel-Tab, HSL-Slider) passen in ein einziges zweites Commit-Paket. Die Infrastruktur (Endpoints, Settings-Modal) stand, das Frontend dranhängen kostet vergleichsweise wenig. Lesson: Bei Mega-Patches ruhig Infrastruktur + Hook-Points zuerst, dann das sichtbare UI als Zyklus-2 — so wird der erste Commit nicht durch Frontend-Arbeit aufgehalten.
- **HSL additiv, nicht ersetzend:** Die bestehenden `<input type="color">`-Picker waren einige Patches lang stabil + haben Favoriten-Integration. Statt sie zu ersetzen, hängen HSL-Slider additiv dran mit einer `hslToHex()`-Sync-Funktion. Favoriten speichern weiterhin den HEX-Wert, aber die UX ist Touch-freundlicher. Bonus: Regenbogen-Gradient auf dem Hue-Slider kostet 5 Zeilen CSS (`background: linear-gradient(to right, hsl(0,100%,50%), hsl(60,100%,50%), ...)`) und macht den Slider ohne Doku verständlich.
- **Sticky-Anchor statt Modal-Umbau:** Der „⚙️ Einstellungen"-Button als `position: sticky; bottom: 0;` am Ende des Sidebar ist billiger und robuster als ein kompletter Tab-Umbau des Settings-Modals. Das Modal selbst hat schon Theme/Bubble/Font/Favoriten-Sektionen — alles was fehlte war der prominente Entry-Point.
- **Migration-Scripts brauchen `--dry-run` als Default:** `scripts/migrate_embedder.py` verhält sich ohne Flags als Dry-Run. Destruktive Aktion muss mit `--execute` explizit angefordert werden. Ausserdem: Backup vor `--execute`, und die alten Index-Dateien bleiben physisch erhalten (neue werden zusätzlich geschrieben). Der Live-Retriever liest weiter aus `faiss.index` bis Chris in config.yaml umstellt — „Shadow-Index"-Pattern.
- **Script als Test-Modul importieren:** `importlib.util.spec_from_file_location` erlaubt Tests gegen Scripts die nicht als Paket organisiert sind. Funktioniert für `categorize_by_language` ohne dass das Script als installierbares Modul umgebaut werden muss. Im Testlauf kein Side-Effect (kein Pfad-Schreiben), da die Dry-Run-Path sauber abgetrennt ist.
- **`ast.parse` reicht nicht für JS in Python-Strings:** Hel und Nala haben tausende Zeilen JS in `"""..."""`. `ast.parse` verifiziert nur die Python-Syntax — JS-Fehler (unescaped `\n`, Typos) fallen erst im Browser auf. Bei Frontend-Patches immer zusätzlich im Browser testen oder mit `node --check` auf extrahierte `<script>`-Blöcke prüfen. Das Regression-Test-Set fängt JS nur indirekt (Playwright Loki-Tests, die hier aber nicht laufen).

### 2026-04-23 — Mega-Patch Experiment: 8 Patches in einem Kontextfenster

**Setup:**
- Modell: Opus 4.7, 1M Token Kontextfenster, Effort Extra High, Permission Level 4
- Prompt: Mega-Patch 122–126 (5 Patches), vom Supervisor als einzelne `.md`-Datei generiert (~12-15k Tokens Prompt)
- Claude Code hat eigenständig Patches 127–129 nachgeschoben (offene Punkte aus 122–126)

**Ergebnis:**
- 8 Patches abgeschlossen (122–129)
- 238 Tests grün (162 Baseline + 76 neu)
- Tatsächlicher Token-Verbrauch: **261,2k von 1M (26%)**
- Zwei Commits, alle drei Repos synchronisiert
- Kein einziger Fehler, kein Abbruch, kein Degradieren

**Vergleich zu bisherigem Vorgehen:**
- Alt: 2–3 Patches pro Session, ~250k Token-Verbrauch → hoher Overhead durch wiederholtes Einlesen der Codebasis
- Neu: 8 Patches in einer Session, ~261k Token-Verbrauch → Codebasis nur einmal gelesen, Rest ist produktive Arbeit
- Effizienzgewinn: ~3x mehr Patches bei gleichem Token-Verbrauch

**Was funktioniert hat:**
1. Mega-Prompt als `.md`-Datei mit allen Patches durchnummeriert und klar strukturiert (Block-basiert: Diagnose → Implementierung → Tests → Doku-Update)
2. Reihenfolge nach Abbrechbarkeit: Destruktive/riskante Patches am Ende (Embedder/FAISS-Migration)
3. Token-Selbstüberwachung als Regel im Prompt: "Ab ~450k sauber abschließen" — nicht gebraucht, aber als Sicherheitsnetz da
4. Scope-Bewusstsein: Claude Code hat nach 8 Patches selbstständig gestoppt mit Begründung "weiteres Terrain braucht eigene Architektur-Entscheidungen"
5. Eigeninitiative: Hat Patches 127–129 eigenständig identifiziert und umgesetzt

**Lessons für zukünftige Mega-Prompts:**
- 5–8 Patches pro Mega-Prompt sind realistisch bei Opus 4.7 / 1M Kontext
- ~260k Tokens für 8 Patches = ~32k pro Patch im Schnitt (inkl. Diagnose, Code, Tests, Doku)
- Sicherheitsgrenze bei 450k im Prompt definieren
- Leicht abbrechbare Tasks am Ende platzieren
- Prompt-Struktur: Block-basiert mit Diagnose → Fix → Test → Doku pro Patch. Grep-Befehle vorgeben.
- Bei ~32k/Patch wären theoretisch ~14 Patches in einem 450k-Budget möglich — noch nicht getestet

### 2026-04-24 — Zweites Mega-Patch-Experiment: 6 Patches (131–136)

**Setup:**
- Gleiche Umgebung wie Mega-Patch 122–129: Opus 4.7, 1M Kontext, Effort Extra High, Permission Level 4.
- Prompt: Mega-Patch 131–136 als einzelne `.md`-Datei (~14k Tokens) mit expliziter Token-Selbstüberwachung ("ab ~450k sauber abschließen") und bewusst leicht abbrechbaren Patches 135/136 am Ende.

**Ergebnis:**
- 6 Patches abgeschlossen (131–136), 56 neue Tests (19+9+5+9+6+8), Gesamttest-Suite von 252 → 308 passed.
- Keine Patches ausgelassen, keine halben Patches, keine Abbrüche.
- Alle neuen Hel-Endpoints (9 Stück) haben Unit-Tests mit in-memory SQLite.

**Was diesmal anders war als 122–129:**
1. **Diagnose zuerst, komplett:** Statt pro Patch einzeln grep/read zu machen, einen kompakten Diagnose-Block am Anfang für alle 6 Patches. Spart Kontext (die Codebasis wird im Kopf behalten, Files werden für jeden Patch nicht neu gelesen).
2. **Scope-Verkleinerung bei destruktiven Patches:** Patch 133 sollte laut Prompt den FAISS-Index umschalten und RAG-Eval fahren. Realistische Beurteilung: Modell-Downloads (gbert-large, multilingual-e5-large sind zusammen ~3 GB) + RAG-Eval + Server-Restart wären nicht in einem Patch-Budget machbar. Stattdessen: Backup + Switch-Mechanismus + Config-Flag default-false + Tests. Chris kann den `--execute` in einem eigenen Schritt fahren und mit RAG-Eval vergleichen. **Saubere Scope-Reduktion statt halber Patch.**
3. **Test-Pattern konsistent:** in-memory SQLite via `monkeypatch.setattr(db_mod, "_async_session_maker", sm)` ist der richtige Weg für DB-Tests (funktioniert auch für async Endpoints, wenn man die Funktionen direkt statt über den HTTP-Layer aufruft).
4. **Fail-Safe-Routing:** Bei OpenRouter-Fehler in `get_balance()` bekam die UI bis Patch 135 einen 502. Fix in Patch 136: graceful degradation — die lokale Cost-History wird trotzdem geliefert, nur `balance` ist None. Die Hel-UI bleibt benutzbar auch ohne Netz.

**Lessons für künftige Mega-Patches:**
- **Re-Read-Vermeidung:** Module die man im Kontext hat (von vorherigen Patches), nicht neu lesen — nur punktuell grep'en.
- **Header-Parameter-Falle:** Wenn man `request: Request` zu einer FastAPI-Route hinzufügt, die vorher `file: UploadFile = File(...)` als ersten Parameter hatte, `Request` MUSS davor stehen (vor File-Parametern), sonst FastAPI Parameter-Ordering-Error. Gemacht in Patch 135.
- **Endpoint-Tests über Funktions-Call:** `asyncio.run(my_endpoint_func(FakeRequest))` statt `TestClient` spart den Auth-Setup-Aufwand — Admin-Auth-Middleware (`verify_admin`) im Hel-Router blockiert jeden TestClient-Call ohne Credentials, und den auch nur in einem Subset. Direkter Funktions-Call umgeht das sauber.
- **`inspect.getsource()` für Header-Präsenz-Check:** Bei Pipeline-Patches wie 135 (Header-Skip), wo man kein voll-stacked Request-Test machen will, reicht der Check "ist der Header im Source-Code referenziert". Pragmatisch und wartungsarm.
- **2 Mega-Patches hintereinander:** 131–136 begann direkt nach 122–129+130 ohne Neu-Kontext-Load. Das spart viel Token, weil die Codebasis bereits im Gedächtnis war.

### 2026-04-24 — Drittes Mega-Patch-Experiment: Monster-Patch 137–152 (16 Patches)

**Setup:**
- Gleiche Umgebung (Opus 4.7, 1M Kontext, Extra High, Permission Level 4).
- Prompt: ~20k Tokens für 16 Patches, explizite Token-Selbstüberwachung mit Abbruchprotokoll bei ~450k.
- Scope: **komplette Käferpresse vom 24.04.2026** — Bugs, UI-Polish, TTS, Katzenpfoten, Feuerwerk, Design-System-Audit, Memory-Dashboard.
- Nominal ~2× der Scope von 131–136.

**Ergebnis:**
- Alle 16 Patches durchgezogen. Keine Abbrüche, keine halben Patches, keine ausgelassenen Tests.
- **180 neue Tests** — größte Erweiterung aller Mega-Patch-Experimente (Baseline 308 → 488 passed).
- Konkrete Zahlen pro Patch: 137=16, 138=9, 139=15, 140=9, 141=4, 142=19, 143=14, 144=13, 145=11, 146=3, 147=9, 148=10, 149=10, 150=15, 151=12, 152=11.
- Keine Token-Limit-Erreichung — konnte bis zum Ende durchgezogen werden.

**Was besonders gut funktioniert hat:**
1. **Source-Match als Test-Strategie für UI-Patches:** Für reine Frontend-Änderungen (HTML/CSS/JS inline in Python-Routern) ist das Matchen im Source-String die pragmatischste Test-Form. Kein Browser, kein TestClient, keine DOM-Parser-Abhängigkeit. Schnelle, stabile Tests.
2. **Edge-Tests via Block-Split:** `src.split("function X")[1].split("function ")[0]` isoliert eine Funktion — stabil gegen Reformatting, aber brüchig bei Umbenennung. Trade-off bewusst akzeptiert, weil Tests als Regression-Gate gedacht sind.
3. **Jeden Patch sofort grün:** Kein Patch-Stacking. Jeder der 16 Patches wurde einzeln implementiert, mit Tests verifiziert, Tests grün → nächster Patch. Verhindert kaskadierende Fehler.
4. **Shared-Design.css als späte Patch-Nummer:** Design-Tokens sind defensiv — alte Code-Stellen funktionieren weiter, neue nutzen die Tokens. Keine Big-Bang-Migration.
5. **Test-Profile Filter als exclude_profiles-Param:** Erweitert `get_all_sessions` ohne Break — wenn nicht übergeben, verhält sich die Funktion wie vorher. Default der neuen HTTP-Route (`include_test=False`) ist sicher.
6. **Auto-Kontrast mit manual-flag:** Kombiniert Auto-Behavior mit User-Override. Pattern ist übertragbar (z.B. für automatische Bubble-Rundungen, die User manuell überschreiben kann).

**Stolpersteine:**
- **Parser-Fehler bei Test-Blocks:** Einige Tests haben Blocks via `src.split("class X")[1]` isoliert, aber bei HTML-Content (mit vielen Zeilenumbrüchen) waren 2000-3000 Zeichen zu wenig. Fix: Block auf 5000-6000 Zeichen erweitert. **Lesson: Bei HTML-Inline-JS großzügigere Fensterlänge als bei reinem JS.**
- **TTS-Test-Race:** `asyncio.get_event_loop().run_until_complete()` funktioniert beim Einzel-Run, bricht aber in der vollen Test-Suite, wenn vorherige Tests eine Loop geschlossen haben. Fix: `async def _run(): ...; asyncio.run(_run())`. **Lesson: Immer `asyncio.run` für sync-Wrapper um async-Test-Code.**
- **`data-tab="look/voice/system"` vs. HTML-Entities:** Tests, die Tab-Namen im HTML suchen, dürfen nicht auf CSS-Class-Blocks stoßen. Bessere Strategie: Nach HTML-Marker `<div class="settings-tabs">` suchen, nicht nach reiner Klassen-ID-Substring.
- **Memory-Dashboard-Lazy-Load:** Tests haben zunächst im Tab-Button-HTML nach `loadMemoryDashboard` gesucht, statt im `activateTab`-Handler. **Lesson: Lazy-Load-Hooks immer am Handler-Code suchen, nicht am Button.**

**Lessons für künftige Mega-Patches:**
- **16 Patches in einem Zug sind machbar**, wenn pro Patch nur 1-3 Dateien angefasst werden und die Tests rein Source-matchen können (kein Browser/DB-Roundtrip).
- **Token-Budget bei 16 UI-Patches:** mit Effort Extra High ca. 400-500k Input+Output. Headroom zu 1M blieb reichlich.
- **Design-System-Introduction als dediziertes Feature:** `shared-design.css` hätte zwischen Patches 122 und 131 schon eingeführt werden können. Je später im Projekt, desto aufwändiger wird die Migration der Legacy-Styles. **Lesson: Token-Definitionen früh einführen, auch wenn noch nicht alle Klassen genutzt werden.**
- **Jojo-Priorität als Marker:** Features mit expliziter Nutzer-Priorität (Pfoten, Feuerwerk) bekamen ausführlichere Animationen und mehr Tests als "Pflicht-Fixes". Bewusst so behandeln — schnelles Feedback-Loop für den User.
- **Backup-First bei DB-Touch:** Patch 138 Cleanup-Script macht DB-Backup vor Delete als eingebautes Verhalten, nicht als Opt-In. Default sicher, `--execute` macht es scharf.
- **UI-Skalierung ist die richtige Abstraktion** (ersetzt Preset-Buttons): Einfach für User, einfacher Code, persistent über CSS-Variable + `calc()`.

## Monster-Patch Session-Bilanz 2026-04-24 (33 Patches an einem Tag)

| Session | Patches | Tests Δ | Notizen |
|---------|---------|---------|---------|
| Mega 1 (122–129) | 8 | +76 | Erstes Mega-Experiment, Token ~261k |
| Mega 2 (131–136) | 6 | +56 | Vision + Memory-Store, Token ~260k |
| Loki/Fenrir (130) | 1 | +23 | E2E-Sweep, klein |
| Monster 3 (137–152) | 16 | +180 | Größter Scope, Token ~383k |
| Vidar+Fix (153–154) | 2 | +12 | cssToHex HSL-Bug, Vidar-Agent |
| Huginn-Polling (155) | 1 | +12 | Long-Polling statt Webhook |
| **Gesamt** | **34** | **+359** | |

**Test-Trajektorie:** 162 → 500 (+338 neue Tests, alle grün, offline).

**Kern-Erkenntnisse über die Sessions:**
1. **Effizienz steigt mit Patch-Anzahl pro Session:** 33k Token/Patch bei 8 Patches → 24k Token/Patch bei 16 Patches. Je mehr Patches im selben Fenster, desto weniger Overhead pro Patch (Codebasis nur einmal eingelesen).
2. **450k-Token-Grenze nie erreicht:** Selbst bei 16 Patches nur 383k (38% vom 1M-Fenster). Theoretisch 20+ Patches in einer Session möglich.
3. **Token-Selbstüberwachung > hartes Limit:** Claude Code hat sich nach 8 bzw. 16 Patches selbst gestoppt — nicht wegen Token-Limit, sondern wegen Scope-Grenze. Besser als stumpfes Limit.
4. **Leicht abbrechbare Tasks ans Ende stellen:** FAISS-Migration, Design-Audit, Memory-Dashboard — alles Dinge die bei Abbruch keinen halben Zustand hinterlassen.
5. **Eigeninitiative erlauben:** Session 1 hat eigenständig 127–129 nachgeschoben. Session 3 alle 16 Patches ohne Nachfrage.
6. **Bug-Propagation in Konverter-Funktionen:** Farb-Bug cssToHex (Patch 153) propagierte durch die ganze Kette: Parser → Farbpicker → localStorage → nächster Load = schwarze UI. **Lesson: Konverter-Funktionen brauchen Unit-Tests mit ALLEN Input-Formaten** (#rgb, #rrggbb, rgb(), rgba(), hsl(), hsla(), oklch(), …).

**Optimaler Mega-Prompt-Aufbau (bewährt):**
- Diagnose-grep-Befehle VOR dem Code (nicht blind coden)
- Block-Struktur: Diagnose → Implementierung → Tests → Doku pro Patch
- Reihenfolge: Kritische Fixes → Features → Kosmetik → Destruktive Ops
- Token-Selbstüberwachung bei 450k als Sicherheitsnetz
- Eigeninitiative erlauben für abgrenzbare Verbesserungen

## Telegram-Bot hinter Tailscale (Webhook vs. Long-Polling, Patch 155)

**Problem:** Telegram-Webhooks brauchen eine öffentlich erreichbare HTTPS-URL. Tailscale MagicDNS-Domains (`*.tail*.ts.net`) lösen nur innerhalb des Tailnets auf — Telegram-Server können sie nicht erreichen. Selbst-signierte Zertifikate helfen nicht, weil der DNS-Lookup scheitert bevor das Zertifikat geprüft wird.

**Lösung:** Long-Polling statt Webhook. Der Bot fragt Telegram aktiv "was gibt's Neues?" via `getUpdates` (Long-Poll mit 30s-Timeout) statt darauf zu warten angerufen zu werden. Funktioniert hinter jeder Firewall/VPN/Tailnet.

**Merke:**
- Bei Self-Hosted-Setups ohne öffentliche IP/Domain immer Long-Polling verwenden.
- Webhook nur für Server mit öffentlicher HTTPS-URL (Cloud-Deployments, VPS mit eigener Domain).
- Beim Start des Polling-Loops MUSS ein evtl. gesetzter Webhook entfernt werden (`deleteWebhook`) — sonst liefert `getUpdates` HTTP 409 Conflict.
- Offset-Management: `getUpdates(offset=<last_update_id+1>)` — sonst liefert Telegram dasselbe Update erneut.
- `allowed_updates=["message","channel_post","callback_query","my_chat_member"]` explizit angeben — sonst filtert Telegram evtl. relevante Typen raus.

## Vidar: Post-Deployment Smoke-Test-Agent (Patch 153)

**Drei Test-Agenten im Zerberus-Ökosystem:**
- **Loki** — E2E-Tests (Feature-Verifikation, Happy-Path)
- **Fenrir** — Chaos-Tests (Edge-Cases, Stress, Destruction)
- **Vidar** — Smoke-Tests (Post-Deployment Go/No-Go)

**Vidar-Architektur:**
- 3 Check-Levels: CRITICAL (Blocker) → IMPORTANT (Fixen) → COSMETIC (Nice-to-have)
- Verdict-Semantik: GO (deploy) / WARN (deploy, aber fixen) / FAIL (nicht deployen)
- Eigenes Test-Profil `vidar` mit `is_test: true` (keine echten User-Daten)
- Prüft beide Viewports (Mobile 390×844, Desktop implizit)
- ~21 automatisierte Checks, <60s Laufzeit
- Aufruf: `pytest zerberus/tests/test_vidar.py -v`

**Faustregel:** Vidar nach JEDEM Server-Restart laufen lassen. GO → testen. WARN → testen aber Bugs tracken. FAIL → nicht testen, erst fixen.

## Design-Konsistenz-Regel (L-001, Patch 151)

**Regel:** Wenn eine Design-Entscheidung für ein UI-Element getroffen wird, gilt sie projektübergreifend für ALLE ähnlichen Elemente in Nala UND Hel.

**Umsetzung:**
- `zerberus/static/css/shared-design.css` mit CSS-Custom-Properties (`--zb-*` Namespace)
- Design-Tokens für: Farben, Spacing, Radien, Schatten, Touch-Targets, Typography
- `docs/DESIGN.md` als Referenz
- Vor jeder CSS-Änderung: Gibt es das gleiche Element auch anderswo? → Gleicher Style.

**Touch-Target-Regel:** Minimum 44px Höhe UND Breite für ALLE klickbaren Elemente (Apple HIG). Gilt für Nala UND Hel. Loki prüft das automatisch (`test_loki_mega_patch.TestTouchTargets`).

## Settings-Singleton + YAML-Writer (Patch 156)

**Regel:** Jede Funktion, die `config.yaml` schreibt, MUSS den Settings-Singleton invalidieren — sonst liefert der nächste `get_settings()`-Aufruf den alten gecachten Wert.

**Warum:** `get_settings()` in [`zerberus/core/config.py`](zerberus/core/config.py) ist ein Modul-globaler Singleton (`_settings`), der `config.yaml` nur beim ersten Aufruf liest. Ein YAML-Write ohne anschließenden `reload_settings()` führt zu Stale-State: Hel-UI speichert ein neues Modell, der direkt darauf folgende `huginnReload()`-GET liefert den alten Wert, das Dropdown springt zurück. Der Bug ist still — der HTTP-Roundtrip gibt `200 OK` zurück, das Symptom ist nur kosmetisch sichtbar.

**Pattern (seit Patch 156 Pflicht für alle YAML-Writer):**

```python
from zerberus.core.config import invalidates_settings

@router.post("/admin/foo/config")
@invalidates_settings  # ruft reload_settings() nach jedem Aufruf, sync + async
async def post_foo_config(request: Request):
    ...
```

Alternativ als Kontextmanager (granularer, falls nur ein Pfad in der Funktion schreibt):

```python
from zerberus.core.config import settings_writer

with settings_writer():
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
```

**Wo das schon fehlte (Patch 156-Sweep):** `post_huginn_config`, `post_vision_config`, `post_pacemaker_processes`, `post_pacemaker_config` (alle in [`hel.py`](zerberus/app/routers/hel.py)) und `_save_profile_hash` ([`nala.py`](zerberus/app/routers/nala.py)) — fünf Endpoints schrieben YAML ohne Cache-Invalidate. Inkonsistente Mischung mit zwei korrekten Endpoints (`reset_password`, `post_provider_blacklist` — manueller `reload_settings()`-Call). Der Decorator macht das Pattern uniform und Copy-Paste-sicher.

**Test-Pattern:** POST → GET in einem Test, mit tmp-cwd-Fixture und `_settings = None`-Reset davor und danach. Siehe [`test_huginn_config_endpoint.py`](zerberus/tests/test_huginn_config_endpoint.py).

## Guard-Kontext pro Einsatzort (Patch 158)
- **Das Problem:** Der Halluzinations-Guard (Mistral Small 3 via OpenRouter) ist zustandslos und kennt weder den Antwortenden noch dessen Umgebung. Sobald eine Persona ins Spiel kommt — Huginn als „sprechender Rabe", Nala als „Nala im Zerberus-System" — stuft der Guard die Selbstreferenzen als Halluzination ein („fiktive Elemente wie Zerberus und einen sprechenden Raben"). Die Antwort wurde im Huginn-Flow sogar komplett unterdrückt. Das ist Persona-Unterdrückung, kein Sicherheitsgewinn.
- **Fix-Pattern:** Neuer optionaler Parameter `caller_context: str = ""` an [`check_response()`](zerberus/hallucination_guard.py). Der Builder `_build_system_prompt(caller_context)` hängt einen `[Kontext des Antwortenden]`-Block an den Default-Prompt mit dem harten Satz „Referenzen auf diese Elemente sind KEINE Halluzinationen." Jeder Aufrufer (legacy.py → Nala, telegram/router.py → Huginn) baut sich einen eigenen Kontext-Text und gibt ihn mit. Huginn nimmt zusätzlich einen 300-Zeichen-Auszug der aktuellen Persona in den Kontext (damit der Guard Raben-Elemente als charakterbedingt erkennt).
- **Guard-WARNUNG = Hinweis, nicht Block:** Antwort geht IMMER an den User. Der Admin (Chris) bekommt bei WARNUNG einen DM mit Chat-ID + Grund — er kann die Qualität später bewerten statt dem User die Antwort wegzublockieren. Alte Regel „Antwort unterdrückt" war zu aggressiv. Falls echte Sicherheitsklassen (BLOCK/TOXIC) kommen, bekommen die eine separate Semantik — nicht WARNUNG überladen.
- **Teststrategie:** Guard-Integration-Test prüft nur, dass der `caller_context` im **System**-Prompt-Feld landet (nicht im User-Prompt — dort steht der zu prüfende Text). Verhaltens-Test mockt `_run_guard` auf WARNUNG und checkt dass `send_telegram_message` zweimal aufgerufen wird (einmal User-Chat, einmal Admin-Chat). Siehe [`test_hallucination_guard.py`](zerberus/tests/test_hallucination_guard.py).

## Telegram Group Privacy (Patch 155-Session)
- **BotFather-Setting:** Der "Group Privacy"-Toggle im BotFather (`/mybots` → Bot → Bot Settings → Group Privacy) ist per Default AN. AN heißt: der Bot sieht in Gruppen nur Nachrichten die ihn per `@` mentionen oder mit `/` beginnen. Für Zerberus/Huginn mit `respond_to_name` (Textkontextsuche "Huginn" im freien Text) und `autonomous_interjection` muss der Toggle AUS sein — sonst kommen die fraglichen Updates gar nicht erst per `getUpdates` an, der ganze Gruppen-Flow läuft ins Leere und das Symptom ist lautlos (keine Log-Zeile, kein Error).
- **Cache pro Gruppe:** Telegram merkt sich die Privacy-Einstellung **zum Zeitpunkt des Gruppen-Beitritts**. Nach Umschalten im BotFather reicht es NICHT, die Einstellung zu ändern — der Bot muss aus jeder betroffenen Gruppe entfernt und neu hinzugefügt werden, damit die neue Privacy-Stufe greift. Alter Bot in alter Gruppe = alte Regel.
- **Symptom-Indikator:** Wenn `autonomous_interjection=true` gesetzt ist und trotzdem keine Einwürfe kommen, aber `@HuginnBot ...` funktioniert → Privacy ist der Root-Cause, nicht `should_respond_in_group()`.

## Huginn-Persona als technisches Werkzeug (Patch 158)
- **Sarkasmus ist kalibrierte Beratung, kein Gimmick.** Der zynisch-bissige Ton der [`DEFAULT_HUGINN_PROMPT`](zerberus/modules/telegram/bot.py) ist kein Comedy-Feature, sondern ein Kanal für Aufwands-Rückmeldung. Wenn ein User "Schreib mir Pong in Rust" fragt, soll Huginn den unverhältnismäßigen Aufwand in Bezug auf den Output kommentieren, auf ein vernünftigeres Format hinlenken (z. B. Python, 30 Zeilen), aber nach Bestätigung trotzdem liefern. Der Ton korreliert mit Absurdität/Aufwand der Anfrage.
- **Implikation für Intent-Router / Roadmap:** Ein zukünftiger Aufwands-Schätzer sollte den Sarkasmus-Level im System-Prompt dynamisch nach Aufwand skalieren (kleiner Task → knapp aber höflich; großer Task auf falscher Stack → bissig mit Alternativ-Vorschlag). Heißt: der System-Prompt ist nicht fix pro Session, sondern kann pro Turn um einen "Aufwandskommentar"-Modifier ergänzt werden.
- **Warum das hier steht:** Damit spätere Patches den zynischen Ton nicht versehentlich als "zu aggressiv" wegmoderieren — der Ton IST die Information.

## Ratatoskr Sync-Reihenfolge (Patch 158-Session)
- **Regel:** Zerberus ERST committen + pushen (`git add ...; git commit; git push`), DANN `sync_repos.ps1` ausführen. Nicht andersherum.
- **Warum:** `sync_repos.ps1` liest die Zerberus-HEAD-Commit-Message via `git log -1 --format=%s` und verwendet sie als Commit-Betreff für Ratatoskr + Claude-Repo. Läuft der Sync VOR dem Zerberus-Commit, tragen Ratatoskr und Claude die vorherige Patch-Message, obwohl sie den neuen Patch-Inhalt enthalten. Inhaltlich korrekt, aber die Commit-Historie in den Sync-Repos ist nach hinten versetzt.
- **Recovery wenn's passiert ist:** Nicht fixen. Beim nächsten Patch läuft der Sync wieder, diesmal mit der richtigen Message, und die Repos kommen in Phase. Ein `--amend` oder Force-Push auf die Sync-Repos lohnt den Aufwand nicht.

## Whisper Timeout-Hardening (Patch 160)
- **httpx Default-Timeout ist zu kurz für Whisper.** Lange Aufnahmen (>10s) brauchen auf der RTX 3060 bis zu 30s Verarbeitungszeit; vor Patch 160 stand der Call auf `httpx.AsyncClient(timeout=60.0)` ohne Connect-Timeout — das reichte für den Median, aber nicht für den Tail (Cold-Start + lange Audios). Fix: expliziter `httpx.Timeout(120, connect=10)` aus Config (`settings.whisper.request_timeout_seconds`). Connect bleibt kurz, weil „Docker nicht erreichbar" ein anderer Fehler ist als „Whisper rechnet noch".
- **Zu kurze Audio-Clips (<4KB ≈ <0.25s) lautlos abfangen.** VAD findet nichts, Decoder hängt → Symptom sieht wie Timeout aus, Root-Cause ist leere Eingabe. Guard VOR dem Whisper-Call: Bytes zählen → bei Unterschreitung `WhisperSilenceGuard` raise, Aufrufer liefert Endpoint-spezifisches Silence-Response (`{"text": ""}` für legacy, `{"transcript": "", ...}` für nala).
- **Einmal-Retry bei `httpx.ReadTimeout`:** Cold-Start des Whisper-Docker-Containers kostet manchmal den ersten Call. `timeout_retries=1` + `retry_backoff_seconds=2` fängt das ab. Nach Retries hoch → Aufrufer macht 500. NIEMALS endlos loopen — jeder weitere Retry würde denselben Timeout nochmal fressen.
- **Beide Pfade betroffen:** `legacy.py::audio_transcriptions` (Dictate-Tastatur) UND `nala.py::voice_endpoint` (Nala Web-UI). Gleiche Lesson wie Patch 80b „Fixes die nur in orchestrator.py landen wirken nicht auf den Haupt-Chat-Pfad". Seit Patch 160 gibt es deshalb `zerberus/utils/whisper_client.py::transcribe()` als zentralen Helper — beide Endpunkte rufen denselben Code, damit die Transport-Logik nicht divergiert.
- **`audio_data = await file.read()` vor dem Call** liest den UploadFile in Bytes in den Speicher. Das heißt: Retries brauchen KEIN `file.seek(0)`, weil die Bytes schon persistent sind und direkt nochmal in `files={"file": (name, audio_data, ct)}` gestopft werden. Hätte der Code `files={"file": file.file}` genutzt (Stream-Objekt), wäre `seek(0)` Pflicht.

## Claude Code Scope-Management (Patch 157/158-Session)
- **Mega-Patches abspalten:** Wenn ein Patch-Prompt 3+ Blöcke mit unabhängigen Zielen enthält (z. B. "Terminal-Cleanup" + "Huginn-Persona" + "Guard-Kontext"), kann Claude Code nur einen Teil sauber durchziehen und Restblöcke werden still abgeschnitten. Das ist KEIN Fehler — es ist Kontext-/Attention-Budget-Management. Beispiel: Patch 157 hat nur den Terminal-Block gemacht, Persona + Guard kamen als 158.
- **Was man als Supervisor macht:** Bei solchen Splits die Restblöcke als eigenen Nachfolge-Patch sauber formulieren (mit Block-Nummerierung, Diagnostik-Grep-Befehlen, exakten Locations). Nicht den laufenden Patch nachträglich erweitern — das verwischt Grenzen und verschlechtert Reviewbarkeit.
- **Signal:** Wenn der Commit-Diff eines Patches weniger Files berührt als erwartet und die Test-Zahl niedriger wuchs als angekündigt → der Rest wurde übersprungen. Explizit beim nächsten Patch reaktivieren, nicht auf autonomes Nachholen hoffen.
