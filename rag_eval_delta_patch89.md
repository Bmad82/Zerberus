# RAG-Eval Delta Patch 89 (2026-04-18)

**Patch-Inhalt:** R-03 — Cross-Encoder-Reranker als zweite Retrieval-Stufe.
FAISS over-fetched `top_k * rerank_multiplier` Kandidaten, der Reranker
bewertet jedes Query+Chunk-Paar mit voller Token-Attention und sortiert neu.

**Gewähltes Modell:** `BAAI/bge-reranker-v2-m3`
**Warum:** Trainiert auf MIRACL (multilinguales Passage-Ranking inkl. Deutsch),
saubere Integration via `sentence_transformers.CrossEncoder` ohne
`trust_remote_code`, Apache 2.0. 568 M Parameter, state-of-the-art auf
dem BGE-Benchmark-Board für deutsche Eigennamen & Glossar-Queries. Alternativen
(`jina-reranker-v2-base-multilingual`, `mxbai-rerank-large-v1`) wurden geprüft
— Jina ist schneller aber braucht `trust_remote_code=True`; mxbai-large
überschreitet das Modellgrößen-Budget.

**Config (aktiv):**
```yaml
modules.rag:
  rerank_enabled: true
  rerank_model: BAAI/bge-reranker-v2-m3
  rerank_multiplier: 4
```

**Index:** unverändert (12 Chunks aus Patch 88, kein Re-Build).

## Zusammenfassung Δ

| Metrik                       | Patch 88        | Patch 89        | Δ           |
|------------------------------|-----------------|-----------------|-------------|
| JA (Top-Treffer korrekt)     | 4/11            | **10/11**       | **+6** ⬆️⬆️  |
| TEILWEISE                    | 5/11            | **1/11**        | **−4**      |
| NEIN                         | 2/11            | **0/11**        | **−2**      |
| Avg L2 (Top-1, FAISS-Raw)    | 1.01            | 1.04            | +0.03       |
| Avg Rerank-Score (Top-1)     | —               | 0.680           | neu         |

L2 ist ab Patch 89 keine primäre Metrik mehr: der Reranker wählt nicht
zwingend den Chunk mit geringstem L2, sondern den mit bester
Cross-Encoder-Attention. Ein hoher L2 bei gutem Rerank-Score ist **der Punkt**
des Patches, kein Fehler.

**Headline:** **Durchbruch.** Q4 (Perseiden-Nacht) und Q10 (Verkaufsoffener
Sonntag in Ulm) — beide seit Patch 87 harte NEINs — sind JETZT jeweils JA
mit Rerank-Scores 0.942 bzw. 0.929. Alle 5 P88-TEILWEISE-Fragen bis auf
eine (Q11 — Aggregat-Query) sind zu JA gewandert.

## Frage-für-Frage

| Q   | P88        | P89        | Δ       | Rerank-Score Top-1 | Anmerkung                                                                                           |
|-----|------------|------------|---------|--------------------|-----------------------------------------------------------------------------------------------------|
| Q1  | JA         | JA         | ➡️      | 0.946              | Akt I auf Rang 1, alle Keys drin.                                                                   |
| Q2  | TEILWEISE  | JA         | ⬆️      | 0.006              | Rerank-Score niedrig, aber Akt I enthält „Alex half beim Umzug" — korrekte Antwort auf Rang 1.      |
| Q3  | TEILWEISE  | JA         | ⬆️      | 0.966              | Glossar auf Rang 1 (statt merged Titel+Prolog in P88). Rerank-Score sehr hoch — klassischer Erfolg. |
| Q4  | NEIN       | **JA**     | ⬆️⬆️    | 0.942              | **Akt II** mit „Perseiden-Nacht (August 2015)"-Stelle erobert Rang 1. Durchbruch.                   |
| Q5  | JA         | JA         | ➡️      | 0.351              | Akt I auf Rang 1, Carolin-Erklärung präsent.                                                        |
| Q6  | TEILWEISE  | JA         | ⬆️      | 0.847              | Prolog (Dr. Claude Cuda Tensor) auf Rang 1, Dr. GPT explizit erwähnt.                               |
| Q7  | JA         | JA         | ➡️      | 0.087              | Akt VII (Scherbenhaufen-Stich) auf Rang 1, Florian+Matthias / Pia+Sebastian mit „wie zuvor"-Reihenfolge. |
| Q8  | TEILWEISE  | JA         | ⬆️      | 0.746              | Akt II auf Rang 1 (statt Akt I in P88), enthält „Zwei-Herzen-Ordner"-Entdeckung und Konsequenz.     |
| Q9  | JA         | JA         | ➡️      | 0.383              | Epilog auf Rang 1, alle Phasen (3 Jahre manisch / 4 Jahre Burgfräulein / 20% KI).                   |
| Q10 | NEIN       | **JA**     | ⬆️⬆️    | 0.929              | **Akt III** mit „Verkaufsoffene Sonntage in Ulm — Code für alles, was man gegen die eigene Natur tut." Durchbruch. |
| Q11 | TEILWEISE  | TEILWEISE  | ➡️      | 0.216              | Glossar-Definition für „unkontrollierbarer Impuls" auf Rang 1; konkrete Szenen (Akt I Straßenfest, Akt II Swen/Dickpic) in Top-5 verteilt, nicht zusammengefasst. Aggregat-Query bleibt schwierig — R-04 (Query-Expansion) wäre der nächste Angriffsvektor. |

