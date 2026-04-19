# SUPERVISOR.md – Zerberus Pro 4.0
*Strategischer Stand für Supervisor-Claude (claude.ai Chat-Instanz)*
*Letzte Aktualisierung: Patch 99 (2026-04-18)*

## Aktueller Patch
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
2. [Patch 97] RAG Q11 bleibt offen — Retrieval ausgereizt. Nächster Kandidat: **R-07 Multi-Chunk-Aggregation** (LLM-seitig: Top-8+ in Kontext, System-Prompt-Hint „alle Treffer aufzählen").
3. ~~Alembic-Setup~~ ✅ Patch 92 — `alembic.ini` + Baseline. **Manueller Aufruf** per `alembic upgrade head`.
4. RAG-Auto-Indexing: falls Konversations-Gedächtnis später wieder gewünscht → als optionalen Config-Schalter reaktivieren
5. [IDEE] Metriken: LLM-Auswertung („Wie haben sich meine Formulierungen verändert?") — Grundlage vorhanden (Patch 91+95)
6. [BACKLOG] Hel RAG-Tab: Dokumentenliste gruppiert anzeigen (pro Dokument eine Zeile mit Chunk-Anzahl)

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
