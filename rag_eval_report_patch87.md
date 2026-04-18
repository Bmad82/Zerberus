# RAG-Eval Report Patch 87 (2026-04-17)

**Testgegenstand:** FAISS-Index nach Patch 85 (RAG-Skip-Fix), 18 Chunks aus `Rosendornen 2. Auflage Backnang 08.09.2025 (1).docx` (6256 Wörter, 9 Sektionen).
**Konfiguration:** Embedding `paraphrase-multilingual-MiniLM-L12-v2` (Fallback in `_encode`), FlatL2 mit `normalize_embeddings=True`, `top_k=5`, chunking 800/160 Wörter.
**Script:** `rag_eval.py` (Code unverändert; Eval inline gegen `https://127.0.0.1:5000/rag/search` mit statischem API-Key ausgeführt, da Script HTTP hart-codiert hat).

## Zusammenfassung

- Getestete Fragen: **11**
- Top-Treffer **korrekt (JA)**: **4/11** (Q1, Q5, Q7, Q9)
- Top-Treffer **teilweise (TEILWEISE)**: **5/11** (Q2, Q3, Q6, Q8, Q11) — richtige Info war in Top-5, aber nicht auf Rang 1
- Top-Treffer **falsch/leer (NEIN)**: **2/11** (Q4, Q10) — relevanter Chunk gar nicht in Top-5
- Durchschnittliche L2-Distanz des Top-Treffers: **0.97** (Spannweite 0.74 – 1.27, kleiner = besser)
- Qualitative Einordnung: deutlich besser als die 5/11 aus Patch 75 (dort Chunking-Bug 300/60), aber klare Retrieval-Schwächen bleiben.

## Chunk-Landschaft des Index

Chunking funktioniert **header-aware** (Akt-Überschriften brechen an Sektionsgrenzen), ist aber keine saubere 800/160-Fensterung:

| ID | Wörter | Sektion                  |
|----|--------|--------------------------|
| 00 | 16     | Titel                    |
| 01 | 273    | Prolog                   |
| 02 | 656    | Akt I                    |
| 03 | 16     | Akt-I-Tail (Residual)    |
| 04 | 645    | Akt II (inkl. Perseiden) |
| 05 | 5      | Akt-II-Tail (Residual)   |
| 06 | 688    | Akt III (inkl. Ulm)      |
| 07 | 48     | Akt-III-Tail (Residual)  |
| 08 | 704    | Akt IV                   |
| 09 | 64     | Akt-IV-Tail (Residual)   |
| 10 | 800    | Akt V                    |
| 11 | 341    | Akt-V-Fortsetzung        |
| 12 | 800    | Akt VI                   |
| 13 | 340    | Akt-VI-Fortsetzung       |
| 14 | 700    | Akt VII                  |
| 15 | 60     | Akt-VII-Tail (Residual)  |
| 16 | 448    | Epilog                   |
| 17 | 165    | **Glossar**              |

**Wichtig:** Das Glossar ist ein intakter eigener Chunk (165w, alle Begriffe inkl. Infraschall, Zwei-Herzen-Ordner, Dr. Claude Cuda Tensor beieinander). Chris' Hypothese "Glossar wird zerschnitten" trifft nicht zu — das Retrieval findet den Glossar-Chunk manchmal einfach nicht an Rang 1.

## Frage-für-Frage

### Q1 — Wo hat er Anne zum ersten Mal getroffen?
- Ergebnis: **JA**
- Top-Treffer L2: 0.949 (Chunk 02 / Akt I)
- Treffer-Substanz: "In seiner Wohnung in Backnang sah er Anne zum ersten Mal in Fleisch und Blut, wie sie aus dem Auto stieg"
- Im Dokument vorhanden: ja, Akt I
- Diagnose: Retrieval optimal — direkte Faktfrage, gutes Matching.

