# SUPERVISOR_ZERBERUS.md – Zerberus Pro 4.0
*Strategischer Stand für die Supervisor-Instanz (claude.ai Chat)*
*Letzte Aktualisierung: Patch 108b + 109 (2026-04-22)*

## Aktueller Patch
**Patch 108b + 109** – RAG-Eval-Erweiterung + SSE-Resilience + Theme-Hardening (2026-04-22)
- **Teil A / 108b – RAG-Eval-Erweiterung:** `rag_eval_questions.txt` um 9 neue Fragen erweitert (Q12–Q20). Drei Cluster: Codex Heroicus (Q12–Q14, category `narrative`), NÉON Kadath Prosa (Q15–Q18, category `lore`), Cross-Document (Q19–Q20) — fragen Fakten-, Aggregations- und Cross-Category-Retrieval ab. **Live-Verifikation der Category-Tags (Upload mit category-Form-Feld, `sources_meta` im Status, `Kategorie: narrative` im Retrieval-Kontext) steht bei Chris aus** — Server-Start + Admin-JWT waren aus der CI-Umgebung nicht praktikabel. Neue Test-Doks (Codex Heroicus, NÉON Kadath) müssen vor dem Eval-Run mit passender Category hochgeladen werden.
- **Teil B / 109 – SSE-Resilience (Cluster B1):** Root-Cause-Analyse: Der SSE-Stream `/nala/events` liefert nur Status-Events (rag_search/llm_start/done), die eigentliche Antwort kommt per `fetch('/v1/chat/completions')` als JSON. Bei 45-s-Frontend-Timeout bricht der fetch ab, obwohl das Backend noch weiterarbeitet und die Antwort via `store_interaction()` in die DB schreibt. Neuer **REST-Fallback auf Retry-Click** in [nala.py](zerberus/app/routers/nala.py): `fetchLateAnswer(sid, userText)` pollt das bestehende `/archive/session/{id}`, sucht rückwärts die letzte user-Message mit passendem Content, liefert die erste nachfolgende assistant-Message zurück. `retryOrRecover(retryText, retrySid, cleanupFn)` kapselt die Entscheidung: späte Antwort gefunden → anzeigen (kein Doppelkonsum von OpenRouter-Credits). Kein späte Antwort → klassisches `sendMessage(retryText)`. `setTypingState('timeout', text, reqSessionId)` + `showErrorBubble(text, retryText, reqSessionId)` bekommen die `reqSessionId` als dritten Parameter durchgereicht. Button zeigt „⏳ Prüfe Server…" während der kurzen Archive-Abfrage. **Kein neuer Endpoint nötig** — `/archive/session/{id}` aus [archive.py](zerberus/app/routers/archive.py) existiert bereits (mit JWT-Auth via `profileHeaders()`).
- **Teil B / 109 – Theme-Hardening (Cluster B2):** `:root` in [nala.py](zerberus/app/routers/nala.py) — `--bubble-user-bg` + `--bubble-llm-bg` auf `rgba(…, 0.88/0.85)`-Defaults umgestellt (gleiche Farbwerte wie `--color-accent` / `--color-primary-mid`, nur mit Alpha). Anti-Invariante dokumentiert: „NIE schwarz auf schwarz". `resetTheme()` erweitert — ruft zusätzlich `resetAllBubbles()` + `resetFontSize()` auf, damit ein vollständiger Reset wirklich alle Overrides löscht (vorher konnten alte schwarze Bubble-Overrides die rgba-Defaults übersteuern). Color-Picker-Verhalten unverändert (liest `cssToHex` aus Theme-Fallback-Kette).
- **Verifikation:** `python -m py_compile zerberus/app/routers/nala.py` OK. `node --check` auf beide NALA_HTML-`<script>`-Blöcke (3318 + 58678 Zeichen) OK. `pytest zerberus/tests/test_intent_transform.py zerberus/tests/test_cleaner.py` → **34 passed** (offline). Live-Playwright-Suite + RAG-Eval-Run stehen bei Chris aus (Server-Start nötig).
- **Scope:** Nur SSE-Fallback + Theme-Defaults + neue Eval-Fragen. Color Picker / Farb-Rad (Backlog Phase 4), Chunking-Weiche (Patch 110), Query-Router mit Category-Filter (Patch 110), Background Memory Extraction (Patch 111), W-001 Sentence-Repetition, Multimodalität ZIP/Bild — NICHT in diesem Patch.

**Patch 108** – RAG Category-Tags + Ratatoskr-Sync + CLAUDE_ZERBERUS Regel 9 (2026-04-22)
- **Cluster 3 – RAG Category-Tags:** Neuer Form-Parameter `category` in [`/hel/admin/rag/upload`](zerberus/app/routers/hel.py) (`Allgemein`/`Narrativ`/`Technisch`/`Persönlich`/`Lore`/`Referenz`). Whitelist `_RAG_CATEGORIES` in hel.py, unbekannte Werte fallen auf `"general"` zurück (`[RAG-108]`-WARNING). Metadata pro Chunk um `category` erweitert (`{"source", "word_count", "category"}`). Altdaten ohne Category lesen via `.get("category", "general")` → keine Migration nötig.
- **Hel-UI:** Neues `<select id="ragCategory" class="profile-select">`-Dropdown unter dem Datei-Button (gleiches Styling wie Profil-Dropdown aus Patch 95). `uploadRagFile()` hängt `category` an FormData. `loadRagStatus()` verarbeitet das neue `sources_meta`-Feld und rendert farbige Badges pro Kategorie in der Index-Übersicht (Farbmap `RAG_CATEGORY_COLORS`). Fallback auf `sources`-Only, falls Endpoint kein `sources_meta` liefert (Backward-Compat).
- **Retriever-Kontext:** `_fmt_hit()` in legacy.py + orchestrator.py ersetzt das alte `[Gedächtnis]: ...`-Format durch `[Quelle: <file> | Kategorie: <cat> | Score: <0.xx>]\n<chunk-text>` — LLM sieht jetzt Herkunft + Kategorie direkt. Category-Filtering beim Retrieval ist bewusst NICHT aktiviert (kommt in Patch 110 Query-Router).
- **Cluster 2 – CLAUDE_ZERBERUS Regel 9** (nicht 8 — existierende Regel 8 = `/v1/`-Auth-Bypass): User-Entscheidungen als klickbare Box in Nala-UI statt Text-Rückfrage.
- **Cluster 1 – Ratatoskr-Sync:** CLAUDE_ZERBERUS.md + SUPERVISOR_ZERBERUS.md nach Ratatoskr kopiert + gepusht.
- **Scope:** Tagging-Mechanik + Anzeige. Chunking-Weiche pro Typ (Patch 109), Query-Router (Patch 110), Background Memory Extraction (Patch 111) sind NICHT in diesem Patch.
- **Verifikation:** Statische Checks grün — `ast.parse` auf hel.py/legacy.py/orchestrator.py OK, `node --check` auf alle 5 `<script>`-Blöcke in hel.py OK (50387 Zeichen + 616 Zeichen). **Live-Upload mit Category-Dropdown + Hel-Übersicht-Rendering steht bei Chris aus (Server-Restart nötig).** Testdoks liegen in `docs/RAG Testdokumente/` (Note: Ordnername hat ein Leerzeichen, nicht Bindestrich — Patch-Prompt-Pfad `RAG-Testdokumente` existiert nicht).

