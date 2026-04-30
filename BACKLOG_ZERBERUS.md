# BACKLOG — Zerberus Pro 4.0
*Konsolidiertes Backlog. Eine Datei, alle offenen Items.*
*Stand: Patch 182 (2026-04-30) — B-001..B-005 erledigt durch P180/P181/P182*
*Coda aktualisiert nach jedem Patch. Chris hakt ab.*

**Workflow:** Coda schreibt neue Items rein nach jedem Patch. Coda prüft im Code, ob ein Item bereits erledigt ist, bevor es übernommen wird. Items, die hier gestrichen werden, landen im Kommentar-Block am Ende.

---

## Sofort (nächste Patches, sicherheits- oder live-relevant)

| ID | Item | Herkunft | Status |
|----|------|----------|--------|
| B-001 | Guard kennt RAG-Inhalte nicht — RAG-Chunks als `rag_context` an `check_response()` reichen, sonst werden korrekte RAG-Antworten als Halluzination geflaggt | L-178a | ERLEDIGT (P180) |
| B-002 | Guard kennt aktive Persona nicht — Persona aus Settings-Cache (`@invalidates_settings`) in `caller_context` einhängen | L-178b | ERLEDIGT (P180, Persona-Cap 300→800) |
| B-003 | Telegram-Bot öffentlich erreichbar — Allowlist `modules.telegram.allowed_users` in config.yaml; unbekannte User werden mit einmaliger Absage abgewiesen, kein LLM-Call | L-178e | ERLEDIGT (P181) |
| B-004 | Intent-Router ADMIN zu sensitiv — „Wie ist das aufgebaut?" wird als ADMIN klassifiziert (erzwingt HitL). Plausibilitäts-Heuristik (Slash-Prefix oder Admin-Keyword), sonst Downgrade auf CHAT | L-178c | ERLEDIGT (P182) |
| B-005 | Sprachnachrichten / Sticker / Dokumente in Telegram-DM laufen lautlos ins Leere — Unsupported-Media-Handler mit freundlicher Absage, kein LLM-Call | L-178g | ERLEDIGT (P182) |

## Mittelfristig (Phase 5 / Architektur)

