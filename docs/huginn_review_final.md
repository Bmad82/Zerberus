# Huginn Architektur-Review — Finale Konsolidierung

**Reviewer:** Mistral (Thinking), Grok, Copilot, GPT-5, Gemini 3 Pro, Opus, DeepSeek
**Datum:** 25. April 2026
**Briefing-Version:** Huginn v1
**Reviews:** 7 unabhängige Durchläufe, iterativ konsolidiert

---

## Tier 1 — Vollständiger Konsens (5+ Reviewer)

| # | Thema | n | Kurzbeschreibung |
|---|-------|---|-------------------|
| K1 | **Fehlender Input-Guard** | 7/7 | Guard prüft nur Output. Jailbreaks, Injection, Nonsense erreichen LLM ungefiltert. |
| K2 | **Docker-Kernel-Sharing** | 6/7 | Container-Escape bei LLM-generiertem Code dokumentiertes Risiko. gVisor/Firecracker als Härtung. |
| K3 | **Prompt-Injection via Gruppe** | 7/7 | Forwarded/edited Messages, Reply-Chains, Multi-Message Injection-Ketten. Telegram = impliziter State. |
| K4 | **OpenRouter SPOF + Kosten** | 7/7 | Kein Fallback, kein Budget-Guard, kein adaptives Downgrade. Guard ebenfalls Cloud → Totalausfall. |
| K5 | **Datei-Output ohne Content-Review** | 5/7 | Kein Guard auf Datei-Inhalt, keine Size-Limits, kein MIME-Check. PDF als Exploit-Vektor. |
| K6 | **Guard kennt User-Prompt nicht** | 5/7 | Nur Output + caller_context. Ohne Input keine semantische Angriffserkennung. |
| K7 | **Vision ohne Pre-Check** | 5/7 | Bilder ohne Size/Type/Content-Prüfung an Qwen. Ressourcen-Exhaustion möglich. |

---

## Tier 2 — Starker Konsens (3–4 Reviewer)

| # | Thema | n | Kurzbeschreibung |
|---|-------|---|-------------------|
| N1 | **WARNUNG ≠ BLOCK** | 4 | WARNUNGs gehen an User. Kein Auto-Eskalationsmechanismus. |
| N2 | **HitL erzwingt State / Timeout** | 4 | RAM-State für wartende Tasks. Server-Restart killt alles. Kein Auto-Reject. |
| N3 | **Rate-Limiting fehlt** | 4 | Kein Budget pro User. HitL-State-Poisoning (OOM) möglich. |
| N4 | **Race Conditions Multi-User** | 4 | Gleichzeitige HitL-Bestätigungen kollidieren. Kein Task-Ownership. |
| N5 | **LLM als Policy-Engine unzuverlässig** | 3 | Drei probabilistische Meinungen, keine deterministische Autorität. |
| N6 | **Persona-Leakage** | 3 | caller_context ist Patch. Modell weiß nicht wann edgy erlaubt. Policy-Layer vor Persona wäre sauberer. |
| N7 | **"Stateless" ist Illusion** | 3 | Telegram = impliziter State, HitL = RAM-State, System-Prompt = editierbarer State. |
| N8 | **Replay-Attacken** | 3 | Keine Deduplizierung. DeepSeek: update_id-Tracking als triviale Lösung. |

---

## Tier 3 — Scharfe Einzelfunde nach Reviewer

### GPT-5 — Konzeptionelle Tiefe

| # | Thema | Kurzbeschreibung |
|---|-------|-------------------|
| G1 | Tool-Chain Injection | Input→LLM→Tool→Output→LLM→User. Jede Stufe Injection-Punkt. Tool-Output implizit trusted. |
| G2 | Kosten als Systemvariable | Nicht Monitoring, sondern aktive Steuerung. Effort_score ohne Kosten-Korrelation. |
| G3 | Policy-Layer vor Persona | LLM (neutral) → Policy Filter → Persona Layer. Rabe erst am Ende. |
| G4 | Trust-Boundaries nicht dokumentiert | Was ist trusted/untrusted? Muss aufgemalt werden können. |
| G5 | Context Sanitizer fehlt | Kein Hard-Cap auf Reply/Forward-Kontext vor LLM-Call. |
| G6 | RISKY_ACTION Intent | "Lösche alles" — Risiko ohne Code-Intent. |
| G7 | Tool-Isolation als Subsystem | Orchestrator entscheidet ob Tool existiert, nicht LLM. |

### Gemini 3 Pro — Physische Stack-Realität