**Patch 107** – Smoke-Tests Phase 1 + Pipeline-Deduplizierung-Doku (2026-04-22)
- **Doku:** `lessons.md` erweitert um (a) Pipeline-Dedup (Dictate cleaned Text bereits vor dem Upload, legacy.py cleaned nochmal — aktuell idempotent-harmlos, Backlog-Item), (b) Hel-Admin-UI Split-Brain-Lektion aus Patch 105, (c) Reranker-Minimum-Score-Lektion aus Patch 105, (d) TRANSFORM-Intent-Regel aus Patch 106.
- **Backlog:** drei neue Items (Pipeline-Dedup-Skip-Flag, DB-Dedup Overnight, Projekt-Workspace als Langzeit-Vision).
- **Tests post-Server-Restart:** Volle Playwright-Suite **71 passed in 74 s** (14 Loki E2E + 28 Fenrir Chaos + 8 Cleaner + 3 LLM-Fallback + 26 neue TRANSFORM-Intent-Tests). Live-curl-Smoke-Tests:
  - T1 (HITL-Guard Englisch): Response kommt durch, `model: deepseek/deepseek-v3.2` ✓
  - T2 (TRANSFORM): `"Übersetze auf Englisch: Heute ist ein guter Tag."` → `"Today is a good day."` — Log zeigt `[TRANSFORM-106] RAG und Query Expansion übersprungen` ✓
  - T3 (RAG-Gegenprobe): `"Was passiert in den Rosendornen bei der Perseiden-Nacht?"` → ausführliche Antwort mit 4 Details. Log zeigt `[THRESHOLD-105] RAG-Top-Score 0.4808 >= Minimum 0.0500 — Kontext behalten (8 Chunks)` ✓
  - T4 (Dialekt): `🐻🐻🐻🐻🐻 Ich gehe nicht nach Hause` → `Ick gehe nich nach Hause` (Patch 103 stabil) ✓
  - T5 (Threshold-Trigger): Englischer Email-Request löste `[THRESHOLD-105] RAG-Top-Score 0.0038 < Minimum 0.0500 — RAG-Kontext verworfen` aus — Threshold-Mechanik live bestätigt ✓

**Patch 106** – RAG-Skip für TRANSFORM-Intent (2026-04-22)
- **Root Cause:** Jeder /v1/chat/completions-Request durchlief die komplette RAG-Pipeline inkl. Query-Expansion (OpenRouter-Call) und Cross-Encoder-Rerank (~47 s auf CPU). Bei Textverarbeitungs-Aufgaben (Übersetzen/Lektorieren/Zusammenfassen) liefert der User den Kontext selbst mit — RAG-Index hat nichts Passendes, der 16k-Token-Monster-Prompt ist reine Latenz.
- **Fix:** Neuer Intent TRANSFORM mit höchster Prio-Reihenfolge (vor COMMAND_TOOL) in [orchestrator.py](zerberus/app/routers/orchestrator.py). `_TRANSFORM_PATTERNS` matched nur am Nachrichtenanfang (`^übersetze|lektoriere|fasse\s+zusammen|stichpunkte|schreib\s+um|kürze|erweitere` + englische Pendants + ue/oe/ae-Transliterationen). Permission-Matrix erweitert: TRANSFORM für alle Rollen (Text-only, kein Tool-Use). RAG-Skip-Logik in BEIDEN Pfaden (orchestrator._run_pipeline + legacy.chat_completions) ergänzt um `skip_rag_transform`. Query Expansion wird automatisch mit ausgelassen, weil sie nur in `_rag_search()` läuft. `[TRANSFORM-106]`-WARNING-Log markiert den Skip.
- **Entfernt:** "übersetze", "zusammenfass(e)" aus `_COMMAND_SAFE_WORDS` (kollidierten mit dem neuen Intent).

**Patch 105** – Hel-Split-Brain-Fix + RAG-Reranker-Threshold (2026-04-22)
- **Root Cause (Split-Brain):** Der im Prompt vermutete Llama-Hardcode in legacy.py existierte nicht — `LLMService.call()` liest sauber aus `settings.legacy.models.cloud_model`. Die ECHTE Fehlerquelle lag in [hel.py](zerberus/app/routers/hel.py): `GET/POST /hel/admin/config` arbeiteten auf `config.json` (`llm.cloud_model`), aber `LLMService` zog aus `config.yaml` (`legacy.models.cloud_model`). Hel-UI zeigte DeepSeek, LLMService rief Llama. Klassischer Split-Brain, eigentlich schon Patch 34 adressiert, in der UI aber nicht nachgezogen.
- **Fix (Split-Brain):** GET liefert jetzt YAML-Werte im gewohnten `{llm: {cloud_model, temperature, threshold}}`-Wrapper (UI-JS unverändert). POST schreibt via `_yaml_replace_scalar()` — line-basierte In-Place-Ersetzung für `legacy.models.cloud_model`, `legacy.settings.ai_temperature`, `legacy.settings.threshold_length`. Kommentare in config.yaml bleiben vollständig erhalten (yaml.safe_dump würde sie zerstören, ruamel.yaml ist nicht installiert). config.yaml manuell auf `deepseek/deepseek-v3.2` gesetzt, config.yaml.example auch. config.json zur Konsistenz mitgezogen (wird aber nicht mehr authoritativ gelesen).
- **Root Cause (Reranker):** Cross-Encoder liefert bei irrelevanten Queries (z.B. "Übersetze …") Top-Scores von 0.003, trotzdem wurden alle 8 Chunks ins Prompt gepumpt. Kein Minimum-Schwellwert.
- **Fix (Reranker):** Neuer Config-Key `modules.rag.rerank_min_score: 0.05` (in config.yaml + config.yaml.example). `_rag_search()` in [orchestrator.py](zerberus/app/routers/orchestrator.py) prüft nach dem Rerank den Top-Score — liegt er unter der Schwelle, wird `[]` zurückgegeben statt 8 Noise-Chunks. Automatisch für legacy.py wirksam (importiert `_rag_search`). `[THRESHOLD-105]`-WARNING-Log beides — behalten und verworfen.

