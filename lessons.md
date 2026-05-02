# lessons.md – Zerberus Pro 4.0 (projektspezifisch)
*Gelernte Lektionen. Nach jeder Korrektur ergänzen.*

Universelle Erkenntnisse: https://github.com/Bmad82/Claude/lessons/

## Konfiguration
- config.yaml = Single Source of Truth|config.json NIE als Konfig-Quelle (Split-Brain P34)
- Module mit `enabled: false` nicht anfassen, auch nicht „kurz testen"
- Pacemaker-Änderungen wirken erst nach Neustart (kein Live-Reload)
- Hel-Admin-UI `/hel/admin/config` arbeitet seit P105 auf config.yaml als SSoT|`_yaml_replace_scalar` (hel.py) = line-basierte In-Place-Ersetzung (erhält Kommentare, yaml.safe_dump würde alles neu serialisieren)|config.json darf NICHT mehr authoritative sein|Debug-Endpunkte dürfen anzeigen, aber nichts liest/schreibt von dort

## Datenbank
- bunker_memory.db nie manuell editieren/löschen
- interactions-Tabelle hat KEINE User-Spalte|User-Trennung nur per Session-ID (unzuverlässig, vor Metrik-Auswertung klären)
- Schema-Check vor Dedup-Query: `interactions` hat `content`+`role`, NICHT `user_message`|`PRAGMA table_info(interactions)` laufen lassen (P113a)
- Composite-UNIQUE-Constraints (z.B. `UNIQUE(project_id, relative_path)`) MÜSSEN als `__table_args__ = (UniqueConstraint(...),)` im SQLAlchemy-Model stehen (P194)|Nur `CREATE UNIQUE INDEX IF NOT EXISTS …` in `init_db` reicht NICHT, weil Test-Fixtures `Base.metadata.create_all` direkt gegen die Models laufen lassen (ohne `init_db`)|Symptom: Repo-Test fügt Duplikat ein → erwartete IntegrityError fliegt nicht|Faustregel: Constraint im Model = Single Source of Truth, init_db nur für DDL die `metadata.create_all` nicht ableiten kann (PRAGMA, ALTER, Migrations-Backfills)

## RAG
- Orchestrator Auto-Indexing aus|erzeugt sonst „source: orchestrator"-Chunks nach manuellem Clear (P68)|verdrängt echte Dokument-Chunks|nach jedem Clear prüfen
- RAG-Upload Mobile: MIME-Check schlägt fehl → nur Dateiendung prüfen (P61)
- chunk_size=800 Wörter|overlap=160 Wörter|Einheit Wörter NICHT Token (P66)
- Code-Pfad prüfen: P78b implementierte RAG-Skip nur in orchestrator.py|Nala-Frontend nutzt legacy.py → Fix griff nicht|IMMER prüfen welcher Router den aktiven Traffic führt (P80b)
- RAG-Skip-Logik MUSS AND-verknüpft sein: nur skippen wenn CONVERSATION UND kurz UND kein ?|OR ist zu aggressiv (P85)
- Residual-Tail-Chunks (<50 Wörter) aus 800/160-Chunking kapern Rang 1 bei normalisierten MiniLM-Embeddings|bei Retrieval-Problemen zuerst Chunk-Längen-Verteilung prüfen (P87)
- `paraphrase-multilingual-MiniLM-L12-v2` findet deutsche Eigennamen/Redewendungen schwach|Cross-Encoder-Reranker oder stärkeres Modell nötig (P87)
- `rag_eval.py` hardcoded auf `http://127.0.0.1:5000`|HTTPS seit P82|Script anpassen oder Eval inline mit SSL-Skip (P87)
- Min-Chunk (P88, `min_chunk_words=120`) in Chunking UND Retrieval|Chunking-Merge=saubere Quelldaten|Retrieval-Filter=Sicherheitsnetz für Alt-Indizes|Reihenfolge: Chunking säubern → Reranker → Modell-Wechsel
- `_search_index()` in `rag/router.py` (NICHT `_rag_search`) = FAISS-Search|Im Orchestrator als `rag_search` importiert + in `_rag_search()` gewrappt|immer tatsächlichen Funktionsnamen prüfen
- Cross-Encoder-Reranker als 2. Stufe: **BAAI/bge-reranker-v2-m3** (Apache 2.0)|FAISS over-fetched `top_k * rerank_multiplier`|Cross-Encoder scored neu|Bei aktivem Rerank L2-Threshold-Filter überspringen|Fail-Safe-Fallback auf FAISS-Order bei Exception (P89)
- Eval-Delta P89: 4/11 JA → 10/11 JA|Q4 (Perseiden) + Q10 (Ulm Sonntag) geheilt
- Reranker-Min-Score (`modules.rag.rerank_min_score`, Default 0.05) filtert nach Cross-Encoder|Top-Score < Threshold → KEIN Chunk relevant → RAG-Kontext verworfen (z.B. Übersetzungs-Requests)|`_rag_search()`, automatisch für legacy+orchestrator|Logging `[THRESHOLD-105]` (P105)
- Pro-Category-Profile statt globale Defaults: `CHUNK_CONFIGS` in [hel.py](zerberus/app/routers/hel.py) mapped Category → `{chunk_size, overlap, min_chunk_words, split}`|narrative/lore: 800/160/120 (mit bge-reranker-v2-m3 validiert, 10/11)|kleinere Chunks (300/60 für reference) brauchen kleinere `min_chunk_words` (50)|sonst mergt Residual-Pass alles (P110)
- Split-Strategien: `chapter` (Prolog/Akt/Epilog/Glossar, `_CHAPTER_RE`)|`markdown` (`# … ######`, `_MD_HEADER_RE`, technical)|`none` (nur Wortfenster, reference)
- Rückwärtskompat: `modules.rag.min_chunk_words` (P88) überschreibt Category-Default
- JSON+CSV als Klartext indizieren: `.json` → `json.dumps(data, indent=2, ensure_ascii=False)`|`.csv` → Header-Zeile + pro Daten-Zeile `"Header1: Wert1; Header2: Wert2"`|nur stdlib
- FAISS kann nicht selektiv löschen|`IndexFlatL2` hat kein `remove(idx)`|Soft-Delete (Metadata-Flag `deleted: true` + Filter beim Retrieval/Status/Reindex) ist O(1) statt O(N)|Index wächst bis Reindex|3 Filter-Stellen MÜSSEN konsistent: `_search_index()` (Retrieval)+`/admin/rag/status` (Listing)+`/admin/rag/reindex` (Rebuild)|Grep `m.get("deleted") is True` in allen 3 Pfaden (P116)
- AST-Chunker mit Signatur-Kontext: Python via `ast.parse`+`ast.get_docstring`+`node.lineno/end_lineno`|JS/TS via Regex (function/class/const-arrow/export default)|YAML via Top-Level-Key-Regex (kein PyYAML nötig) (P122-126)
- Code-Chunks brauchen `context_header`: `# Datei: …\n# Funktion: foo (Zeile 12-24)`|hebt Embedding ohne Code zu ändern
- **Category-Filter als Datenschutz-Schicht (P178)**|Wenn ein gemeinsamer RAG-Index mehrere Vertraulichkeits-Stufen mischt (privat/öffentlich), kann der Caller den Filter NICHT dem Index überlassen|`_search_index` liefert ALLE Chunks zurück, auch wenn der Reranker sie hochrankt|Der Caller (z.B. `_huginn_rag_lookup` für den Telegram-Bot) muss NACH dem Search-Call eine harte Whitelist auf `metadata.category` anwenden|Konsequenz: Über-Fetch-Faktor (top_k × 4) damit der Filter genug Kandidaten zum Auswählen hat|Test: Sentinel-Strings (z.B. `BLAUE_FLEDERMAUS_4711`) in personal-Doku platzieren, Query gezielt darauf richten, prüfen dass Sentinel NICHT im LLM-Prompt landet|Default-Whitelist im Code, NICHT in config.yaml (gitignored, P102-Lesson) — sonst greift der Schutz nach `git clone` nicht
- Neue RAG-Kategorie hinzufügen|MUSS in BEIDEN: `_RAG_CATEGORIES` (sonst fällt Upload auf "general" zurück) UND `CHUNK_CONFIGS` (sonst nimmt `_chunk_text` `general`-Defaults)|Beide sind in [`hel.py`](zerberus/app/routers/hel.py)|Test: `curl -F category=neuename` → Response `"category": "neuename"` (NICHT "general")

## Security / Auth
- JWT blockiert externe Clients (Dictate, SillyTavern)|`static_api_key` in config.yaml als Workaround (P59/64)
- .env nie loggen/committen
- Rosa Security Layer: Dateien sind Vorbereitung|Verschlüsselung NICHT aktiv
- Profil-Key-Umbenennung (z.B. user2→jojo): `password_hash` explizit prüfen+loggen|fehlende Hashes = lautlose Login-Fehler (P83)
- **/v1/-Endpoints MÜSSEN auth-frei bleiben**|Dictate-App (Android-Tastatur) kann keine Custom-Headers (X-API-Key, JWT)|JEDER Auth-Patch: `/v1/chat/completions`+`/v1/audio/transcriptions` ohne Auth testen|Bypass: `_JWT_EXCLUDED_PREFIXES` (Prefix `/v1/`) in `core/middleware.py`|Dauerbrenner-Regression (Hotfix 103a)

## Deployment
- start.bat braucht `call venv\Scripts\activate` vor uvicorn
- spaCy einmalig: `python -m spacy download de_core_news_sm`
- `%~dp0` = Script-Directory|ersetzt hardcodierte Pfade in .bat|in Anführungszeichen bei Leerzeichen: `cd /d "%~dp0"` (P117)
- Python-Code bereits portabel (`Path("config.yaml")`, `Path("./data/vectors")`)|Doku-Markdowns mit absoluten Pfaden = Beispiele für Chris, keine Code-Refs

## Pacemaker
- Double-Start: `update_interaction()` muss async sein|alle Call-Sites brauchen await (P59)
- Lock-Guard prüfen ob Task läuft bevor neuer startet

## Architektur / Bekannte Schulden
- interactions-Tabelle ohne User-Spalte → Per-User-Metriken erst nach Alembic-Fix vertrauenswürdig
- Rosa Security Layer NICHT aktiv (nur Dateivorbereitung)
- JWT + static_api_key: externe Clients brauchen X-API-Key
- RAG Lazy-Load-Guard in `_encode()`: `_model` war None bei erstem Upload|immer auf None prüfen vor Modell-Nutzung

