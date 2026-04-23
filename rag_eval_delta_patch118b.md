# RAG-Eval Delta — Patch 118b

**Datum:** 2026-04-23
**Hardware:** RTX 3060 (12 GB VRAM), torch 2.5.1+cu124, CUDA 12.4
**Eval-Script:** `rag_eval.py` (24 Zeilen in `rag_eval_questions.txt`, davon 20 echte Fragen + 4 Kommentar-Header)
**Index-Stand:** 61 Chunks aus 9 Dokumenten (Kadath Prosa-Version jetzt zweifach mit gesamt **6 lore-Chunks** indiziert)

## Kontext

Letzte offene Aufgabe aus Phase 3: NÉON-Kadath Prosa-Version wurde im Index hinzugefügt. In Patch 114b waren Q15–Q18 (Kadath-Welt) alle **NEIN** — schlicht weil das Dokument fehlte. Ziel des 118b-Evals: Verifikation, dass die Kadath-Lücke geschlossen ist und der Category-Boost (Patch 111) jetzt wie vorgesehen greift.

## Index-Delta (Patch 114b → Patch 118b)

| Kategorie   | Patch 114b | Patch 118b | Delta |
|-------------|-----------:|-----------:|------:|
| personal    | 15         | 32         | +17   |
| narrative   | 7          | 12         | +5    |
| technical   | 0          | 11         | +11   |
| lore        | 0          | 6          | +6    |
| **Gesamt**  | 22         | 61         | +39   |

(Personal +technical kamen aus früheren Zwischen-Patches, lore ist der eigentliche 118b-Fokus.)

## Ergebnis Q1–Q20 (Top-1 Score + Bewertung)

| #  | Frage (gekürzt)                              | Top-Score | Top-Quelle        | Bewertung 114b | Bewertung 118b |
|---:|----------------------------------------------|-----------|-------------------|----------------|----------------|
|  1 | Wo Anne zum ersten Mal getroffen?            | 0.513     | Rosendornen       | JA             | JA             |
|  2 | Umzug Sonnenbühl→Backnang wer half?          | 0.421     | Rosendornen       | JA             | JA             |
|  3 | Glossar „Infraschall"                        | 0.434     | Rosendornen       | JA             | JA             |
|  4 | Perseiden-Nacht + Fotografie                 | 0.428     | Rosendornen       | TEILWEISE      | TEILWEISE      |
|  5 | Warum Anne Kontakt zu Carolin abbrach        | 0.432     | Rosendornen       | JA             | JA             |
|  6 | Unterschied Dr. GPT vs Dr. Claude            | 0.474     | Rosendornen       | JA             | JA             |
|  7 | Anne: Kinder/Männer/Reihenfolge              | 0.427     | Anne-Rosen JSON   | JA             | JA             |
|  8 | Zwei-Herzen-Ordner + Annes Entdeckung        | 0.460     | Rosendornen       | JA             | JA             |
|  9 | Heilungsphasen im Epilog                     | 0.548     | Rosendornen       | JA             | JA             |
| 10 | „Verkaufsoffener Sonntag in Ulm"             | 0.377     | Rosendornen       | JA             | JA             |
| 11 | Anne „unkontrollierbarer Impuls"             | 0.435     | Rosendornen       | JA             | JA             |
| 12 | Eiserner Architekt + Schmied                 | 0.418     | Codex Heroicus    | JA             | JA             |
| 13 | Drei Dämonen der Konfiguration               | 0.394     | Codex Heroicus    | TEILWEISE      | TEILWEISE      |
| 14 | Siegfrieds Schrei + finaler Befehl           | 0.448     | Codex Heroicus    | TEILWEISE      | TEILWEISE      |
| 15 | **Neo-Backnang Einwohner/Konzern**           | **0.512** | **Kadath (lore)** | **NEIN**       | **JA**         |
| 16 | **Dr. Alexei Kozlov + Motto**                | 0.433     | Codex (!)         | **NEIN**       | **TEILWEISE**  |
| 17 | **Salon de Néon Adresse + Fixer**            | **0.405** | **Kadath (lore)** | **NEIN**       | **JA**         |
| 18 | **Gustav Produkte/Alter**                    | 0.383     | Codex (!)         | **NEIN**       | **TEILWEISE**  |
| 19 | Infraschall Cross-Doc                        | 0.390     | Kadath (lore)     | TEILWEISE      | JA             |
| 20 | Schwäbische Ortsnamen Cross-Doc              | 0.538     | Kadath (lore)     | TEILWEISE      | JA             |

