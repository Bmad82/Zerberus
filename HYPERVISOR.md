# HYPERVISOR.md – Zerberus Pro 4.0
*Strategischer Stand für Hypervisor-Claude (claude.ai Chat-Instanz)*
*Letzte Aktualisierung: Patch 90 (2026-04-18)*

## Aktueller Patch
**Patch 90** – Aufräum-Sammelpatch: rag_eval HTTPS + Hel-Backlog (2026-04-18)
- Block A (Backlog 7): `rag_eval.py` auf HTTPS umgestellt — `BASE_URL` default `https://127.0.0.1:5000`, Self-Signed-Cert via `_SSL_CTX` (Hostname-Check + Verify aus), SSL-Context wird in `_query_rag()` an `urlopen()` durchgereicht. `BASE_URL` und `API_KEY` per Env-Var (`RAG_EVAL_URL`, `RAG_EVAL_API_KEY`) override-bar. Eval läuft jetzt ohne inline-Workaround.
- Block B (N-F09b): Hel bekommt Schriftgrößen-Wahl analog Nala — neue CSS-Var `--hel-font-size-base` (Default 15 px), 4 Presets (13/15/17/19) als 44 px-Touch-Buttons unter `<h1>`. Persistenz via `localStorage('hel_font_size')`, Early-Load-IIFE im `<head>` verhindert FOUC.
- Block C (N-F10): Defensiver Landscape-Fix für Nala UND Hel via `@media (orientation: landscape) and (max-height: 500px)` — Header-/Padding-Reduktion, Modals auf `90vh/dvh`, Input-Bar kompakter. Hel kommt durch das padding-basierte Scroll-Layout grundsätzlich klar.
- Block D (H-F02): WhisperCleaner-UI komplett von Roh-JSON-Textarea auf Karten-Liste umgestellt. Pattern/Replacement/Kommentar pro Regel, Comment-Only-Einträge als Sektions-Header gerendert, Trash-Button mit Confirm, „Regel hinzufügen" + „Kommentar/Sektion". Save-Pfad rekonstruiert das JSON aus dem DOM und blockiert bei ungültigem Regex (Pattern-Validierung mit JS-RegExp inkl. `(?i)`-Strip).
- Pflicht-Updates: HYPERVISOR/PROJEKTDOKUMENTATION/README/backlog_nach_patch83 fortgeschrieben. Backlog-Item 7 erledigt.

**Patch 89** – R-03 Cross-Encoder-Reranker (RAG zweite Stufe) (2026-04-18)
- Neues Modul `zerberus/modules/rag/reranker.py`: `rerank()` lädt `BAAI/bge-reranker-v2-m3` lazy, bewertet FAISS-Kandidaten mit voller Token-Attention, Fail-Safe fällt auf FAISS-Order zurück.
- `_search_index()` (rag/router.py): neue Parameter `query_text`, `rerank_enabled`, `rerank_model`, `rerank_multiplier`. FAISS over-fetched `top_k * 4` Kandidaten, filtert kurze Chunks, Reranker sortiert neu, trim auf `top_k`.
- Orchestrator `_rag_search()` reicht Rerank-Config an `_search_index` durch; L2-Threshold-Filter (`1.5`) wird bei aktivem Rerank übersprungen (eigene Relevanz-Metrik).
- Config: `modules.rag.rerank_enabled: true`, `rerank_model: BAAI/bge-reranker-v2-m3`, `rerank_multiplier: 4` (config.yaml + .example).
- **Eval-Delta: 4/11 JA → 10/11 JA** (P88: 4 JA / 5 TEILWEISE / 2 NEIN → P89: 10 JA / 1 TEILWEISE / 0 NEIN). **Q4 (Perseiden-Nacht) und Q10 (Verkaufsoffener Sonntag in Ulm) beide geheilt** — Rerank-Scores 0.942 / 0.929.
- Cold-Start: Modell-Download ~2.5 min (2.27 GB nach `~/.cache/huggingface/hub/`), dann Cache-Load ~3 s, Inference 200–300 ms/Query.
- Report: `rag_eval_delta_patch89.md`, `rag_eval_results.txt` neu.
- **Manueller Server-Restart** war wegen des neuen Imports (`reranker.py`) nötig — `--reload` hat ihn nicht selbst erkannt. Nur dieser eine Restart.

