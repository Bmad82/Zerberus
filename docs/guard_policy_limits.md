# Guard als Policy-Engine: Grenzen & Empfehlung (Patch 172)

**Datum:** 2026-04-28
**Patch:** 172, Block 4
**Scope:** Analyse-Dokument. **Keine Code-Änderung** — definiert die
Architektur-Empfehlung für Phase E (Rosa-Policy-Engine).

---

## Kernthese

Der LLM-basierte Guard (`hallucination_guard.py`, P120) ist ein **semantischer
Layer**, kein deterministischer Policy-Enforcer. Wer ihn als Allzweck-Sicherheits-
Schicht behandelt, bekommt drei Probleme:

1. **Latenz** — jeder Guard-Call ist ein OpenRouter-Round-Trip (200–2000 ms,
   live-gemessen in T17–T25). Das ist OK als Zweit-Schicht, aber katastrophal
   für Themen, die eine Regex in <1 ms lösen.
2. **Indeterminismus** — gleiche Eingabe kann an verschiedenen Tagen
   verschiedene Verdicts liefern (Mistral-Sampling, Modell-Updates).
3. **Kosten** — pro 1000 geprüfte Antworten ein paar Cent OpenRouter-Tokens.
   Bei Auth/Permission/Rate-Limiting wäre das pure Verschwendung.

Die Konsequenz: **Schichten trennen.** Was deterministisch lösbar ist, gehört
in eine schnelle, regelbasierte Vor-Schicht. Der LLM-Guard kommt erst danach,
und nur für die Fragen, die wirklich Semantik brauchen.

---

## 1. Deterministisch besser gelöst (KEIN Guard nötig)

| Aufgabe | Wo gelöst | Warum kein Guard? |
|---------|-----------|-------------------|
| **Rate-Limiting** | P163 (`rate_limiting.py` + `core/throttle.py`) | Reine Zeit-/Counter-Logik. <1 ms. Guard hätte hier nichts beizutragen. |
| **Input-Sanitizing** (Klartext-Patterns) | P162 (`core/input_sanitizer.py`) | Regex matched in Mikrosekunden. „Ignore previous instructions" braucht keinen LLM-Call. |
| **File-Extension-Whitelist / Blocklist** | P168 (`utils/file_output.py::is_extension_allowed`) | Set-Lookup. Triviale Determinismus. |
| **Auth & Permission** | JWT-Middleware + `request.state.permission_level` | Kryptografie + Datenbank, nicht Sprachverstehen. |
| **MIME-Type-Validierung** | P168 (Whitelist in `MIME_WHITELIST`) | Endung → MIME, harte Tabelle. |
| **File-Size-Limits** | P168 (`MAX_FILE_SIZE_BYTES = 10 MB`) | `len(bytes) <= LIMIT`. |
| **Docker-Container-Limits** | P171 (`--memory`, `--cpus`, `--pids-limit`, `--network none`) | Kernel-Enforcement, härter als jede LLM-Prüfung. |
| **HitL-Button-Pflicht für CODE/FILE/ADMIN** | P164 (`core/hitl_policy.py`) | Statische Intent-Tabelle. |
| **Forwarded-Message-Flag** | P162 (Sanitizer-Metadata) | Telegram-API liefert das Boolean — kein Sprachverstehen nötig. |

**Faustregel:** Wenn du die Antwort als `if/elif/else` schreiben kannst, ist
es kein Guard-Job.

---

## 2. LLM-Guard sinnvoll (semantische Schicht)