| ID | Item | Herkunft | Status |
|----|------|----------|--------|
| B-010 | FAISS-Migration durchziehen — P133-Backup vorhanden, Switch wartet auf Chris (`--execute`) | SUPERVISOR Item 14 / Phase-5-Vorbereitung | OFFEN |
| B-011 | Prosodie-Pipeline (Gemma 4 E2B) — siehe `backlog_SER_prosody.md` für Architektur-Konzept | Phase-5-Vorbereitung | OFFEN |
| B-012 | Phase 5a (Nala Projekte) ab P180 — siehe `NALA_PROJEKTE_PRIORISIERUNG.md` und `NALA_PROJEKTE_FEATURE_SPEC.md` | Handover P178 / Long-Backlog | OFFEN |
| B-013 | Background Memory Extraction (Phase 4) — wurde aus Phase 2 umgeplant, aktuell NICHT implementiert | SUPERVISOR Item 11 | OFFEN |
| B-014 | Phase 4 Features: LLM-basierte Category-Detection, Sancho-Panza Veto-Layer, Halluzinations-Detektor, Multimodalität ZIP/Bild-Upload, Color Picker | SUPERVISOR Item 12 | OFFEN |
| B-015 | R-07 Multi-Chunk-Aggregation für Aggregat-Queries („Nenn alle Momente wo …") — Query-Typ-Erkennung + top_k 8+ ohne Rerank-Cut | SUPERVISOR Item 2 / RAG R-07 | OFFEN |
| B-016 | Runtime-Config-Endpoint statt statisches RAG für Modell-/GPU-/Patch-Info — verhindert dass „Welches Modell bist du?" durch indizierte Doku altert | L-178h / L9 | ERLEDIGT (P185 — `zerberus/utils/runtime_info.py` mit `build_runtime_info(settings)` + `append_runtime_info(prompt, settings)`. Injection in [`legacy.py`](zerberus/app/routers/legacy.py) und [`telegram/router.py`](zerberus/modules/telegram/router.py), Position NACH Persona, VOR RAG. Liefert Modellname (Kurzname), Guard-Modell, RAG/Sandbox-Status. RAG-Doku bleibt statisch für Architektur, Live-Werte kommen aus Settings.) |

## Bugs / Quality (Hel + Nala)

| ID | Item | Herkunft | Status |
|----|------|----------|--------|
| B-020 | Kostendelta letzter Prompt fehlt — `balance_vorher - balance_jetzt` wird nirgends berechnet (state für vorherigen Balance-Wert fehlt). Im Code-Sweep verifiziert: kein `cost_delta`/`prev_balance` gefunden | H-02 | OFFEN |
| B-021 | BERT/Sentiment springt zwischen Extremen — nur maximale Ausschläge, Normalisierungsfehler in `oliverguhr/german-sentiment-bert` prüfen | H-04 | OFFEN |
| B-022 | Hel Metriken: nur User-Eingaben in Berechnung verifizieren — explizit kommentieren, dass LLM-Outputs NICHT einfließen | H-05 | OFFEN |
| B-023 | RAG 500er beim ersten Upload nach Clear — Lazy-Load-Guard greift nicht immer | Reminder backlog_nach_patch83 | OFFEN |
| B-024 | HSL-Farbfehler bei Browser-Kaltstart — cssToHex-Konvertierung → schwarze UI, Dauerläufer seit P153 | L8 | ERLEDIGT (P183 — vierter Anlauf, zentraler `sanitizeBubbleColor` mit regex-basiertem `_isBubbleBlack` + `cleanBlackFromStorage` Sweep an Pre-Render und in `showChatScreen`. Alle 12 setProperty-Pfade jetzt durchgeschleift. 49 Tests in `test_patch183_blackbug.py`. Hoffentlich endgültig.) |
| B-025 | Manuell getippter Text → DB-Speicherung verifizieren | SUPERVISOR Item 1 | OFFEN |

## Features / UX

| ID | Item | Herkunft | Status |
|----|------|----------|--------|
| B-030 | N-F02 Lade-Indikator: drehendes Rad + „Denke nach …" als Upgrade über die P144-Katzenpfoten — prüfen ob Katzenpfoten den Wunsch bereits abdecken | N-F02 | UNKLAR |
| B-031 | Hel RAG-Tab: Dokumentenliste gruppiert anzeigen (eine Zeile pro Dokument mit Chunk-Anzahl) | SUPERVISOR Item 6 | OFFEN |
| B-032 | Metriken-Tabelle optisch überarbeiten | Reminder | OFFEN |
| B-033 | `:active` statt `:hover` durchgängig auf Mobile prüfen | Reminder | OFFEN |
| B-034 | Metriken: LLM-Auswertung („Wie haben sich meine Formulierungen verändert?") — Grundlage seit P91+95 vorhanden | SUPERVISOR Item 5 | IDEE |

## RAG-Qualität (Bonus, nach P89-Reranker weniger dringend)

| ID | Item | Herkunft | Status |
|----|------|----------|--------|
| B-040 | R-02 Embedding-Modell-Upgrade auf `BAAI/bge-m3` als Bi-Encoder — Reranker kompensiert MiniLM-Schwäche aktuell ausreichend | RAG R-02 | PRIO RUNTER |
| B-041 | R-05 Sektion-Typ als Metadata-Feld (Akt/Prolog/Epilog/Glossar beim Chunking mitspeichern) — Bonus-Optimierung | RAG R-05 | OFFEN |
| B-042 | R-06 MMR/Diversity-Reranking — Bonus-Optimierung | RAG R-06 | OFFEN |
| B-043 | RAG-Auto-Indexing als optionaler Config-Schalter reaktivieren (falls Konversations-Gedächtnis später wieder gewünscht) | SUPERVISOR Item 4 | OFFEN |

## Infrastruktur / DevOps

| ID | Item | Herkunft | Status |
|----|------|----------|--------|
| B-050 | torch-CUDA-Installation prüfen — RTX 3060 ungenutzt wenn pip-Default-CPU-Variante installiert ist. Aktuelle Empfehlung: `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 --force-reinstall` (laut lessons.md P111b) + nach jedem Reinstall `pip install --upgrade "typing-extensions>=4.13.2"` | SUPERVISOR Item 14 (P111b-Update) | OFFEN |
| B-051 | Pre-Commit-Hook / CI-Schritt: `node --check` auf alle `<script>`-Blöcke in `hel.py` + `nala.py` (schnellere Variante der `TestJavaScriptIntegrity`-Runtime-Tests) | SUPERVISOR Item 7 | OFFEN |
| B-052 | Globale Lessons pflegen — nach jedem Patch prüfen, ob eine Erkenntnis universell ist (→ `Bmad82/Claude/lessons/`), oder projektspezifisch (→ `Zerberus/lessons.md`) | SUPERVISOR Item 3 | DAUERAUFGABE |

## Whisper / Pipeline

| ID | Item | Herkunft | Status |
|----|------|----------|--------|
| B-060 | W-001 Sentence-Repetition-Bug (Whisper) | SUPERVISOR Item 13 | OFFEN |
| B-061 | Relative Pfade Audit | SUPERVISOR Item 13 | OFFEN |

## Langfristig / Ideen

| ID | Item | Herkunft | Status |
|----|------|----------|--------|
| B-070 | Easter Egg: `// make love ... not war — W.F. Weiher, Stanford AI Lab, 1967` als Kommentar tief im Rosa-Code (erstes dokumentiertes Software-Easter-Egg) | L10 | IDEE |
| B-071 | ChatML-Wrapper / SillyTavern-Style Personas — Persona nicht als flacher System-Prompt sondern als strukturiertes Character-Card-Format (ChatML `<|im_start|>system`). Felder: Persönlichkeit, Beispiel-Dialoge, Szenario, First-Message. Importierbare `.json`-Character-Cards über Hel hochladbar. Löst Persona-Drift über lange Konversationen (DeepSeek fällt nach ~10-15 Turns in RLHF-Default zurück). Priorität: nach Prosodie + Tool-Use. | Chat 2026-04-30 | IDEE |
| B-072 | Huginn Voice→Whisper-Pipeline — Sprachnachrichten in Telegram-DMs über lokalen Whisper-Container transkribieren statt freundlich abzulehnen (P182 Block 2 Option B). Audio von TG-API runterladen → an localhost:8002 → Transkript → normaler Text-Pfad. Abhängigkeit: Whisper-Container muss laufen. | P182 / L7 | IDEE |

---

## Verweise

- **Prosodie-Pipeline (Gemma 4 E2B):** Architektur-Konzept in [`backlog_SER_prosody.md`](backlog_SER_prosody.md) — Tracking läuft hier (B-011)
- **Nala Projekte Phase 5:** [`NALA_PROJEKTE_PRIORISIERUNG.md`](NALA_PROJEKTE_PRIORISIERUNG.md) (Tracking B-012)
- **Feature-Spec Phase 5:** [`NALA_PROJEKTE_FEATURE_SPEC.md`](NALA_PROJEKTE_FEATURE_SPEC.md)
- **Live-Test-Erkenntnisse:** [`SUPERVISOR_ZERBERUS.md`](SUPERVISOR_ZERBERUS.md) Sektion „Live-Test-Erkenntnisse (Sweep nach P178b, 2026-04-30)"
- **Lessons:** [`lessons.md`](lessons.md) (projektspezifisch) + `Bmad82/Claude/lessons/` (universell)

---

## Status-Legende

- **OFFEN** — noch nicht angefangen, in der Pipeline für einen Patch
- **UNKLAR** — Item-Beschreibung deckungsgleich mit etwas das vermutlich schon erledigt wurde, Live-Verifikation nötig
- **PRIO RUNTER** — durch andere Patches teilweise abgefangen, kein Akutthema
- **IDEE** — Wunsch ohne konkrete Spec, kein Patch geplant
- **DAUERAUFGABE** — Prozess statt Item

---

<!--
Verifiziert erledigt während P179-Konsolidierung (NICHT mehr im Backlog):
- H-01 LLM-Dropdown $/1M Token: erledigt — `hel.py:1170/1177/1193/1194/1354/1355` zeigen `$/1M` mit `* 1_000_000` (vermutlich seit P84/P90)
- H-03 Hel Metriken Chat-Text zu breit: erledigt in P146 — `hel.py:3590/3591` truncate `raw[:50] + '…'`
- L-178d system-Kategorie im Hel-Frontend-Dropdown: erledigt in P178c
- L-178f start.bat Port-Fix: erledigt — netstat+taskkill-Schleife in start.bat:9-14
- SUPERVISOR Item 8 Doppelte Pipeline-Verarbeitung (Dictate): erledigt in P135 — `X-Already-Cleaned`-Header in `legacy.py:364-367` und `nala.py:3958-3962`
- SUPERVISOR Item 9 DB-Deduplizierung Overnight-Job: erledigt in P134 — `zerberus/utils/db_dedup.py` + Tests in `test_db_dedup.py`
- N-F03 Wiederholen-Button: erledigt in P98
- N-F04 Bearbeiten-Button: erledigt in P98
- N-F09b Schriftgröße Hel: erledigt in P90
- N-F10 Display-Rotation: erledigt in P90
- H-F01 Sticky Tab-Leiste: erledigt in P99
- H-F03 Mehr Metriken-Auswahl: Grundlage erledigt in P91 (Chart.js)
- H-F04 Loki & Fenrir Reports im Hel-Dashboard: erledigt in P96
- R-01 Mindest-Chunk-Länge: erledigt in P88 (`min_chunk_words=120`)
- R-03 Cross-Encoder-Reranker: erledigt in P89 (`bge-reranker-v2-m3`)
- R-04 Query-Expansion via LLM: erledigt in P97
-->