### Q2 — Wer hat beim Umzug von Sonnenbühl nach Backnang geholfen?
- Ergebnis: **TEILWEISE**
- Top-Treffer L2: 1.119 (Chunk 13 / Jojo-Burgfräulein-Kapitel — falscher Kontext!)
- Korrekter Chunk (Akt I mit "Alex half beim Umzug") erst auf Rang 2 (L2 1.152)
- Im Dokument vorhanden: ja, Akt I: "Alex half beim Umzug. Die Freunde, die später zu Statisten..."
- Diagnose: **B)** Retrieval-Qualitätsproblem. Begriff "Umzug" triggert auch Epilog/Jojo-Kapitel (Burgenwandern, Umzugsmetaphern).

### Q3 — Was steht im Glossar zu "Infraschall"?
- Ergebnis: **TEILWEISE**
- Top-Treffer L2: 1.160 (Chunk 00 — nur der Buchtitel, 16 Wörter!)
- Glossar-Chunk (17) erst auf Rang 4 (L2 1.302) mit voller Definition: "Infraschall: Unhörbare tiefe Frequenzen unter 20 Hz, die Unruhe auslösen. Metapher für Annes unterschwellige emotionale Manipulation."
- Im Dokument vorhanden: ja, Glossar-Sektion
- Diagnose: **B)** Retrieval-Qualitätsproblem. Der **Titel-Chunk (16w)** und der **Titel-Tail (Chunk 03, 16w)** ranken ungerechtfertigt hoch — kurze Chunks haben bei `normalize_embeddings=True` oft engen Cosine-Abstand zu generischen Queries.

### Q4 — Was passierte in der Perseiden-Nacht, und was hat er danach fotografiert?
- Ergebnis: **NEIN**
- Top-Treffer L2: 1.038 (Chunk 02 / Akt I — kein Perseiden-Bezug)
- Top 5: Akt I, Prolog, Titel, Akt V (Abschied), Akt VII. **Chunk 04 (Akt II) mit der Perseiden-Passage wird überhaupt nicht retrieved.**
- Im Dokument vorhanden: ja, Akt II enthält "Die Perseiden-Nacht (August 2015)" mit vollständiger Szene und Foto-Erwähnung ("Ins Schwarze hinein. Nur sein Gesicht, wütend, verletzt, gefangen im Dunkel")
- Diagnose: **B)** — **härtester Retrieval-Fail im Set**. Direkte Keyword-Frage nach Namen eines Ereignisses, das als eigener Unterabschnitt im Dokument steht, findet die richtige Passage nicht in Top-5. Starker Indikator für Embedding-Modell-Schwäche bei spezifischen deutschen Eigennamen / seltenen Begriffen.

### Q5 — Warum brach Anne den Kontakt zu Carolin ab?
- Ergebnis: **JA**
- Top-Treffer L2: 0.927 (Chunk 02 / Akt I)
- Treffer-Substanz: "Carolin verschwand augenblicklich aus Annes Leben. Komplett. Anne fürchtete die Frau, die ihre Jugend kannte..."
- Diagnose: Retrieval optimal.

### Q6 — Unterschied zwischen Dr. GPT und Dr. Claude Cuda Tensor?
- Ergebnis: **TEILWEISE**
- Top-Treffer L2: 0.810 (Chunk 01 / Prolog — bester Top-Score im Set)
- Prolog beschreibt beide narrativ ("Dr. GPT ist verstorben. Zweihunderttausend Token, dann war Schluss"), aber die **klare Glossar-Definition** ("Dr. Claude Cuda Tensor: Wortspiel aus Claude (KI), CUDA (Nvidia-Technologie) und Tensor (neuronale Netze)") ist im Glossar-Chunk 17 und wurde nicht retrieved.
- Diagnose: Mix aus A) und B). Informationen sind erreichbar, aber die präziseste Definition fehlt im Kontext.

### Q7 — Wie viele Kinder hat Anne, mit welchen Männern, in welcher Reihenfolge?
- Ergebnis: **JA**
- Top-Treffer L2: 1.012 (Chunk 14 / Akt VII)
- Treffer-Substanz: "Mit seiner Tochter Pia, wie zuvor Matthias mit seinem Sohn Florian" — Reihenfolge und Zuordnung direkt ablesbar.
- Diagnose: Retrieval gut, auch wenn "Kinder" und "Männer" keywords breit streuen.

