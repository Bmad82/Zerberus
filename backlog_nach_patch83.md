# Backlog – Stand nach Patch 83
*Hier landet alles was nicht sofort dran ist. Wird bei Patch-Planung konsultiert.*

---

## 🐛 Bugs (Hel)

**H-01 — LLM-Dropdown falsche Preiseinheit**
`$/1000 Token` → `$/1M Token`. Formel: `cost * 1_000_000`.
In Patch 69a für Metriktabelle gefixt, Dropdown vergessen.

**H-02 — Kostendelta letzter Prompt fehlt**
`balance_vorher - balance_jetzt` wird nirgends berechnet. State für vorherigen Balance-Wert fehlt.

**H-03 — Hel Metriken: Chat-Text zu breit**
Kürzen auf ersten Satz + Timestamp, analog Nala-Archiv (Patch 77).

**H-04 — Hel Metriken: BERT/Sentiment springt zwischen Extremen**
Nur maximale Ausschläge. Normalisierungsfehler in oliverguhr/german-sentiment-bert prüfen.

**H-05 — Hel Metriken: Nur User-Eingaben verifizieren**
Sicherstellen dass LLM-Outputs nicht in Metrikberechnung einfließen. Explizit kommentieren.

---

## ✨ Features — Nala

**N-F02 — Lade-Indikator: drehendes Rad + „Denke nach…"**
Sichtbar während LLM arbeitet. (Patch 76 hat Typing-Bubble, das hier wäre ein Upgrade)

**N-F03 — Wiederholen-Button an jeder Chat-Bubble**
Letzte Nachricht = neu senden. Weiter oben = Fork (alter Verlauf bleibt).

**N-F04 — Bearbeiten-Button an jeder Chat-Bubble**
Letzte = direkte Änderung. Weiter oben = Fork.

**N-F09b — Schriftgröße Hel** ✅ ERLEDIGT in Patch 90
~~Nala-Teil in Patch 86 erledigt (4 Presets via `--font-size-base`). Pendant für Hel-UI fehlt noch.~~
Hel hat jetzt analog 4 Presets (13/15/17/19) via `--hel-font-size-base`, Persistenz `localStorage('hel_font_size')`, Early-Load-IIFE gegen FOUC.

**N-F10 — Display-Rotation prüfen** ✅ ERLEDIGT in Patch 90
~~Landscape-Mode testen, CSS-Fix falls nötig.~~
Defensive `@media (orientation: landscape) and (max-height: 500px)`-Regeln in Nala UND Hel: Header/Padding/Modal-Höhen reduziert. Nala dank `100dvh` ohnehin Keyboard-tolerant; Hel padded-scroll ist von Haus aus tolerant.

---

## ✨ Features — Hel

**H-F01 — Sticky Tab-Leiste (Karteikarten)**
Tabs oben, immer sichtbar, per Wischgeste wechselbar.

**H-F02 — Dialekt-JSON + WhisperCleaner: UX statt Rohtext** ✅ ERLEDIGT in Patch 90 (WhisperCleaner-Teil)
~~Scrollbare Liste statt JSON-Editor. Strukturiertes Formular.~~
WhisperCleaner: Karten-Liste mit Pattern/Replacement/Kommentar pro Regel, Sektion-Header für Comment-Only-Einträge, Add/Trash-Buttons, JS-Regex-Validierung blockiert Save bei ungültigem Pattern. **Dialekt-JSON-Teil offen** — gleiche Behandlung sinnvoll, aber bisher nicht umgesetzt.

**H-F03 — Mehr Metriken-Auswahl + feineres Design** ✅ GRUNDLAGE IMPLEMENTIERT in Patch 91
~~Konzept noch offen. Chris will explizit erinnert werden.~~
Chart.js 4.4.7 ersetzt den manuellen Canvas-Chart. 5 Metriken (BERT, Rolling-TTR, Shannon Entropy, Hapax Ratio, Ø Wortlänge), Toggle-Pills mit Info-Icons. Zeitraum-Chips (7/30/90 Tage, Alles, Custom-Range). Pinch-to-Zoom (Touch), Wheel-Zoom (Desktop), Zoom-Reset. Feineres Design: dünne Linien 1.5 px, keine Punkte, Tooltips im Hel-Dark-Theme. Datentabelle in `<details>` ausgeklappt, `table-layout: fixed`. Backend-API `/hel/metrics/history` bekommt `{meta, results}`-Envelope + Zeitraum-Filter. Offen als Folge-Backlog: Per-User-Filter-UI, LLM-Auswertung.

