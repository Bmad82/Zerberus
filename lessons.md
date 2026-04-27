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
- Hel-UI fail-safe (P136): bei OpenRouter-Fehler in `get_balance()` graceful degradation|lokale Cost-History trotzdem|`balance` None|UI bleibt benutzbar ohne Netz

### Modellwahl + Scope
- Nicht automatisch das neueste/größte Modell|Sweet Spot pro Job: Guard=Mistral Small 3 (billig+schnell)|Prosodie=Gemma 4 E4B (Audio-fähig)|Transkription=Whisper (spezialisiert)
- Free-Tier-Modelle = Werbung, überlastet, schlechte Qualität|Paid bevorzugen
- Feature-Creep-Gefahr: Audio-Sentiment-Pipeline braucht eigenes Architektur-Dokument (`docs/AUDIO_SENTIMENT_ARCHITEKTUR.md`) damit Scope klar
- Bibel-Fibel: Token-Optimierungs-Regelwerk (im Claude-Projekt)|Potenzial für komprimierte System-Prompts an DeepSeek
- Patch-Nummern: keine freihalten für „Reserve"|lückenlos durchnummerieren|spätere Patches mit Suffix (127a etc.)
- Mega-Patches abspalten: Patch-Prompt mit 3+ unabhängigen Blöcken → Claude Code zieht nur einen sauber durch, Rest still abgeschnitten|KEIN Fehler = Kontext-/Attention-Budget-Mgmt|P157 hat nur Terminal gemacht, Persona+Guard kamen als P158|Restblöcke als eigenen Nachfolge-Patch sauber formulieren|Signal: Diff < erwartet + Test-Zahl < angekündigt → Rest übersprungen|explizit reaktivieren, nicht auf autonomes Nachholen hoffen
- Design-System früh einführen: `shared-design.css` hätte zwischen P122 und P131 schon|je später, desto aufwändiger Migration der Legacy-Styles|Token-Definitionen früh, auch wenn nicht alle Klassen genutzt