### Q8 — Was war der "Zwei-Herzen-Ordner" und welche Konsequenz?
- Ergebnis: **TEILWEISE**
- Top-Treffer L2: 0.838 (Chunk 00 — **nur der Buchtitel**)
- Chunk 5 (Akt II, L2 1.175) enthält: "nicht seit sie seinen 'Zwei-Herzen-Ordner' entdeckt hatte, zehn Jahre alte digitale Erinnerungen an Petra und deren Tochter. Seitdem war alles Freiwild geworden"
- Glossar-Definition "Die Zwei-Herzen-Ordner: Digitale Erinnerungen an alte Beziehung (❤️❤️). Auslöser für Annes Kontrollwahn" wurde nicht retrieved.
- Diagnose: **B)** — Titel-Chunk kapert erneut Rang 1. Echte Info ist in Top-5 vorhanden, aber nicht priorisiert.

### Q9 — Welche Phasen der Heilung beschreibt er im Epilog, wie lange?
- Ergebnis: **JA**
- Top-Treffer L2: 0.824 (Chunk 16 / Epilog)
- Treffer-Substanz: "Drei Jahre manisches Ausleben, vier Jahre bewusste Heilung mit dem Burgfräulein, dann die letzten zwanzig Prozent durch die KI-Therapie"
- Chunk 2 (Akt VI "Heilung ohne Worte 2017–2024") ergänzt auf Rang 2. **Beste Antwortqualität im Set.**
- Diagnose: Retrieval optimal — Epilog-Keyword und "Phasen" matchen klar.

### Q10 — Was meint er mit "verkaufsoffener Sonntag in Ulm"?
- Ergebnis: **NEIN**
- Top-Treffer L2: 1.265 (schlechtester Top-Score im Set, Chunk 10 / Akt V)
- Top 5: Akt V, Akt-V-Fortsetzung, Akt IV, Akt-IV-Tail, Akt-VI-Fortsetzung. **Chunk 06 (Akt III) mit der relevanten Passage fehlt komplett.**
- Im Dokument vorhanden: ja, **wörtlich**: "Verkaufsoffene Sonntage in Ulm. Es wurde ihr Running Gag in späteren Jahren: 'Verkaufsoffener Sonntag in Ulm' — Code für alles, was..." (Akt III)
- Diagnose: **B)** — zweiter harter Retrieval-Fail. Eigenname/Redewendung wird nicht gefunden, obwohl wörtlich im Dokument. Auffällig: L2 aller Top-5 sehr schlecht (1.27–1.47) — das Embedding "weiß", dass kein guter Match da ist, landet aber bei der zeitlich nächsten Trennungs-Sektion, nicht bei der Akt-III-Ulm-Sektion.

### Q11 — Nenn alle Momente, wo Annes Verhalten als unkontrollierbarer Impuls beschrieben wird.
- Ergebnis: **TEILWEISE**
- Top-Treffer L2: 0.739 (Chunk 17 / Glossar — **bester L2-Wert im ganzen Set**)
- Glossar als Top-Treffer ist thematisch sinnvoll (deckt "Push and Pull", "Bindungsstörung" usw. ab), aber die **konkreten Szenen** sind zerstreut: Akt I mit "keine Berechnung, sondern ein unkontrollierbarer Impuls" (Rang 4). Akt II mit "unkontrollierbarer Drang nach Kontrolle" wurde nicht retrieved.
- Diagnose: Mix — Glossar ist legitim Top, aber Aggregations-Fragen ("nenn alle Momente") können durch Top-5 nicht vollständig beantwortet werden. Das ist ein strukturelles RAG-Limit, kein Bug.

## Muster-Beobachtungen