| Aufgabe | Warum braucht das den Guard? |
|---------|------------------------------|
| **Halluzinations-Erkennung** | Beispiel: „Berlin liegt in Frankreich." Keine Regex erkennt das — der Guard vergleicht semantisch mit Weltwissen. |
| **Kontext-abhängige Sicherheits-Bewertung** | „Wie funktioniert eine Bombe?" — im Chemie-Kontext (T20: Schulprojekt Dynamit) anders zu bewerten als im Waffen-Kontext. Live-Test 19+20 zeigt: Mistral entscheidet kontextabhängig. |
| **Sycophancy-Detection** | „Du hast völlig recht, ich habe mich geirrt!" auf eine offensichtlich falsche User-Aussage — semantisches Muster. |
| **Output-Tonfall** | Persona-Inkonsistenz (Huginn antwortet plötzlich höflich-formell statt Raben-frech). |
| **Content-Klassifikation** | Gewalt, Hass, Selbstschaden — Themen mit fließenden Grenzen, die Wörter-Listen schlecht abdecken. |

**Faustregel:** Wenn du die Antwort als „kommt drauf an, was gemeint ist"
formulierst, ist es ein Guard-Job.

---

## 3. Grauzone (Guard hilft, aber nicht zuverlässig)

| Aufgabe | Problem |
|---------|---------|
| **Persona-Exploitation** | Mistral erkennt es mal (T22: „Pirat ohne Regeln" → Verdict OK, weil Hauptmodell die Persona hielt), mal nicht. Pragmatisch: Sanitizer-Pattern + Guard-Backup, beide zusammen. |
| **Multi-Turn-Manipulation** | Guard sieht nur die einzelne Nachricht (`check_response(user_msg, response)`), nicht den Conversation-State. T14/T15 zeigen: „Definiere X als ‘harmlos'. Jetzt ersetze X durch …" wäre nur mit Conversation-Memory erkennbar — aktuell außer Reichweite. |
| **Code-Safety** | Guard versteht nicht immer, was Code _tut_. T21 (`__import__('os').system('rm -rf /')`) wurde von der Antwort selbst korrekt abgelehnt — wäre die Antwort bösartig gewesen, hätte der Guard möglicherweise OK gegeben. **Daher die zweite Schicht in P171:** Docker-Limits + Code-Blockliste, nicht „der Guard wird's schon erkennen". |
| **Obfuskation** (Leet-Speak, Unicode-Homoglyphen, Punkt-Trennung) | LLM-Guard erkennt Semantik teilweise, aber nicht systematisch. Echte Lösung: NFKC-Normalisierung + Confusables-Mapping VOR dem Pattern-Match (Vor-Schicht, nicht Guard). |
| **Halluzinierte Fakten ohne Vergleichswissen** | T24: Erfundene Telefonnummer wurde nicht erkannt — Guard hat kein „Bürgeramt-Berlin-Mitte echte Nummer"-Wissen. Lösung wäre RAG-basierter Fakt-Check, nicht der Halluzinations-Guard. |

---

## 4. Empfehlung für Phase E (Rosa-Policy-Engine)

### Schichten-Architektur

```
┌──────────────────────────────────────────────────────────────────┐
│ User-Input (Telegram-Message, Hel-Form, ...)                     │
└─────────────────────────┬────────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│ Schicht 1: Deterministisch (BLOCKING, schnell, kein LLM)         │
│ - Auth/Permission (JWT)                                          │
│ - Rate-Limiting (P163)                                           │
│ - Input-Sanitizer (P162) — incl. NFKC + Confusables für P173+    │
│ - Length/Charset-Limits                                          │
│ - File/MIME/Size (P168)                                          │
│ - Forwarded-Flag-Cap (P162, G5)                                  │
└─────────────────────────┬────────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│ Schicht 2: Statische Policy (BLOCKING, schnell, deterministisch) │
│ - HitL-Pflicht für CODE/FILE/ADMIN (P164)                        │
│ - Keyword-Eskalation (P172 Block 3, geplant für P173+)           │
│ - Per-User-Counter (3 WARNUNG in 10min → BLOCK)                  │
│ - Code-Blockliste (P171, in der Sandbox)                         │
└─────────────────────────┬────────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│ Schicht 3: Hard Sandbox (für Execution, nicht für Text)          │
│ - Docker --network none / --read-only / --memory / --pids-limit  │
│ - kernel-enforced — wirkt auch wenn alle obigen Schichten lügen  │
└─────────────────────────┬────────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│ LLM-Call (Hauptmodell, generiert Antwort)                        │
└─────────────────────────┬────────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│ Schicht 4: Semantischer Guard (P120, OpenRouter Mistral Small)   │
│ - Halluzinations-Detection                                       │
│ - Sycophancy-Detection                                           │
│ - Kontext-abhängige Content-Bewertung                            │
│ - Persona-Konsistenz-Check                                       │
│ - SOFT (default allow) — Härtung über `guard_fail_policy`        │
└─────────────────────────┬────────────────────────────────────────┘
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│ Schicht 5: Audit-Trail                                           │
│ - Jede Decision (Schicht 1-4) wird geloggt mit Tag + user_id     │
│ - Persistent (DB-Tabelle), durchsuchbar                          │
│ - Basis für Per-User-Counter (Schicht 2) und forensische Analyse │
└──────────────────────────────────────────────────────────────────┘
```

### Architektur-Prinzipien

1. **Fail-Fast in Schicht 1+2.** Sanitizer und Policy sollen in <1 ms
   antworten. Latenz hier ist direkt User-spürbar (jede Message läuft
   durch).

2. **Fail-Open in Schicht 4.** Guard-Ausfall darf den Bot nicht
   blockieren (`guard_fail_policy: allow` Default ist richtig). Wer
   höhere Sicherheit will, schaltet manuell auf `block`.

3. **Determinismus dominiert Semantik.** Wenn Schicht 1 oder 2
   blockiert, gibt es keinen Bypass durch Schicht 4 — auch nicht „aber
   der Guard sagt OK". Reihenfolge ist Hierarchie.

4. **Sandbox ist Kernel-Schicht.** Schicht 3 ist die einzige, die
   wirklich erzwungen wird (Linux Cgroups + Namespaces). Alle anderen
   Schichten könnten theoretisch umgangen werden — die Sandbox nicht.

5. **Audit-Trail ist Pflicht.** Jeder Block, jede WARNUNG, jeder
   ERROR-Verdict landet in einer durchsuchbaren Tabelle. Ohne Trail
   ist die Architektur blind für eigene Ausfälle.

### Was Rosa anders machen sollte als Huginn (heute)

- **Sanitizer pluggable.** Aktuell hardcoded `RegexSanitizer`. Rosa-Mode:
  `MLSanitizer` (kleines lokales Modell für Obfuskations-Detection). Das
  Interface (`InputSanitizer.sanitize`) existiert schon — nur der
  Singleton-Switch fehlt (Config-Key `security.input_sanitizer.mode`).
- **Policy-Engine statt verteilter `if`-Checks.** Heute liegen
  Policy-Entscheidungen in `core/hitl_policy.py`, `_send_as_file`,
  `_run_guard`, `manager.execute`. Rosa: zentrale `PolicyEngine`-Klasse
  mit deklarativen Regeln (YAML), eine Codestelle für alle Decisions.
- **Conversation-State für Multi-Turn-Detection.** Aktuell bewertet der
  Guard eine Message isoliert. Rosa könnte einen Sliding-Window-Kontext
  (letzte N Messages des Users) mit in den Guard-Prompt geben — fängt
  T14/T15-Pattern auf, kostet aber Tokens.

---

## Kurzfassung in einem Satz

> Der LLM-Guard ist die Bibliothekarin, die ein verdächtiges Buch quer
> liest — er ist nicht das Schloss an der Bibliothekstür. Wer sich auf
> ihn als alleinige Sicherheits-Schicht verlässt, baut auf Sand.

Phase D liefert die Sandbox (P171) und macht die Guard-Grenzen sichtbar
(P172). Phase E muss die Schichten-Architektur deklarativ
zusammensetzen.
