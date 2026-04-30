> 📌 Referenz-Dokument. Tracking des Prosodie-Features erfolgt in [`BACKLOG_ZERBERUS.md`](BACKLOG_ZERBERUS.md) (B-011).

# Backlog-Eintrag: Prosodie-Bewusste Eingabeverarbeitung (Speech Emotion Recognition Layer)

**Status:** Konzept, Backlog (nach Rosa Corporate Security Layer)
**Kategorie:** Architektur-Erweiterung, Worker-Protection / Kintsugi-Feature
**Priorität:** Mittel-langfristig, nicht vor Patch 100+
**Pre-Condition:** User-Trennung in `interactions`-Tabelle muss implementiert sein (sonst keine saubere User-Zuordnung der Prosodie-Daten möglich, Consent-Logik würde in der Luft hängen).

## Problem

Der aktuelle Spracheingabe-Pfad ist lossy:

```
Audio → Whisper → Text → German-BERT-Sentiment → Label → Prompt
```

Whisper verwirft bei der Transkription alle prosodischen Informationen (Tonhöhe, Tempo, Lautstärke-Verlauf, Stimmqualität, Pausen, Atmung). Das nachgelagerte BERT-Sentiment rekonstruiert Emotion ausschließlich aus dem *resultierenden Text*. Folgen:

- **Sarkasmus/Ironie unerkennbar:** "Das ist ja toll" mit flacher Stimme wird als positiv gelabelt
- **Inkongruenz zwischen Text und Stimme geht verloren:** Jemand sagt "mir geht's gut" mit zitternder, leiser Stimme → Nala hat keine Chance, das zu merken
- **Keine Arousal-Detektion:** Erregung, Stress, Erschöpfung sind prosodisch klar, textlich aber oft unsichtbar

## Lösungsansatz: Parallel-Extraktion (Option A)

Kein Ersatz von Whisper, sondern **Ergänzung** um einen parallelen Speech-Emotion-Recognition-Pfad:

```
Audio ──┬── Whisper-large-v3 ──→ Text (wie bisher, Primärpfad)
        │
        └── SER-Modell ──→ Prosodie-Metadaten
                            {valence, arousal, dominance, 
                             confidence, prosody_flags}
                                    ↓
                         Orchestrator injiziert als separates
                         Kontext-Feld in Nala's System-Prompt
```

## Warum nicht Gemma 3n als End-to-End-Ersatz?

Geprüft und verworfen (Stand Patch 80):
- End-to-End-Audio-LLM würde Transkriptions-Persistierung, FAISS-Indexierung und RAG-Flow brechen
- Gemma 3n als reiner ASR-Ersatz wäre schlechter als Whisper bei deutschem Fachvokabular
- Gemma's implizite Emotionsverarbeitung ist nicht debuggbar und nicht auditierbar (DSGVO-kritisch)
- Saubere Modul-Trennung = Zerberus-Philosophie

## Technische Kandidaten für SER-Modul

- `audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim` — Valence/Arousal/Dominance, robust, multilingual
- `padmalcom/wav2vec2-large-emotion-detection-german` — deutschspezifisch
- Alternativ: eigenes Fine-Tuning auf wav2vec2-base für domain-spezifische Kalibrierung

Läuft parallel zu Whisper auf RTX 3060 (geschätzt ~1-2 GB VRAM zusätzlich), könnte als dritter Docker-Container neben Whisper (8002) und Ollama (8003) deployed werden, z.B. Port 8004.

## Output-Format für Orchestrator

```json
{
  "transcript": "Das ist ja toll",
  "prosody": {
    "valence": -0.3,
    "arousal": 0.1,
    "dominance": 0.2,
    "primary_emotion": "flat",
    "congruence_with_text": "low",
    "flags": ["possible_sarcasm", "low_energy"],
    "confidence": 0.78
  }
}
```

## Integration in Nala-Prompt

Der Orchestrator erweitert den User-Turn um ein strukturiertes Prosodie-Feld:

```
User-Input (Text): "Das ist ja toll"
User-Input (Prosodie): flach, monoton (Valence: -0.3, Arousal: 0.1)
Hinweis: Text-Prosodie-Inkongruenz erkannt, mögliche Ironie/Frustration
```

Nala kann dann entscheiden, ob sie den Subtext adressiert oder neutral antwortet - je nach Persona-Konfiguration.

## Verbindung zu Rosa (Semantic Drift Detection)

Prosodie-Metadaten sind ein **starkes Zusatzsignal** für Rosa's 5-stufige Drift-Detection:
- Plötzliche Arousal-Spitze ohne inhaltlichen Grund → Eskalations-Flag
- Langanhaltend niedrige Valence über mehrere Sessions → Wellbeing-Hinweis (mit User-Consent)
- Text-Prosodie-Inkongruenz → möglicher Stress-/Maskierungs-Indikator

## Worker-Protection-Rahmen (nicht verhandelbar)

- **Opt-In zwingend:** Prosodie-Analyse nur bei explizitem User-Consent pro Session oder global
- **Kein Performance-Scoring:** Keine Aggregation zu "Mitarbeiter-Stress-Scores" oder ähnlichem
- **Transparenz-Dashboard:** User sieht, welche prosodischen Features wann extrahiert wurden
- **Admin-User-Trennung:** Admin sieht nur aggregierte System-Metriken, niemals individuelle Prosodie-Daten
- **Rohaudio wird verworfen:** Nach Extraktion der Features wird die Audio-Datei gelöscht (oder nur bei expliziter User-Archivierung behalten)

## Mögliche Patch-Sequenz (später)

1. SER-Modell als Docker-Container deployen (Port 8004), isolierte Tests
2. Parallel-Pipeline im Orchestrator implementieren (Whisper + SER parallel)
3. Prosodie-Feld in Prompt-Template integrieren, A/B-Test gegen Baseline
4. Opt-In-UI in Nala bauen (User-Consent + Transparenz)
5. Rosa-Integration: Prosodie als zusätzliches Drift-Signal
6. Admin-Dashboard-Erweiterung in Hel (nur aggregierte, anonymisierte System-Metriken)

## Offene Fragen für später

- Latenz-Budget: Wie viel zusätzliche Verzögerung ist für Jojos iPhone-Workflow akzeptabel?
- Caching-Strategie für SER-Features bei wiederholten Phrases?
- Wie werden Prosodie-Daten in `bunker_memory.db` persistiert (oder bewusst *nicht*)?
- DSGVO: Sind Stimmfeatures "biometrische Daten" im Sinne Art. 9 DSGVO? → Rechtsberatung nötig vor Produktivgang

## Kintsugi-Bezug

"Das Gebrochene sichtbar machen." Wenn jemand sagt "mir geht's gut", aber die Stimme es widerlegt, dann ist genau das der Moment, in dem ein empathisches System aufmerksam werden sollte - nicht um zu überwachen, sondern um zu *sehen*. Das ist ein Feature, das kein rein textbasiertes System jemals leisten kann, und es ist konsistent mit der Grundphilosophie von Zerberus/Nala: Technologie im Dienst des Menschen, nicht umgekehrt.