| # | Thema | Kurzbeschreibung |
|---|-------|-------------------|
| P1 | VRAM-Kollision Whisper/FAISS | Whisper + Sentence-Transformers + RAG-Reindex = CUDA OOM → crasht gesamten Zerberus. |
| P2 | Whisper zerstört Regex-Router | Phonetische Fehler machen Keyword-Matching unbrauchbar. Braucht fehlertoleranten Classifier. |
| P3 | Long-Polling in ASGI = Thread-Starvation | getUpdates blockiert FastAPI Event-Loop. Huginn muss eigener Prozess sein. |
| P4 | HitL-State-Paradoxie | HitL widerspricht Stateless fundamental. RAM-State = fragil. |
| P5 | 500-Zeichen-Grenze = UX-Desaster | Telegram erlaubt 4096 Zeichen. Entscheidung muss intent-basiert sein. |
| P6 | Markdown→PDF crasht bei LLM-Output | Kaputte Tags, ungeschlossene Tabellen. Braucht Sanitizer. |
| P7 | Whisper Queue-Blocking | Audio muss von Text-Pipeline entkoppelt sein. |
| P8 | Sarkasmus + NLP-Bestätigung toxisch | "Ja genau, mach den Server kaputt" — GO oder STOP? |
| P9 | AUDIO_RETRY Intent | Whisper-Müll erkennen, nicht dem LLM zuschreiben. |
| P10 | ABORT_TASK Intent | Wartenden HitL-Task aktiv abbrechen. |

### Opus — Architektur-Chirurgie

| # | Thema | Kurzbeschreibung |
|---|-------|-------------------|
| O1 | getUpdates liefert unkontrolliert Payload-Typen | Contacts, Locations, Polls, Dice — ungetestete Typen = Deserialisierungs-Angriffsfläche. TypeError killt Worker. |
| O2 | Edited Messages = nachträgliche Input-Mutation | User editiert Nachricht nach Reaktion. Bei HitL: editiert Anfrage, klickt dann ✅ auf alte Bestätigung. |
| O3 | Callback-Query-Spoofing | Telegram validiert nicht ob klickender User = anfragender User. User B klickt User As ✅-Button. By design. |
| O4 | Intent-Router ohne Feedback-Loop | Router entscheidet, LLM kann nicht sagen "falsch klassifiziert". Kein Re-Route-Protokoll. |
| O5 | Effort-Score = impliziter Jailbreak-Verstärker | Hoher Score → System-Prompt fordert provokantere Antwort. Policy-Bypass durch eigenes System. |
| O6 | System-Prompt = sicherheitskritischer State | Editierbar, keine Versionierung, kein Audit-Log, kein Rollback. Guard-Entscheidungen hängen davon ab. |
| O7 | Guard-Latenz nicht budgetiert | Input-Guard + LLM + Output-Guard = 3 Cloud-Roundtrips = 6–15 Sekunden. In Telegram inakzeptabel. |
| O8 | Kosten-Multiplikator bei Code-Intent | Input-Guard → LLM → Sancho-Panza → Output-Guard → Sandbox = 5 sequentielle Schritte, 3 Cloud-Calls. |
| O9 | Kein strukturiertes Logging | Multi-Agent-System blind debuggen. Bei 5+ Usern unmöglich. |
| O10 | Keine Graceful Degradation definiert | OpenRouter 503 → was sagt Huginn? Guard-Call fail → Antwort ungeprüft raus oder zurückhalten? |
| O11 | Admin ≠ User nicht unterschieden | Admin bestätigt sich selbst. Kein Rollen-Konzept für zweiten Admin. |
| O12 | Intent als LLM-Output statt Regex | LLM liefert `{"intent": "CODE", "confidence": 0.9}` mit. Eliminiert Router-Subsystem komplett. |

### DeepSeek — Telegram-Protokoll-Robustheit

| # | Thema | Kurzbeschreibung |
|---|-------|-------------------|
| D1 | Telegram 429 Rate-Limits | Bot in Gruppe mit autonomen Einwürfen riskiert Shadowban. Ohne 429-Handling + Queue gehen Nachrichten verloren. |
| D2 | HitL-NLP ohne Task-Bindung | "Mach" bei zwei wartenden Tasks → welche wird bestätigt? Braucht explizite Task-ID-Referenz. |
| D3 | System-Prompt-Selbst-Injection | Admin baut versehentlich Guard-Bypass in editierbaren Prompt ein. Prompt braucht Policy-Validierung. |
| D4 | Kein Pre-Flight für Sandbox-Code | Kein Linter vor Sandbox-Ausführung. 80% offensichtlich defekter Code sofort abfangen. |
| D5 | Keine User-Quarantäne | Kein Mechanismus zum temporären/permanenten Sperren einzelner User. 20 Zeilen Code. |
| D6 | Datenschutz-Transparenz fehlt | Gruppen-User wissen nicht dass Nachrichten an DeepSeek (China) / Mistral gehen. Kein /datenschutz-Command. |
| D7 | Keine Bild-Kompression vor Vision | 10-MP-Fotos direkt an Cloud. Resize auf 1024px spart Faktor 10 Payload. |
| D8 | update_id als triviale Replay-Prevention | Höchste verarbeitete update_id speichern, niedrigere verwerfen. Persistent über Neustarts. |
| D9 | channel_post nicht behandelt | Bot in Channel → unerwartete Updates → Abstürze. Chat-Typ-Prüfung fehlt. |
| D10 | Message-Threads (Topics) ignoriert | message_thread_id nicht behandelt. Antworten landen in falschem Thread. |

---

## Fehlende Intents (kumulativ, alle 7 Reviewer)