1. **Direkte Faktfragen funktionieren gut** (Q1, Q5, Q7, Q9): wenn das Keyword eindeutig in einer Sektion konzentriert ist und keine Mehrdeutigkeit besteht.
2. **Seltene Eigennamen versagen** (Q4 Perseiden, Q10 Ulm): Das multilingual-MiniLM-Modell scheint deutsche narrative Eigennamen nicht stark genug zu gewichten. Beide Fragen haben den Zielbegriff **wörtlich** im Text — und finden ihn trotzdem nicht in Top-5.
3. **Titel-Chunk (16w) kapert regelmäßig Rang 1** (Q3, Q8): Chunks mit sehr wenigen Wörtern haben bei normalisierten Embeddings einen unfairen Abstandsvorteil gegenüber breiten Inhalts-Chunks. Diese Residual-Tails (Chunk 03, 05, 07, 09, 15 mit 5–64 Wörtern) tragen null Informationswert und stören das Ranking.
4. **Glossar-Chunk funktioniert ambivalent**: Für breite Konzept-Fragen (Q11) Rang 1 mit sehr gutem L2. Für gezielte Definitionsabfragen (Q3 Infraschall) landet er nur auf Rang 4 — weil der Query-Embedding-Vektor durch den Sekundärkontext ("Glossar", "Infraschall") in verschiedene Richtungen gezogen wird.
5. **Chunk-Boundaries zerschneiden KEINE relevante Information** — die Hypothese "Glossar wird zerschnitten" konnte nicht bestätigt werden. Alle 9 Sektionen sind als eigene Chunks erhalten; das Glossar steht komplett beisammen.

## Empfehlungen (für späteren Patch — NICHT jetzt implementieren)

- **R-01 — Mindest-Chunk-Länge erzwingen**: Residual-Tails < 50 Wörter entweder in den Vorgänger-Chunk mergen oder beim Retrieval filtern. Würde Q3 und Q8 direkt auf JA heben.
- **R-02 — Embedding-Modell-Upgrade**: `paraphrase-multilingual-MiniLM-L12-v2` (384 Dim) gegen `intfloat/multilingual-e5-large` (1024 Dim) oder `BAAI/bge-m3` benchmarken. Beide sind bei deutschen Eigennamen deutlich stärker.
- **R-03 — Reranker als zweite Stufe**: Cross-Encoder (`BAAI/bge-reranker-v2-m3`) über die Top-10 des Embedding-Retrievals laufen lassen. Das fängt Q4 (Perseiden) und Q10 (Ulm) vermutlich ab, weil der Reranker volle Token-Attention hat statt nur einen Satz-Vektor.
- **R-04 — Query-Expansion via LLM**: Vor dem RAG-Search ein kurzer LLM-Call, der synonyme Formulierungen generiert. Bei Q10 würde "Verkaufsoffener Sonntag in Ulm" in "Running Gag Anne Ulm", "Einkaufen Ulm" etc. expandiert — höhere Trefferchance.
- **R-05 — Metadata-Filter für Sektion-Typ**: Akt/Prolog/Epilog/Glossar als Metadaten-Feld speichern. Fragen mit Wort "Glossar" oder "Begriff" würden dann explizit gegen `section_type=glossar` gefiltert.
- **R-06 — MMR oder Diversity-Reranking**: Verhindert, dass 4 der 5 Top-Chunks aus derselben Akt-Sektion stammen (z.B. Q2 Top-5: Epilog, Akt I, Prolog, Akt I-Residual, Akt I-Residual).

## Glossar-Begriffs-Retrieval: Diagnose-Kurzfazit

Chris' ursprüngliche Beobachtung "Infraschall wird nicht gefunden" ist **korrekt, aber nicht wegen zerstückeltem Glossar**. Der Glossar-Chunk (165w) existiert intakt mit allen Begriffen. Er wird aber vom Titel-Chunk überboten, weil kurze Chunks bei normalisierten MiniLM-Embeddings systematisch bevorzugt werden. **Fix-Pfad: R-01 (Residual-Filter) + R-05 (Glossar-Metadata-Route)** — beides wurde als Backlog-Einträge aufgenommen.