---

## 📝 Reminder
- Metriken-Tabelle optisch überarbeiten → Chris erinnern wenn's ansteht
- :active statt :hover durchgängig prüfen (Mobile)
- RAG 500er beim ersten Upload nach Clear — Lazy-Load-Guard greift nicht immer

---

## 🔎 RAG-Qualität (aus Patch 87 Eval-Report)

Basis: `rag_eval_report_patch87.md`. **Eval-Stand nach Patch 89: 10/11 JA, 1/11 TEILWEISE, 0 NEIN.** Q4 + Q10 geheilt durch Cross-Encoder-Reranker.

**R-01 — Mindest-Chunk-Länge filtern** ✅ ERLEDIGT in Patch 88
~~Residual-Tails < 50 Wörter entweder mit Vorgänger-Chunk mergen oder beim Retrieval ausfiltern.~~
Implementiert mit `min_chunk_words=120` in Chunking (Fix A: merge in Vorgänger) und Retrieval (Fix B: over-fetch + filter). Index ging von 18 → 12 Chunks. Erwartung „Q3/Q8 von TEILWEISE auf JA" hat sich aber **nicht erfüllt** — Status-Verteilung unverändert. Diagnose siehe `rag_eval_delta_patch88.md`. Embedding-Modell ist die eigentliche Bremse.

**R-02 — Embedding-Modell-Upgrade evaluieren** 📉 PRIO RUNTER
Nach Patch 89 deutlich weniger dringend: Der Cross-Encoder-Reranker kompensiert die MiniLM-Schwäche bei Eigennamen ausreichend. Trotzdem als Option fürs Protokoll — `BAAI/bge-m3` als Bi-Encoder würde die Rerank-Kandidatenmenge noch etwas hübscher machen. Kein Akutthema.

**R-03 — Cross-Encoder-Reranker als zweite Stufe** ✅ ERLEDIGT in Patch 89
~~`BAAI/bge-reranker-v2-m3` über die Top-N des FAISS-Retrievals.~~
Implementiert als eigenständiges Modul `zerberus/modules/rag/reranker.py` mit Lazy-Load-Guard und Fail-Safe-Fallback. FAISS over-fetch `top_k * 4`, Rerank sortiert neu. **Eval-Delta: 4/11 JA → 10/11 JA.** Q4 (Perseiden 0.942) und Q10 (Ulm 0.929) beide auf Rang 1. Details: `rag_eval_delta_patch89.md`.

**R-04 — Query-Expansion via LLM vor RAG** ⭐ NÄCHSTER KANDIDAT (für Aggregat-Queries)
Kurzer LLM-Call vor `_search_index`, der synonyme Formulierungen / Stichworte erzeugt. Nach Patch 89 ist Q11 („Nenn alle Momente wo…") das einzige verbleibende TEILWEISE — Aggregat-Query, die mehrere Chunks gleichzeitig gewichtet braucht. Query-Expansion könnte hier jeweils einen Rerank-Call pro expandierte Sub-Query triggern, dann die Hits mergen. Alternative: LLM-seitige Multi-Chunk-Aggregation im Orchestrator (Top-N gleichzeitig in den Kontext, kein zweiter Retrieval-Pass).

**R-05 — Sektion-Typ als Metadata-Feld**
Akt/Prolog/Epilog/Glossar beim Chunking mitspeichern. Definitionsfragen ("Was steht im Glossar zu…") können dann auf `section_type=glossar` gefiltert werden. Nach Patch 89 nur noch Bonus-Optimierung — Glossar-Queries (Q3, Q6) funktionieren durch den Reranker bereits zuverlässig.

**R-06 — MMR/Diversity-Reranking**
Verhindert, dass Top-5 aus derselben Sektion kommt. Nach Patch 89 weniger dringend, weil Rerank die relevanten Chunks genauer trifft und Top-5-Diversität weniger kritisch ist.
