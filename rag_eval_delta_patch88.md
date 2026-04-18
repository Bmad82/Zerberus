# RAG-Eval Delta Patch 88 (2026-04-18)

**Patch-Inhalt:** R-01 — Residual-Chunk-Filter (Chunking-Zeit-Merge + Retrieval-Zeit-Filter), `min_chunk_words=120`.
**Index-Stand vor Patch 88:** 18 Chunks (5 Residual-Tails 5–64w + 16w-Titel-Chunk).
**Index-Stand nach Patch 88:** 12 Chunks (`merged_residuals=6`); kürzester Chunk ist das Glossar mit 165w.
**Eval-Methode:** identisch zu Patch 87 (HTTPS, statischer API-Key, `top_k=5`, 11 Fragen aus `rag_eval_questions.txt`).

## Zusammenfassung Δ

| Metrik                       | Patch 87        | Patch 88        | Δ        |
|------------------------------|-----------------|-----------------|----------|
| JA (Top-Treffer korrekt)     | 4/11            | 4/11            | ±0       |
| TEILWEISE                    | 5/11            | 5/11            | ±0       |
| NEIN                         | 2/11            | 2/11            | ±0       |
| Avg L2 Top-Treffer           | 0.97            | 1.01            | +0.04    |
| Chunks im Index              | 18              | 12              | −6       |
| Kürzester Chunk              | 5w              | 165w (Glossar)  | +160w    |

**Headline:** R-01 hat die Chunk-Landschaft sauber gemacht (alle Residuals weg, Titel-Chunk im merged Akt-Prolog-Chunk aufgegangen), aber die Status-Verteilung der 11 Fragen bleibt 1:1 identisch zu Patch 87. Die Erwartung „Q3 und Q8 von TEILWEISE auf JA heben" ist **nicht eingetroffen**.

## Frage-für-Frage

| Q   | Status P87 | Status P88 | Δ   | L2 P87 | L2 P88 | Anmerkung                                                                 |
|-----|------------|------------|-----|--------|--------|---------------------------------------------------------------------------|
| Q1  | JA         | JA         | ➡️  | 0.949  | 0.949  | Akt-I-Chunk auf Rang 1, alle Keywords präsent.                            |
| Q2  | TEILWEISE  | TEILWEISE  | ➡️  | 1.119  | 1.119  | Top-Treffer ist immer noch Akt-VI-Forts. (Jojo-Burgfräulein), echtes Akt I auf Rang 2. |
| Q3  | TEILWEISE  | TEILWEISE  | ➡️  | 1.160  | 1.175  | **Erwartung verfehlt**: Glossar bleibt Rang 4. Titel-Chunk ist weg, aber merged Titel+Prolog (289w) räumt Rang 1, nicht Glossar. |
| Q4  | NEIN       | NEIN       | ➡️  | 1.038  | 1.038  | Perseiden-Chunk (Akt II) bleibt unsichtbar in Top-5. R-01-Territorium nicht. |
| Q5  | JA         | JA         | ➡️  | 0.927  | 0.927  | Akt-I-Chunk, alle Keywords.                                               |
| Q6  | TEILWEISE  | TEILWEISE  | ➡️  | 0.810  | 1.111  | Top-Score schlechter, weil Titel-Chunk (16w, P87 Rang 1 mit L2 0.810) jetzt Teil eines 289w-Chunks ist. Inhaltlich gleich gut: Prolog-Inhalt mit Dr.-GPT-Stelle ist auf Rang 1. Glossar-Definition fehlt weiter in Top-5. |
| Q7  | JA         | JA         | ➡️  | 1.012  | 1.012  | Akt VII, alle Keywords.                                                   |
| Q8  | TEILWEISE  | TEILWEISE  | ⬆️  | 0.838  | 0.921  | **Marginale Verbesserung**: Akt II (mit „Zwei-Herzen-Ordner"-Stelle) wandert von Rang 5 (P87) auf Rang 4 (P88). Titel-Chunk ist weg, dafür räumt Akt I Rang 1 (kein Zwei-Herzen-Bezug). Status bleibt TEILWEISE. |
| Q9  | JA         | JA         | ➡️  | 0.824  | 0.824  | Epilog auf Rang 1, alle Phasen drin.                                      |
| Q10 | NEIN       | NEIN       | ➡️  | 1.265  | 1.265  | Akt-III-Chunk (mit Ulm-Passage) bleibt out-of-Top-5. R-01 hilft nicht — bestätigt R-03 als nächsten Kandidaten. |
| Q11 | TEILWEISE  | TEILWEISE  | ➡️  | 0.739  | 0.739  | Glossar auf Rang 1, konkrete Szenen verteilt.                             |

## Beobachtung & Diagnose

1. **Chunk-Landschaft ist sauber** — Fix A funktioniert wie spezifiziert. 6 Residuals (16/16/5/48/64/60w) sind verschwunden, kürzester Chunk ist 165w (Glossar). Im Eval taucht in keinem Top-5 ein Chunk unter 120w auf.

2. **Avg-L2 leicht schlechter (+0.04)** ist erwartet: kurze Chunks hatten bei normalisierten Embeddings systematisch kleine L2-Werte zu generischen Queries. Mit dem Wegfall der „künstlich guten" Kurzchunks wandert der Top-Treffer auf realistischere Distanzen. Das ist **kein Qualitätsverlust**, sondern eine ehrliche Distanz.

3. **Glossar-Queries (Q3, Q6) profitieren NICHT** wie erwartet. Hypothese aus Patch 87: „Titel-Chunk kapert Rang 1" → Wegfall sollte Glossar nach oben spülen. Realität: stattdessen räumt der **merged Titel+Prolog-Chunk** (289w) oder ein anderer langer Chunk Rang 1, weil das Embedding bei „Was steht im Glossar zu X?" nicht das Wort „Glossar" stark genug gewichtet. **Das Embedding-Modell ist die Bremse, nicht die Chunk-Größe.**

4. **Q4 (Perseiden) und Q10 (Ulm) sind weiterhin NEIN** — exakt wie Patch 87 vorausgesagt. Das sind R-02/R-03-Probleme: Cross-Encoder-Reranker (R-03) ist der wahrscheinlichste Heilkandidat, weil er volle Token-Attention statt Satz-Vektor hat.

5. **Die L2-Schwelle (`_RAG_L2_THRESHOLD = 1.5` im Orchestrator) bleibt sicher** — alle Top-1-Treffer liegen darunter, kein Filter-Eingriff in der Pipeline.

## Fazit

R-01 erledigt die saubere Chunk-Hygiene und verhindert, dass künftig 16-Wort-Titel-Chunks Glossar-Queries kapern (Pre-Merge im Chunking + Sicherheitsnetz im Retrieval). Aber: Die quantitativen JA/TEILWEISE/NEIN-Werte sind unverändert. **Der nächste sinnvolle Schritt ist R-03 (Cross-Encoder-Reranker)**, weil R-01 das Hauptproblem (Embedding-Modell ranked Glossar/Eigennamen schlecht) nicht adressiert.
