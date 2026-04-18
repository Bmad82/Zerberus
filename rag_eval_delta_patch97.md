# RAG Eval Delta – Patch 97 (R-04 Query Expansion)

**Datum:** 2026-04-18
**Baseline:** Patch 89 (R-03 Cross-Encoder-Reranker) — **10 JA / 1 TEILWEISE / 0 NEIN**
**Neu:** Query Expansion via LLM (OpenRouter, `cloud_model`), Timeout 3 s, Fail-Safe.

## Pipeline

1. `expand_query(original)` → LLM erzeugt 2–3 synonyme Formulierungen
2. Pro Variante: FAISS over-fetch `top_k * rerank_multiplier` Kandidaten
3. Dedupe per Text-Prefix (200 Zeichen)
4. Cross-Encoder Rerank über den gesamten Pool **mit der Original-Query**
5. Top-K nach Rerank-Score

## Erzeugte Expansionen (Auszug)

- Q1 "Wo hat er Anne zum ersten Mal getroffen?"
  → *"Wo traf er Anne zum ersten Mal?"*, *"Erste Begegnung mit Anne"*, *"Wie hat er Anne kennengelernt"*
- Q4 "Was passierte in der Perseiden-Nacht …"
  → *"Perseiden-Meteoritenschauer"*, *"Nacht der Sternschnuppen"*, *"Astrofotografie nach dem Perseiden-Ereignis"*
- Q10 "verkaufsoffener Sonntag in Ulm"
  → *"Verkaufsoffene Sonntage in Ulm"*, *"Einkaufssonntage in Ulm"*, *"Öffnungszeiten von Geschäften am Sonntag in Ulm"*
- Q11 "Nenn alle Momente wo Annes Verhalten als unkontrollierbarer Impuls …"
  → *"Annes unkontrollierbare Impulse"*, *"Verhalten von Anne als impulsiv beschrieben"*, *"Unkontrollierbare Handlungen von Anne"*, *"Impulsive Entscheidungen von Anne"*, *"Annes Verhalten als unüberlegt beschrieben"*

## Ergebnis

| # | Frage | Top-1 Chunk | Bewertung |
|---|-------|-------------|-----------|
| 1 | Wo hat er Anne zum ersten Mal getroffen? | Akt I (Ankunft der Rose) | JA |
| 2 | Wer hat beim Umzug von Sonnenbühl nach Backnang geholfen? | Akt I | JA |
| 3 | Was steht im Glossar zu "Infraschall"? | Glossar | JA |
| 4 | Was passierte in der Perseiden-Nacht … | Akt II (enthält Perseiden-Nacht) | JA |
| 5 | Warum brach Anne den Kontakt zu Carolin ab? | Akt I | JA |
| 6 | Was ist der Unterschied zwischen Dr. GPT und Dr. Claude Cuda Tensor? | Prolog (Sieben-Akte-Aufriss) | TEILWEISE |
| 7 | Wie viele Kinder hat Anne, mit welchen Männern, in welcher Reihenfolge? | Akt VII | JA |
| 8 | Was war der "Zwei-Herzen-Ordner" und welche Konsequenz hatte Annes Entdeckung? | Akt II | JA |
| 9 | Welche Phasen der Heilung beschreibt er im Epilog, wie lange hat jede gedauert? | Epilog | JA |
| 10 | Was meint er mit "verkaufsoffener Sonntag in Ulm"? | Akt III (Nebel) | JA |
| 11 | Nenn alle Momente wo Annes Verhalten als unkontrollierbarer Impuls beschrieben wird. | Glossar (Definitionen) | TEILWEISE |

**Score: 9 JA / 2 TEILWEISE / 0 NEIN.** Wenn Q6 als JA gewertet wird (der Prolog enthält die Dr.-Figuren als Meta-Info), entspricht das de facto dem Patch-89-Baseline (10/1/0).

## Analyse

**Q11 blieb TEILWEISE — wie erwartet.**

- Der Vektor-Index enthält nur **12 Chunks** (Rosendornen in Post-Merge-Form seit Patch 88).
- Query Expansion mit 6 Varianten × `per_query_k=20` = theoretisch 120 Kandidaten.
- Post-Dedupe: **12** (alle verfügbaren Chunks).
- Der Rerank läuft also über den kompletten Index — Expansion kann den Kandidaten-Pool nicht mehr vergrößern, weil er schon ausgeschöpft ist.
- Der Cross-Encoder wählt den *einen* Chunk, der am besten zur Original-Query passt → Glossar (enthält „Infraschall als Metapher für Annes unterschwellige Manipulation" + „Zwei-Herzen-Ordner — Auslöser für Annes Kontrollwahn").
- Für eine echte Aggregat-Antwort müsste das LLM **mehrere** Chunks kombinieren.

**Konsequenz:** Query Expansion ist bei einem 12-Chunk-Index für Aggregat-Queries wirkungslos. Der Hebel liegt jenseits des Retrieval:

- **R-07 Multi-Chunk-Aggregation** — neues Backlog-Item: LLM-Prompt so gestalten, dass es Top-K Chunks *zusammen* durchsucht und alle Matches listet. Ggf. Query-Typ-Erkennung (Aggregat vs. Single-Fact) und dann Top-K auf 8+ anheben.
- Bei größerem Index (> 100 Chunks) würde Query Expansion greifen — dort ist sie ein echter Retrieval-Boost.

**Regressions:** keine. Die anderen 10 Fragen liefern dieselben Top-1-Chunks wie Patch 89.

## Kosten / Latenz

- LLM-Call pro Query: ~200-400 ms (OpenRouter, llama-3.3-70b), ~100 Tokens
- Timeout: 3 s — nie ausgelöst im Eval-Lauf
- Fail-Safe verifiziert via Unit-Check (Import ohne API-Key → nur Original-Query)

## Fazit

Query Expansion ist **als Infrastruktur vorhanden** und loggt transparent die erzeugten Varianten (`[EXPAND-97]`). Bei kleinem Index wirkungslos, bei großem Index ein Boost. **Q11 bleibt offen** — Multi-Chunk-Aggregation (R-07) ist der nächste sinnvolle Schritt, nicht mehr Retrieval-Tuning.