## Pipeline / Routing
- Nala-Frontend routet über `/v1/chat/completions` (legacy.py), NICHT Orchestrator|Fixes nur in orchestrator.py wirken nicht auf Haupt-Chat
- Pipeline-Fix: alle 3 Pfade prüfen — legacy.py, orchestrator.py, nala.py /voice
- Änderungen in legacy.py betreffen externe Clients (Dictate, SillyTavern)|prüfen ob `/v1/audio/transcriptions`+Static-API-Key-Bypass intakt (P82)
- Dictate-Tastatur schickt bereits transkribierte+bereinigte Texte|legacy.py wendet Whisper-Cleaner erneut an (idempotent, harmlos)|bei nicht-idempotenten Regeln Skip-Flag/`X-Already-Cleaned: true` nötig|Backlog (P107)
- TRANSFORM-Intents (Übersetzen/Lektorieren/Zusammenfassen) skippen RAG komplett|User liefert Text mit|RAG-Treffer = Lärm|Cross-Encoder-Rerank kostet ~47s CPU für nichts|Pattern-Match NUR am Nachrichtenanfang (sonst kapert „übersetze" mitten im Text jede Frage)|`_TRANSFORM_PATTERNS` in orchestrator.py (P106)

## Dialekt-Weiche (P103)
- Marker-Länge ×5 (Teclado/Dictate-Pattern)|×2-Variante in `core/dialect.py` produzierte 400/500 (Offset-Bug)|IMMER ×5
- Wortgrenzen-Matching Pflicht: `apply_dialect` muss `re.sub(r'(?<!\w)KEY(?!\w)', ...)` nutzen, NICHT `str.replace()`|sonst matcht `ich` in `nich` → `nick`|case-sensitive bleibt|Multi-Wort-Keys (`haben wir`) funktionieren via Length-sortiertem Durchlauf

## Frontend / JS in Python-Strings
- `'\n'` in Python-HTML-Strings wird als Newline gerendert + bricht JS-String-Literale (`SyntaxError: unterminated string literal`)|innerhalb `ADMIN_HTML="""..."""`/`NALA_HTML="""..."""` immer `'\\n'`/`\\r`/`\\t`|betrifft jeden Python-String mit JS|Erstmals P69c (`exportChat`), erneut P100 (`showMetricInfo`)
- JS-Syntax mit `node --check` verifizieren: HTML aus Router rendern|`<script>`-Blöcke extrahieren|einzeln durch `node --check`|`TestJavaScriptIntegrity` (P100) fängt im Browser, `node --check` = Pre-Commit
- Playwright ohne `pageerror`-Listener fängt JS-Parse-Errors NICHT|`page.on("pageerror", ...)` MUSS VOR `page.goto()` registriert werden|sonst initiale Script-Errors verschluckt
- Python `\u{…}` in HTML-Strings bricht Parse: ES6+ JS-Syntax, Python erwartet 4 Hex-Chars → `SyntaxError: truncated \uXXXX escape`|Emoji direkt einfügen oder `\\u{1F50D}` doppel-escape (analog `\n` in JS-Strings)
- Python-String-Falle bei JS-Regex-Charakterklassen: `[^\n\r\[]+` wird zu echten Newlines → `Invalid regular expression`|entweder `[^\\n\\r\\[]+` (Python-Doppel-Escape) oder einfacher `(.+)` (P118a)

## SSE / Streaming Resilience (P109)
- Frontend-Timeout ≠ Backend-Timeout: 45s-Frontend-Timeout bricht `fetch()` ab|Backend arbeitet weiter + speichert Antwort via `store_interaction()`|Naiver Retry = doppelte LLM-Calls (Kosten + Session-Historie verwirrt)
- Fix-Pattern: Retry-Button prüft erst per `/archive/session/{id}` ob DB Antwort enthält|nur sonst echter Retry|`fetchLateAnswer(sid, userText)` + `retryOrRecover(retryText, retrySid, cleanupFn)` in [nala.py](zerberus/app/routers/nala.py)
- Signatur: Retry-Handler (`setTypingState`, `showErrorBubble`) müssen `reqSessionId` als 3. Param durchreichen|nicht `sessionId` zur Click-Zeit (User kann gewechselt haben)
- Heartbeat statt fixem Timeout (P114a): Server sendet alle 5s `event: heartbeat\ndata: processing\n\n`|Frontend `addEventListener('heartbeat', …)` setzt 15s-Watchdog zurück|Hard-Stop bei 120s|CPU-Fallback bleibt funktionsfähig, GPU bekommt straffe 15s
- `retry: 5000` am Stream-Anfang ändert EventSource-Reconnect-Interval dauerhaft (SSE-Spec)|5s = zahm gegen Mobile-Reconnect-Stürme
- `window.__nalaSseWatchdogReset` als loses Coupling: SSE-Listener-Binding lebt sessionübergreifend, Watchdog gehört zu fetch-Transaktion|Global-Funktion-Pointer (null bei kein Request) erlaubt Cross-Wire ohne harte Abhängigkeit

## Theme-Defaults (P109)
- Anti-Invariante „nie schwarz auf schwarz": Bubble-Background-Defaults in `:root` müssen lesbare Werte haben, auch ohne Theme/Favorit|`rgba(…, 0.85-0.88)` mit Theme-Hex als Basis|User-Bubble `rgba(236, 64, 122, 0.88)` + LLM-Bubble `rgba(26, 47, 78, 0.85)` = Chris's Purple/Gold
- Reset muss vollständig sein: `resetTheme()` darf nicht nur 5 Theme-Farben|sonst bleiben alte Bubble-Overrides in localStorage aktiv|Lösung: `resetTheme()` ruft `resetAllBubbles()`+`resetFontSize()` mit auf

## RAG GPU-Acceleration (P111)
- torch-Variante checken: `pip list | grep torch` zeigt `+cpu` wenn CUDA fehlt|RTX 3060 ungenutzt bis `pip install torch --index-url https://download.pytorch.org/whl/cu121`|Device-Helper aus P111 fällt defensiv auf CPU zurück (keine Regression, aber kein Speed-Up)
- VRAM-Threshold 2GB: MiniLM ~0.5GB + bge-reranker-v2-m3 ~1GB + Puffer|Whisper ~4GB, BERT-Sentiment ~0.5GB → 12GB-Karte hat 7GB frei|Check verhindert nur OOM bei parallelem VRAM-Bedarf
- CrossEncoder nimmt `device` direkt: `sentence-transformers >= 2.2`|`CrossEncoder(model, device=...)`|keine `.to(device)`-Verschiebung nötig|verifiziert via `inspect.signature(CrossEncoder.__init__)`
- `_cuda_state()` isolieren: mockbar ohne torch-Dependency|`monkeypatch.setattr(dev_mod, "_cuda_state", lambda: (True, 8.0, 12.0, "RTX 3060"))`|9 Unit-Tests ohne GPU
- pip default = CPU-only (P111b): `pip install torch` zieht `2.x.y+cpu`|GPU braucht CUDA-Extra-Index: `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 --force-reinstall`|CUDA-Version per `nvidia-smi` (Driver 591.44 = CUDA 13.1 akzeptiert cu11x/cu12x)|RTX 3060 stabil mit cu124 + torch 2.5.1
- typing-extensions Ripple: `torch 2.5.1+cu124` downgradet `typing_extensions` auf 4.9.0|`cryptography>=46` braucht 4.13.2+|nach jedem torch-Reinstall: `pip install --upgrade "typing-extensions>=4.13.2"`|Symptom: `ImportError: cannot import name 'TypeIs' from 'typing_extensions'`
- `+cu124`-Suffix als Beweis: `pip list | grep torch` muss `torch 2.5.1+cu124` zeigen|ohne Suffix = CPU-only|`torch.cuda.is_available() == True` UND `torch.cuda.get_device_name(0)` = echter GPU-Status

## Query-Router / Category-Boost (P111)
- Wortgrenzen-Matching IMMER: naives Substring-`in` findet `api` in `Kapitel` → false positive|`re.search(r'(?<!\w)kw(?!\w)', text.lower())` Pflicht|Multi-Wort-Keys (`"ich habe"`) fallen auf `in` zurück (Leerzeichen=Wortgrenze)|analog P103
- Boost statt Filter: hartes Drop = Retrieval brüchig (Heuristik-Fehlklassifikation = leeres Ergebnis)|Score-Bonus (Default 0.1) auf `rerank_score` (P89) bzw. `score`-Fallback verschiebt nur Reihenfolge|Flag `category_boosted: True` pro Chunk
- Keyword-Listen kurz halten: lieber false negatives als false positives|5-15 Keywords/Category|LLM-basierte Detection in Phase 4

## Auto-Category-Detection (P111)
- Extension-Map statt Content-Analyse: `Path(filename).suffix.lower()` → dict|Content-LLM-Call in Phase 4|`.json`/`.yaml`/`.md`→technical|`.csv`→reference|`.pdf`/`.txt`/`.docx`→general (Chris kann overriden)
- `"general"` als Detection-Trigger behalten (nicht nur `"auto"`)|Alt-Uploads mit general meist unbewusst gesetzt|User-Override (narrative/technical) gewinnt immer

## DB-Dedup / Insert-Guard (P113a)
- Dedup-Scope ≠ alle Rollen: `whisper_input` hat oft `session_id=NULL` (Dictate-Direct-Logging)|nur `role IN ('user','assistant')` mit konkreter session_id deduplizieren|Insert-Guard in `store_interaction` prüft session_id vorher → Whisper-Pipeline unberührt
- 30-Sekunden-Fenster Sweet Spot: LLM-Call + Retry-Button bei Timeout liegen 15-45s auseinander|30s fängt Double-Inserts ab ohne legitime Nachsendungen zu blocken

## Whisper Sentence-Repetition (P113b / W-001b)
- Wort-Dedup ≠ Satz-Dedup: `detect_phrase_repetition` (P102) cappt N-Gramme bis 6 Wörter|ganze Sätze >6 Wörter rutschen durch|`detect_sentence_repetition` splittet an `(?<=[.!?])\s+` + dedupliziert konsekutive Sätze (case-insensitive, whitespace-collapsed)
- Reihenfolge zwingend: erst Mikro (Phrase) → dann Makro (Sentence)|sonst werden interne Phrase-Loops erst gekürzt + dann zu früh als gleich erkannt
- Nicht-konsekutive behalten: `"A. B. A."` bleibt `"A. B. A."` (Refrain-Safe)

## Whisper Timeout-Hardening (P160)
- httpx Default zu kurz: lange Aufnahmen (>10s) brauchen RTX 3060 bis 30s|Vor P160: `httpx.AsyncClient(timeout=60.0)` ohne Connect-Timeout|Fix: `httpx.Timeout(120, connect=10)` aus Config (`settings.whisper.request_timeout_seconds`)|Connect kurz (Docker-nicht-erreichbar ≠ Whisper-rechnet)
- Zu kurze Audio-Clips (<4KB ≈ <0.25s) lautlos abfangen: VAD findet nichts, Decoder hängt|Symptom = Timeout, Root = leere Eingabe|Guard VOR Whisper: Bytes zählen → `WhisperSilenceGuard` raise|Endpoint-spezifisches Silence-Response (`{"text":""}` legacy, `{"transcript":"",...}` nala)
- Einmal-Retry bei `httpx.ReadTimeout`: Cold-Start kostet manchmal ersten Call|`timeout_retries=1`+`retry_backoff_seconds=2`|nach Retries → 500|NIEMALS endlos loopen
- Beide Pfade betroffen: `legacy.py::audio_transcriptions` UND `nala.py::voice_endpoint`|seit P160 zentraler `zerberus/utils/whisper_client.py::transcribe()`|gleiche Lesson wie P80b
- `audio_data = await file.read()` vor Call → Bytes in Memory persistent|Retries brauchen KEIN `file.seek(0)`|Stream-Objekt-Pfad würde `seek(0)` brauchen

## Background Memory Extraction (P115)
- Overnight-Erweiterung statt eigener Cron: 04:30-APScheduler-Job aus P57 (BERT-Sentiment) wird um Memory-Extraction erweitert|Reihenfolge Sentiment→Extraction egal|read-only auf 24h-Nachrichten
- Cosine aus L2 bei normalisierten Embeddings: MiniLM mit `normalize_embeddings=True`|FAISS IndexFlatL2 → L2-Distanz|`cos = 1 - L2²/2`|Threshold 0.9 (cos) ≈ L2 0.447
- Fail-Safe in Overnight-Job: Exception aus `extract_memories()` darf Job NICHT abbrechen|`try/except` außen|Sentiment hängt nicht von Extraction ab
- Source-Tag mit Datum: `source: "memory_extraction_2026-04-23"`|erlaubt späteres Soft-Delete (P116) per Datum|kein Rollover-Bug (`datetime.utcnow().strftime("%Y-%m-%d")` einmal pro Batch)
- Prompt-Template mit `{messages}`-Placeholder: `str.format()` auf Konstante|alle anderen `{}` als `{{}}` escapen|JSON-Output-Beispiel: `[{{"fact": "..."}}]` doppelt geklammert

## XSS / URL-Encoding (P116)
- `encodeURIComponent` für Query-Param mit Leerzeichen: `source=Neon Kadath.txt` bricht|`?source=Neon%20Kadath.txt` funktioniert|JS: `fetch('/…?source=' + encodeURIComponent(source))` — nie Template-Strings direkt
- XSS-Hardening bei Card-Rendering: `innerHTML` mit User-Daten (Dateiname)|`source.replace(/"/g, '&quot;')` + `source.replace(/</g, '&lt;')`|Datenherkunft intern (Admin), aber defensiv kostet nichts

## Decision-Boxes + Feature-Flags (P118a)
- Marker-Parsing ohne `eval()`/innerHTML-Injection: Regex findet `[DECISION]…[/DECISION]`|`[OPTION:wert]` Label|Text außerhalb Marker → `document.createTextNode`|nur `<button>` strukturell gebaut|Kein `innerHTML` mit User/LLM-Content
- Feature-Flag als Dict-Default in Settings: `features: Dict[str, Any] = {"decision_boxes": True}` in `config.py`|config.yaml gitignored → Default im Pydantic-Model (wie OpenRouter-Blacklist P102)|`settings.features.get("decision_boxes", False)` greift nach Clone auf True
- `append_decision_box_hint()` zentral in `core/prompt_features.py`|importiert in legacy.py UND orchestrator.py|Doppel-Injection per `"[DECISION]" in prompt`-Check verhindert

## Dateinamen-Konvention (P100)
- Projektspezifische CLAUDE.md IMMER `CLAUDE_[PROJEKTNAME].md`|Supervisor `SUPERVISOR_[PROJEKTNAME].md`
- Patch-Prompts IMMER vollen Dateinamen
- Hintergrund: P100 — Claude Code verwechselte projektspezifische mit globaler

## sys.modules-Test-Isolation (P169)
- `sys.modules["..."] = X` direkt setzen ist eine Falle|der Eintrag bleibt nach dem Test gesetzt, nachfolgende Tests sehen das Fake-Objekt statt des echten Moduls
- Symptom: `ImportError: cannot import name 'X' from '<unknown module name>'`|tritt auf wenn ein anderes Test-Modul ein `from <pkg.module> import X` macht und das Modul ein SimpleNamespace o. ä. ist
- Lösung: IMMER `monkeypatch.setitem(sys.modules, "<pkg.module>", fake)` — pytest restored den Original-Eintrag automatisch
- Maskierung: solche Bugs werden oft durch alphabetische Test-Reihenfolge versteckt, fallen erst auf wenn ein neuer Test alphabetisch NACH dem polluierenden Test einen Re-Import macht
- Pattern aus P169: `test_memory_extractor.py` setzte `sys.modules["zerberus.modules.rag.router"] = SimpleNamespace(...)`|`test_patch169_bugsweep` brach beim Import von `_ensure_init`|3-Zeilen-Fix mit `monkeypatch.setitem`

## RAG-Status Lazy-Init (P169)
- FAISS-Globals (`_index`, `_metadata`) werden in `zerberus/modules/rag/router.py` lazy ueber `_init_sync` rehydriert — getriggert nur durch Search/Index/Reset
- Reine Read-Endpoints (`GET /admin/rag/status`, `GET /admin/rag/documents`) liefen ohne `_ensure_init`-Aufruf|Folge: Hel-RAG-Tab zeigte „0 Dokumente" bis zum ersten Schreibvorgang, danach erschien plötzlich der ganze Bestand
- Faustregel: Jeder Endpoint, der RAG-Modul-Globals liest, MUSS `await _ensure_init(settings)` davor aufrufen — auch wenn er „nur" anzeigt
- Skip wenn `modules.rag.enabled=false`|sonst läufst du Gefahr, ein deaktiviertes Subsystem aus Versehen zu initialisieren

## HitL-Gate aus dem Long-Polling-Loop (P168)
- Direkt-`await` auf `wait_for_decision` im Telegram-Handler = Deadlock|Long-Polling-Loop awaited Updates SEQUENZIELL|der Click der das Gate auflöst kommt erst durch wenn der vorherige Handler returned|aber der vorherige Handler wartet auf den Click → Hängt bis Sweep-Timeout (5 min)
- Lösung: HitL-Wait + Folge-Aktion (z. B. `send_document`) als `asyncio.create_task` spawnen|Handler returned schnell, Long-Polling kann Click verarbeiten, Background-Task wird via `asyncio.Event` entlassen
- Pattern in [`router.py::_send_as_file`](zerberus/modules/telegram/router.py): bei effort=5+FILE → `create_task(_deferred_file_send_after_hitl(...))` statt direkt `await`
- Faustregel: Jeder `wait_for_decision`/`Event.wait` aus einem Long-Polling-Handler MUSS in einem separaten Task laufen|gleiches Prinzip wie der Sweep-Loop selbst (P167) — der hat das Pattern schon vorgemacht

## Telegram sendDocument (P168)
- httpx-Multipart/form-data baut sich automatisch wenn man `files={"document": (filename, bytes, mime)}` + `data={...}` an `client.post(...)` gibt|kein zusätzlicher Encoder nötig
- Caption-Limit ist 1024 ZS (nicht 4096 wie bei sendMessage)|Markdown-Caption mit Backticks → bei Match-Fehler HTTP-Fehler|Fallback ohne `parse_mode` analog zu sendMessage-Pattern
- Telegram-Limit für sendDocument: 50 MB|für unsere LLM-Outputs sind 10 MB sinnvoll (Schutz gegen LLM-Halluzinationen wie „schreib mir den Linux-Kernel")
- Datei-Endungs-Sicherheit: Whitelist + explizite Blocklist (Belt-and-suspenders)|nicht nur „erlaubt-was-bekannt-ist" — falls ein Bug im Format-Detector eine ``.exe``-Endung produziert, fängt die Blocklist sie

## Telegram-Bot hinter Tailscale (P155)
- Webhooks brauchen öffentliche HTTPS-URL|Tailscale MagicDNS (`*.tail*.ts.net`) löst nur intern|Self-Signed-Certs helfen nicht (DNS-Lookup scheitert vorher)
- Lösung Long-Polling: `getUpdates` (Long-Poll 30s-Timeout) statt warten|funktioniert hinter jeder Firewall/VPN/Tailnet
- Self-Hosted ohne öffentliche IP/Domain → IMMER Long-Polling
- Beim Polling-Start: evtl. gesetzten Webhook entfernen (`deleteWebhook`)|sonst HTTP 409 Conflict
- Offset: `getUpdates(offset=<last_update_id+1>)`|sonst Telegram liefert dasselbe erneut
- `allowed_updates=["message","channel_post","callback_query","my_chat_member"]` explizit|sonst filtert TG evtl. relevante Typen raus

## Telegram Group Privacy (P155)
- BotFather GroupPrivacy default AN|AN = Bot sieht in Gruppen nur `@`-Mention oder `/`-Start|Für `respond_to_name`+`autonomous_interjection` MUSS AUS|sonst kommen Updates gar nicht per `getUpdates`, ganzer Gruppen-Flow läuft ins Leere, Symptom lautlos
- Cache pro Gruppe: TG merkt Privacy-Stufe **zum Zeitpunkt des Beitritts**|Nach BotFather-Toggle reicht NICHT, einfach umzuschalten|Bot aus jeder Gruppe entfernen+neu hinzufügen
- Symptom-Indikator: `autonomous_interjection=true` aber keine Einwürfe + `@HuginnBot` funktioniert → Privacy = Root-Cause, NICHT `should_respond_in_group()`

## Vidar: Post-Deployment Smoke-Test (P153)
- 3 Test-Agenten: Loki=E2E (Happy-Path)|Fenrir=Chaos (Edge/Stress)|Vidar=Smoke (Go/No-Go)
- Vidar-Architektur: 3 Levels CRITICAL→IMPORTANT→COSMETIC|Verdict GO/WARN/FAIL|Test-Profil `vidar` mit `is_test: true`|beide Viewports (Mobile 390×844 + Desktop)|~21 Checks|<60s|`pytest zerberus/tests/test_vidar.py -v`
- Faustregel: Vidar nach JEDEM Server-Restart|GO→testen|WARN→testen+Bugs tracken|FAIL→nicht testen, erst fixen

## Design-Konsistenz (L-001, P151)
- Design-Entscheidung für UI-Element gilt projektübergreifend für ALLE ähnlichen in Nala UND Hel
- `zerberus/static/css/shared-design.css` mit `--zb-*`-Namespace|Design-Tokens: Farben/Spacing/Radien/Schatten/Touch/Typography|`docs/DESIGN.md` als Referenz
- Vor jeder CSS-Änderung: gleiches Element anderswo? → gleicher Style
- Touch-Target: Min 44px H+B für ALLE klickbaren Elemente (Apple HIG)|Loki prüft via `test_loki_mega_patch.TestTouchTargets`

## Settings-Singleton + YAML-Writer (P156)
- Jede `config.yaml`-Schreibfunktion MUSS Singleton invalidieren|sonst stale Wert
- Warum: `get_settings()` in [`config.py`](zerberus/core/config.py) liest `config.yaml` nur beim ersten Aufruf|YAML-Write ohne `reload_settings()` = Stale-State (Hel speichert neues Modell, `huginnReload()`-GET liefert alten Wert, Dropdown springt zurück)|HTTP 200 OK aber kosmetisches Symptom
- Pattern: `@invalidates_settings` Decorator (sync+async) ODER `with settings_writer():` Kontextmanager
- Fehlte vor P156-Sweep: `post_huginn_config`/`post_vision_config`/`post_pacemaker_processes`/`post_pacemaker_config` ([`hel.py`](zerberus/app/routers/hel.py)) + `_save_profile_hash` ([`nala.py`](zerberus/app/routers/nala.py))
- Test-Pattern: POST→GET in einem Test mit tmp-cwd-Fixture + `_settings = None`-Reset → [`test_huginn_config_endpoint.py`](zerberus/tests/test_huginn_config_endpoint.py)

## Guard-Kontext pro Einsatzort (P158)
- Problem: Halluzinations-Guard (Mistral Small 3) zustandslos, kennt weder Antwortenden noch Umgebung|Persona-Selbstreferenzen (Huginn=Rabe, Nala=Zerberus) als Halluzination eingestuft|Antwort im Huginn-Flow komplett unterdrückt = Persona-Unterdrückung, kein Sicherheitsgewinn
- Fix: optionaler `caller_context: str = ""` an [`check_response()`](zerberus/hallucination_guard.py)|`_build_system_prompt(caller_context)` hängt `[Kontext des Antwortenden]`-Block + harten Satz „Referenzen auf diese Elemente sind KEINE Halluzinationen."|Aufrufer (legacy.py→Nala, telegram/router.py→Huginn) bauen eigenen Kontext|Huginn nimmt 300-Zeichen-Persona-Auszug
- WARNUNG = Hinweis, NICHT Block: Antwort geht IMMER an User|Admin (Chris) bekommt DM mit Chat-ID+Grund bei WARNUNG|alte Regel „Antwort unterdrückt" zu aggressiv|echte Sicherheitsklassen (BLOCK/TOXIC) bekommen separate Semantik
- Teststrategie: Guard-Integration prüft nur `caller_context` im **System**-Prompt-Feld (NICHT User-Prompt)|Verhaltens-Test mockt `_run_guard` auf WARNUNG + checkt `send_telegram_message` 2× (User+Admin) → [`test_hallucination_guard.py`](zerberus/tests/test_hallucination_guard.py)

## Huginn-Persona als technisches Werkzeug (P158)
- Sarkasmus = kalibrierte Beratung, kein Gimmick|zynisch-bissiger Ton der [`DEFAULT_HUGINN_PROMPT`](zerberus/modules/telegram/bot.py) = Kanal für Aufwands-Rückmeldung|„Schreib Pong in Rust" → Aufwand kommentieren, vernünftigeres Format vorschlagen (Python, 30 Zeilen), nach Bestätigung trotzdem liefern|Ton korreliert mit Absurdität/Aufwand
- Implikation Roadmap: zukünftiger Aufwands-Schätzer skaliert Sarkasmus-Level dynamisch (kleiner Task→knapp+höflich|großer Task auf falschem Stack→bissig+Alternative)|System-Prompt nicht fix, kann pro Turn um „Aufwandskommentar"-Modifier ergänzt werden
- Spätere Patches dürfen Ton NICHT als „zu aggressiv" wegmoderieren — Ton IST Information

## Ratatoskr Sync-Reihenfolge (P158)
- Zerberus ERST commit+push, DANN `sync_repos.ps1`|nicht andersherum
- Warum: `sync_repos.ps1` liest Zerberus-HEAD-Commit-Message via `git log -1 --format=%s` als Commit-Betreff für Ratatoskr+Claude-Repo|Sync VOR Commit → Sync-Repos tragen vorherige Patch-Message obwohl neuen Inhalt
- Recovery: nicht fixen|nächster Patch syncs wieder mit richtiger Message → Phase eingeholt|`--amend`/Force-Push lohnt nicht

## Huginn-Roadmap + Review-Referenz (P161)
- Ab P162 referenzieren Patch-Prompts Finding-IDs (`K1`, `O3`, `D8` etc.) statt Beschreibung|Nachschlagen [`docs/huginn_review_final.md`](docs/huginn_review_final.md)|Sequenz+Phasen [`docs/huginn_roadmap_v2.md`](docs/huginn_roadmap_v2.md)
- Doku-Patch hat eigene Patch-Nummer|reine `.md`-Konsolidierungen kosten Slot (commit+sync+Roadmap)|nicht „neben" Code-Patch unterbringen|P159+P161 sind reine Doku-Patches
- Renumber-Disziplin: Doku-Patch zwischen Code-Patches verschiebt Folgenummern|Hinweis im Roadmap-Header oben|effektive Nummern führt SUPERVISOR_ZERBERUS.md

## Input-Sanitizer + Telegram-Hardening (P162)
- `structlog` NICHT installiert|Patch-Prompts aus 7-LLM-Review schlagen oft `structlog.get_logger()` vor|Zerberus nutzt `logging.getLogger(...)`|nicht blind übernehmen, auf vorhandenes Setup adaptieren (Stil wie [`bot.py`](zerberus/modules/telegram/bot.py)/[`hitl.py`](zerberus/modules/telegram/hitl.py))
- Sanitizer-Findings geloggt, NICHT geblockt|Huginn-Modus: `RegexSanitizer` lässt Injection-Treffer durch|Guard (Mistral Small) entscheidet final|False-Positive-Risiko bei Regex auf Deutsch real, Huginn-Persona/Sarkasmus oft nicht von Jailbreak unterscheidbar|`blocked=True`-Pfad für Rosa reserviert (config-driven)
- Singleton mit Test-Reset: `get_sanitizer()` = Modul-Singleton wie `get_settings()`|Tests resetten State zwischen Runs (`_reset_sanitizer_for_tests()` als autouse-Fixture in [`test_input_sanitizer.py`](zerberus/tests/test_input_sanitizer.py))|sonst leakt Implementierung
- Persistente Offset-Datei kontaminiert ältere Tests: `data/huginn_offset.json` wird vom `long_polling_loop` geschrieben|ohne `monkeypatch.setattr(bot_module, "OFFSET_FILE", tmp_path/"off.json")` schreibt jeder Loop-Test in echte Datei|Pre-existierende Tests (`test_long_polling_loop_advances_offset`, `test_long_polling_handler_exception_does_not_break_loop`) brauchten Patch nachträglich|Lesson: bei jeder neuen Modul-Level-Konstante mit File-IO sofort an Test-Isolation denken
- Update-Typ-Filter GANZ oben in `process_update()`: vor Event-Bus, vor Manager-Setup, vor allem|sonst Setup-Zeit für triviale Filter-Cases (channel_post/edited_message/poll)|Reihenfolge in [`router.py`](zerberus/modules/telegram/router.py): `enabled`-Check → Update-Typ-Filter → Bus → Manager → Callback → extract_message_info
- Edited-Messages = Jailbreak-Vektor: harmlose Nachricht editieren auf „ignore previous instructions" + erneut verarbeiten = zweite Antwort|TG liefert `edited_message`-Updates auch unangefordert|Filter PFLICHT, nicht nice-to-have
- Callback-Spoofing in Gruppen: TG validiert nicht, wer Inline-Button klickt|Bot muss validieren|Vor P162 nur Admin-DM (implizit sicher)|Mit `requester_user_id` jetzt für In-Group-Buttons vorbereitet|Erlaubte Klicker: `{admin_chat_id, requester_user_id}`|String-Vergleich (TG `from.id` int, Config oft String)
- `message_thread_id` muss durchgereicht werden, nicht nur extrahiert|Forum-Topics verlieren sonst Kontext (Antwort im General statt Thread)|`extract_message_info()` exposed Feld|ALLE `send_telegram_message`-Calls in `router.py` reichen als kwarg durch|Pattern: `message_thread_id=info.get("message_thread_id")` immer mitgeben|TG ignoriert wenn None → nur truthy ins Payload

## Token-Effizienz bei Doku-Reads (P163)
- Keine rituellen File-Reads|`SUPERVISOR_ZERBERUS.md`/`lessons.md`/`CLAUDE_ZERBERUS.md` werden via CLAUDE.md schon in den Kontext geladen|Re-Read = 2-4k Token verschwendet pro Patch
- Regel: Datei nur lesen wenn (a) NICHT im Kontext sichtbar ODER (b) man sie direkt danach schreiben will|„Lies alles nochmal als Sicherheit" ist kein guter Grund
- Doku-Updates bleiben Pflicht|aber am Patch-Ende|EIN Read→Write-Zyklus pro Datei|kein separater Read am Anfang + Write am Ende
- Neue Einträge in CLAUDE_ZERBERUS.md + lessons.md IMMER im Bibel-Fibel-Format (Pipes|Stichpunkte|ArtikelWeg)|sonst zerfasert die in P163 gewonnene Kompression wieder

## Intent-Router via JSON-Header (P164)
- Architektur-Entscheidung: Intent kommt vom Haupt-LLM via JSON-Header in der eigenen Antwort, NICHT via Regex/Classifier|Whisper-Transkriptionsfehler machen Regex unbrauchbar|Extra-Classifier-Call verdoppelt Latenz
- Format: `{"intent":"CHAT|CODE|FILE|SEARCH|IMAGE|ADMIN", "effort":1-5, "needs_hitl":bool}`|allererste Zeile, optional in ```json-Fence|Body folgt darunter
- Parser [`core/intent_parser.py`](zerberus/core/intent_parser.py): Brace-Counter statt naivem `[^}]+`-Regex (Header mit Sonderzeichen)|Robustheit-Garantien: kein Header→CHAT/3/false+Body=Original, kaputtes JSON→Default+Warning, Unbekannt-Intent→CHAT, effort außerhalb 1-5→geclampt, non-numeric effort→3, JSON-Array statt Objekt→kein Header
- `INTENT_INSTRUCTION` in [`bot.py`](zerberus/modules/telegram/bot.py)|wird via `build_huginn_system_prompt(persona)` an Persona angehängt|Persona darf leer sein (User-Opt-Out), Intent-Block bleibt Pflicht
- Guard sieht IMMER `parsed.body` (ohne Header)|sonst meldet Mistral Small den JSON-Header als Halluzination|User sieht ebenfalls `parsed.body` ohne Header
- Edge-Case: LLM liefert nur Header ohne Body → Roh-Antwort senden (Header inklusive)|hässlich aber besser als leere TG-Nachricht|in Praxis selten
- HitL-Policy [`core/hitl_policy.py`](zerberus/core/hitl_policy.py): NEVER_HITL={CHAT,SEARCH,IMAGE} überstimmt LLM `needs_hitl=true` (K5-Schutz gegen Effort-Inflation)|BUTTON_REQUIRED={CODE,FILE,ADMIN} braucht Inline-Keyboard|ADMIN erzwingt HitL auch bei `needs_hitl=false` (K6-Schutz gegen jailbroken LLM)|`button` heißt ✅/❌ Inline-Keyboard, NIE „antworte 'ja' im Chat"
- Aktueller Stand P164: Policy evaluiert + loggt + Admin-DM-Hinweis|echter Button-Flow für CODE/FILE/ADMIN-Aktionen folgt mit Phase D (Sandbox/Code-Exec)|Effort-Score ebenfalls nur geloggt (Datengrundlage Phase C Aufwands-Kalibrierung)
- Gruppen-Einwurf-Filter: autonome Antworten nur bei {CHAT,SEARCH,IMAGE}|CODE/FILE/ADMIN unterdrückt|Bot darf in Gruppen nicht autonom Code ausführen
- Logging-Tags: `[INTENT-164]` (Parser+Router), `[EFFORT-164]` (Effort-Bucketing low/mid/high), `[HITL-POLICY-164]` (Policy-Decisions)

## Auto-Test-Policy (P165)
- Mensch=unzuverlässiger Tester|Coda=systematisch+unbestechlich
- Alles was automatisiert testbar ist MUSS automatisiert getestet werden
- Nur UI-Rendering/echte Geräte/echte Mikrofone/UX-Gefühl bleiben beim Menschen
- Live-Validation-Scripts in `scripts/` für API-abhängige Features (Beispiel: [`scripts/validate_intent_router.py`](scripts/validate_intent_router.py))
- Doku-Konsistenz-Checker [`scripts/check_docs_consistency.py`](scripts/check_docs_consistency.py): Patch-Nummer-Sync|tote Links|Log-Tag-Validierung|Import-Resolvability|Settings-Keys|nach jedem Patch additiv zu pytest
- Retroaktiv: Code ohne Tests → Tests nachrüsten bei Gelegenheit (kein eigener Patch nötig)
- P165-Sweep: 88 neue Tests (test_dialect_core, test_prompt_features, test_hitl_manager, test_language_detector, test_db_helpers) für vorher untestete Pure-Function-Module

## HitL-Hardening (P167)
- HitL-State im RAM = Datenverlust bei Restart|jede Reservierung muss in SQLite|`hitl_tasks`-Tabelle mit UUID4-Hex-PK ist Source-of-Truth|In-Memory-Cache nur Beschleuniger + `asyncio.Event`-Notifizierung
- Async API + Sync-Backward-Compat-Wrapper nebeneinander: neue async `create_task`/`resolve_task`/`expire_stale_tasks`/`get_pending_tasks` schreiben in DB|alte sync `create_request`/`approve`/`reject`/`get` bleiben rein in-memory für Pre-167-Tests|`persistent=False`-Schalter im Konstruktor erspart DB-Stub für Unit-Tests|`HitlRequest = HitlTask` + `@property`-Aliase (`request_id`/`request_type`/`requester_chat_id`/`requester_user_id`) damit alter Code weiterläuft
- Ownership-Check sitzt im Router-Callback, nicht im Manager|`is_admin = clicker == admin_chat_id`, `is_requester = clicker == task.requester_id`|`is_admin_override` an `resolve_task` durchreichen → ein einzelner WARNING-Log `[HITL-167] Admin-Override` reicht für Audit-Trail
- Auto-Reject-Sweep als eigener `asyncio.Task`: gestartet in `startup_huginn()` (nach `_get_managers`), gestoppt in `shutdown_huginn()` (vor Polling-Cancel)|Sweep-Loop ruft `expire_stale_tasks()` und Callback `on_expired(task)` für Telegram-Hinweis|Cancel via `CancelledError` → durchreichen, sonst hängt Server-Shutdown
- Default-Werte für HitL (`timeout_seconds=300`/`sweep_interval_seconds=30`) im Pydantic-Model `HitlConfig` ([`core/config.py`](zerberus/core/config.py)) — config.yaml gitignored, deshalb müssen Sicherheits-Defaults im Code stehen (analog `OpenRouterConfig.provider_blacklist`)|Modul liest mit `HitlConfig().timeout_seconds` als Fallback, config.yaml darf überschreiben
- P8 wird operationalisiert durch das Routing: Callback-Handler nimmt **nur** `callback_query`-Events, nie Text-Eingaben|„Ja mach mal" als CODE-Confirm ist damit konstruktiv unmöglich, nicht nur „nicht erlaubt"
- Test-Pattern für DB-Tasks: `tmp_db`-Fixture mit eigener SQLite (analog `test_memory_store.py`)|Stale-Tasks per `update().values(created_at=...)` in DB zurückdatieren statt Real-Time-Sleep|`_reset_telegram_singletons_for_tests()` für Router-Tests, weil HitlManager als Modul-Singleton läuft

## Log-Hygiene + Repo-Sync-Verifikation (P166)
- Routine-Heartbeats fluten Terminal|Pacemaker-Puls/Watchdog-Healthcheck/Audio-Transkripte → DEBUG, nicht INFO|sichtbar bleibt nur Start/Stop/Problem
- Audio-Transkript-Logs: nur Längen-Einzeiler auf INFO|voller Text auf DEBUG (für Debugging on-demand)
- Telegram-Long-Poll-Exception (DNS/Network) → DEBUG + Modul-Counter `_consecutive_poll_errors`|nach `_POLL_ERROR_WARN_THRESHOLD` (=5) genau EINE WARNING|bei Erfolg Counter-Reset + INFO „Verbindung wiederhergestellt"|Modul-Singleton via `_LAST_POLL_FAILED`-Flag, weil `[]` doppeldeutig (Long-Poll-OK vs Fehler)
- `_reset_poll_error_counter_for_tests()` als Test-Reset-Helper analog zu Rate-Limiter/Sanitizer-Pattern
- `sync_repos.ps1` ohne Verifikation = Hoffnung|`scripts/verify_sync.ps1` (P166) prüft `git status` clean + `git log origin/main..HEAD` leer für alle 3 Repos|Exit-Code 1 bei Drift|Coda darf nicht weitermachen ohne ✅
- Workflow-Reihenfolge ist hart: commit→push (Zerberus)→sync_repos.ps1→verify_sync.ps1|sync VOR commit lädt alte Commit-Message (P158-Lesson)
- Legacy-Härtungs-Inventar [`docs/legacy_haertungs_inventar.md`](docs/legacy_haertungs_inventar.md): 27 Härtungen aus `legacy/Nala_Weiche.py` durchgangen|23 übernommen, 4 obsolet (durch P156/P160 ersetzt), 0 fehlend|+9 zusätzliche Härtungen über Legacy hinaus (P158/P162/P163/P164/P109/P113a)

## Sync-Pflicht nach jedem Push (P164)
- Coda-Setup pusht zuverlässig nach Zerberus, vergisst aber `sync_repos.ps1`|Ratatoskr+Claude-Repo driften unbemerkt
- Regel: Sync ist LETZTER Schritt jedes Patches|Patch gilt erst als abgeschlossen wenn alle 3 Repos synchron|nicht „am Session-Ende" oder „nach 5 Patches"
- Wenn Claude Code Sync nicht ausführen kann (Umgebung): EXPLIZIT melden „⚠️ sync_repos.ps1 nicht ausgeführt — bitte manuell nachholen"|stillschweigendes Überspringen NICHT zulässig

## Rate-Limiting + Graceful Degradation (P163)
- Per-User-Limit gegen Telegram-Spam: 10 msg/min/User|Cooldown 60s|InMemory-Singleton in [`core/rate_limiter.py`](zerberus/core/rate_limiter.py) mit Interface `RateLimiter` (Rosa-Skelett für Redis-Variante) + `InMemoryRateLimiter` (Huginn-jetzt)|`first_rejection`-Flag liefert genau EIN „Sachte, Keule"-Reply, danach still ignorieren
- Sliding-Window pro User: nur Timestamps der letzten 60s halten|`cleanup()` entfernt Buckets nach 5min Inaktivität|Test-Reset via Modul-Singleton-Reset (`rate_limiter._rate_limiter = None`)
- Rate-Limit-Check in `process_update()` GANZ oben (nach Update-Typ-Filter, vor Sanitizer/Manager)|nur für `message`-Updates, nicht für `callback_query`|`user_id` aus `message.from.id`
- Guard-Fail-Policy konfigurierbar: `security.guard_fail_policy` ∈ {`allow`,`block`,`degrade`}|Default `allow` (Huginn-Modus, Antwort durchlassen + Log-Warnung)|`block` blockiert mit User-Hinweis|`degrade` reserviert für lokales Modell (Ollama-Future)|`_run_guard()` returnt `{"verdict": "ERROR"}` bei Fail → router prüft Policy
- OpenRouter-Retry mit Backoff: nur bei 429/503/„rate"|2s/4s/8s exponentiell|max 3 Versuche|400/401/etc. NICHT retryen (Bad Request bleibt Bad Request)|Nach Erschöpfung: User-Fallback „Meine Kristallkugel ist gerade trüb."
- Ausgangs-Throttle pro Chat in `bot.py`: `send_telegram_message_throttled` mit 15 msg/min/Chat (konservativ unter TG-Limit 20/min/Gruppe)|wartet via `asyncio.sleep` statt Drop|nur für autonome Gruppen-Einwürfe nötig (DMs nicht limitiert)|Modul-Singleton `_outgoing_timestamps` als defaultdict
- Config-Keys VORBEREITET, nicht aktiv gelesen: `limits.per_user_rpm`/`limits.cooldown_seconds`/`security.guard_fail_policy` in `config.yaml`|aktives Reading mit Config-Refactor Phase B|jetzige Defaults im Code (max_rpm=10, cooldown=60, fail_policy="allow")
- Logging-Tags: `[RATELIMIT-163]` (rate-limiter) + `[HUGINN-163]` (router/bot)

## Mega-Patch-Erkenntnisse (Sessions 122-152, 2026-04-23/24)

### Effizienz
- 33k Token/Patch bei 8 Patches → 24k/Patch bei 16 Patches|je mehr Patches im selben Fenster, desto weniger Overhead (Codebasis nur einmal eingelesen)
- 450k-Token-Grenze nie erreicht (max 383k bei 16 Patches = 38% von 1M)|theoretisch 20+ Patches möglich
- Token-Selbstüberwachung > hartes Limit|Claude Code stoppt sich nach Scope-Grenze, nicht Token-Limit
- Re-Read-Vermeidung: Module aus Kontext nicht neu lesen, nur grep'en

### Strategie
- Leicht abbrechbare Tasks ans Ende (FAISS-Migration, Design-Audit, Memory-Dashboard)|hinterlassen bei Abbruch keinen halben Zustand
- Eigeninitiative erlauben: Session 1 hat 127-129 nachgeschoben|Session 3 alle 16 ohne Nachfrage
- Mega-Prompt-Aufbau: Diagnose-grep-Befehle VOR Code|Block-Struktur Diagnose→Implementierung→Tests→Doku|Reihenfolge: Kritische Fixes→Features→Kosmetik→Destruktive
- Diagnose zuerst, komplett (Mega 2): kompakter Diagnose-Block für alle Patches statt pro Patch|spart Kontext
- Scope-Verkleinerung bei destruktiven Patches: P133 sollte FAISS umschalten + RAG-Eval|Modell-Downloads (~3 GB) + Eval + Restart unrealistisch in einem Patch|Stattdessen Backup+Switch+Config-Flag default-false+Tests|Chris fährt `--execute` separat = saubere Scope-Reduktion

### Test-Pattern
- in-memory SQLite via `monkeypatch.setattr(db_mod, "_async_session_maker", sm)` auch für async Endpoints (Funktionen direkt statt HTTP-Layer)
- Header-Parameter-Falle: `request: Request` MUSS vor File-Parametern stehen (P135)|sonst FastAPI Parameter-Ordering-Error
- Endpoint-Tests über Funktions-Call: `asyncio.run(my_endpoint_func(FakeRequest))` statt TestClient|spart Auth-Setup (Admin-Auth blockiert TestClient ohne Credentials)
- `inspect.getsource()` für Header-Präsenz-Check|pragmatisch + wartungsarm
- Source-Match als Test-Strategie für UI-Patches (HTML/CSS/JS inline in Python-Routern)|kein Browser/TestClient/DOM-Parser|schnell+stabil
- Edge-Tests via Block-Split: `src.split("function X")[1].split("function ")[0]`|stabil gegen Reformatting, brüchig bei Umbenennung|bewusst akzeptiert
- Bei HTML-Inline-JS großzügigere Fensterlänge (5000-6000 statt 2000-3000) wegen Zeilenumbrüchen
- Immer `asyncio.run` für sync-Wrapper um async-Test-Code|`get_event_loop().run_until_complete()` bricht in voller Suite (geschlossene Loops)
- Lazy-Load-Hooks am Handler-Code suchen, NICHT am Button (Memory-Dashboard-Lesson)
- Tab-Tests: nach HTML-Marker `<div class="settings-tabs">` suchen, NICHT reine Klassen-ID-Substring|Vermeidet Treffer in CSS-Class-Blocks
- Modul-Loader darf nicht hart crashen: `main.py` iteriert `pkgutil.iter_modules()` + importiert `modules/<x>/router.py`|Helper-Pakete ohne Router (z.B. `memory` seit P115) müssen vor Import per `router.py`-Existenzcheck als INFO-Skip geloggt werden, NICHT ERROR

### Polish/Migration
- HSL additiv, nicht ersetzend: bestehende `<input type="color">`-Picker behalten + HSL-Slider dranhängen mit `hslToHex()`-Sync|Favoriten speichern weiter HEX|Touch-freundlich|Regenbogen-Gradient auf Hue-Slider = 5 Zeilen CSS
- Sticky-Anchor statt Modal-Umbau: „⚙️ Einstellungen" als `position: sticky; bottom: 0;` am Sidebar-Ende|billiger+robuster als Tab-Umbau
- Migration-Scripts: `--dry-run` als Default|destruktive Aktion mit `--execute` explizit|Backup vor `--execute`|alte Index-Dateien physisch erhalten (neue zusätzlich)|Live-Retriever liest weiter bis Chris in config.yaml umstellt = Shadow-Index-Pattern
- Script als Test-Modul importieren: `importlib.util.spec_from_file_location` für Tests gegen Scripts ohne Paket-Setup|Dry-Run-Path sauber abgetrennt = kein Side-Effect
- `ast.parse` reicht nicht für JS in Python-Strings: Hel/Nala haben tausende Zeilen JS in `"""..."""`|`ast.parse` verifiziert nur Python|JS-Fehler fallen erst im Browser auf|Frontend-Patches: zusätzlich Browser/`node --check` auf extrahierte `<script>`-Blöcke
- Bubble-Shine = ::before, kein SVG: 135°-Gradient `rgba(255,255,255,0.14)`→`transparent`|z-index 0 Shine, `message > *` z-index 1|funktioniert mit beliebigen Hintergrundfarben
- Prompt-Kompression als WERKZEUG, kein Prozess: `compress_prompt()` nicht zur Laufzeit|nur manuell auf Backend-System-Prompts|`preserve_sentiment=True` schützt user-facing Marker (Nala/Rosa/Huginn/Chris/warm/liebevoll)|Sicherheitsnetz: <10% des Originals → Original zurückgeben
- Spracherkennung ohne langdetect: Wortlisten-Frequenz + Code-Token-Filter (def/class/function/import) + Umlaut-Boost (+3 für DE)|für DE/EN-Dokumente reicht|<5 Tokens → Default DE
- Config-Endpoints maskieren Token: `GET /admin/huginn/config` gibt `bot_token_masked` zurück (`abcd…wxyz`)|POST akzeptiert nur ohne „…" im gesendeten Token|Frontend-Reload speichert maskierten Wert nicht versehentlich
- Cleanup-Script: DB-Backup vor Delete als eingebautes Verhalten, nicht Opt-In|Default sicher, `--execute` macht scharf
- HSL-Bug cssToHex (P153) propagierte: Parser→Farbpicker→localStorage→nächster Load = schwarze UI|Konverter-Funktionen brauchen Unit-Tests mit ALLEN Input-Formaten (#rgb, #rrggbb, rgb(), rgba(), hsl(), hsla(), oklch(), …)
- **Black-Bug VIERTER Fix (P183) — wiederkehrender Bug nicht an der Symptomstelle patchen, sondern zentralen Sanitizer bauen der ALLE Pfade abdeckt.** Vorgänger P109/P153/P169 hatten exakte String-Match-Listen mit nur 4 Schwarz-Formaten (`#000000`, `#000`, `rgb(0, 0, 0)`, `rgba(0,0,0,1)`). Nicht erfasst: `hsl(0,0%,0%)`, `rgb(0,0,0)` ohne Spaces, `rgba(0,0,0,1.0)` Float-Alpha, `rgba(0, 0, 0, 1)` mit Whitespace vor 1, leere/transparent Werte. Plus 4 Codepfade (bubblePreview/bubbleTextPreview/applyHsl/loadFav) setzten Bubble-CSS-Vars KOMPLETT OHNE Guard. Lesson: bei wiederkehrenden Bugs (3+ Anläufe gescheitert) FORENSISCHE INVENTUR aller Codepfade vor Symptom-Patch — `setProperty('--bubble-` grep liefert das Inventar, jeder Pfad MUSS durch zentralen Sanitizer (`sanitizeBubbleColor` mit regex-basiertem `_isBubbleBlack` für HSL-L<5%, alle RGB/RGBA-Whitespace-Varianten, alle Hex-Alpha-Notationen). Plus localStorage-Sweep VOR der IIFE und nochmal in `showChatScreen` (defense-in-depth — falls zwischen Pre-Render und Login ein Codepfad schwarze Werte zurückschreibt). Test-Pattern: Python-Mirror der `_isBubbleBlack`-Logik mit parametrize über alle bekannten Schwarz-Varianten + Source-Audit dass jede setProperty-Stelle den Guard hat.
- Init-Reihenfolge bei wiederkehrenden Frontend-Bugs IMMER dokumentieren (P183): IIFE im `<head>` läuft VOR DOM-Ready, dann DOMContentLoaded → doLogin → showChatScreen → applyAutoContrast → Settings-Init liest computed CSS. Race: Wenn ein späterer Codepfad (z.B. SSE-Event, Profil-Reload) eine CSS-Var nach IIFE überschreibt, helfen Pre-Render-Guards nicht — dann zweiter Sweep nötig. Bei HSL-Slidern: `min="10"` für L im HTML, aber Sanitizer trotzdem auf L<5% prüfen (defense-in-depth gegen direkte localStorage-Manipulation oder Pre-P183-Daten).
- **Runtime-Infos (P185)|Modellname + Guard-Modell + Modul-Status aus Config lesen statt statisch im RAG-Dokument pflegen.** Statische RAG-Doku eignet sich für langfristige Fakten (Architektur, Naming, Komponenten-Beschreibungen, Halluzinations-Negationen), NICHT für volatile Werte (Modellname, Patch-Stand, Modul-Aktivierung). Pattern: gemeinsame Utility `zerberus/utils/runtime_info.py` mit `build_runtime_info(settings) -> str` + `append_runtime_info(prompt, settings) -> str`, defensiv gegen kaputte/partielle Settings (Pydantic UND Dict UND None müssen funktionieren). Position im Prompt-Stack: NACH Persona, VOR RAG-Kontext — der RAG-Block kann dann auf "[Aktuelle System-Informationen]" als Marker zeigen. Test-Pattern: ein Source-Audit-Test pro Router der Reihenfolge der Calls (`_wrap_persona` < `append_runtime_info` < `_inject_rag_context` / `append_decision_box_hint`) verifiziert — sonst rutscht ein zukünftiger Patch zwischen die Stufen und Persona-Drift entsteht. RAG-Doku-Update bekommt einen Hinweis-Absatz: was statisch bleibt vs. was zur Laufzeit kommt.
- **Nala-Persona-Pfad (P184) — Frontend-Settings und Backend-LLM-Call haben GETRENNTE Prompt-Pfade.** Frontend speichert via `POST /nala/profile/my_prompt` in `system_prompt_{profil}.json`. Backend liest via `load_system_prompt(profile_name)` in `legacy.py` und prependet als `role=system` ins messages-Array. Verdrahtung selten kaputt — meistens ist der Bug das LLM-Verhalten: DeepSeek v3.2 ignoriert abstrakte System-Prompts bei kurzen User-Inputs ("wie geht's?") und fällt auf den Assistant-Default zurück ("Alles gut hier, danke"). Lesson: bei Persona-Bug-Reports IMMER ZUERST den finalen System-Prompt loggen (`[PERSONA-184]` INFO-Log mit `profile`, `len`, `first200`) — wenn die Persona im Log auftaucht aber die Antwort generisch ist, liegt es am LLM, nicht an der Verdrahtung. Fix-Pattern für LLM-Persona-Drift: explizit `# AKTIVE PERSONA — VERBINDLICH`-Header voranstellen mit Hinweis dass Persona AUCH bei kurzen Nachrichten gilt|der generische Default-Prompt wird NICHT gewrappt (er IST der Default). Debug-Log-Pattern: persistent als INFO-Level mit Patch-Tag — nicht temporär entfernen, sondern dauerhaft drinlassen für künftige Diagnose. Bei weiterhin Persona-Drift trotz Wrapping: ChatML-Wrapper als nächste Eskalations-Stufe (B-071).
- Hel-UI fail-safe (P136): bei OpenRouter-Fehler in `get_balance()` graceful degradation|lokale Cost-History trotzdem|`balance` None|UI bleibt benutzbar ohne Netz

### Modellwahl + Scope
- Nicht automatisch das neueste/größte Modell|Sweet Spot pro Job: Guard=Mistral Small 3 (billig+schnell)|Prosodie=Gemma 4 E4B (Audio-fähig)|Transkription=Whisper (spezialisiert)
- Free-Tier-Modelle = Werbung, überlastet, schlechte Qualität|Paid bevorzugen
- Feature-Creep-Gefahr: Audio-Sentiment-Pipeline braucht eigenes Architektur-Dokument (`docs/AUDIO_SENTIMENT_ARCHITEKTUR.md`) damit Scope klar
- Bibel-Fibel: Token-Optimierungs-Regelwerk (im Claude-Projekt)|Potenzial für komprimierte System-Prompts an DeepSeek
- Patch-Nummern: keine freihalten für „Reserve"|lückenlos durchnummerieren|spätere Patches mit Suffix (127a etc.)
- Mega-Patches abspalten: Patch-Prompt mit 3+ unabhängigen Blöcken → Claude Code zieht nur einen sauber durch, Rest still abgeschnitten|KEIN Fehler = Kontext-/Attention-Budget-Mgmt|P157 hat nur Terminal gemacht, Persona+Guard kamen als P158|Restblöcke als eigenen Nachfolge-Patch sauber formulieren|Signal: Diff < erwartet + Test-Zahl < angekündigt → Rest übersprungen|explizit reaktivieren, nicht auf autonomes Nachholen hoffen
- Design-System früh einführen: `shared-design.css` hätte zwischen P122 und P131 schon|je später, desto aufwändiger Migration der Legacy-Styles|Token-Definitionen früh, auch wenn nicht alle Klassen genutzt
- Sandbox = Kernel-Schicht, nicht App-Schicht (P171): Docker `--network none --read-only --memory --pids-limit --security-opt no-new-privileges` ist die einzige WIRKLICH erzwungene Schutzschicht|Code-Blockliste ist Belt+Suspenders|jede Logik im Userspace kann theoretisch umgangen werden (Bug, Bypass, jailbroken LLM) — Cgroups+Namespaces nicht|deshalb Container-Cleanup IMMER, auch bei Crash/Timeout (`docker rm -f` mit eindeutigem Namen `zerberus-sandbox-<uuid>`)|kein Volume-Mount, niemals
- Custom-pytest-Marker brauchen Registrierung (P171/P172): `@pytest.mark.docker` / `@pytest.mark.guard_live` ohne `pytest_configure` → `PytestUnknownMarkWarning` und Marker-Filter (`pytest -m guard_live`) funktioniert weniger sauber|Registrierung in `conftest.py::pytest_configure` mit `config.addinivalue_line("markers", "...")`|kombiniert mit `skipif` für Auto-Skip wenn Voraussetzung fehlt (Docker-Daemon, OPENROUTER_API_KEY)
- Patch-Spec ≠ Repo-Realität (P172): Spec sagte „Ollama + Mistral Small", echter Guard ist OpenRouter-basiert (`mistralai/mistral-small-24b-instruct-2501`)|VOR dem Test-Schreiben Implementation lesen, nicht der Spec blind folgen|Test-Comment dokumentiert die Diskrepanz für später
- Test-Pattern für „dokumentierte Lücke" (P172): `pytest.xfail("KNOWN-LIMITATION-XXX: <Begründung> Empfehlung: <konkreter Pattern-Vorschlag>")`|nicht skip, nicht failed — xfail kommuniziert „bekannt, geplant gefixt, nicht in diesem Patch"|jede xfail-Meldung muss konkrete Empfehlung haben, sonst rottet sie
- Mehrschichtige Sicherheit > Allzweck-LLM-Guard (P172): LLM-Guard ist semantisch (Halluzinationen, Tonfall, Sycophancy) — NICHT für Auth, Rate-Limit, MIME, Sanitizing zuständig|jede Schicht macht EINEN Job in <1ms (Schicht 1+2 deterministisch) oder semantisch (Schicht 4 LLM)|Determinismus dominiert Semantik = Schicht 1+2-Block ist final, kein Bypass durch „Guard sagt OK"|fail-fast in 1+2, fail-open in 4 (Verfügbarkeit > Sicherheit per Default)
- Live-Test-Robustheit gegen LLM-Indeterminismus (P172): Tests gegen externen LLM-Guard können vereinzelt ERROR liefern (JSON-Parse, Rate-Limit, Modell-Glitch) ohne dass der Code-Pfad kaputt ist|Akzeptanz-Set für „semantisch ok"-Tests sollte ERROR mit Print-Log einschließen|nicht für „Inhalts"-Tests aufweichen — nur für Verfügbarkeits-Sanity-Checks
- Inline-Flag-Group für selektive Case-Sensitivity (P173): bei globalem `re.IGNORECASE` einzelne Tokens case-sensitive halten via `(?-i:DAN)`|Beispiel: Jailbreak-Alias „DAN" ist immer GROSS, der Vorname „Dan" nicht — `(?-i:DAN)` triggert nur auf den Alias|Alternative wäre Pattern-für-Pattern Compile mit unterschiedlichen Flags, aber Inline-Group hält die Regel-Liste flach
- NFKC vor Pattern-Match statt Pattern-Explosion (P173): Unicode-Homoglyph-Bypass (Ⅰgnore, ﬁle, full-width) lassen sich pro Pattern mit Confusables-Mapping erschlagen, aber NFKC-Normalisierung im Hauptpfad löst alle auf einmal|Deutsche Umlaute (ä/ö/ü/ß) und Emoji bleiben unter NFKC unverändert — kein Datenverlust|Finding `UNICODE_NORMALIZED: NFKC` wenn der Text geändert wurde gibt downstream Guard ein Signal, dass etwas „nicht ganz normal" war
- xfails fixen UND xfails behalten (P173): die 11 P172-xfails zerfielen in zwei Klassen: 7 mit konkreter Pattern-Empfehlung (T02–T05, T09, T13, T14) → fixbar in einem Patch|4 mit „Out-of-Scope, semantisch" (T06/T07/T08 Obfuskation, T16 Persona) → bleiben xfail by design|jeder xfail-Reason MUSS klar zwischen „noch nicht gefixt" und „bewusste Architektur-Entscheidung" unterscheiden — sonst werden später aus „bewusst" ausversehen Pflicht-Fixes
- FP-Boundary-Tests parallel zu jedem neuen Pattern (P173): jedes neue Sanitizer-Pattern braucht mind. 1 Boundary-Test in `TestKeineFalsePositives` der den Pattern bewusst NICHT trifft|„Du bist jetzt der Tutor und Dan ist mein Bruder" für DAN-Pattern, „Gib mir bitte ein Beispiel" für `gib`-Pattern, „[Klick hier](https://example.com)" für javascript:-Pattern|sonst rottet die FP-Liste, weil jeder Pattern-Add unbeobachtet die FP-Surface vergrößert
- Interface-only-Patches als Phasen-Anker (P173, Phase E): Message-Bus-Interfaces (`message_bus.py` + `transport.py`) wurden in P173 OHNE einen einzigen Konsumenten gemerged|bewusste Reihenfolge: erst Interface stabilisieren, dann Adapter (P174 Telegram), dann Pipeline-Migration (P175)|Vorteil: kein Big-Bang-Refactor, Reviewer kann Interfaces in Ruhe lesen ohne von 500 Diff-Zeilen Telegram-Code abgelenkt zu werden|Risiko: ungenutzte Klassen rotten — Gegenmaßnahme ist die Patch-Liste in der Interface-Datei (P174/P175 explizit benannt, sonst rottet das Versprechen)
- Monkey-Patch-Punkte sind ein Refactor-Vetorecht (P174): bestehende Tests in `test_telegram_bot.py`/`test_hitl_hardening.py`/`test_rate_limiter.py`/`test_file_output.py`/`test_hallucination_guard.py` patchen `telegram_router.send_telegram_message`/`call_llm`/`_run_guard`/`_process_text_message` als Modul-Attribute|jede Umbenennung oder Signatur-Änderung dieser Symbole bricht ~15 Tests gleichzeitig|deshalb in P174 BEWUSST keinen Cutover (`process_update` → `handle_telegram_update`) gemacht — neue Funktionalität läuft parallel, der legacy-Pfad bleibt unverändert|Cutover passiert in P175 wenn die neuen Pfade alle komplexen Fälle (Group, Callbacks, Vision, HitL-BG) abdecken
- DI-Pipeline statt direkte Imports (P174): `core/pipeline.py::process_message` nimmt `PipelineDeps`-Dataclass mit `sanitizer`/`llm_caller`/`guard_caller`/`should_send_as_file`/etc.|damit hat die Pipeline NULL Telegram-/HTTP-/OpenRouter-Imports, ist trivial testbar und für Nala/Rosa-Adapter wiederverwendbar|Tests injizieren simple async-lambdas statt monkey-patching|Nachteil: der Adapter (`router.py::handle_telegram_update`) muss alle Deps explizit zusammenstecken — etwas Boilerplate, aber explizit ist hier besser als magic
- Pipeline-Result statt nackter OutgoingMessage (P174): `process_message` liefert `PipelineResult{message, reason, intent, effort, needs_hitl, guard_verdict, sanitizer_findings, llm_latency_ms}` statt direkt `OutgoingMessage | None`|Vorteil: Caller (Adapter) kann Logging + Telemetrie konsistent bauen, Tests asserten auf strukturierte Felder statt durch String-Inhalte zu fummeln|`reason` als Discriminator (`ok`/`sanitizer_blocked`/`llm_unavailable`/`guard_block`/`empty_input`/`empty_llm`) macht Verzweigungen explizit
- Photo-Bytes-Lazy-Resolve (P174): der Adapter füllt `attachments=[]` und legt nur `photo_file_ids` in `metadata` ab|das Resolven via `get_file_url` braucht `bot_token` + async + ggf. Vision-Modell-Auswahl — gehört NICHT in `translate_incoming` (die Methode soll synchron + idempotent + ohne Netzwerk sein)|Pattern: in `translate_incoming` nur das was OHNE I/O machbar ist, alles andere Lazy-Resolve im Vision-Pfad
- Photo-Bytes-Lazy ≠ HitL-Lazy (P174): die HitL-Background-Tasks sind echte Sideeffects mit `asyncio.create_task` — die lassen sich NICHT in eine `OutgoingMessage` packen|für den Phase-E-Cutover (P175) muss der Adapter explizit eine "Background-Aktion"-Capability bekommen (z.B. `OutgoingMessage.deferred_actions: list[Callable]` oder ein zweites Adapter-API)|in P174 bewusst NICHT gelöst — diese Komplexität gehört in den nächsten Patch wenn der Cutover ansteht
- Trust-Mapping konservativ (P174): TelegramAdapter mappt `private+admin_chat_id` → ADMIN, aber ein Admin der in einer Gruppe schreibt bleibt PUBLIC — die Gruppe ist ein öffentlicher Kontext, der Admin-Status der Person ändert daran nichts|wenn ein Admin in einer Gruppe etwas Admin-Spezifisches tun will (z.B. Bot-Settings ändern), gehört das per DM erlaubt zu werden, nicht per Trust-Eskalation in der Gruppe
- Adapter-Send raised statt schweigt (P175): NalaAdapter.send raised `NotImplementedError` mit konkretem Hinweis auf SSE/EventBus, RosaAdapter raised mit Hinweis auf das Trust-Boundary-Diagramm|Alternative wäre `return False` — aber dann wundert sich der Caller warum nichts ankommt und sucht den Fehler an der falschen Stelle|raised mit Begründung ist deutlich teurer im Debugging (Crash) aber ehrlicher in der Architektur-Aussage ("dieser Pfad existiert nicht aus gutem Grund")
- Trust-blinde Checks trotz Trust-Mapping (P175): HuginnPolicy moduliert `severity` per Trust-Level, aber die Checks selbst (Rate-Limit, Sanitizer) feuern für alle Trust-Stufen gleich|Begründung: ein Admin der sich in eine Loop schreibt soll genauso ratelimitet werden wie ein Random-User — der Trust-Status ändert nichts am Schutz vor sich selbst|Trust-Mapping ist für SEVERITY/AUDIT, nicht für BYPASS
- Findings ≠ Eskalation in der Pre-LLM-Schicht (P175): HuginnPolicy reicht Sanitizer-Findings ohne `blocked` nur durch, eskaliert NICHT auf DENY|sonst rotten WARNUNG-Patterns: jeder Pattern-Treffer würde den Pre-Pass blockieren, und das LLM-Guard (semantische Schicht 4) bekäme keine Chance mehr Kontext zu liefern|Determinismus dominiert Semantik HEISST: deterministisch BLOCKEN nur wenn deterministisch SICHER, sonst durchlassen und semantisch entscheiden lassen
- HitL-Check braucht parsed_intent — sonst kein Check (P175): der HitL-Pre-Check in HuginnPolicy ist OPTIONAL und greift nur wenn der Caller einen `parsed_intent` mitgibt|ohne wäre die Reihenfolge falsch: HitL-Policy basiert auf dem Intent-Header der LLM-Antwort, also POST-LLM-Call|wer den HuginnPolicy.evaluate vor dem LLM-Call ruft hat keinen Intent — und das ist ok, der HitL-Check passiert dann eben in der Pipeline-Stage NACH dem LLM-Call (zwei Pässe der gleichen Policy mit unterschiedlichen Argumenten)
- Phase-Abschluss als expliziter Patch-Block (P175): Phase E wird in P175 explizit "abgeschlossen" — eine Tabelle in PROJEKTDOKUMENTATION listet alle Skelett-Dateien mit Status (✅ Implementiert / ⬜ Stub) und Patch-Nummer|der Sinn: spätere Patches müssen nicht raten ob noch ein Stück fehlt, der Status ist dokumentiert|Stubs (RosaAdapter) zählen explizit als "Phase E komplett" — die Implementierung ist Phase F, aber der Anker steht|Pattern wiederholbar: jede Phase endet mit einem ähnlichen Block in der Doku
- Cutover-Sperrgrund dokumentieren statt verstecken (P175): die ursprüngliche P175-Spec verlangte den Cutover (`process_update` → `handle_telegram_update`) — der wurde bewusst NICHT gemacht und die Begründung steht in der Patch-Doku ("~15 Tests monkey-patchen die Modul-Attribute")|wichtig: NICHT still scope-shrinken|wenn ein Patch weniger macht als die Spec verlangt, gehört das in die Patch-Doku mit Grund + wo die Migration stattdessen passiert|sonst denkt der nächste Reviewer der Patch sei unvollständig
- Adapter-Symmetrie ist okay-asymmetrisch (P175): Telegram-Adapter implementiert `send` (HTTP-POST an Bot-API), Nala-Adapter raised in `send` (SSE/EventBus statt Push), Rosa-Adapter raised komplett (Stub)|der `TransportAdapter`-Vertrag verlangt `send` als Methode, aber NICHT dass jede konkrete Implementierung sie sinnvoll erfüllen muss|`NotImplementedError` mit Begründung ist eine LEGITIME Implementierung — sie sagt "diese Operation existiert in diesem Transport nicht"|Alternative wäre ein zweites Interface (`PushTransport` vs. `PullTransport`) — overengineered für 3 Adapter, lieber eine raised-Methode
- Coda-Autonomie als kodifizierte Regel (P176): vor P176 landeten `docker pull`, `pip install` und Sandbox-Setup als „bitte ausführen, Chris"-TODOs in den Patch-Spec — das schleifte Setup-Schritte über Wochen mit|Lösung: explizite Sektion „Coda-Autonomie" in CLAUDE_ZERBERUS.md mit harter Faustregel „Coda übernimmt ALLES was er kann", und Eskalation NUR bei physisch Unmöglichem (Auth, Hardware, UX-Gefühl)|Effekt sofort sichtbar: P171 baute die Sandbox-Logik, schickte aber „bitte docker pull ..." als Checkliste — P176 hat die Pulls in Block 1 selbst gemacht, in der gleichen Session in der die Regel niedergeschrieben wurde|Pattern wiederholbar: jede Setup-Aktion die Coda technisch ausführen kann (docker, pip, curl, npm, python -m) gehört NICHT als TODO in die Patch-Doku, sondern wird ausgeführt
- Pre-existing Failures ≠ neue Bugs (P176): der Patch-Prompt sprach von „122-134 Failures pre-existing" — Annahme war Cluster A/B/C (Event-Loop, sys.modules-Pollution, Singleton-Drift)|tatsächliche Verteilung: 105 E2E-Tests ohne Server (Loki/Fenrir/Vidar) + 9 Live-Guard-Tests (OpenRouter-Mistral) + 2 echte Bugs (1 Slice-Window, 1 Mistral-Indeterminismus)|Lehre: bevor man Test-Infrastruktur refactored, das volle Failure-Listing einmal manuell sortieren — die Hypothese vom Patch-Prompt kann breit daneben liegen|„Event loop is closed" sah aus wie Cluster A, war aber ein Pytest-Thread-Warning aus aiosqlite-Cleanup, nicht ein Test-Failure
- pytest.ini `addopts -m` lässt sich per CLI overriden (P176): `addopts = -m "not e2e and not guard_live"` als Default-Skip führt nicht dazu dass `pytest -m e2e` leer wird — der zweite `-m` auf der Kommandozeile gewinnt (argparse last-wins-Semantik)|damit ein einziger Schalter („Default-Run sauber") die Marker-Selektion regiert, OHNE dass die Opt-In-Pfade (`pytest -m e2e`, `pytest -m guard_live`) Sonderhandling brauchen|Vorteil gegenüber `pytest_collection_modifyitems`-Hook: der Marker-Filter ist in der Config sichtbar, nicht in einem Hook versteckt
- E2E-Marker als Pflicht-Hygiene für jeden Server-abhängigen Test (P176): jeder Test der Playwright/Selenium/HTTP-Calls gegen `https://127.0.0.1:5000` macht braucht `pytestmark = pytest.mark.e2e` oder `@pytest.mark.e2e` als Klassen-/Funktions-Marker|sonst landen sie im Default-Run und scheitern reihenweise mit „Connection refused"|Folgekosten: jede neue UI-Test-Datei (Loki-Sweep, Fenrir-Chaos, Vidar-Smoke) muss den Marker setzen — Pattern dokumentiert in CLAUDE_ZERBERUS.md unter Coda-Autonomie
- Hardcoded Slice-Windows in Source-Match-Tests rotten (P176): Test-Pattern „source.split('function X')[1][:6000]" verfehlt das Ziel sobald die Source weiterwächst|P148 setzte 6000, P169/P170 ergänzten Code in `renderDialectGroups`, der Slice verfehlte `delete-entry` ab P170 um 69 Zeichen|Bessere Patterns: (a) ohne Slice arbeiten und auf `'delete-entry' in (rest := source.split('function X')[1].split('function ', 1)[0])` (Funktions-Block bis zum nächsten `function`), oder (b) Slice mit großzügiger Reserve (10000+) und zusätzlicher Funktions-Ende-Erkennung|Lehre: jede „magic number"-Slice ist eine Zeitbombe, die beim nächsten Patch im Bereich crasht
- Pytest-Thread-Warnings sind kosmetisch, nicht fatal (P176): `aiosqlite`-Background-Threads versuchen `call_soon_threadsafe` auf einem Loop, den `asyncio.run()` bereits geschlossen hat → `PytestUnhandledThreadExceptionWarning` mit „Event loop is closed"-Stacktrace|Test selbst grün, weil die Background-Cleanup-Task von der Hauptassertion nicht aufgewartet wird|Filter via `filterwarnings = ignore::pytest.PytestUnhandledThreadExceptionWarning` in `pytest.ini` macht den Output sauber, ohne den Test-Pfad zu ändern|nicht jeder Stacktrace ist ein Bug — manche sind Lifecycle-Race-Conditions ohne Konsequenz
- Cutover via Feature-Flag statt Big-Bang (P177): `process_update` wurde NICHT durch eine umgeschriebene Variante ersetzt — der bisherige Body lebt 1:1 als `_legacy_process_update` weiter, das neue `process_update` ist eine 6-Zeilen-Weiche|Vorteil 1: alle Module-Level-Monkey-Patches in den Tests (`send_telegram_message`, `call_llm`, `_run_guard`, `_process_text_message`) funktionieren weiter, weil Legacy dieselben Funktionen aufruft — keine 15 Tests gleichzeitig kaputt|Vorteil 2: Chris kann live umschalten (`use_message_bus: true/false` in config.yaml + uvicorn `--reload`) ohne Restart, und bei Problemen sofort zurück|Vorteil 3: Default `false` ändert für Bestandsnutzer NICHTS — der Patch ist neutral wenn niemand das Flag dreht
- Legacy-Pfad als Fallback ist KEIN Dead-Code (P177): `_legacy_process_update` wird auch bei `use_message_bus: true` weiterhin aufgerufen — `handle_telegram_update` delegiert komplexe Pfade (Callbacks, Photos, Channel-Posts, Edited-Messages, Gruppen-Chats) per Early-Return an Legacy zurück|Begründung: HitL-Callback-Resolve, Vision-Pipeline, autonomer Gruppen-Einwurf sind Telegram-spezifisch und passen nicht in die transport-agnostische `core.pipeline` (DI-only, text-only)|Pattern: bei einem Architektur-Cutover NICHT versuchen alle Pfade in einer Operation zu migrieren, sondern den linearen Happy-Path durch die neue Architektur schicken und alle Sonderfälle als Delegations-Returns markieren — die Sonderfälle wandern später schrittweise nach
- Per-Call-Feature-Flag-Read statt Singleton-Cache (P177): `pipeline_cfg = settings.modules.get("pipeline", {})` direkt im Router, kein gecachter Wert|Test-Pattern beweist Live-Switch: drei aufeinanderfolgende Calls mit unterschiedlichem Flag treffen unterschiedliche Pfade|Alternative wäre Settings-Cache mit Invalidate-on-Write — overengineered für ein Bool, der per uvicorn `--reload` ohnehin neu geladen wird|Lehre: für Feature-Flags die selten gesetzt werden lieber pro Call lesen als ein Cache-Invalidierungs-Protokoll bauen
- Stable-API ≠ Implementierungs-Detail (P177): `process_update` bleibt der einzige Caller-Entry — Webhook-Endpoint, Long-Polling-Loop und Tests rufen unverändert dort an|`_legacy_process_update` und `handle_telegram_update` sind Implementierungen, austauschbar via Flag|wichtig: KEIN externer Code soll direkt `handle_telegram_update` aufrufen — der Cutover-Patch in P177 hat eine alte P174-Doku verändert die suggerierte „neue Caller dürfen direkt rufen", weil das die Migrations-Strategie zerstört (jeder Caller wäre ein eigener Cutover-Punkt)
- Test-Doppel-Strategie für Cutover (P177): `test_cutover.py` mockt `_legacy_process_update` und `handle_telegram_update` als Capture-Funktionen, dann verifiziert es welcher Pfad pro Update-Typ aufgerufen wurde|Vorteil: KEIN echter LLM/Guard/HTTP-Call nötig, jeder Test in <100ms|Nachteil: testet nur die Weiche, nicht das End-to-End-Verhalten — dafür gibt's separate Pipeline-Tests (P174 `test_pipeline.py`) und Adapter-Tests (P174 `test_telegram_adapter.py`)|Pattern wiederholbar für jede Architektur-Weiche: Capture-Mock auf beide Pfade, dann Routing-Verhalten verifizieren — End-to-End separat halten

## Live-Test-Erkenntnisse Patch 178 (Huginn-Selbstwissen)
- L-178a Guard kennt RAG nicht|Guard-Call (Mistral Small 3) prüft LLM-Antworten ohne Wissen über RAG-Inhalte|Korrekte RAG-Antworten (z.B. "Patch 178", "981 Tests") werden als Halluzination geflaggt|Fix: Guard-Call braucht RAG-Chunks als Referenz-Kontext (stateless bleiben)
- L-178b Guard kennt Persona nicht|Wenn Persona in Hel geändert wird, weiß der Guard nichts davon|Persona-konforme Antworten könnten als verdächtig geflaggt werden|Fix: Guard-Call liest aktive Persona aus Settings-Cache (`@invalidates_settings` existiert bereits)
- L-178c Intent-Router ADMIN zu sensitiv|Fragen ÜBER das System ("Wie ist das aufgebaut?") werden als ADMIN-Intent klassifiziert statt als FRAGE/WISSEN|ADMIN erzwingt immer HitL → Reibung im Live-Betrieb|Fix: ADMIN-Intent-Schwelle erhöhen, Unterscheidung „Frage über System ≠ Befehl an System"
- L-178d system-Kategorie fehlt im Hel-Frontend-Dropdown|Backend (`_RAG_CATEGORIES` + `CHUNK_CONFIGS`) hat `system` seit P178|Frontend-Dropdown zeigt es nicht → Upload nur per curl möglich|Fix: `system` in Hel-Frontend-Dropdown aufnehmen
- L-178e Telegram-Bot öffentlich erreichbar|Jeder mit dem Bot-Usernamen kann DM starten und OpenRouter-Credits verbrennen|Fix: Allowlist in config.yaml (`allowed_user_ids`), unbekannte User werden still ignoriert (kein Error, einfach keine Antwort)
- L-178f Coda-Server blockiert Port 5000|Coda startet eigenen Uvicorn als Background-Task → start.bat kann nicht starten|Fix: `start.bat` killt ALLE Prozesse auf Port 5000 vor uvicorn-Launch (`netstat -ano | findstr :5000` + `taskkill /F /PID`)|Bereits in start.bat seit P178
- L-178g Sprachnachrichten in Telegram-DM funktionieren nicht|Voice-Message an Huginn im DM → keine Antwort, kein Log-Eintrag|Status: Voice-Handler greift nur in Gruppen oder fehlt komplett|TODO: Handler-Pfad untersuchen (mutmaßlich in `telegram_router._process_voice_message`)
- L-178h LLMs wissen nicht welches Modell sie sind|DeepSeek V3.2 sagt manchmal er sei GPT (Trainingsdata-Kontamination)|Persona + RAG überschreiben schwaches Trainings-Echo komplett|Konsequenz: Dynamische Runtime-Infos (aktives Modell, GPU-Status, Patch-Version) sollten LIVE aus Config gelesen werden, nicht aus statischem RAG indiziert werden (sonst veraltet)

## Guard + RAG-Kontext (P180)
- Guard muss wissen, was dem Antwortenden als Referenz zur Verfügung stand|Sonst: jede RAG-basierte Antwort liest sich für den Guard wie erfundene Fakten ("woher kommt 'Tailscale'? steht nicht in der User-Frage")|Fix in P180: `check_response(rag_context=...)` reicht den RAG-Lookup-String an den System-Prompt durch, mit explizitem Marker "Fakten aus diesem Referenz-Wissen sind KEINE Halluzinationen"|Truncation bei 1500 Zeichen schützt das knappe Mistral-Small-Token-Budget — der Guard braucht keine vollen Chunks, nur genug um Material wiederzuerkennen
- Persona-Cap im Guard-Context von 300 → 800 Zeichen erhöht (P180)|300 reichte nicht: bei längeren Personas (Huginn-Raben-Persona ≈ 600 Zeichen) wurde die Hälfte abgeschnitten, der Guard sah nur die ersten paar Zeilen und hatte keinen Zugang zu Verhaltens-Klauseln in der unteren Hälfte|800 ist ein harter Trade-off: noch im Mistral-Small-Token-Budget, aber genug für realistisch große Personas|Truncation-Marker `[... Persona gekuerzt]` macht das Limit transparent
- Dual-Placement vs. Duplikation (P180)|RAG-Kontext steht jetzt sowohl im User-Prompt (P120, als Faktenmaterial mit Cap 2000) als auch im System-Prompt (P180, als Halluzinations-Whitelist mit Cap 1500)|Tokens werden minimal redundant verbraten, aber: User-Prompt + System-Prompt erfüllen unterschiedliche Rollen — User-Prompt ist Diskussions-Material ("hier ist Kontext"), System-Prompt ist Verhaltens-Anweisung ("treat these facts as legitimate")|Den Guard auf nur eine Stelle zu reduzieren würde diese Trennung verlieren

## Telegram-User-Allowlist (P181)
- Allowlist mit leerer `allowed_users`-Liste = alle erlaubt (Safety-Fallback)|Sonst wäre eine vergessene Liste in `mode: allowlist` ein Total-Lock-Out — niemand kommt rein, auch der Admin nicht (wenn er nicht selbst die Liste pflegt)|Wer wirklich nur den Admin will, soll auf `mode: admin_only` umstellen|Logging-Konsequenz: leere Liste loggt KEINEN Block, weil intern als "open" behandelt — sieht in Logs aus, als wäre die Allowlist inaktiv (ist sie effektiv auch)
- Admin ist im allowlist-Mode IMMER erlaubt, unabhängig von der Liste|Gegen-Beispiel: Chris pflegt die Liste, vergisst seine eigene ID einzutragen — in `admin_only` lock-out, in `allowlist` ohne Admin-Override auch|Lösung: der Allowlist-Check vergleicht `user_id` zusätzlich gegen `admin_chat_id` und durchläuft bei Match|Tests `test_allowlist_mode_admin_always_allowed` als Regression-Schutz
- Absage-Rate-Limit (1/h pro User) gegen Telegram-Outbound-Rate (P181)|Wenn ein gesperrter User 100 Nachrichten in einer Minute schickt, würden sonst 100 Absage-Sends an Telegram gehen|Telegram throttled bei ~30 msg/sec global → unsere "freundlichen" Absagen würden uns selbst rate-limiten (echte Antworten an andere User würden verzögern)|Module-state `_denied_users_last_notice: dict[int, float]` mit Hour-Window — minimaler Memory-Footprint (1 Eintrag pro denied User pro Stunde)
- Allowlist VOR Rate-Limit, VOR Sanitizer, VOR RAG, VOR LLM (P181)|Reihenfolge ist kostenkritisch: jeder Schritt nach Allowlist verbrät Tokens/CPU für jemanden, der gar nicht erst da sein dürfte|OpenRouter-Tokens für denied User wären die schlimmste Kategorie (Geld direkt verbrennen)|Pattern: alle "Pre-Flight"-Filter (Allowlist, Channel-Filter, Edited-Filter, Unsupported-Media) gehören VOR alle "Processing"-Schritte (RAG, LLM, Guard)
- Gruppen-Chats sind Allowlist-frei (P181)|Begründung: Gruppen sind Tailscale-intern, dort entscheidet die Gruppen-Mitgliedschaft den Zugang|Außerdem: autonome Gruppen-Einwürfe haben keinen "Requesting User" — die Allowlist wäre nicht anwendbar|Implementiert via `chat_type == "private"`-Check, sodass `supergroup`/`group` durchläuft

## ADMIN-Intent Plausibilitäts-Heuristik (P182)
- ADMIN-Verdict wird auf CHAT downgegradet wenn der User-Text keine Admin-Marker hat (P182)|Vorher klassifizierte das LLM auch "Wie geht's dir?" als ADMIN, was den HitL-Button auslöste — nervt mehr als es schützt (False-Positive-Rate hoch)|Heuristik: Slash-Prefix (`/status`, `/help`) ODER Admin-Keyword (`status`, `restart`, `config`, ...) → bleibt ADMIN, sonst → CHAT|Backward-Compat: Plausi-Check greift nur wenn der Caller `user_message` mitgibt — alle bestehenden Tests bleiben grün, nur der Telegram-Router profitiert (er gibt user_msg explizit mit)
- Token-Match statt Substring-Match für Admin-Keywords (P182)|`"stat" in text` würde "vorstats" matchen (false positive); `\b(stat|status|...)\b` matcht nur ganze Wörter|Implementiert via `re.findall(r"[a-zäöüß]+", text)` + `set & ADMIN_TOKENS`|Test `test_admin_keyword_substring_matched_nicht_falsch` als Regression-Schutz mit "Voraussetzung" (enthält "auss", aber kein Admin-Token)
- ADMIN-Intent-Schwelle ist sicherheitskritisch: lieber False-Negative als False-Positive (P182)|Wenn die Heuristik einen echten Admin-Befehl als CHAT downgraded, kommt halt eine Chat-Antwort statt HitL-Button — der User wiederholt mit `/status` und ist dann unmissverständlich|Wenn die Heuristik bei jedem Smalltalk einen HitL-Button feuert, ist die Reibung permanent|False-Positive-Kosten >> False-Negative-Kosten in dieser Achse

## Unsupported-Media-Handler (P182)
- Voice/Audio/Sticker/Document/Video → freundliche Absage statt lautloses Verschlucken (P182)|Vorher fiel ein Voice-Message-Update durch alle Filter und kam in `_process_text_message` als "empty" raus — der User sah keine Reaktion und wartete ins Leere|Jetzt: kurze Erklärung "kann ich noch nicht verarbeiten" + `reply_to_message_id` damit die Absage als Antwort auf die Voice-Message erscheint|Telegram-API-Detail: `message.voice`/`message.audio`/`message.sticker` etc. sind eigene Top-Level-Felder, nicht in `message.text`/`caption`
- Photo bleibt UNTERSTÜTZT (Vision-Pfad), trotz strukturell ähnlich zu Voice|Photo geht über `image_urls` an das Vision-Modell und kommt als normale Antwort raus|Wichtig in `_detect_unsupported_media`: NICHT auf `photo` matchen, sonst killt der Filter den Vision-Pfad (Regression-Risiko: einer könnte Photo "der Konsistenz halber" in die UNSUPPORTED_MEDIA-Liste schreiben)|Test `test_photo_returns_none` als Regression-Schutz
- Position des Media-Filters: NACH Allowlist, VOR Rate-Limit (P182)|NACH Allowlist verhindert dass denied User die freundliche Voice-Erklärung bekommen (würde Bot-Existenz an gesperrten User leaken)|VOR Rate-Limit damit ein Voice-Spammer die freundliche Erklärung bekommt statt "Sachte Keule"-Reply (bessere UX)|Trade-off: Voice-Spam kann den Bot dazu bringen, 100x die freundliche Absage zu senden — kein Schutz hier außer Telegram's eigenes Outbound-Limit, aber 100 freundliche Absagen sind weniger schlimm als 100 LLM-Calls

## Architektur-Mismatch zwischen Patch-Spec und Code (P182)
- Patch-Spec sprach von "ADMIN-Confidence ≥ 0.85", aber der Intent-Parser liefert keine Confidence (P182)|Architektur: das LLM emittiert harte Labels (`{"intent": "ADMIN", "needs_hitl": true}`), keine Wahrscheinlichkeiten|Konsequenz für die Umsetzung: das Konzept "höhere Schwelle" wurde umgedeutet auf "höhere Plausibilität" — Heuristik-basierter Downgrade statt numerischer Threshold|Lehre: bei Patches die eine Architektur voraussetzen die nicht existiert, lieber pragmatisch reinterpretieren als vortäuschen die Architektur sei da|Im Patch-Body explizit dokumentieren, dass die Spec angepasst wurde + Begründung

## Prosodie / Audio (P188-191)
- Whisper verwirft Prosodie (Pitch/Tempo/Stress)|Parallel-Pipeline mit Gemma E2B noetig|Whisper NICHT ersetzen (spezialisiert auf Wort-Erkennung, schlecht in Stimm-Analyse)
- Gemma 4 E2B vs E4B: identischer Audio-Encoder|Decoder-Unterschied nur fuer tiefes Reasoning|fuer Prosodie-Extraktion reicht E2B (3.4 GB Q4_K_M)|DeepSeek uebernimmt die Interpretation der Prosodie-Daten im Chat-Kontext
- llama.cpp Audio-Backend: CLI (`llama-mtmd-cli`) funktioniert JETZT|Server-API (Issue #21868) noch offen — `input_audio` Content-Block fehlt in `llama-server`|Dual-Path-Client (`GemmaAudioClient.mode = none/cli/server`) abstrahiert beides, Switch in einer Property
- Prosodie ist Opt-In (Worker-Protection)|Audio-Bytes leben nur im Request-Scope|tmp-Datei sofort `unlink()`|kein Schreiben in `interactions`-Tabelle|Consent per Header `X-Prosody-Consent: true` (P191)|Hel-Admin sieht nur Aggregate (Counter+Modus), KEINE individuellen mood/valence/arousal-Werte

## RAG / FAISS (P187+)
- DualEmbedder aktiv seit P187|DE: `T-Systems-onsite/cross-en-de-roberta-sentence-transformer` (GPU)|EN: `intfloat/multilingual-e5-large` (CPU)|Flag `modules.rag.use_dual_embedder` (Default `false` in `config.yaml.example`, lokal seit 2026-05-01 `true`)|MiniLM-Pfad bleibt als Fallback erhalten
- `migrate_embedder.py`: `--dry-run` zuerst|`--execute` mit automatischem Backup nach `data/backups/pre_patch129_*`|Sprache wird per `detect_language` aus Dokument-Inhalt erkannt, NICHT aus Prompt|Dimensions-Mismatch zwischen DE/EN-Modellen bleibt im jeweiligen Index gekapselt

## Frontend (P186+P192)
- Auto-TTS triggert NACH `addMessage(reply, 'bot')` (entspricht SSE-done-Moment im non-streaming Pfad)|nicht pro Chunk|Audio-Stop bei: `loadSession`, `doLogout`, `handle401`, Toggle-OFF|`window.__nalaAutoTtsAudio`-Pattern analog SSE-Watchdog
- Sentiment-Triptychon (P192): drei Chips pro Bubble — BERT 📝 (Text), Prosodie 🎙️ (Stimme), Konsens 🎯 (Fusion)|Mehrabian-Regel: bei `confidence > 0.5` dominiert Prosodie, sonst Fallback auf BERT|Inkongruenz-Erkennung 🤔 wenn BERT positiv und Prosodie-Valenz negativ (Text sagt gut, Stimme sagt schlecht)|User-Bubbles links unten, Bot-Bubbles rechts unten|44px Touch-Targets|`build_sentiment_payload(text, prosody, bert_result)` in [`utils/sentiment_display.py`](zerberus/utils/sentiment_display.py)
- Triptychon-Daten kommen ADDITIV in `/v1/chat/completions`-Response (`sentiment.user` + `sentiment.bot`)|OpenAI-Schema bleibt formal|Clients die nur `choices` lesen ignorieren das neue Feld

## Whisper-Endpoint Enrichment (P193)
- `/v1/audio/transcriptions` Response erweitert: `text` bleibt IMMER (Backward-Compat fuer Dictate, SillyTavern, Generic-Clients)|optional `prosody` (P190) und `sentiment` mit `bert.{label,score}` + optional `consensus.{emoji,incongruent,source}`|`compute_consensus(bert_label, bert_score, prosody)` zentrale Helfer-Funktion fuer alle Pfade
- `/nala/voice` JSON-Response identisch erweitert + zusaetzlich SSE-Events `event: prosody` und `event: sentiment` ueber `/nala/events`|Frontend-Triptychon kann via JSON-Response (sync) ODER SSE-Events (async) gespeist werden|named SSE-Events brauchen `event: <name>\ndata: <json>\n\n` Format, nicht das default-`data:`-only Pattern
- Fail-open Pattern: BERT-/Konsens-Fehler darf den Audio-Endpoint nicht abbrechen|`try/except` mit Logger-Warnung, `sentiment`-Feld bleibt einfach weg|nur Whisper-Fehler ist hart (P190-Erbe)

## Datei-Storage / Multipart-Upload (P196)
- Beim Schreiben von Upload-Bytes IMMER atomar: `tempfile.mkstemp(dir=target.parent)` + `os.replace(tmp, target)`|`tempfile.mkstemp` muss IM Ziel-Ordner liegen, sonst macht `os.replace` einen Cross-Volume-Move statt atomarem Rename|Server-Kill mitten im Schreiben hinterlaesst dann hoechstens ein `.upload_*.tmp`, kein halbgeschriebenes Ziel|Im Fehlerfall Temp-File im `except` aufraeumen, sonst sammelt sich Muell
- Storage-Pfad-Funktionen in Endpoint-Code MUSS man als Funktion (nicht Modul-Konstante) anlegen, damit Tests sie monkeypatchen koennen — `monkeypatch.setattr(mod, "_get_storage_base", lambda: tmp_path)` ist sauberer als ein Settings-Override mit Cache-Invalidierung
- SHA-Dedup-Schutz beim Delete: Wenn mehrere Owner denselben Inhalt referenzieren koennen, MUSS der Delete-Endpoint VOR dem Bytes-Loeschen `count_sha_references(sha, exclude_file_id=...)` aufrufen|`count > 0` → nur DB-Eintrag weg, Bytes bleiben|`count == 0` → Bytes + Eintrag weg|verhindert dass Owner A versehentlich Bytes loescht, die Owner B noch braucht|Response-Feld `bytes_removed: bool` macht die Entscheidung fuer den Client transparent
- Filename-Sanitierung mit drei Achsen: Path-Traversal (`..`-Komponenten strippen), absolute Pfade (leading `/` weg), leere Segmente (double-slash `//` collapsen)|Backslash auf Forward-Slash normalisieren, weil Windows-Clients `\` schicken|Wenn nach Sanitierung nichts uebrig bleibt → `ValueError`, nicht Default-Name vergeben — der Caller soll 400 zurueckgeben
- Cross-Project-Delete-Schutz: `delete_endpoint(project_id, file_id)` MUSS `file_meta["project_id"] == project_id` pruefen|Sonst kann ein Angreifer per ID-Raten Files anderer Projekte loeschen|`get_file(file_id)` allein reicht nicht — der Owner-Check ist explizit
- Frontend-Massen-Uploads: Sequenziell (`for + await`) statt `Promise.all`|Parallele XHRs ueberrennen den Server bei 50-Datei-Drops und die Progress-Anzeige wird unleserlich|Trade-Off: 50 × 100 ms = 5s Mehrkosten, fuer Admin-UI akzeptabel|`xhr.upload.addEventListener('progress', ...)` pro Datei statt globaler Counter — Nutzer sehen welche Datei gerade laeuft
- Drop-Zone-Events nur EINMAL verdrahten (Flag `_dropWired` o.ae.)|`addEventListener` in einer Funktion, die mehrfach aufgerufen wird (z.B. nach Tab-Wechsel) → handler-Liste waechst, Memory-Leak + Doppel-Triggers|Stattdessen `data-*`-Attribute auf dem DOM-Knoten setzen, Handler liest die Werte per `dataset.*`
- Extension-Blacklist gehoert in den Defaults der `Config`-Klasse (Pydantic), nicht nur in `config.yaml`|`config.yaml` ist gitignored — frisch geklontes Repo waere ohne Schutz|Override per `config.yaml` weiterhin moeglich, aber der Default greift IMMER
- Composite-FK + Cross-Reference-Storage gilt nicht nur fuer Project-Files: dasselbe Pattern (mehrere Owner, Inhalts-Dedup, Delete-Schutz) tritt bei Avatar-Uploads, geteilten Anhaengen, Wiki-Attachments etc. auf — der Reference-Counter ist universeller Schutzbaustein

## 🪦 Der Schwarze Bug — Post Mortem (P109 → P153 → P169 → P183)

Der hartnäckigste Bug in Zerberus. Vier Patches, drei Fehldiagnosen, ein Endgegner.

**Symptom:** Nach Kaltstart (Browser zu, abmelden, neu einloggen) waren alle Bubbles schwarz. Unlesbar, hässlich, Jojo-Impact bei jedem Login.

**Chronologie der Fehlschläge:**
- P109: `resetTheme()` erweitert, rgba-Defaults → Bug kam wieder
- P153: `cssToHex()` HSL-Parser-Bug gefunden (H/S/L als RGB-Bytes) → Bug kam wieder
- P169: Favoriten-Loader schrieb schwarze Werte VOR dem Guard → Bug kam wieder

**Warum er immer wiederkam:** Jeder Fix adressierte EINEN Codepfad. Aber es gab 12 Stellen die `--bubble-*-bg` setzten, und 5 davon hatten KEINEN Guard. Zusätzlich waren die `BLACK_VALUES`-Listen simple String-Matches mit nur 4 Formaten — `hsl(0,0%,0%)`, `rgba(0,0,0,1.0)`, `rgb(0, 0, 0)` mit verschiedenen Whitespace-Mustern rutschten alle durch.

**Was ihn endgültig getötet hat (P183):**
1. Forensische Analyse BEVOR Code geändert wurde — alle 12 Pfade kartiert
2. Zentraler `sanitizeBubbleColor()` mit regex-basiertem `_isBubbleBlack()` statt String-Liste
3. ALLE 12 `setProperty`-Pfade durch den Sanitizer geschleift — kein Bypass möglich
4. `cleanBlackFromStorage()`-Sweep beim Login — korrupte Werte werden proaktiv entfernt
5. HSL-Slider: Lightness-Minimum auf 10% → fast-schwarz (`#1a1a1a`) wird auch abgefangen

**Universelle Lessons:**
- Bei wiederkehrenden Bugs NIEMALS an der Symptomstelle patchen|erst ALLE Codepfade inventarisieren
- String-basierte Blacklists sind brüchig|Regex oder semantische Prüfung (Luminanz < Schwelle) ist robuster
- „Ist gefixt" heißt nichts wenn nur EIN Pfad gefixt ist und es 12 gibt
- Init-Reihenfolge dokumentieren|Race Conditions zwischen IIFE/DOMContentLoaded/Login/Favoriten sind unsichtbar
- Defense-in-depth: Sanitizer an JEDER Setzstelle + proaktiver Storage-Sweep + Backend-Filter = drei Schichten

## Persona-Layer-Merge (P197)
- Mehrstufige Persona (System → User → Projekt) NICHT als monolithischen String aus 3 Quellen kombinieren|stattdessen einen markierten Block pro Layer dranhängen|`[PROJEKT-KONTEXT — verbindlich für diese Session]` als eindeutiger Marker → Substring-Check für Tests/Logs + Schutz vor Doppel-Injection in derselben Pipeline
- Pure-Function vs. DB-Schicht trennen|`merge_persona(base, overlay, slug)` als reine String-Funktion (synchron testbar, keine Mocks nötig)|`resolve_project_overlay(id)` als async DB-Resolver — getrennt, weil sonst der Helper zu testen unmöglich wird ohne tmp_db|Lazy-Import des `projects_repo` im Resolver gegen Zirkular-Importe (das Repo importiert selbst `database`, das wiederum andere Helper holt)
- Aktivierung über Header (`X-Active-Project-Id: <int>`) statt persistenter Spalte als ersten Schritt|kein Schema-Risiko, kein Migration-Risiko, der Frontend-Caller entscheidet pro Request|Persistente Auswahl (`active_project_id` an `chat_sessions`) ist später trivial nachrüstbar — der Header-Reader ist die einzige Stelle, die geändert werden muss
- Reihenfolge VOR `_wrap_persona`-Marker (P184), damit der `# AKTIVE PERSONA — VERBINDLICH`-Header auch das Projekt-Overlay umschließt|sonst stünde der Projekt-Block AUSSERHALB der "verbindlichen Persona" und das LLM könnte ihn entwerten|Source-Audit-Test verifiziert die Reihenfolge per Substring-Position (`idx_merge < idx_wrap`)
- Header-Case-Sensitivity-Falle: FastAPI's `Request.headers` (Starlette) ist case-insensitive, ein Test-`dict` aber nicht|Reader IMMER mit Lowercase-Fallback bauen, sonst grünt der Endpoint-Test aber das Unit-Test mit `dict` schlägt fehl|`headers.get("X-Active-Project-Id") or headers.get("x-active-project-id")`
- `tone_hints` aus User-Input case-insensitive deduplizieren|leere Strings/Whitespace strippen, Nicht-Strings filtern (`isinstance(item, str)`)|UI-Bugs schicken sonst `[42, None, "", "  ", "ok", "OK", "Ok"]` → Output `["ok"]` (erstes Vorkommen gewinnt, Schreibweise behalten)
- Defensive Behaviors für Header-basierte Auswahl: unbekannte ID → kein Crash, einfach kein Overlay|negativer/0/Buchstaben-Header → ignoriert (positive int-Constraint)|archiviertes Projekt → Slug trotzdem loggen, damit Bug-Reports diagnostizierbar bleiben (mit Slug kann Chris in der Hel-UI nachgucken warum aktiv aber archiviert)
- Telegram-Pfad NICHT verdrahten, wenn Persona-Welt fundamental anders ist|Huginn hat keine User-Profile und keine Projekt-Bindung — Project-Awareness braucht eigene UX (`/project <slug>`-Befehl)|als Doku-Punkt in HANDOVER+CLAUDE.md vermerken, nicht still überspringen, sonst sucht die nächste Instanz vergebens nach der Telegram-Verdrahtung

## Template-Generierung beim Anlegen (P198)
- Templates als regulaere `project_files`-Eintraege im SHA-Storage (nicht als separater Sonder-Pfad)|gleicher Storage-Layout wie Uploads → Hel-Datei-Liste, RAG-Index, Code-Execution-Pipeline sehen sie ohne Spezialfall|spart eine zweite Persistenz-Schicht und verhindert Drift zwischen "Template-Files" und "User-Files"
- Pure-Python-String-Templates statt Jinja|Bedarf ist trivial (zwei Files mit drei Variablen), Jinja-Dependency rechtfertigt sich nicht|Render-Funktionen synchron + I/O-frei → unit-bar ohne tmp_db, deterministisch via `now`-Parameter (kein `datetime.utcnow()`-Drift in Tests)
- Idempotenz via Existenz-Check auf `relative_path` VOR dem Schreiben|`list_files` einmal abrufen, dann pro Template-File `if rel in existing_paths: skip`|UNIQUE-Constraint waere der zweite Fallback, aber explizite Pruefung ist klarer und liefert keine `IntegrityError`-Exception, die der Caller abfangen muesste
- Best-Effort-Verdrahtung im Endpoint|Wenn Template-Generierung crasht (Disk-Full, DB-Lock), darf das Anlegen des Projekts NICHT abbrechen|`try/except Exception` mit `logger.exception` und leerem `template_files`-Feld in der Response|Source-Audit-Test mit Mock-Crash verifiziert das Verhalten
- Feature-Flag in der Pydantic-Settings-Klasse (Default `True`) statt as if-Statement im Code|Tests koennen den Flag-Wert pro Test umstellen, Migrations-Tools koennen ihn global abschalten|Default-Wert IM Modell, weil `config.yaml` gitignored — sonst fehlt das Default nach `git clone`
- Bestehende File-Count-Tests, die auf `count == N` pruefen, brechen bei automatischer Template-Generierung|Loesung: `disable_auto_template`-Autouse-Fixture in den betroffenen Test-Modulen, der das Flag global abschaltet|alternativ: Tests auf `count >= N` aufweichen, aber Autouse-Fixture haelt die Aussagekraft der Original-Tests intakt
- Helper-Modul ohne FastAPI-Import|Atomic Write als lokale Kopie aus dem Endpoint, nicht als Import|Vorteil: CLI-Migrations und zukuenftige Hintergrund-Jobs koennen den Helper benutzen, ohne den ganzen Web-Stack zu laden
- Git-Init NICHT halbgar nachruesten|SHA-Storage ist kein Working-Tree (Bytes liegen unter Hash-Pfaden, nicht unter `relative_path`)|Git-Init macht erst Sinn, wenn ein echter `_workspace/`-Layout existiert (mit Code-Execution-Pipeline)|als bewusste Auslassung in HANDOVER dokumentieren, nicht still uebergehen

## Per-Projekt-RAG-Index (P199)
- Pure-Numpy-Linearscan ist fuer kleine Per-Projekt-Indizes (≤10k Chunks) FAISS ueberlegen|`argpartition(-sims, k-1)[:k]` + `argsort` liefert exakt das gleiche Top-K wie ein FAISS-FlatL2-Search auf normalisierten Vektoren|Tests laufen dependency-frei (kein faiss-Mock), Setup-Overhead ist Null, Persistierung als `vectors.npy` + `meta.json` ist trivial|FAISS-Wechsel spaeter ist 10-Zeilen-Tausch in `top_k_indices` + `load_index`/`save_index`, Public-API bleibt
- Embedder als monkeypatchbare Funktion (`_embed_text`), nicht als Modul-Singleton|Tests `monkeypatch.setattr(projects_rag, "_embed_text", fake_embed)` und der echte SentenceTransformer wird nie geladen (kein 80MB-Download in CI)|Singleton lebt zwar im Modul (`_get_embedder` fuer den Production-Pfad), wird aber NIE direkt aus der Async-API gerufen — `_embed_text` ist die einzige Schnittstelle|Hash-basierter 8-dim-Pseudo-Embedder reicht fuer deterministische Top-K-Tests
- Per-Projekt-Index ISOLIERT vom globalen Index halten|projekt-spezifische Inhalte gehoeren NICHT in den globalen Memory-RAG, sonst tauchen sie in anderen Sessions als Falsch-Treffer auf|eigener Storage-Pfad (`<data_dir>/projects/<slug>/_rag/`), eigener Embedder-Pfad, eigenes Index-Schema|der globale Pfad bleibt fuer Cross-Session-Memory zustaendig
- Idempotenz pro `file_id`, nicht pro `sha256`|gleicher relative_path mit anderem Inhalt → neue file_id (oder geloeschtes + neu angelegtes File hat sowieso neue file_id)|sha-basierte Idempotenz bricht hier|loesung: vor dem Indexen alte Eintraege mit derselben file_id rauswerfen, dann neu indexieren|deterministischer Embedder macht Re-Index mit gleichem Inhalt zu einem No-Op-Resultat
- Trigger-Punkte als Best-Effort|Indexing-Fehler darf den Hauptpfad (Upload, Materialize, Delete) NICHT abbrechen — der DB-Eintrag und die Storage-Bytes muessen konsistent sein, der Index ist Cache|spaeterer Reindex-Call kann die Luecke schliessen|Pattern identisch zu P198 Materialize-Best-Effort
- Defensive Behaviors mit konkretem `reason`-Code statt boolean|`{"chunks": int, "skipped": bool, "reason": str}`-Status-Dict mit klaren Codes (`indexed`/`empty`/`binary`/`too_large`/`bytes_missing`/`no_chunks`/`embed_failed`/`rag_disabled`)|in Bug-Reports macht der Code klar, an welcher Stelle die Pipeline abgebrochen ist|Tests koennen pro Edge-Case gezielt pruefen
- Inkonsistenter Index → leere Basis ist sauberer als partial-Recovery|wenn nur `vectors.npy` ODER nur `meta.json` da ist, ist das Ergebnis ohne den anderen Teil unbrauchbar|Reset + WARN-Log ist die einzig saubere Antwort|der naechste Index-Call baut auf der leeren Basis auf
- Embedder-Dim-Wechsel zwischen Sessions toleriert|`top_k_indices` mit Dim-Mismatch liefert leeres Ergebnis statt Crash|`index_project_file` baut bei Dim-Mismatch im vstack-Versuch den Index neu auf (nicht fehlertraechtig erweitern)|damit ist Embedder-Switch ein Implementation-Detail, kein blocking-Event
- Atomic Write fuer JEDE persistierte Datei (vectors UND meta) via `tempfile.mkstemp` im Ziel-Ordner + `os.replace`|halbe Files nach Server-Kill werden durch die Inkonsistenz-Erkennung beim naechsten Load gefangen|kein Permanent-Schaden moeglich
- Position der Chat-Wirkung NACH allen Persona-/Runtime-/Decision-/Prosodie-Schichten, VOR `messages.insert(0, system)`|RAG-Block ist pro-Query-Kontext, keine Persona-Regel — er aendert sich pro User-Frage|der Build-Stack ist: `load_system_prompt → merge_persona (P197) → _wrap_persona (P184) → append_runtime_info (P185) → append_decision_box_hint (P118a) → inject_prosody_context (P190) → format_rag_block (P199) → messages.insert`|Source-Audit-Test verifiziert die Reihenfolge per Substring-Position
- RAG-Block-Marker eindeutig waehlen (`[PROJEKT-RAG — Kontext aus Projektdateien]`)|Substring-Check fuer Tests + Doppel-Injection-Schutz greift sauber|nicht "Kontext" oder "Files" oder andere generische Bezeichner — der Marker muss sich aus dem normalen Persona-Text klar abheben
- MiniLM-L6-v2 als Pragma-Default fuer Per-Projekt|Dual-Embedder (P187) waere konsistenter mit dem globalen Pfad, aber: zwei Modelle mit unterschiedlichen Dimensionen (768/1024) machen das Index-Schema komplizierter, DE-Modell ist GPU-only-Default → bricht CPU-only-Setups|MiniLM ist 384-dim, schnell, auf jedem Setup ohne extra Config lauffaehig|Trade-off Cross-lingual-Qualitaet akzeptiert, spaeterer Upgrade ist 10-Zeilen-Tausch in `_embed_text`
- Code-Chunker-Reuse statt Neubau|`code_chunker.chunk_code` (P122) liefert AST/Regex-basierte semantische Einheiten fuer .py/.js/.ts/.html/.css/.json/.yaml/.sql|fuer Prosa lokaler Para-Splitter mit weichen Absatz-Grenzen + Sentence-Fallback + hartem Char-Slice|bei Python-SyntaxError im Code-Pfad → automatischer Fallback auf Prose-Splitter (kaputte Files werden trotzdem indexiert, statt im Index zu fehlen)
