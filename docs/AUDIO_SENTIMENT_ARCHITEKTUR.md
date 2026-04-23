# Audio-Sentiment-Architektur — Zerberus Pro 4.0

**Stand:** 2026-04-23, Patch 120
**Status:** Teilweise implementiert, Prosodie noch offen

---

## Ziel

Nala soll nicht nur verstehen WAS der User sagt, sondern WIE er es meint.
Das dient dem Fingerspitzengefühl des Assistenten im Umgang mit dem User:

- Jojo fragt sachlich nach einem Medikament, ist aber aufgeregt → Nala antwortet behutsam
- Chris redet gut drauf, aber der Ton signalisiert Frustration → Nala geht vorsichtiger vor
- Sarkasmus, versteckte Sorgen, Aufregung hinter ruhigem Text → alles erkennbar

Das ist kein Gimmick. Das ist das Herzstück der Persönlichkeit von Nala.

---

## Architektur-Schichten (Pipeline-Reihenfolge)

### Schicht 1: Whisper (lokal, Docker, Port 8002)

- **Aufgabe:** Reine Transkription, Speech-to-Text
- **Modell:** faster-whisper-server (Container "der_latsch", `fedirz/faster-whisper-server:latest-cuda`)
- **Output:** Text-Transkript
- **Bleibt unangetastet.** Bestes WER, spezialisiert, nicht ersetzbar.
- **Bekannter Bug:** W-001b Satz-Repetitions-Loop. Patch 113b fängt konsekutive
  Sätze mit Interpunktion; Patch 120 ergänzt einen Subsequenz-Matcher für
  lange Satz-Loops ohne Punkte (Phrase-Filter geht nur bis 6 Wörter).
- **Auto-Restart:** Patch 119 Watchdog stoppt die stündliche Loop-Degradation.

### Schicht 2: BERT Sentiment (lokal, GPU)

- **Aufgabe:** Schnelle Text-Sentiment-Klassifikation
- **Modell:** `oliverguhr/german-sentiment-bert`
- **Output:** positive / negative / neutral + Confidence-Score
- **Läuft live bei jeder Nachricht** → geht als Metadata an DeepSeek (Verifikation
  dass das tatsächlich im Prompt landet steht noch aus, siehe Roadmap).
- **Zusätzlich:** Overnight-Batch (04:30 Cron-Job, Patch 57) für Metriken/Dashboard.
- **Limitation:** Erkennt nur Text-Stimmung. Sarkasmus, Ironie, versteckte
  Emotionen werden oft als "neutral" klassifiziert.

### Schicht 3: Prosodie / Audio-Sentiment (GEPLANT — noch nicht implementiert)

- **Aufgabe:** Stimmungsanalyse aus dem Audio-Signal (Tonhöhe, Tempo, Pausen, Aufregung)
- **Kandidaten:**
  - **Gemma 4 E4B** — kann Audio nativ verarbeiten, klein genug für Edge/lokal
    - Problem: Lokal = VRAM-Konkurrenz mit Whisper + BERT + Embedding + Reranker
    - Problem: Über OpenRouter kein Audio-Input möglich
  - **emotion2vec** — spezialisiertes SER-Modell, leichtgewichtig
  - **wav2vec2 / HuBERT** — Facebook/Meta Audio-Encoder, feintunable
  - **Voxtral Small (Mistral)** — Audio-fähig, aber ~\$100 / 1 M Sekunden Audio-Input
- **Offene Fragen:**
  - Passt ein lokales Audio-Modell neben Whisper auf die RTX 3060 (12 GB)?
  - Kann man VRAM freimachen (z.B. Whisper nach Transkription auf CPU verlagern)?
  - Lohnt sich ein Cloud-Call für jede einzelne Sprachnachricht (Kosten, Latenz)?
- **Output-Schema (Vorschlag):** `{"arousal": 0..1, "valence": 0..1, "dominance": 0..1}`
- **Ziel:** Geht zusammen mit BERT-Score als Metadata an DeepSeek.

### Schicht 4: DeepSeek V3.2 (Cloud, OpenRouter)

- **Aufgabe:** Antwortgenerierung mit Fingerspitzengefühl
- **Input:** User-Text + RAG-Kontext + BERT-Sentiment + Prosodie-Signal (wenn verfügbar)
- **Das LLM weiß dadurch sofort:** User ist aufgeregt / ruhig / frustriert / gut drauf
- **Formuliert die Antwort entsprechend:** behutsam, direkt, sachlich, empathisch.

