# RAG-Eval Delta — Patch 114b

**Datum:** 2026-04-23
**Hardware:** RTX 3060 (12 GB VRAM), torch 2.5.1+cu124, CUDA 12.4
**Eval-Script:** `rag_eval.py` (24 Zeilen in `rag_eval_questions.txt`, davon 20 echte Fragen + 4 Kommentar-Header)
**Index-Stand zum Zeitpunkt:** 19 Chunks aus 2 Dokumenten (nur Rosendornen + Codex Heroicus, **NÉON-Kadath fehlt komplett im Index**)

## Kontext

Erster Eval-Durchlauf NACH Fix der torch+CUDA-Pipeline (Patch 111b). Vorher lief Reranker (bge-reranker-v2-m3) auf CPU → ~26–47 s pro Query + Mobile-SSE-Timeouts. Jetzt GPU.

## Performance

- **Eval-Dauer gesamt:** ~60–90 s für 24 Queries → **~2.5–4 s pro Query** inkl. FAISS-Search + Cross-Encoder-Rerank (top_k=8).
- **Vergleich CPU-Baseline (aus Patch 89/108b Logs):** ~26 s pro Query → **GPU ~8× schneller** (im erwarteten 5–10× Bereich laut gpu-ml.md).
- **Server-Start-Memory:** keine Crashs bei Modell-Load auf CUDA. 10.98 GB VRAM frei → reichlich Puffer für Whisper parallel.
- Kein einziger SSE-Timeout während des Evals (ohne Heartbeat hätte CPU-Pfad mehrmals abgebrochen).

## Ergebnis Q1–Q20 (Score = Top-1 FAISS-Score; *=expected doc nicht im Index)

| #  | Frage (gekürzt)                                  | Top-Score | Quelle             | Bewertung   |
|---:|--------------------------------------------------|-----------|--------------------|-------------|
|  1 | Wo Anne zum ersten Mal getroffen?                | 0.513     | Rosendornen        | JA          |
|  2 | Umzug Sonnenbühl→Backnang wer half?              | 0.465     | Rosendornen        | JA          |
|  3 | Glossar „Infraschall"                            | 0.434     | Rosendornen        | JA          |
|  4 | Perseiden-Nacht + Fotografie                     | 0.428     | Rosendornen        | TEILWEISE   |
|  5 | Warum Anne Kontakt zu Carolin abbrach            | 0.519     | Rosendornen        | JA          |
|  6 | Unterschied Dr. GPT vs Dr. Claude Cuda Tensor    | 0.474     | Rosendornen        | JA          |
|  7 | Anne: Kinder/Männer/Reihenfolge                  | 0.497     | Rosendornen        | JA          |
|  8 | Zwei-Herzen-Ordner + Annes Entdeckung            | 0.460     | Rosendornen        | JA          |
|  9 | Heilungsphasen im Epilog + Dauern                | 0.548     | Rosendornen        | JA          |
| 10 | „Verkaufsoffener Sonntag in Ulm"                 | 0.365     | Rosendornen        | JA          |
| 11 | Anne „unkontrollierbarer Impuls" Momente         | 0.575     | Rosendornen        | JA          |
| 12 | Eiserner Architekt + Schmied                     | 0.418     | Codex Heroicus     | JA          |
| 13 | Drei Dämonen der Konfiguration + Zahlen          | 0.458     | Codex Heroicus     | TEILWEISE   |
| 14 | Siegfrieds Schrei + finaler Befehl               | 0.448     | Codex Heroicus     | TEILWEISE   |
| 15 | Neo-Backnang Einwohner/Konzern*                  | 0.381     | Codex Heroicus (!) | **NEIN**    |
| 16 | Dr. Alexei Kozlov + Spitzname + Motto*           | 0.370     | Codex Heroicus (!) | **NEIN**    |
| 17 | Salon de Néon Adresse + Fixer*                   | 0.380     | Codex Heroicus (!) | **NEIN**    |
| 18 | Gustav Produkte/Alter*                           | 0.383     | Codex Heroicus (!) | **NEIN**    |
| 19 | Infraschall Cross-Doc                            | 0.411     | Rosendornen        | TEILWEISE   |
| 20 | Schwäbische Ortsnamen Cross-Doc                  | 0.400     | Codex Heroicus     | TEILWEISE   |