## Gesamtstatistik

| Bewertung  | Patch 114b | Patch 118b | Delta |
|------------|-----------:|-----------:|------:|
| JA         | 11         | **15**     | +4    |
| TEILWEISE  | 5          | 5          | ±0    |
| NEIN       | **4**      | **0**      | **−4**|

**Ergebnis:** Alle 4 NEIN-Treffer aus 114b wurden geschlossen. Davon 2× vollständig (JA) und 2× partiell (TEILWEISE, Kadath-Chunks jetzt im Top-k aber nicht auf Platz 1). Zusätzlich zwei Cross-Doc-Fragen (Q19 Infraschall, Q20 Ortsnamen) von TEILWEISE auf JA verbessert — Kadath liefert hier jetzt relevante Zweit-Kontexte.

## Beobachtungen

- **Q15 Neo-Backnang** und **Q17 Salon de Néon**: Kadath klar Top-1 (0.51 / 0.40). Category-Boost `lore` greift sauber.
- **Q16 Kozlov** und **Q18 Gustav**: Codex Heroicus schlägt Kadath um ~0.05. Ursache wahrscheinlich: beide Namen wirken wie narrative-Figuren, Codex-Boost überwiegt. Kadath-Chunks stehen aber auf Platz 2 im Top-k → die Antworten werden trotzdem möglich, nur der Reranker-Score ist knapper.
- **Q20 Schwäbische Ortsnamen**: Top-Score 0.538 ist der **höchste Kadath-Score des Evals** — Kadath erwähnt Neo-Backnang/Sonnenbühl in einem Dokument, das thematisch breit passt → Cross-Doc-Retrieval funktioniert.
- **Q4 Perseiden-Nacht** und **Q13 Dämonen-Zahlen** bleiben weiter TEILWEISE — das sind bekannte Retrieval-Schwächen (Long-Tail-Details in einzelnen Chunks), keine Regression durch Kadath-Upload.

## Kadath-Index-Details

Zwei Einträge unter `category=lore`:
- `NÉON - Kadath (Prosa Version (mehr oder weniger)).txt` — 3 Chunks (früherer Upload, alter Dateiname)
- `NÉON - Kadath (Prosa Version) das süße ding is nur ne schauspielerin warten wir kurz bis sie vorbei sind.txt` — 3 Chunks (118b-Upload, neuer Dateiname)

Profile: `800/160/120 split=chapter`. Der Doppeleintrag entsteht, weil der frühere Upload unter anderem Dateinamen existierte; für die Retrieval-Qualität hier irrelevant (beide werfen identischen Inhalt). Optional: später via `/hel/admin/rag/clear` + gezieltem Re-Upload bereinigen.

## Nächste Schritte (Phase 4 Kandidaten)

1. Reranker-Feintuning für `lore`-Category: Q16 (Kozlov) und Q18 (Gustav) knapp an Codex verloren — eventuell `lore`-Boost im Reranker leicht anheben oder `max_length` auf 1024.
2. Doppeleintrag des alten Kadath-Dateinamens bereinigen (kosmetisch).
3. TRANSFORM-Intent-Fragen zum Eval-Set hinzufügen (Patch 106 Skip-Logik).
4. Jojo-iPhone-Test (letzter offener Phase-3-Punkt).