### Schicht 5: Ach-laber-doch-nicht Guard (Cloud, OpenRouter, Patch 120)

- **Aufgabe:** Post-Processing-Check auf Sycophancy + Halluzination
- **Modell:** Mistral Small 3 (24 B, ~\$0.05 / \$0.08 pro 1 M Tokens)
- **Implementierung:** [zerberus/hallucination_guard.py](../zerberus/hallucination_guard.py), `check_response(user_msg, assistant_answer, rag_context)`
- **Zustandslos:** Sieht nur User-Nachricht + Antwort + optional RAG-Kontext, NIE den Chatverlauf.
- **Fail-open:** Bei Fehler (HTTP non-200, Timeout, JSON-Parse-Miss) geht die Antwort unverändert durch.
- **SKIP-Schwelle:** Antworten < 50 geschätzte Tokens (~37 Wörter) werden nicht geprüft.
- **Feature-Flag:** `settings.features["hallucination_guard"]` (Default `True`).

---

## Datenfluss (Sequenzdiagramm)

```text
User spricht ins Mikrofon
        │
        ▼
   [Whisper] ──────────────────────► Text-Transkript
        │                                  │
        │ (WAV bleibt erhalten)            │
        │                                  ▼
        │                            [BERT Sentiment] ──► pos/neg/neutral
        │                                  │
        ▼                                  │
   [Prosodie-Modell] ──► Arousal/Valence   │
   (GEPLANT)                │              │
                            ▼              ▼
                    ┌─────────────────────────────┐
                    │     DeepSeek V3.2            │
                    │  Input: Text + RAG +         │
                    │  BERT-Score + Prosodie-Score │
                    │  Output: Antwort             │
                    └──────────┬──────────────────┘
                               │
                               ▼
                    [Ach-laber-doch-nicht Guard]
                    (Mistral Small 3, synchron hinter dem LLM-Call)
                               │
                               ▼
                         Antwort an User
```

---

## Kosten-Übersicht pro Nachricht

| Schicht | Kosten | Latenz |
|---------|--------|--------|
| Whisper | Kostenlos (lokal) | ~1–3 s |
| BERT | Kostenlos (lokal) | <100 ms |
| Prosodie | TBD | TBD |
| DeepSeek V3.2 | ~\$0.001–0.01 | 2–8 s |
| Guard (Mistral Small 3) | ~\$0.00002 | <1 s (SKIP bei kurzen Antworten) |
| **Gesamt** | **~\$0.01 max** | **meist parallel, nicht additiv** |

---

## Design-Entscheidungen

1. **Whisper bleibt für Transkription.** Kein anderes Modell hat besseres WER.
2. **BERT bleibt für Schnell-Sentiment.** Kostenlos, lokal, schnell genug für Live.
3. **Prosodie ist der fehlende Baustein.** Erst damit versteht Nala wirklich wie etwas gemeint ist.
4. **Guard ist ein anderes Modell als DeepSeek.** Gleicher Prüfer = Sycophant prüft sich selbst.
5. **Mistral Small 3 für den Guard.** Billigstes schnellstes Modell das Deutsch kann.
6. **Free-Tiers sind Werbung.** Paid bevorzugen, Free nur als Fallback. Überlastete Free-Modelle liefern schlechte Qualität.
7. **API-Calls parallel** (asyncio.gather) wo möglich, nicht sequentiell. Aktuell läuft der Guard synchron hinter dem LLM-Call; eine parallele Schaltung macht erst Sinn, wenn es noch andere Post-Processing-Schritte gibt.
8. **Prosodie muss ggf. lokal laufen.** OpenRouter unterstützt kein Audio-Input für Gemma 4 E4B. Lokale Lösung = VRAM-Planung nötig (RTX 3060, 12 GB, bereits belegt durch Whisper ~4 GB + Embedding + Reranker + BERT).

---

## Roadmap

- [x] BERT Sentiment live (implementiert seit frühen Patches)
- [x] BERT Overnight-Batch (04:30 Cron, Patch 57/90+)
- [x] Guard: Ach-laber-doch-nicht mit Mistral Small 3 (Patch 120)
- [ ] BERT-Score als Metadata an DeepSeek liefern (prüfen ob bereits implementiert)
- [ ] Prosodie-Modell evaluieren (VRAM, Kandidaten, Latenz)
- [ ] Prosodie-Signal an DeepSeek liefern
- [ ] Gesamtpipeline End-to-End testen mit Jojo (iPhone via Tailscale)