**Patch 88** – R-01 Residual-Chunk-Filter (Retrieval + Chunking) (2026-04-18)
- Fix A: `_chunk_text()` in hel.py merged Residual-Chunks unter `min_chunk_words=120` in den Vorgänger (Post-Processing-Pass nach kapitelaware Chunking)
- Fix B: `_search_index()` in rag/router.py: over-fetch `top_k*2`, filter Chunks unter `min_chunk_words`, trim auf `top_k`. Sicherheitsnetz für alte Indizes ohne re-build. Caller-Sites in orchestrator + /rag/search angepasst.
- Neue Config: `modules.rag.min_chunk_words: 120` (config.yaml + .example)
- Re-Index Rosendornen: 18 → **12 Chunks** (`merged_residuals=6`), kürzester Chunk ist Glossar (165w). Metadata enthält jetzt `word_count`-Feld für O(1)-Filter.
- Eval-Delta: **4/11 JA, 5/11 TEILWEISE, 2/11 NEIN — identisch zu Patch 87**. Avg L2 1.01 (P87: 0.97, +0.04 ist erwartet, kurze Chunks fehlen jetzt als „künstlich gute" Treffer).
- Erwartung „Q3/Q8 von TEILWEISE auf JA" **nicht eingetroffen** — der merged Titel+Prolog-Chunk (289w) und Akt-I-Chunk (672w) räumen Rang 1 statt Glossar. Embedding-Modell ist die Bremse, nicht Chunk-Größe.
- Q4/Q10 wie erwartet weiter NEIN — bestätigt R-03 (Cross-Encoder-Reranker) als nächsten Fix-Kandidat.
- Bonus: `start.bat` bekommt `--reload`-Flag (greift erst beim nächsten Start), damit künftige Patches keine manuellen Neustarts mehr brauchen.
- Report: `rag_eval_delta_patch88.md`, `rag_eval_results.txt` neu

**Patch 87** – RAG-Eval Durchlauf + Qualitätsanalyse (2026-04-17)
- Eval gegen 11 Fragen gefahren (Script unverändert, inline gegen HTTPS/API-Key wegen hartem `http://` in `rag_eval.py`)
- Ergebnis: **4/11 JA, 5/11 TEILWEISE, 2/11 NEIN**, Avg L2 Top-Treffer 0.97
- Harte Fails: Q4 (Perseiden-Nacht) und Q10 (Verkaufsoffener Sonntag in Ulm) — beide Begriffe **wörtlich im Dokument**, aber relevanter Chunk nicht in Top-5
- Chris' Hypothese "Glossar wird zerschnitten" widerlegt: Glossar ist intakter 165-Wort-Chunk mit allen Begriffen
- Tatsächliches Problem: 5 Residual-Tail-Chunks (5–64w) + 16-Wort-Titel-Chunk kapern bei unspezifischen Queries Rang 1 — MiniLM-Embedding bevorzugt kurze Chunks systematisch
- Keine Code-Änderungen in diesem Patch. 6 Backlog-Einträge R-01…R-06 in `backlog_nach_patch83.md` (Chunk-Länge-Filter, Embedding-Upgrade, Reranker, Query-Expansion, Sektion-Metadata, MMR)
- Report: `rag_eval_report_patch87.md` im Projektroot

**Patch 86** – Nala Hamburger + Settings-Overhaul (N-F05…N-F09) (2026-04-17)
- N-F05: Export aus Sidebar raus, neuer 💾-Icon-Button in der Top-Bar → Modal mit 3 Formaten (PDF via jsPDF-CDN, Text via bestehendem `exportChat()`, Zwischenablage mit `copyToClipboard()`-Fallback)
- N-F06: Abmelden aus der Top-Bar raus, dezent ins Hamburger-Menü (`.sidebar-action-btn-muted`, kein Rot-Alarm)
- N-F07: Bubble-Farben individuell einstellbar — 4 neue CSS-Vars (`--bubble-user-bg/text`, `--bubble-llm-bg/text`) mit Fallback auf Theme-Farben, Per-Picker- und Global-Reset
- N-F08: Hamburger-Menü (☰ Navigation: Archiv, Neue Session, Passwort, Abmelden) vs. neuer 🔧-Icon (Einstellungen: Theme, Bubble, Schrift, Favoriten) klar getrennt
- N-F09: Schriftgröße einstellbar — 4 Presets (13/15/17/19 px) via `--font-size-base` auf `.message` und `#text-input`
- Clipboard-Helper `copyToClipboard()` mit `document.execCommand('copy')`-Fallback (HTTP-LAN-Kontexte wie Tailscale-iPhone, siehe Patch 68) — `copyBubble()` nutzt ihn jetzt auch
- Favoriten-Slots v2: speichern Theme + Bubble + Schriftgröße als Ein-Klick-Set; alte v1-Favoriten (nur Theme) bleiben kompatibel
- Early-Load-IIFE im `<head>` erweitert → kein FOUC bei Bubble-Farben/Schrift nach Reload

**Patch 85** – UI-Bugs + Hel-Akkordeon + Metriken-Diagnose + RAG-Fix (2026-04-17)
- A1: Typing-Bubble — `llm_start` SSE-Event fehlte in legacy.py (nur im Orchestrator), jetzt in allen 3 Codepfaden emittet
- A2: Touch-Support — `:active` zu allen `:hover`-Regeln in nala.py + hel.py hinzugefügt (genereller Sweep)
- A3: Kostendelta — `_prevBalance` wird jetzt in `localStorage` persistiert (überlebt Page-Refresh)
- B: Hel Akkordeon-Layout — Tab-System ersetzt durch Akkordeon (Metriken offen, Rest eingeklappt, 44px Touch-Targets)
- C: BERT-Sentiment-Dämpfung — `_compute_sentiment()` nutzt gedämpfte Konfidenz statt Rohwerte (verhindert 0/1-Extreme)
- D: **RAG-Fix (KRITISCH)** — Skip-Logik war OR-verknüpft: `intent==CONVERSATION OR (kurz AND kein ?)` → skippte auch QUESTION-Intent. Jetzt AND-verknüpft. Betrifft legacy.py UND orchestrator.py

**Patch 84** – Hel Bugfixes: Preise, Kosten-Delta, Metriken, Sentiment (2026-04-15)
- H-01: LLM-Dropdown Preiseinheit `$/1k` → `$/1M Token` (Faktor ×1.000.000)
- H-02: Kostendelta — Balance-Differenz wird bei jedem Refresh angezeigt (`_prevBalance` im Frontend)
- H-03: Metriken-Tabelle — Chat-Text auf ersten Satz (max 80 Zeichen) gekürzt, Timestamp als `DD.MM.YYYY HH:MM`
- H-04: BERT/Sentiment — `_compute_sentiment()` nutzt jetzt Score-gewichtete Werte statt nur Extreme (-1/0/1)
- H-05: Metriken nur User-Eingaben — Chart, Summary und Overnight-Job filtern auf `role='user'`

**Patch 83** – Jojo Login + Admin-Tools + RAG-Debug + Whisper-Filter + Sternenregen (2026-04-15)
- Jojo Login: Hash verifiziert (war korrekt), Debug-Logging `[DEBUG-83]` am Login-Endpoint
- Hel: Neuer Tab "User-Verwaltung" mit Hash-Tester (`POST /hel/admin/auth/test-hash`) und Passwort-Reset (`POST /hel/admin/auth/reset-password`)
- RAG: Debug-Logging `[DEBUG-83]` nach `_rag_search()` in legacy.py + orchestrator.py (L2-Distanz, Text-Preview)
- Whisper: 4 neue Halluzinations-Patterns (Untertitel breit, Punkte, Dankesfloskeln, Copyright) + Silence-Detection in beiden Voice-Endpoints
- Nala: Sternenregen-Animation bei Tap/Click (CSS `starFall` + JS `spawnStars()`)

**Patch 82** – Dictate Fast Lane + start.bat Fix (2026-04-14)
- Diagnose: /v1/audio/transcriptions war bereits JWT-exempt, SSL-Cert gültig bis Mai 2026
- Middleware: Static-API-Key-Check VOR Pfad-Exclusions gezogen → Fast Lane greift auf allen Endpoints
- Debug-Logging mit [DEBUG-82] Prefix für alle Auth-Entscheidungen
- start.bat: sauberer Neuschrieb — Port-Kill, cd, activate, uvicorn mit SSL, pause
- lessons.md: Dictate/externe-Clients-Lesson unter Pipeline/Routing ergänzt

**Patch 81** – System-Prompt Chris + RAG-Reset + Lessons (2026-04-14)
- System-Prompt Chris: Persona statt "ich bin ein Programm", Smalltalk erlaubt, RAG-Kontext nur bei Dokument-Fragen
- RAG-Index geleert und Rosendornen neu hochgeladen (automatisiert via curl)
- Hel RAG-Tab: UI-Text "~300 Wörter" → "~800 Wörter" korrigiert
- lessons.md: Pipeline-Routing-Lesson + RAG-Pfad-Lesson ergänzt

**Patch 80b** – Smalltalk-Bug: Diagnose + Fix (2026-04-14)
- URSACHE: Frontend-Chat geht über `legacy.py` (`/v1/chat/completions`), NICHT über `orchestrator.py` — Patch 78b Skip-Logik griff dort nicht
- Fix B: `legacy.py` bekommt RAG-Skip-Logik (CONVERSATION-Intent, kurze Msgs), Intent-Snippets, Fallback-Permission
- Fix C: `system_prompt_chris.json` entschärft — "Sag genau das: Dazu habe ich keine Information" ersetzt durch kontextabhängige Regel (nur bei Dokument-Fragen)
- Debug-Logging mit `[DEBUG-80b]` Prefix in beiden Pfaden (orchestrator.py + legacy.py)

**Patch 79** – start.bat + Theme-Editor + Export-Timestamp (2026-04-14)
- start.bat: Terminal bleibt sichtbar, Log scrollbar
- Theme-Editor: hardcodierte Farben tokenisiert (`--color-accent`), alle Picker in localStorage + Favoriten
- Export-Dateiname: `nala_export_YYYY-MM-DD_HH-MM.txt`

**Patch 78b** – RAG/History-Kontext-Fix (2026-04-14)
- Session-History: [VERGANGENE SESSION]-Labels → LLM behandelt alte Chats nicht als aktiven Kontext
- RAG: kein Trigger bei CONVERSATION-Intent oder kurzen Eingaben ohne Fragezeichen
- System-Prompt: Fallback-Permission für Allgemeinwissen + Smalltalk ergänzt

**Patch 78** – Login-Fix + config.json Cleanup + Profil-Key Rename (2026-04-14)
- `nala.py` `/profile/login`: Profilsuche jetzt case-insensitive über Key und `display_name` (Login mit „Jojo" oder „jojo" funktioniert)
- `config.json` im Projektroot gelöscht (war Überbleibsel, kollidierte mit `config.yaml` als einziger Konfigurationsquelle)
- `config.yaml` + `config.yaml.example`: Profil-Key `user2` → `jojo` umbenannt; DB-Check ergab keine `user2`-Einträge in `interactions`

**Patch 77** – Nala UI Overhaul: Archiv + Design + Theme-Editor (2026-04-12)
- Archiv-Sidebar: Titel+Preview (40/80 Zeichen), client-seitige Volltextsuche, Pin-Funktion (sessionStorage)
- Design: Button-Tiefeneffekt (gold box-shadow + scale), Bubble-Radien, Input-Glow, Sidebar-Header-Trennlinie
- Theme-Editor: 4 CSS-Variablen via color-picker, localStorage-Persistenz, Reset, 3 Favoriten-Slots; früher Load im `<head>`

**Patch 76** – Nala UI: Bug-Fixes + Ladeindikator (2026-04-12)
- Textarea-Collapse-Bug behoben: blur-Handler setzt `height = ''` wenn leer (CSS-Default)
- Whisper-Insert-Logik: Selektion=ersetzen, Cursor=einfügen, leer=setzen
- Ladeindikator: Mikrofon pulsiert gold bei Whisper-Verarbeitung (disabled + pulseGold-Animation)
- Typing-Bubble: drei springende Punkte erscheinen bei SSE `llm_start`, verschwinden mit Antwort

**Patch 75** – RAG Embedding-Fix + Chunk-Size-Fix (2026-04-12)
- `_encode()` in `rag/router.py`: Lazy-Load-Guard für `_model` (war `None` beim Upload → 500er)
- `_chunk_text()` in `hel.py`: `chunk_size`/`overlap` hardcoded auf 800/160 Wörter (war fälschlicherweise 300/60)
- `config.yaml`: `chunk_size: 800`, `chunk_overlap: 160` dokumentiert
- Index geleert, Rosendornen neu hochgeladen (18 Chunks bei 6256 Wörtern, 9 Sektionen korrekt), `rag_eval.py` ausgeführt

**Patch 73** – Self-Service Passwort-Reset (2026-04-12)
- POST /nala/change-password: altes PW prüfen, neues hashen (bcrypt rounds=12), config.yaml schreiben, reload_settings()
- Nala-Sidebar: Button „🔑 Passwort ändern" öffnet Modal mit 3 Feldern + clientseitiger Validierung
- Kein Admin-Eingriff nötig — Profil-Isolation via JWT garantiert

**Patch 72** – Nala Begrüßung Fix (2026-04-12)
- `nala.py` `GET /nala/greeting`: Regex-Fix – Name wird nur übernommen wenn `candidate[0].isupper()` (schließt Artikel wie „ein" aus)
- Tageszeit-abhängige Begrüßung: Guten Morgen (06–12), Hallo (12–18/22–06), Guten Abend (18–22)
- Greeting-Format geändert: „Hallo, [Name]! Wie kann ich dir helfen?" statt „Hallo! Ich bin [Name]..."
- Sauberer Fallback ohne Namen: „Hallo! Wie kann ich dir helfen?"

**Patch 71** – Jojo Login Fix (2026-04-12)
- `config.yaml`: `user2.password_hash` von leerem String auf gültigen bcrypt-Hash gesetzt (`rounds=12`)
- Login-Button für Jojo reagierte nicht — leerer Hash wurde als "kein Passwort" interpretiert, bcrypt-Vergleich schlug lautlos fehl
- Passwort für Jojo: `jojo123`

**Patch 70** – RAG-Eval + Startup-Logging + Hel-UI (2026-04-12)
- `rag_eval_questions.txt` + `rag_eval.py` erstellt: 11 Fragen gegen `/rag/search`, Ergebnis in `rag_eval_results.txt`
- `config.yaml` + `config.yaml.example`: `modules.rag.eval_source_doc` auf `docs/Rosendornen_2_Auflage.docx` gesetzt
- `main.py`: Startup-Logging überarbeitet — DB, Docker, Whisper, Ollama, RAG/FAISS, EventBus je ✅/❌ mit ANSI-Farben
- `hel.py`: BACKLOG-TODO-Kommentar im RAG-Tab für gruppierte Dokumentenliste (pro Dokument, nicht pro Chunk)

**Patch 69c** – SyntaxError-Fix exportChat (2026-04-11)
- `'\n'` in Python-HTML-String wurde als echtes Newline gerendert und brach JS-String-Literal in `exportChat()` → durch `'\\n'` ersetzt (nala.py:1175)

**Patch 69b** – Dokumentations-Restrukturierung (2026-04-11)
- `lessons.md` neu angelegt: alle Gotchas, Fallstricke und hart gelernte Lektionen zentral
- `CLAUDE.md` bereinigt: alle Patch-Changelog-Blöcke entfernt, nur operative Abschnitte behalten
- `HYPERVISOR.md` + `PROJEKTDOKUMENTATION.md`: Roadmap-Ideen (Metriken-Auswertung, RAG-Eval) ergänzt

**Patch 69a** – Bug-Fixes: Whisper-Cleaner, Kostenformat, Patch-67-Verifikation (2026-04-11)
- BUG 1: `whisper_cleaner.json` Duplikat-Wort-Pattern: `"replacement": "$1"` → `"\\1"` (war Python-re-Fehler, `$1` ist JS-Syntax)
- BUG 2: Kostenanzeige in `hel.py` umgestellt auf `$X.XX / 1M Tokens` (Formel: `cost * 1_000_000`) — betrifft „Letzte Anfrage"-Label und Metriktabelle
- BUG 3: Patch-67-Endpunkte verifiziert — `/nala/greeting`, `/archive/sessions`, `/archive/session/{id}`, `/nala/export` alle vollständig implementiert, keine Änderungen nötig

**Patch 68** – Login-Bug + RAG-Cleanup (2026-04-10)
- BUG 1+2: `crypto.randomUUID()` scheitert in HTTP-Non-Secure-Kontexten (LAN-Mobile) → `generateUUID()`-Fallback; `keypress` → `keydown` für Login-Felder; `type="button"` am Submit-Button
- BUG 3: Orchestrator-Auto-Indexing (`source: "orchestrator"`) deaktiviert — verhindert unerwartete Chunks nach RAG-Clear
- BUG 4: Emoji `📄` (Surrogate-Pair `\uD83D\uDCC4`) in RAG-Quellenliste durch `[doc]` ersetzt (Encoding-Fix)
- Bonus: `import io` in `nala.py` ergänzt (war für Export-Endpoint fehlend)

**Patch 67** – Nala Frontend UI-Fixes (2026-04-10)
- Textarea Auto-Expand: `<input>` → `<textarea>`, focus=expand (96–140px), blur=collapse; Shift+Enter=Newline
- Vollbild-Modal: `⛶`-Button öffnet 88vw×68vh Overlay, Text wird übernommen/abgebrochen
- Bubble-Toolbar: Timestamp + Kopieren-Button auf Hover; `copyBubble()` mit visuelem Feedback
- Sidebar-Buttons: „Neue Session" + „Exportieren" (als `.txt` im `[HH:MM] Rolle: Text`-Format)
- Archiv-Bug: `/archive/sessions` + `/archive/session/{id}` jetzt mit `profileHeaders()` → JWT-401 behoben
- Dynamische Begrüßung: `GET /nala/greeting` liest Charaktername aus System-Prompt via Regex
- Neue State-Variable `chatMessages[]` tracked alle Nachrichten für Export

**Patch 66** – RAG Chunk-Optimierung (2026-04-10)
- `_chunk_text()` in `hel.py`: `chunk_size` 300→**800 Wörter**, `overlap` 50→**160 Wörter** (20 %)

## Offene Items (Backlog)
1. Manuell getippter Text → DB-Speicherung verifizieren
2. [Patch 89] RAG-Qualität nach R-03-Fix: **10/11 JA, 1/11 TEILWEISE, 0 NEIN**. Q4 + Q10 geheilt. Offen: **Q11** (Aggregat-Query „Nenn alle Momente wo…") — Reranker liefert Glossar-Definition, konkrete Szenen bleiben über mehrere Chunks verteilt. **Nächster Kandidat: R-04 (Query-Expansion) oder LLM-seitige Multi-Chunk-Aggregation**, nicht mehr Retrieval-Qualität. R-02 (Embedding-Upgrade) nach hinten verschoben — Reranker kompensiert MiniLM-Schwäche ausreichend. Reports: `rag_eval_delta_patch89.md`.
3. Alembic-Setup (Dauerläufer)
4. RAG-Auto-Indexing: falls Konversations-Gedächtnis später wieder gewünscht → als optionalen Config-Schalter reaktivieren
5. [IDEE] Metriken: Interaktive Auswertung (Zeiträume, LLM-Auswertung, D3/Canvas-Zoom, Mobile-first) — Konzept noch nicht final
6. [BACKLOG] Hel RAG-Tab: Dokumentenliste gruppiert anzeigen (pro Dokument eine Zeile mit Chunk-Anzahl) — TODO in hel.py eingetragen
7. ~~[BACKLOG] `rag_eval.py` hardcoded auf `http://127.0.0.1:5000`~~ ✅ Patch 90 — HTTPS-Default + `_SSL_CTX` + `RAG_EVAL_URL`-Env-Override.
8. [N-F02/N-F03/N-F04] Nala Bubble-Tooling (Wiederholen / Bearbeiten / Lade-Indikator-Upgrade) — siehe `backlog_nach_patch83.md`. Nicht akut.
9. [H-F01/H-F03] Hel: Sticky Tab-Leiste (statt Akkordeon Wisch-Tabs) und mehr Metriken-Auswahl — Konzepte offen.

## Architektur-Warnungen
- `interactions`-Tabelle hat keine User-Spalte — User-Trennung nur per Session-ID (unzuverlässig)
- Rosa Security Layer: NICHT implementiert — Dateien im Projektordner sind nur Vorbereitung
- JWT blockiert externe Clients komplett — static_api_key ist der einzige Workaround

## Langfrist-Vision
Metric Engine = kognitives Tagebuch + Frühwarnsystem für Denkmuster-Drift.
Rosa Corporate Security Layer = letzter Baustein vor kommerziellem Einsatz.
Telegram-Bot als Zero-Friction-Frontend für Dritte (keine Tailscale-Installation nötig).

## Dont's für Hypervisor
- PROJEKTDOKUMENTATION.md NICHT vollständig laden (1900+ Zeilen = Kontextverschwendung)
- Memory-Edits max 500 Zeichen pro Eintrag
- Session-ID ≠ User-Trennung — Metriken pro User erst nach DB-Architektur-Fix vertrauenswürdig