| Intent | Beschreibung | Quelle |
|--------|-------------|--------|
| ADMIN | /stats, /restart, Logs | Mistral |
| HELP | "Wie funktionierst du?" | Mistral |
| FEEDBACK | "Der letzte Code war kaputt" | Mistral |
| CONTEXT | Fragen nach Kontext (→ ablehnen) | Mistral |
| MEDIA_ANALYZE | Sprachnachrichten, Dokumente, Bilder | Grok |
| TOOL_USE | "Rechne das", "Wetter" | Grok |
| SUMMARY_REQUEST | "Fasse zusammen" | Grok |
| ESCALATE_TO_NALA | "Ernsthaftes Thema" | Grok |
| ERROR_HANDLING | "Was ist schiefgelaufen?" | Copilot |
| MULTI_STEP_TASK | Mehrere LLM-Runden | Copilot |
| SANDBOX_ANALYZE_ONLY | Code-Analyse ohne Execution | Copilot |
| CHAIN_REQUEST | "Mach X, dann Y" | Copilot |
| RISKY_ACTION | "Lösche alles" — Risiko ohne Code | GPT-5 |
| AUDIO_RETRY | Whisper-Müll erkennen | Gemini |
| ABORT_TASK | HitL-Task aktiv abbrechen | Gemini |

---

## Bestätigt (korrekt adressiert, mit Caveats)

| Design-Entscheidung | Status | Caveats |
|---------------------|--------|---------|
| Kein RAG/Memory | ✅ Solide | — |
| Stateless-Design | ⚠️ Bedingt | Telegram = impliziter State, HitL = RAM-State, System-Prompt = editierbarer State |
| HitL für Code-Execution | ⚠️ Bedingt | Braucht Task-Ownership, Task-ID-Bindung, Timeout, Callback-Spoofing-Schutz |
| Tailscale-VPN | ⚠️ Bedingt | Intern nicht Zero-Trust. Kompromittiertes Device → FastAPI-Zugriff |
| Guard mit caller_context | ⚠️ Patch | Braucht Policy-Layer-Redesign langfristig |

---

## Offene Architektur-Entscheidungen

| # | Frage | Relevante Funde |
|---|-------|-----------------|
| 1 | Huginn als eigener Worker-Prozess oder im FastAPI-Prozess? | P3, O7 |
| 2 | Intent-Erkennung: Regex, Classifier, oder LLM-Output? | P2, O4, O12 |
| 3 | HitL-State: RAM-only, SQLite-backed, oder Redis? | P4, N2, D2 |
| 4 | Guard-Architektur: seriell (3x Latenz) oder parallel (Error-Handling-Komplexität)? | O7, O8 |
| 5 | Graceful Degradation bei OpenRouter-Ausfall? | O10, K4 |
| 6 | Persona-Architektur: caller_context-Patch oder Policy→Persona-Pipeline? | N6, G3, O5 |
| 7 | Trust-Boundary-Diagramm erstellen? | G4 |
| 8 | VRAM-Scheduling: Whisper vs. FAISS vs. Embeddings? | P1 |
| 9 | Datenschutz-Transparenz für Gruppen-User? | D6 |
| 10 | Admin-Rollen-Konzept (für Alan)? | O11 |

---

## Rauschen (irrelevant für aktuelles Setup)

- Microservices, Horizontal Scaling, Prometheus/Grafana, Kubernetes
- PostgreSQL statt SQLite (Huginn ist stateless)
- aiogram statt python-telegram-bot (Geschmackssache)
- HMAC-Signierung (HTTPS + Bot-Token + Tailscale reicht)
- Side-Channel-Attacks in Sandbox (erst bei Wachstum)
- Firecracker (Overkill für Heimserver)

---

## Reviewer-Schärfe-Profil

| Reviewer | Fokus | Stärke | Schwäche |
|----------|-------|--------|----------|
| Mistral | Breite | Vollständige Abdeckung, Implementierung | Token-Sturmgewehr |
| Grok | Telegram | Sauberes Format, Protokoll-Awareness | Wenig Architektur-Tiefe |
| Copilot | Edge-Cases | Viele Intent-Ideen, Breite | Redundant |
| GPT-5 | Konzepte | Trust-Boundaries, Tool-Chain, Policy-Design | Wenig Telegram-spezifisch |
| Gemini 3 Pro | Hardware/Ops | VRAM, Whisper-Regex, ASGI, physische Realität | Wenige Security-Vektoren |
| **Opus** | **Architektur** | **Callback-Spoofing, Latenz-Budget, Effort-as-Jailbreak, System-Prompt-as-State** | **Wenig Ops** |
| **DeepSeek** | **Protokoll** | **Telegram 429, update_id, Task-Bindung, Datenschutz, channel_post-Handling** | **Wenig konzeptionell** |

---

## Statistik

- **Einzigartige Probleme identifiziert:** 72
- **Nach Deduplizierung:** 49
- **Davon Konsens (3+ Reviewer):** 15
- **Davon scharfe Einzelfunde:** 34
- **Fehlende Intents vorgeschlagen:** 15
- **Offene Architektur-Entscheidungen:** 10