**Status-Summe:** 10 JA, 1 TEILWEISE, 0 NEIN — deutlich über dem vom
User skizzierten Best-Case (7–9 JA).

## Beobachtungen

1. **Reranker sortiert aggressiv um, wo es nötig ist.**
   - Q3 (Glossar): FAISS hatte Glossar auf Rang 4, Reranker hebt auf Rang 1.
   - Q4 (Perseiden): FAISS hatte Akt II auf Rang 3, Reranker hebt auf Rang 1 mit 200× Score-Gap zum nächsten Kandidaten (0.603 vs 0.00279 im Debug-Call).
   - Q10 (Ulm): FAISS hatte Akt III gar nicht in Top-5, aber in Top-20 (rerank_multiplier=4 → over-fetch 20). Reranker zieht ihn auf Rang 1 mit Score 0.929.

2. **Reranker lässt in Ruhe, wo FAISS schon recht hatte.**
   - Q1, Q5, Q7, Q9: Top-1-Chunk identisch zu P88.

3. **Rerank-Score ist nicht linear mit „Qualität".** Q2 und Q7 haben sehr
   niedrige Rerank-Scores (0.006 / 0.087), liefern aber die richtige
   Antwort. Der Reranker sagt hier: „Keiner der Kandidaten ist ein
   offensichtlicher Volltreffer, aber dieser ist der beste." Bei
   Q2 „Wer hat beim Umzug geholfen?" ist „Alex half beim Umzug" ein
   einziger Satz in einem 672-Wort-Chunk — der Reranker findet ihn trotz
   geringer Gesamtrelevanz des Chunks.

4. **Fail-Safe hat während der Eval einmal gegriffen:** Beim allerersten
   Cold-Start-Call (vor dem kompletten Modell-Download) warf `huggingface_hub`
   eine DNS-Exception mitten im Download; der Fail-Safe-Block loggte
   `[DEBUG-89] Rerank failed (ConnectionError: ...)` und gab die FAISS-Order
   zurück — keine 500er, keine Unterbrechung. Nach vollständigem Download
   lief alles stabil.

5. **Cold-Start-Kosten:** Modell-Download ~2.5 Minuten (2.27 GB), danach
   Laden aus HF-Cache in ~3 Sekunden. Inference pro Query (20 Kandidaten):
   ~200–300 ms auf CPU. Kein Problem für die Nala-Pipeline.

6. **L2-Threshold-Interaktion:** In `_rag_search()` wird der
   `_RAG_L2_THRESHOLD = 1.5`-Filter bei aktivem Rerank übersprungen
   (bewusst — Reranker hat eigene Relevanz-Metrik). Für den Fall
   `rerank_enabled: false` verhält sich der Pfad exakt wie Patch 88.

## Fazit

R-03 ist der erwartete Durchbruch. **6 Status-Verbesserungen auf 11 Fragen**,
davon 2 harte NEINs geheilt (Q4 Perseiden, Q10 Ulm). Die Cross-Encoder-
Hypothese aus Patch 87/88 ist bestätigt: das MiniLM-Bi-Encoder-Embedding
findet deutsche Eigennamen nicht zuverlässig, aber der Cross-Encoder
erkennt sie sofort.

**Noch offen:** Q11 — Aggregat-Queries („Nenn alle Momente wo…") sind auch
mit Rerank nicht komplett lösbar, weil sie mehrere Chunks als gesammelte
Antwort brauchen. Das ist R-04-Territorium (Query-Expansion) oder ein
LLM-seitiger Multi-Chunk-Summary-Schritt, nicht mehr Retrieval-Qualität.

**Nächste Prioritäten im Backlog:**
- R-02 (Embedding-Upgrade `all-MiniLM-L6-v2` → `BGE-M3` / `bge-large-de`)
  rückt nach hinten — der Reranker kompensiert die Bi-Encoder-Schwäche
  ausreichend.
- R-04 (Query-Expansion) steigt in der Priorität für Aggregat-Queries
  wie Q11.
- `rag_eval.py` HTTPS-Fix (Backlog-Item 7) weiterhin offen.