**Zusammenfassung:** 11 JA, 5 TEILWEISE, **4 NEIN — alle wegen fehlendem NÉON-Kadath-Dokument im Index (nicht Retrieval-Qualität).**

## Vergleich zum vorherigen Eval (Patch 89: 10/11 JA)

- Q1–Q11 (Rosendornen) bleiben **stabil auf hohem Niveau** (10 JA, 1 TEILWEISE bei Q4 Perseiden). Das ist konsistent mit dem Patch-89-Delta (Cross-Encoder-Reranker).
- Q4 Perseiden-Nacht weiterhin TEILWEISE — der Perseiden-Chunk landet oberhalb der Schwelle, aber die genauen Fotografie-Details fehlen im Top-1. Nächster Schritt: Cross-Encoder `max_length` auf 1024 erhöhen oder top_k auf 10 ziehen.
- Q10 Ulm (in Patch 89 frisch geheilt) bleibt gefixt.

## Category-Boost (Patch 111) — Beobachtungen

- Die Queries 12–14 (Codex Heroicus, category=narrative) liefern korrekt Codex-Chunks — Boost greift wie erwartet, die Chunks tauchen deutlich vor Rosendornen-Chunks auf.
- Queries 15–18 (erwartete category=lore) bekommen KEINEN Boost, weil das NÉON-Kadath-Dokument fehlt. Der Retriever fällt auf Codex zurück → hoher Boost auf narrative, NÉON gewinnt nicht. Ist systemisch korrekt.
- Empfehlung: Nach Upload von NÉON-Kadath (category=lore) erneuten Eval-Lauf starten und prüfen, ob Q15–Q18 dann JA werden. Das ist der echte Category-Boost-Test.

## Empfehlungen für weitere Eval-Fragen

1. **NÉON-Kadath hochladen** (als `category=lore`) und Q15–Q18 wiederholen — sonst ist die Cross-Doc-Breite (Q19/Q20) inhaltlich limitiert.
2. **TRANSFORM-Intent-Fragen hinzufügen** (Patch 106 Skip-Logik testen): Z.B. „Übersetze folgenden Satz ins Englische: Infraschall manipuliert uns." → sollte ohne RAG-Kontext antworten.
3. **Dedup-Regression-Frage:** Eine einfache Re-Frage aus einer bekannten Session schicken und via `/archive/session/{id}` prüfen, dass nur ein Eintrag in der DB landet (Patch 113a Guard).
4. **Heartbeat-Simulation:** Künstlich lange RAG-Query (top_k=20, rerank_multiplier=5) — Frontend-Watchdog darf NICHT abbrechen, solange Heartbeats kommen (Patch 114a).
5. **Query-Router-False-Positive:** Ein Codex-Wort das mitten in einem Frage-Satz vorkommt — Wortgrenzen-Matching testen (z.B. „Was bedeutet Narbe?" darf nicht den narrative-Boost kriegen).

## Technische Notizen zum Setup

- `pip install torch --index-url https://download.pytorch.org/whl/cu124 --force-reinstall` zog numpy 2.2 rein, welches faiss 1.7.4 brach (`_ARRAY_API not found`). Fix: `pip install "numpy<2"` → numpy 1.26.4.
- typing_extensions wurde auf 4.9.0 downgegradet, cryptography 46.0.7 braucht ≥4.13.2. Fix: `pip install --upgrade "typing-extensions>=4.13.2"`.
- Lesson hinterlegt in `lessons.md` + `C:\Users\chris\Python\Claude\lessons\gpu-ml.md`.