**Patch 104** – HITL-Guard Scope + B-24 Overnight-Sentiment + Patch-102-Nachtrag (2026-04-22)
- **Block 1 – Patch-102-Nachtrag:** Fehlender Patch-102-Eintrag in `SUPERVISOR_ZERBERUS.md` zwischen Patch 103 und Patch 101 nachgetragen (Cluster 1 Session-Isolation/B-02/B-06, Cluster 2 Zustandsanzeige/B-03/B-08/B-09/B-11/B-17, Cluster 3 Whisper RepFilter/B-01, Cluster 4 LLM-Fallback/B-20, Cluster 5 Begrüßung/B-05, Cluster 6 Input/B-07, Tests 44/45 grün). Markiert mit „(Eintrag nachgetragen in Patch 104)".
- **Block 2 – HITL-Guard Scope (Root Cause):** Der Permission-Check (`_PERMISSION_MATRIX` aus Patch 47) feuerte unconditional in `_run_pipeline` ([orchestrator.py:349](zerberus/app/routers/orchestrator.py:349)) und im `/v1/chat/completions`-Handler ([legacy.py:148](zerberus/app/routers/legacy.py:148)). Defaultmäßig läuft Dictate ohne JWT → `permission_level="guest"`, das nur QUESTION + CONVERSATION erlaubt. Englische Texte mit Wörtern wie „download", „script", „tool" wurden als COMMAND_TOOL klassifiziert und mit der HitL-Antwort geblockt — obwohl der Guard ursprünglich nur externe Bot-Channels (Telegram/WhatsApp) absichern sollte. **Fix:** Neue Konstante `_HITL_PROTECTED_CHANNELS = {"telegram","whatsapp"}` in [orchestrator.py](zerberus/app/routers/orchestrator.py), `_run_pipeline` bekam neuen `channel: str | None = None`-Parameter — Permission-Check läuft jetzt nur noch wenn `channel in _HITL_PROTECTED_CHANNELS`. `legacy.py /v1/chat/completions` ruft den Block gar nicht mehr auf (Dictate-only), behält aber `detect_intent()` für die nachgelagerte RAG-Skip-Logik. `[HITL-104]`-WARNING-Log markiert das Skip an beiden Stellen. Telegram/WhatsApp-Router müssen beim späteren Einbau `channel="telegram"`/`"whatsapp"` an `_run_pipeline()` übergeben — dann greift der Guard wieder.
- **Block 3 – B-24 Overnight-Sentiment (Root Cause):** Die Query in [overnight.py](zerberus/modules/sentiment/overnight.py) übergab `datetime.utcnow().isoformat()` = `2026-04-21T08:42:29.116695` (T-Separator) als `:since`. SQLAlchemy speichert Timestamps in SQLite aber als `2026-04-21 18:50:50.611657` (Space-Separator). Lexikografischer String-Vergleich: `T` (0x54) > ` ` (0x20), also `2026-04-21T...` > `2026-04-21 18:50:50` → **alle Yesterday-UTC-Rows fielen lautlos raus**. Genau das Fenster, das der 04:30-Cron auswerten soll. **Fix:** SQLite-natives `WHERE i.timestamp >= datetime('now', '-24 hours')` statt Python-side ISO-String. `datetime`/`timedelta`-Imports entfernt (jetzt unused). `[B24-104]`-WARNING-Log mit Treffer-Anzahl. **Manueller Testlauf:** Vorher 0 Messages, nach Fix **28 Messages** gefunden (Filter `role=user`, `bert_sentiment_label NULL`, last 24h).
- **Verifikation:** Imports sauber (`venv/Scripts/python -c "from zerberus.app.routers.orchestrator import _HITL_PROTECTED_CHANNELS"` → `{'whatsapp', 'telegram'}`). Overnight-Query liefert 28 Treffer statt 0.
- **Offene Punkte:** Live-Server-Verifikation der HITL-Skip-Logik (curl-Tests aus Patch-Prompt) durch Chris nach Server-Start. Wenn Telegram-/WhatsApp-Router gebaut werden, dort `channel="telegram"`/`"whatsapp"` an `_run_pipeline()` übergeben.

**Patch 103** – Dialekt-Weiche Reparatur + UI-Quick-Wins (2026-04-22)
- **Cluster 1 (B-18) – Dialekt-Weiche Hauptfix:** Root Cause lag in [`zerberus/core/dialect.py`](zerberus/core/dialect.py) — Marker waren auf ×2 Emojis statt ×5 gesetzt (`"🐻🐻"`/`"🥨🥨"`/`"✨✨"`). Bei Input `🐻🐻🐻🐻🐻 hallo` matchte die ×2-Variante, `rest` behielt drei übrige Emojis, die durch das Wort-Matching und `ChatCompletionResponse` bis in den 400/500 liefen. Fix: Marker auf ×5 (`"🐻🐻🐻🐻🐻"` etc.). Logging auf `[DIALECT-103]`-WARNING-Prefix.
- **Cluster 1 Bonus – Wortgrenzen-Matching in `apply_dialect`:** Die `.replace()`-basierte Substring-Ersetzung produzierte Artefakte wie `"Ich gehe nicht" → "Ick gehe nick"` (weil `ich` innerhalb von `nich` matched). Jetzt `re.sub(r'(?<!\w)KEY(?!\w)', ...)` mit ASCII+Umlaut-Grenzen. Ergebnis stimmt: `"Ich gehe nicht nach Hause" → "Ick gehe nich nach Hause"`. Multi-Wort-Keys (`"haben wir" → "hamm wa"`) funktionieren weiter dank Length-sortiertem Durchlauf.
- **Cluster 2 (B-10) – Persistent Login:** JWT-Token-Lebensdauer in [`config.py`](zerberus/core/config.py) und [`config.yaml`](config.yaml) auf 525600 Minuten (365 Tage). Frontend: [`nala.py`](zerberus/app/routers/nala.py) `loadSessions()` + `loadSession()` werfen bei 401 jetzt `handle401()` (Login-Modal) statt stiller "Auth-Fehler"-Meldung.
- **Cluster 3 (B-15, F-07) – Modals + Firefox Autofill:** Login-Inputs auf `autocomplete="off"` bzw. `"new-password"` umgestellt (Firefox-Autofill-Bar weg). Zentraler Backdrop-Close-Handler in IIFE am Script-Ende — klickt man neben ein Modal (`#settings-modal`, `#export-modal`, `#fullscreen-modal`, `#pw-modal`), schließt es. `#ee-modal` hat eigenen Handler (Zeile 2412), nicht doppelt.
- **Cluster 4 (B-23) – Pinned Sessions:** Zwei kombinierte Bugs. (1) `sessionStorage` → `localStorage` in `getPinnedIds`/`setPinnedIds` — Pins überleben jetzt Browser-Neustart. Einmalige Migration aus sessionStorage eingebaut. (2) `<ul id="pinned-list">` im HTML wurde nie befüllt — `renderSessionList()` aufgeteilt in zwei Sektionen, `#pinned-heading` + `#pinned-list` dynamisch sichtbar. HTML-Reihenfolge auf "📌 Angepinnt oben, 📋 Letzte Chats unten" umgestellt. Helper `buildSessionItem(s, isPinned)` extrahiert.
- **Cluster 5 (B-13, B-14) – Favoriten-Default beim Start:** Neuer localStorage-Key `nala_last_active_favorite`. `saveFav(n)` + `loadFav(n)` setzen ihn, `resetTheme()` löscht ihn. Die Start-IIFE (`<head>`, vor erstem Render) prüft zuerst auf `nala_last_active_favorite` + liest den Favoriten-Slot direkt (v2-Schema: theme + bubble + fontSize) und wendet CSS-Props an — Fav hat Vorrang vor flachem `nala_theme`. Favorit überlebt Browser-Neustart.
- **Tests:** 25/25 Unit-Tests grün (Cleaner/LLM-Fallback/Loki). Playwright/Fenrir-E2E übersprungen, weil Server nicht lief — Unit-Level-Verifikation der Dialekt-Logik via direkten Python-Import erfolgreich ("nicht" → "nich", "Ich" → "Ick").
- **Dictate-Tastatur (Dev.Emperor) Kompatibilität:** Beide Endpunkte `/v1/chat/completions` (Text) und `/v1/audio/transcriptions` (Audio) sind OpenAI-kompatibel und live. Keine Änderung am Tastatur-Konfig-Endpoint `https://desktop-rmuhi55.tail79500e.ts.net:5000/v1` nötig.
- **Offene Punkte:** Manuelle Live-Verifikation via curl durch Chris nach Server-Start. Playwright-E2E optional nach Server-Start.

**Patch 102** – Session-Isolation + Zustandsanzeige + Whisper RepFilter + LLM-Fallback + Begrüßung + Input-Fix (2026-04-22) *(Eintrag nachgetragen in Patch 104)*
- **Cluster 1 (B-02/B-06) – Session-Isolation:** AbortController für aktiven LLM-Request, Snapshot von `reqSessionId` für Stale-Check. `abortActiveChat()` in `loadSession`/`newSession`/`doLogout`/`handle401`. `[SESSION-102]`-WARNING bei verworfener Stale-Response. Verhindert, dass die Antwort einer alten Session in den neuen Chat reinrutscht.
- **Cluster 2 (B-03/B-08/B-09/B-11/B-17) – Zustandsanzeige:** Spinner-Rad statt springender Punkte (CSS `@keyframes spin`). `showTypingIndicator()` + `setTypingState('running'|'timeout'|'error')` sofort beim Send. `lockInput()`/`unlockInput()` für Textarea + Send-Button (finally-Block garantiert Freigabe). 45-s-Frontend-Timeout via AbortController, `[TIMEOUT-102]`-Console-Warning + Retry-Button. `showErrorBubble()` bei NetworkError + Retry-Button.
- **Cluster 3 (B-01) – Whisper Phrasen-Repetition-Filter:** Neuer `detect_phrase_repetition()` in `cleaner.py` — N-Gram-basiert (2–6 Wörter). Konfigurierbar via `config.yaml whisper_cleaner.repetition_filter`. `WhisperCleanerConfig` + `RepetitionFilterConfig` in `config.py`. `[WHISPER-REP-102]`-WARNING bei Erkennung. 8 neue Unit-Tests in `test_cleaner.py`.
- **Cluster 4 (B-20) – LLM-Fallback absichern:** `OpenRouterConfig.provider_blacklist` Default = `['chutes', 'targon']` (config.yaml ist gitignored — Default in `config.py` garantiert dass die Blacklist nach `git clone` greift). `[FALLBACK-102]`-WARNING-Logs an beiden Fallback-Pfaden in `legacy.py`. 3 neue Unit-Tests in `test_llm_fallback.py` (Source-Inspektion → robust gegen Loop-Konflikte).
- **Cluster 5 (B-05) – Begrüßung:** `/nala/greeting` liefert nur `{prefix, name}` — `name` = `display_name` (User), nicht `char_name` (Char). Frontend `GREETING_VARIANTS` mit 4 Templates, zufällige Auswahl. `char_name`-Extraktion aus System-Prompt entfernt (Quelle des „Hallo, Nala!"-Bugs).
- **Cluster 6 (B-07) – Input-Verhalten Mobile-First:** `isTouchDevice()`-Detection — Mobile: Enter macht IMMER Zeilenumbruch. Desktop: Toggle in Settings (Enter sendet vs. Shift+Enter sendet). Default Desktop = Enter sendet (Patch-67-Kompatibilität). localStorage-Key `zerberus_enter_sends` persistiert User-Wahl.
- **Tests:** 44/45 grün (1 bestehender Fenrir-Chaos-ERROR mit leerem String, nicht Patch-102-relevant). 11 neue Unit-Tests (8 cleaner + 3 llm_fallback) → 100 % grün. Alle 14 Loki-E2E-Tests + 19/20 Fenrir-Tests grün.

**Patch 101** – Template-Konsolidierung + R-07 Multi-Chunk-Aggregation (2026-04-21)
- **Block A (Zerberus):** CLAUDE_ZERBERUS.md Header-Cleanup (`/ Rosa` raus), neue Regel 6 (Dateinamen `CLAUDE_ZERBERUS.md`/`SUPERVISOR_ZERBERUS.md` FINAL, inkl. Ratatoskr-Kopien), neue Regel 7 (Mobile-first: `:active`-Fallback, 44px-Touch-Targets, `keydown` statt `keypress`, `type="button"` auf Non-Submit-Buttons). Neue Sektionen „Supervisor-Patch-Prompts" (immer als .md, nie inline) und „Ratatoskr-Sync" (Copy-Liste + PowerShell-Rezept, Pfade `C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr\` und `C:\Users\chris\Python\Claude\`).
- **Block B (Ratatoskr):** Alte `CLAUDE.md`/`HYPERVISOR.md` waren seit Patch 100-Fix bereits entfernt. Neue CLAUDE_ZERBERUS.md gespiegelt, gepusht (Commit `ad7bec2`).
- **Block C (Bmad82/Claude globales Repo):** Vorhandene Templates (CLAUDE.md, SUPERVISOR.md) NICHT überschrieben — sind bereits umfangreicher als der Patch-Prompt vorgab (inkl. Bug-Tracker-Integration, Handy-First-Sektion, Whisper-Hinweis). Stattdessen zwei universelle Patch-100-Lessons in die passenden thematischen Dateien eingetragen: `lessons/frontend-js.md` + node-`--check`-Pre-Commit, `lessons/testing.md` + `pageerror`-Listener-vor-`goto`. Gepusht (Commit `aa3293c`).
- **Block D (R-07 Multi-Chunk-Aggregation):** `_RAG_TOP_K` in orchestrator.py von 3 auf **8** erhöht (wirkt via Import auch auf legacy.py). Aggregation-Hint nach dem `context_lines`-Block in BEIDEN Pfaden (legacy.py + orchestrator.py): „WICHTIG: Wenn die Frage nach Aufzählung/Liste/Zusammenfassung über MEHRERE Abschnitte fragt, nutze ALLE oben stehenden Kontext-Abschnitte." `[AGG-101]` Debug-Logging auf WARNING-Level. `rag_eval.py TOP_K 5→8`.
- **RAG-Eval post-Restart:** Alle 11 Fragen liefern jetzt je 8 Chunks. **Q11 (Aufzählungs-Query „alle Momente wo Annes Verhalten als unkontrollierbarer Impuls beschrieben wird"):** Chunk 4 mit der exakten Phrase „ein unkontrollierbarer Impuls" (Score 0.417) ist jetzt stabil im Top-8-Output — bei TOP_K=5 vorher rausgefallen, weil der Reranker das Glossar-Chunk (0.575) favorisiert. **Retrieval-Engpass ist gelöst.** Der Aggregation-Hint wirkt nur im Live-Chat (nicht im Eval, da rag_eval.py nur Retrieval misst). **Live-Verifikation Q11 steht bei Chris aus.**
- Dateinamen-Bestätigung: `CLAUDE.md`/`HYPERVISOR.md` existieren nirgends mehr (Zerberus, Ratatoskr sauber). HYPERVISOR-Referenzen in `docs/PROJEKTDOKUMENTATION.md` sind historische Patch-Einträge — bewusst nicht geändert (keine Geschichts-Revision).

**Patch 100** – Meilenstein: Hel-Hotfix + JS-Integrity-Tests + Easter Egg (2026-04-19)
- **Teil 1 — Hotfix SyntaxError:** `hel.py:1290` enthielt seit Patch 91 `'\n\n'` in einem plain Python-`"""..."""`-String → Python rendert echtes Newline → JS-String-Literal bricht, gesamtes Hel-Script tot. Fix: `'\n\n'` → `'\\n\\n'`. Seit Patch 99 akut sichtbar, weil `activateTab` zur kritischen Init-Funktion wurde.
- **Teil 2 — TestJavaScriptIntegrity:** Neue Testklasse in `test_loki.py` mit `pageerror`-Listener VOR `goto` (sonst werden Parse-Errors verschluckt). Zwei Tests für /hel/ + /nala. **Full-Suite: 34 passed in 54 s** (32 + 2 neue).
- **Teil 3 — Easter Egg:** `Architekt_und_Sonnenblume.png` nach `zerberus/static/pics/` kopiert. In Nala: `sendMessage` fängt `rosendornen`/`patch 100` ab und öffnet `#ee-modal` (gold-Border, Sternenregen-Hintergrund via `spawnStars` + 400ms-Interval, fade-in 1.5s). In Hel: neuer 12. Tab `ℹ️ About` mit demselben Bild + Version-Block (`Patch 100`, Tests 34/34, RAG 10/11) + Entwickler-Credits.
- Bekannter Bug-Typ (Lesson Patch 69c): `\n` in JS-Strings innerhalb Python-HTML-Strings IMMER als `\\n` schreiben. `lessons.md` um Abschnitt „Frontend / JS in Python-Strings" erweitert.

**Patch 99** – Hel Sticky Tab-Leiste (H-F01) (2026-04-18)
- Block A: Neue `.hel-tab-nav` (`position: sticky; top:0; z-index:100`) direkt unter `<h1>` mit 11 Tabs (📊 Metriken, 🤖 LLM, 💬 Prompt, 📚 RAG, 🔧 Cleaner, 👥 User, 🧪 Tests, 🗣 Dialekte, 💗 Sysctl, ❌ Provider, 🔗 Links). Horizontal scrollbar auf Mobile (`overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none`), 44 px Touch-Targets, Unterstrich-Highlight in `#ffd700` für aktiven Tab.
- Block B: Neue `activateTab(id)`-Funktion ersetzt das alte `toggleSection` (als Alias belassen für Altcode). Versteckt/zeigt Sections via `data-tab`-Attribut + `.active`-Klasse. Lazy-Loads (loadMetrics / loadSystemPrompt / loadRagStatus / loadPacemakerConfig / loadProviderBlacklist) laufen genau einmal pro Sektion (`_HEL_LAZY_LOADED`-Set). Aktiver Tab in `localStorage('hel_active_tab')` persistiert. Early-Load-IIFE im `<head>` vermeidet FOUC + Default auf 'metrics'.
- Block C: Jede `.hel-section` bekam `data-tab="..."`-Attribut; `.hel-section-header`-Zeilen bleiben im HTML (Rückwärtskompat für `toggleSection`-Altcode) aber sind per CSS `display: none !important`. `.hel-section-body.collapsed`-Regel neutralisiert (`max-height: none !important`) — Tabs verwalten die Sichtbarkeit auf Section-Ebene, nicht mehr auf Body-Ebene.
- Block D: Full-Test-Suite post-Restart: **32 passed in 49.93 s** — bestehende Selektoren (`#metricsCanvas`, `#section-metrics`, `.time-chip`) funktionieren unverändert weiter, weil Metriken der Default-Tab ist und alle Sektion-IDs bestehen bleiben. 11 `data-tab`-Attribute im gerenderten HTML verifiziert.
- Server-Reload-Lesson zum dritten Mal in dieser Session: zombie-Worker aus früheren `--reload`-Versuchen blockierten Port 5000 → mehrere uvicorn-Prozess-Trees parallel, neue Prozesse banden keinen Port mehr. Clean via `Get-WmiObject Win32_Process`-Scan + `taskkill` aller Python-Uvicorn-PIDs, dann frischer Start. **`lessons.md` um harten Punkt dazu ergänzt.**
- Backlog-Item 10 (H-F01) ✓ erledigt. Backlog komplett abgearbeitet bis auf R-07 (Multi-Chunk-Aggregation, siehe Q11).

**Patch 98** – Wiederholen & Bearbeiten an Chat-Bubbles (N-F03/N-F04) (2026-04-18)
- Block A: Neuer 🔄-Button (`.bubble-action-btn`) in der `msg-toolbar` nur an User-Bubbles — `retryMessage(text, btn)` ruft `sendMessage(text)` erneut auf. Kein Fork / kein History-Rewrite; frühere Nachrichten werden einfach als neue Message ans Ende gehängt. Kurzer Gold-Flash (CSS-Klasse `.copy-ok` 800 ms) als visuelles Feedback.
- Block B: Neuer ✏️-Button ebenfalls nur an User-Bubbles — `editMessage(text, btn)` kopiert den Text in die Textarea, fokussiert sie, triggert das bestehende Auto-Expand (96–140 px) und setzt den Cursor ans Ende. Kein Auto-Senden — der User editiert und drückt Enter selbst.
- Block C: CSS-Sweep auf die bestehende `.copy-btn`-Klasse erweitert auf `.copy-btn, .bubble-action-btn` (28 px Standard, 44 px Touch-Target via `@media (hover: none) and (pointer: coarse)`, leicht opake Toolbar bei Touch-Geräten für Dauer-Sichtbarkeit). LLM-Bubbles behalten weiterhin nur 📋 + Timestamp.
- Block D: Full-Test-Suite post-Restart: **32 passed in 50 s** — keine Regressions an den bestehenden Bubble-Selektoren. Markers im HTML (`bubble-action-btn`, `retryMessage`, `editMessage`): 7 Treffer wie geplant.
- Backlog N-F03 + N-F04 ✓ erledigt.

**Patch 97** – R-04: Query Expansion für Aggregat-Queries (2026-04-18)
- Block A: Neues Modul [query_expander.py](zerberus/modules/rag/query_expander.py) — `expand_query(query, config)` macht einen kurzen OpenRouter-Call (System-Prompt: "erzeuge 2-3 alternative Formulierungen als JSON-Liste"), 3 s Timeout, Fail-Safe auf Original-Query bei Timeout / HTTP-Fehler / Parse-Fehler. Modell aus `modules.rag.query_expansion_model` oder Fallback auf `legacy.models.cloud_model`.
- Block B: Integration in `/rag/search` (router.py) UND `_rag_search` (orchestrator.py). Pro Variante FAISS-Call mit `rerank_enabled=False`, Dedup per Text-Prefix-Key (200 Zeichen), **finaler Rerank einmal über den kombinierten Pool mit der ORIGINAL-Query** (Relevanz-Bewertung an der echten User-Absicht verankert). `per_query_k = top_k * rerank_multiplier` — jede Sub-Query über-fetched. Diagnose-Logs `[EXPAND-97]` auf WARNING-Level zeigen Original / Expansionen / Pool-Größe.
- Block C: legacy.py bedient sich via Import bei `_rag_search` — automatisch gedeckt. Config: `query_expansion_enabled: true`, `query_expansion_model: null` in `config.yaml` + `.example`.
- Block D: Eval-Lauf nach Server-Restart — 11 Fragen. **Ergebnis: 9-10 JA / 1-2 TEILWEISE / 0 NEIN** (wie Patch 89). Expansion feuerte bei allen 11 Fragen (3-5 Varianten je Frage, siehe Logs). **Q11 bleibt TEILWEISE wie erwartet** — der Index hat nur 12 Chunks, Dedupe schöpft ihn komplett aus → Expansion kann den Pool nicht vergrößern, Rerank wählt trotzdem Glossar. Bestätigt: Retrieval ist ausgereizt, nächster Hebel ist **R-07 Multi-Chunk-Aggregation** auf LLM-Seite (neu im Backlog).
- Server-Reload-Lesson reproduziert: `--reload` erkannte die Änderungen nicht (Worker-Prozess hing), manueller Kill + Neustart nötig (analog Patch 95).

**Patch 96** – Testreport-Viewer in Hel (H-F04) (2026-04-18)
- Block A: Zwei neue Endpoints am Ende von [hel.py](zerberus/app/routers/hel.py): `GET /hel/tests/report` liefert `zerberus/tests/report/full_report.html` als `HTMLResponse` (404+JSON falls nicht vorhanden), `GET /hel/tests/reports` listet alle `*.html`-Dateien mit `mtime`+`size`. `_REPORT_DIR` per `Path(__file__).resolve().parents[2] / "tests" / "report"` — robust gegen aktuelles Working-Dir. Auth via `verify_admin`-Router-Dependency.
- Block B: Neue Akkordeon-Sektion `🧪 Testreports` direkt nach Metriken (vor LLM & Guthaben), gleicher `.hel-section`/`toggleSection`-Pattern wie alle anderen Sektionen. Card mit Erklärungstext, Button „Letzten Report öffnen" (öffnet `/hel/tests/report` in neuem Tab via `window.open`), darunter Tabelle aller Reports (Datei, Stand-Datum, Größe, Link). JS-Funktion `loadReportsList()` läuft im DOMContentLoaded-Handler, neben `loadProfilesList` aus Patch 95.
- Block C: API verifiziert (`/hel/tests/report` → HTTP 200, `/hel/tests/reports` → 3 Dateien gelistet). Hel-HTML enthält 6 neue Marker (`section-tests`, `loadReportsList`, `reportsList`). **Test-Suite re-run nach allen drei Patches: 32 passed in 56 s.**
- Backlog-Item 11 (H-F04) ✓ erledigt.

**Patch 95** – Per-User-Filter im Hel Metriken-Dashboard (2026-04-18)
- Block A: Neuer Endpoint `GET /hel/metrics/profiles` ([hel.py:2242](zerberus/app/routers/hel.py:2242)) — `SELECT DISTINCT profile_key FROM interactions WHERE profile_key IS NOT NULL`. Auth-Schutz automatisch über `verify_admin`-Router-Dependency. Spaltennot-Check identisch zu `metrics_history` (PRAGMA table_info), gibt `{"profiles": []}` zurück falls Patch-92-Spalte fehlt.
- Block B: Frontend-Dropdown (`<select id="profileSelect" class="profile-select">`) **vor** den Zeitraum-Chips ([hel.py:489](zerberus/app/routers/hel.py:489)). Default-Option „Alle Profile" + dynamisch aus dem Endpoint befüllt. Eigener CSS-Block `.profile-select` matched die `.time-chip`-Optik (gold-Ring, 36 px, `:active`-Fallback). `loadMetricsChart()` hängt `&profile_key=...` an die URL wenn ausgewählt; `change`-Event ruft `loadMetricsChart(_currentTimeRange)` → kombiniert sich sauber mit Zeitraum-Chips.
- Block C: Verifikation: `/hel/metrics/profiles` → `{"profiles":["chris"]}`. `/hel/metrics/history?profile_key=chris&limit=1` → 1 Result. `/hel/metrics/history?profile_key=nonexistent` → 0 Results (Filter aktiv). HTML enthält 10 neue Marker (`profileSelect`, `metric-profile-filter`, `loadProfilesList`).
- Server-Reload-Lesson: `--reload` blieb hängen, weil parallel ein langlaufender Voice-Request lief; selektives Killen des Worker-Prozesses (Reloader-PID erhalten) löste das ohne kompletten Neustart.

**Patch 94** – Loki & Fenrir Erstlauf + Test-Bugfix (2026-04-18)
- Block A: Erstlauf der Patch-93-Suite gegen den live Server (`https://127.0.0.1:5000`) — `pytest zerberus/tests/ -v --html=zerberus/tests/report/full_report.html --self-contained-html`. Ergebnis Erstlauf: **31 passed, 1 skipped** in 49 s. Alle 14 Chaos-Payloads (XSS, SQLi, Log4Shell, Prompt-Injection, Emoji-Bombe, RTL, Nullbyte, Path-Traversal, …) ohne 500er. Bogus-Dates auf `/hel/metrics/history` korrekt abgefangen. **Keine App-Bugs gefunden.**
- Block B: Ein Test-Bug behoben — `TestNavigation::test_hamburger_menu_opens` skippte stillschweigend, weil der Selector nur `<button>` matchte; in nala.py:885 ist der Hamburger ein `<div class="hamburger">`. Locator um `.hamburger` erweitert ([test_loki.py:81](zerberus/tests/test_loki.py:81)). Re-Run: **32 passed, 0 skipped** in 47 s.
- Block C: HTML-Report `zerberus/tests/report/full_report.html` (78 KB, self-contained) verifiziert.

**Hotfix Git-Hygiene** – Push-fähig machen (2026-04-18)
- `GitHubDesktopSetup-x64.exe` (181 MB) aus gesamter Git-History entfernt via `git filter-repo`.
- `.gitignore` erweitert um: `*.bak`, `backup_p*/`, `*.exe`, `.cache/`, `*.bin/safetensors/onnx`, `Ratatoskr/`, OS/IDE-Dateien.
- 9 `.bak`-Dateien + `backup_p69/` aus Git-Tracking entfernt (lokal erhalten). Keine Secrets im Repo.
- Erstes erfolgreiches Push zu `https://github.com/Bmad82/Zerberus.git` (neuer Branch `main`).

**Patch 93** – Loki & Fenrir: Playwright E2E + Chaos-Tests (2026-04-18)
- Block A: Playwright 1.58 + Chromium installiert, `zerberus/tests/` Package mit `conftest.py`. Test-Profile `loki`/`fenrir` in `config.yaml` angelegt (bcrypt-Hashes).
- Block B: `test_loki.py` — 4 Test-Klassen (TestLogin, TestChat, TestNavigation, TestHel) plus `TestMetricsAPI`. Mobile-Viewport als Default (390×844).
- Block C: `test_fenrir.py` — `CHAOS_PAYLOADS` (15 bösartige Strings: XSS, SQL-Injection, Log4Shell, Prompt-Injection, Emoji-Bomben, RTL-Arabisch, Nullbytes).

**Patch 92** – DB-Fix: `profile_key` in `interactions` + Alembic-Setup (2026-04-18)
- Neue Spalte `profile_key TEXT DEFAULT NULL` in `interactions` per `ALTER TABLE` (idempotent). Altdaten: `profile_name → profile_key` für 76/4667 Zeilen kopiert.
- Alembic 1.12.1 initialisiert — Baseline-Revision `7feab49e6afe_baseline_patch92_profile_key`. **Kein Auto-Upgrade beim Serverstart.**
- Backup: `bunker_memory_backup_patch92.db` vor der Migration gezogen.

**Patch 91** – Metriken-Dashboard Overhaul (Chart.js + Zeiträume + Metrik-Toggles) (2026-04-18)
- Chart.js 4.4.7 + `chartjs-plugin-zoom` 2.0.1 + hammer.js 2.0.8 via CDN. 5 Zeitraum-Chips, 5 Metrik-Toggle-Pills, Zoom-Reset-Button.
- Response-Envelope: `{meta: {from,to,count,profile_key,profile_key_supported}, results: [...]}`.

**Patch 90** – Aufräum-Sammelpatch (2026-04-18)
- `rag_eval.py` auf HTTPS umgestellt. Hel: Schriftgrößen-Wahl. Landscape-Fix für Nala+Hel. WhisperCleaner-UI auf Karten-Liste umgestellt.

**Patch 89** – R-03 Cross-Encoder-Reranker (RAG zweite Stufe) (2026-04-18)
- Neues Modul `reranker.py`: `BAAI/bge-reranker-v2-m3`. **Eval-Delta: 4/11 JA → 10/11 JA.**

**Patch 88** – R-01 Residual-Chunk-Filter (2026-04-18)
- `min_chunk_words=120` in Chunking + Retrieval. Re-Index: 18 → 12 Chunks.

**Patch 87** – RAG-Eval Durchlauf + Qualitätsanalyse (2026-04-17)
- Ergebnis: 4/11 JA, 5/11 TEILWEISE, 2/11 NEIN. Residual-Tail-Chunks als Hauptproblem identifiziert.

**Patch 86** – Nala Hamburger + Settings-Overhaul (N-F05…N-F09) (2026-04-17)
- Export-Modal, Abmelden ins Hamburger-Menü, Bubble-Farben, Schriftgröße, Favoriten v2.

**Patch 85** – UI-Bugs + Hel-Akkordeon + RAG-Fix (2026-04-17)
- Typing-Bubble in allen 3 Codepfaden. Touch-Support `:active`-Sweep. RAG-Skip-Logik OR→AND.

**Patch 84** – Hel Bugfixes: Preise, Kosten-Delta, Metriken, Sentiment (2026-04-15)

**Patch 83** – Jojo Login + Admin-Tools + RAG-Debug + Whisper-Filter + Sternenregen (2026-04-15)

**Patch 82** – Dictate Fast Lane + start.bat Fix (2026-04-14)

**Patch 81** – System-Prompt Chris + RAG-Reset (2026-04-14)

**Patch 80b** – Smalltalk-Bug: Diagnose + Fix (2026-04-14)

**Patch 79** – start.bat + Theme-Editor + Export-Timestamp (2026-04-14)

**Patch 78b** – RAG/History-Kontext-Fix (2026-04-14)

**Patch 78** – Login-Fix + config.json Cleanup (2026-04-14)

**Patch 77** – Nala UI Overhaul: Archiv + Design + Theme-Editor (2026-04-12)

**Patch 76** – Nala UI: Bug-Fixes + Ladeindikator (2026-04-12)

**Patch 75** – RAG Embedding-Fix + Chunk-Size-Fix (2026-04-12)

**Patch 73** – Self-Service Passwort-Reset (2026-04-12)

**Patch 72** – Nala Begrüßung Fix (2026-04-12)

**Patch 71** – Jojo Login Fix (2026-04-12)

**Patch 70** – RAG-Eval + Startup-Logging + Hel-UI (2026-04-12)

**Patch 69c** – SyntaxError-Fix exportChat (2026-04-11)

**Patch 69b** – Dokumentations-Restrukturierung (2026-04-11)

**Patch 69a** – Bug-Fixes: Whisper-Cleaner, Kostenformat (2026-04-11)

**Patch 68** – Login-Bug + RAG-Cleanup (2026-04-10)

**Patch 67** – Nala Frontend UI-Fixes (2026-04-10)

**Patch 66** – RAG Chunk-Optimierung (2026-04-10)

## Offene Items (Backlog)
1. Manuell getippter Text → DB-Speicherung verifizieren
2. [Patch 101] R-07 Live-Chat-Verifikation für Q11: Aggregation-Hint im echten Nala-Chat testen (z.B. „Nenn alle Momente wo Annes Verhalten als unkontrollierbarer Impuls beschrieben wird"). Wenn LLM jetzt mehrere Treffer aufzählt statt nur das Glossar zusammenzufassen → R-07 abgeschlossen. Wenn nicht → Reranker-Tuning (Glossar-Chunk bekommt zu hohen Score).
3. [Patch 101] Globale Lessons pflegen — nach jedem Patch prüfen ob eine Erkenntnis universell ist (→ Bmad82/Claude/lessons/), oder projektspezifisch (→ Zerberus/lessons.md).
4. RAG-Auto-Indexing: falls Konversations-Gedächtnis später wieder gewünscht → als optionalen Config-Schalter reaktivieren
5. [IDEE] Metriken: LLM-Auswertung („Wie haben sich meine Formulierungen verändert?") — Grundlage vorhanden (Patch 91+95)
6. [BACKLOG] Hel RAG-Tab: Dokumentenliste gruppiert anzeigen (pro Dokument eine Zeile mit Chunk-Anzahl)
7. [Patch 100] Pre-Commit-Hook / CI-Schritt: `node --check` auf alle `<script>`-Blöcke in hel.py + nala.py (schnellere Variante der `TestJavaScriptIntegrity`-Runtime-Tests)
8. [Patch 107] Doppelte Pipeline-Verarbeitung: Dictate-Tastatur schickt bereits gecleanten Text, legacy.py cleaned nochmal. Aktuell harmlos (idempotent), aber bei nicht-idempotenten Regeln problematisch. Lösung: `X-Already-Cleaned`-Header oder Channel-basierter Skip in [legacy.py](zerberus/app/routers/legacy.py) `clean_transcript`-Aufruf.
9. [Patch 107] DB-Deduplizierung: Tastatur-Retries bei schlechtem Empfang erzeugen identische aufeinanderfolgende Messages in `bunker_memory.db`. Overnight-Job soll Duplikate erkennen und markieren/entfernen. Kriterium: gleicher `profile_key` + gleicher `content` + `timestamp` innerhalb von 60 Sekunden.
10. [Langzeit] Projekt-Oberfläche in Nala: Eigener Workspace pro Projekt mit Dateien, eigenem RAG-Index, Code-Execution in Docker-Sandbox mit Sancho-Panza-Veto (Multi-Agent-Prüfung vor Execution). War von Anfang an geplant, aus Scope rausgefallen. Abhängigkeit: Rosa/Heimdall für Execution Oversight.
11. [Patch 108 → Phase 2 Folge-Patches] Category-Tagging ist jetzt da, aber noch KEIN Filtering. Folge-Arbeit: **Chunking-Weiche pro Doc-Typ (Patch 109)**, **Query-Router mit Category-Filter (Patch 110)**, **Background Memory Extraction (Patch 111)**.
12. [Patch 108] W-001 Sentence-Repetition-Bug (Whisper), Multimodalität ZIP/Bild-Upload, Relative Pfade — aus Scope raus, in Backlog verschoben.

### Erledigt in Patches 105-109
- Llama-Hardcode-Verdacht (Patch 105 — kein Hardcode, stattdessen Split-Brain in Hel gefixt)
- Reranker-Threshold fehlt (Patch 105)
- RAG-Skip für Textverarbeitung (Patch 106)
- RAG Category-Tagging beim Upload + Anzeige (Patch 108)
- CLAUDE_ZERBERUS Regel 9 „User-Entscheidungen als klickbare Box" (Patch 108)
- SSE-Timeout doppelter OpenRouter-Call beim Retry (Patch 109 — REST-Fallback via `/archive/session/{id}`)
- Theme-Defaults ohne rgba-Tiefe + unvollständiger `resetTheme()` (Patch 109)

## Architektur-Warnungen
- Rosa Security Layer: NICHT implementiert — Dateien im Projektordner sind nur Vorbereitung
- JWT blockiert externe Clients komplett — static_api_key ist der einzige Workaround
- Chart.js, zoom-plugin, hammer.js via CDN — bei Air-Gap ist das Metriken-Dashboard tot

## Langfrist-Vision
Metric Engine = kognitives Tagebuch + Frühwarnsystem für Denkmuster-Drift.
Rosa Corporate Security Layer = letzter Baustein vor kommerziellem Einsatz.
Telegram-Bot als Zero-Friction-Frontend für Dritte (keine Tailscale-Installation nötig).

## Dont's für Supervisor
- PROJEKTDOKUMENTATION.md NICHT vollständig laden (1900+ Zeilen = Kontextverschwendung)
- Memory-Edits max 500 Zeichen pro Eintrag
- Session-ID ≠ User-Trennung — Metriken pro User erst nach DB-Architektur-Fix vertrauenswürdig
- Patch-Prompts IMMER als `.md`-Datei generieren — NIE inline im Chat. Claude Code erhält den Inhalt per Copy-Paste aus der Datei (Patch 101).
- Dateinamen `CLAUDE_ZERBERUS.md` und `SUPERVISOR_ZERBERUS.md` sind FINAL — in Patch-Prompts nie mit alten Namen (`CLAUDE.md`, `HYPERVISOR.md`) referenzieren (Patch 100/101).
- Lokale Pfade: Ratatoskr liegt unter `C:\Users\chris\Python\Rosa\Nala_Rosa\Ratatoskr\` (nicht `Rosa\Ratatoskr\`), Bmad82/Claude unter `C:\Users\chris\Python\Claude\` (nicht `Rosa\Claude\`). Patch-Prompts mit falschen Pfaden → immer erst verifizieren, nicht raten.
