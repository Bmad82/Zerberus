# Zerberus / Nala – Dein persönlicher KI-Assistent

---

## Was ist das?

Zerberus ist ein persönlicher KI-Assistent, der auf deinem eigenen Computer läuft.
Du sprichst mit ihm – per Sprache oder per Text – und er antwortet dir auf Deutsch.

Der Assistent heißt **Nala** (manchmal auch „Rosa"). Du erreichst ihn über deinen Browser,
egal ob du zu Hause bist oder von unterwegs via Tailscale zugreifst.

**Was Nala kann:**
- Gesprochene Fragen verstehen (Mikrofon → Whisper → Antwort)
- Sich an frühere Gespräche erinnern (semantisches Gedächtnis)
- Verschiedene Dialekte und Stile (Berlinerisch, Schwäbisch, Emoji-Modus)
- Auf dich zugeschnittene Antworten je nach Profil und System-Prompt
- Code in einer abgesicherten Umgebung ausführen (Docker-Sandbox)

Das Besondere: Deine Daten bleiben bei dir. Kein Cloud-Speicher, kein Tracking –
nur dein lokaler Computer und eine KI-API deiner Wahl.

---

## Was brauchst du?

Bevor du anfängst, besorg dir folgende Dinge (alle kostenlos oder mit kostenlosem Einstieg):

- **OpenRouter-Account + API-Key** – Das ist die KI-Schnittstelle, über die Nala denkt.
  Kostenlos registrieren unter: https://openrouter.ai
  Nach der Registrierung: API-Key erstellen und aufschreiben.

- **Tailscale** – Damit kannst du Nala sicher von unterwegs erreichen (Handy, anderer PC),
  als wärst du im Heimnetz. Kostenlos unter: https://tailscale.com
  Nach der Installation: Gerät hinzufügen, Tailscale-Domain deines PCs notieren
  (sieht so aus: `mein-pc.tail12345.ts.net`).

- **Docker Desktop** – Für erweiterte Funktionen (Code-Ausführung in der Sandbox).
  Kostenlos unter: https://docker.com
  Einfach installieren, danach starten.

- **BIOS: Virtualisierung aktivieren** – Einmalig nötig für Docker.
  Beim PC-Start BIOS öffnen (meist F2 oder Entf-Taste), dann
  „Intel VT-x" (Intel-PC) oder „AMD-V" (AMD-PC) aktivieren.
  Einmal gemacht, nie wieder nötig.

- **Python 3.11 oder neuer** – Muss auf deinem PC installiert sein.

---

## Installation

### Schritt 1 – Repository herunterladen

Lade das Projekt herunter (grüner „Code"-Button → „Download ZIP") und entpacke es,
oder klone es mit Git:

```
git clone <repository-url>
cd Zerberus
```

### Schritt 2 – Konfiguration einrichten

Im Projektordner findest du die Datei `config.yaml.example`.
Kopiere sie und benenne die Kopie um in `config.yaml`:

```
copy config.yaml.example config.yaml
```

Öffne `config.yaml` mit einem Texteditor (z.B. Notepad++ oder VS Code) und fülle aus:

- **`cloud_api_url`** – bleibt wie es ist (OpenRouter-Adresse)
- **`whisper_url`** – ersetze `YOUR_TAILSCALE_DOMAIN` durch deinen PC-Namen in Tailscale,
  z.B. `http://mein-pc.tail12345.ts.net:8002/v1/audio/transcriptions`
- **`profiles`** → `password_hash` – leer lassen für kein Passwort,
  oder später über das Admin-Dashboard setzen

Erstelle außerdem eine Datei namens `.env` im Projektordner mit diesem Inhalt:

```
OPENROUTER_API_KEY=sk-or-v1-dein-key-hier
```

### Schritt 3 – Virtuelle Umgebung einrichten

Öffne ein Terminal (cmd oder PowerShell) im Projektordner:

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Das lädt alle nötigen Bibliotheken herunter. Nur einmal nötig.

### Schritt 4 – Starten

Doppelklicke auf **`start.bat`** im Projektordner – oder starte im Terminal:

```
start.bat
```

Nala ist jetzt erreichbar unter: **http://localhost:5000/nala**

---

## Für Entwickler: GitHub & eigene Instanz

1. Repository klonen: `git clone https://github.com/DEIN_USERNAME/zerberus.git`
2. `config.yaml.example` → `config.yaml` kopieren und ausfüllen
3. `.env` anlegen mit: `OPENROUTER_API_KEY=...` und `ADMIN_PASSWORD=...`
4. JWT-Secret generieren: `python -c "import secrets; print(secrets.token_hex(32))"`
   → In `config.yaml` unter `auth.token_secret` eintragen
5. `venv` einrichten: `python -m venv venv`
6. Dependencies: `venv\Scripts\activate` dann `pip install -r requirements.txt`
7. `start.bat` starten

**Wichtig:** Niemals `config.yaml` oder `.env` committen — beide sind in `.gitignore`.

---

## Die Nala-Oberfläche

Öffne im Browser: `http://localhost:5000/nala`

### Login
Wähle dein Profil aus der Liste und gib dein Passwort ein (falls eines gesetzt ist).
Ohne Passwort einfach auf „Einloggen" klicken.

### Chat
Tippe deine Nachricht in das Eingabefeld unten und drücke Enter oder den Senden-Button.
Nala antwortet als Textnachricht.

### Spracheingabe
Klicke auf das Mikrofon-Symbol und sprich. Whisper wandelt deine Sprache in Text um,
Nala versteht und antwortet. Kurze Sprechpause = Aufnahme endet automatisch.

### Sessions
Links in der Seitenleiste siehst du deine bisherigen Gespräche.
Klicke auf eine Session, um sie weiterzuführen oder nochmal nachzulesen.
„Neue Session" startet ein frisches Gespräch.

---

## Das Hel-Dashboard (Admin)

Das Admin-Dashboard erreichst du unter: `http://localhost:5000/hel`

Hier steuerst du alles, was Nala ausmacht. Eine Übersicht der wichtigsten Bereiche:

### Modell wählen
Unter „Konfiguration" kannst du das KI-Modell wechseln (z.B. von Llama auf GPT-4o).
OpenRouter stellt eine Liste verfügbarer Modelle bereit.

### Temperatur
Ebenfalls in der Konfiguration: Je höher die Temperatur (0–2), desto kreativer und
freier antwortet Nala. Für sachliche Antworten eher 0.3–0.5, für Gespräche 0.7–1.0.

### Guthaben
Zeigt deinen aktuellen OpenRouter-Kontostand. So siehst du, wie viel du bisher verbraucht hast.

### Cleaner
Hier kannst du Korrekturen für Whisper-Transkriptionen einstellen.
Zum Beispiel: „wispa" → „Whisper", Füllwörter wie „ähm" entfernen.

### Fuzzy-Dictionary
Eine Liste von Wörtern, die Nala kennen soll (z.B. Namen, Fachbegriffe).
Wenn Whisper ein Wort falsch erkennt, sucht der Fuzzy-Matcher das ähnlichste Wort aus dieser Liste.

### Dialekte
Hier kannst du vordefinierte Antwort-Muster für Dialekte aktivieren oder anpassen.
Ein Emoji-Marker am Anfang deiner Nachricht aktiviert den jeweiligen Modus.

### System-Prompt
Der System-Prompt gibt Nala ihre Persönlichkeit. Hier kannst du einstellen,
wie Nala redet, was sie weiß und wie sie sich verhält. Pro Profil einstellbar.

### Metriken
Statistiken über deine Gespräche: Wortanzahl, Stimmung (Sentiment), Kosten pro Anfrage.
Gut um zu sehen, was das System alles verarbeitet hat.

### Sessions
Liste aller gespeicherten Gespräche. Du kannst Sessions exportieren (als JSON) oder löschen.

### Debug
Für technisch Interessierte: Zeigt den internen Systemzustand, aktive Module,
den letzten Pipeline-Durchlauf und mehr.

---

## Das Gedächtnis von Nala (RAG)

Stell dir Nalas Gedächtnis wie einen Zettelkasten vor: Für jedes Gespräch legt sie
einen kleinen Zettel an – und wenn du sie etwas fragst, durchsucht sie den ganzen
Zettelkasten nach dem passendsten Zettel.

Das Gleiche funktioniert jetzt auch mit eigenen Dokumenten. Du kannst eine Textdatei
oder ein Word-Dokument hochladen – zum Beispiel Notizen, ein Protokoll oder einen Artikel –
und Nala wird den Inhalt "gelesen" haben. Beim nächsten Gespräch kann sie daraus zitieren
oder darauf aufbauen.

### Wie lädst du ein Dokument hoch?

1. Öffne das Hel-Dashboard unter `http://localhost:5000/hel`
2. Klicke auf den Reiter **„Gedächtnis / RAG"**
3. Klicke auf „Datei auswählen" und wähle eine `.txt`- oder `.docx`-Datei aus
4. Klicke auf „Hochladen & Indizieren"
5. Nach kurzer Zeit erscheint eine Bestätigung, z.B.: *„23 Chunks indiziert aus Rosendornen.txt"*

Das war's. Nala kennt jetzt den Inhalt deines Dokuments.

### Was passiert dabei?

Das Dokument wird in kleine Abschnitte von etwa 300 Wörtern zerlegt (mit etwas Überlapp,
damit keine Sätze an Abschnittsgrenzen zerrissen werden). Jeder Abschnitt wird in eine
mathematische „Bedeutungs-Darstellung" umgewandelt und im Gedächtnis-Index gespeichert.

Beim nächsten Gespräch vergleicht Nala deine Frage mit allen gespeicherten Abschnitten
und zieht die passendsten als Kontext heran – bevor sie antwortet.

### Wie teste ich, ob es funktioniert hat?

Einfach Nala fragen! Wenn du z.B. eine Datei mit Gartennotizen hochgeladen hast,
frag: *„Was weißt du über Rosendornen?"* oder *„Was steht in meinen Notizen über…?"*

In der Index-Übersicht siehst du außerdem die Anzahl der gespeicherten Abschnitte
und welche Dateien bereits indiziert wurden.

### Index leeren

Wenn du alle hochgeladenen Dokumente aus dem Gedächtnis entfernen möchtest,
klicke im Reiter „Gedächtnis / RAG" auf **„Index leeren"**.
Die automatisch gespeicherten Gesprächserinnerungen bleiben davon unberührt.

---

## Der Pacemaker – Warum gibt es ihn?

Whisper (das Spracherkennungs-Programm) läuft in einem Docker-Container.
Wenn du eine Weile nicht mit Nala gesprochen hast, „schläft" der Container ein –
das erste Mal Sprechen dauert dann deutlich länger, weil der Container erst wieder
aufgeweckt werden muss.

Der **Pacemaker** verhindert das: Er schickt alle 4 Minuten ein kurzes, stilles
Signal an Whisper – quasi ein leises „Bist du noch da?" – damit der Container
wach bleibt.

### Wie lange läuft der Pacemaker?

Sobald du das erste Mal mit Nala gesprochen hast, startet der Pacemaker automatisch.
Er läuft standardmäßig **2 Stunden (120 Minuten)** – danach stoppt er, wenn nichts
mehr passiert ist. Sobald du wieder sprichst, startet er erneut.

### Laufzeit einstellen

Wenn du möchtest, dass der Pacemaker kürzer oder länger läuft:

1. Öffne das Hel-Dashboard unter `http://localhost:5000/hel`
2. Klicke auf den Reiter **„Systemsteuerung"**
3. Ändere den Wert im Feld „Pacemaker-Laufzeit (Minuten)"
4. Klicke auf „Speichern"

Die Änderung gilt ab dem nächsten Neustart des Servers.

---

## Zugriff von unterwegs (Tailscale)

Mit Tailscale kannst du Nala auch vom Handy oder einem anderen Computer aus erreichen,
ohne deinen Router konfigurieren zu müssen.

### Einrichtung
1. Tailscale auf deinem Hauptcomputer (dem mit Zerberus) installieren und starten.
2. Tailscale auch auf deinem Handy oder Laptop installieren, mit demselben Account einloggen.
3. In Tailscale siehst du nun die IP oder den Namen deines Hauptcomputers.

### Nala von unterwegs
Öffne im Browser deines Handys oder Laptops:
`http://DEIN-TAILSCALE-GERÄTENAME:5000/nala`

Stelle sicher, dass Zerberus auf dem Hauptcomputer läuft (start.bat aktiv).

---

## Häufige Fragen / Probleme

**Nala antwortet nicht auf Spracheingabe**
→ Läuft Whisper? Whisper ist ein separates Programm auf Port 8002.
  Prüfe die `whisper_url` in deiner `config.yaml`.

**„OPENROUTER_API_KEY not found"**
→ Hast du die `.env`-Datei im Projektordner angelegt?
  Inhalt: `OPENROUTER_API_KEY=sk-or-v1-dein-key-hier`

**Docker-Sandbox funktioniert nicht**
→ Läuft Docker Desktop? Starte es und warte, bis das Symbol in der Taskleiste grün zeigt.
  Falls Fehler wegen Virtualisierung: BIOS prüfen (Intel VT-x / AMD-V aktiviert?).

**Ich komme nicht ins Hel-Dashboard**
→ Das Passwort für Hel steht in deiner `.env`-Datei als `HEL_PASSWORD=...`.
  Falls nicht gesetzt, ist das Default-Passwort leer (einfach Enter drücken).

**Ich habe mein Profil-Passwort vergessen**
→ Öffne `config.yaml` und setze `password_hash: ''` für dein Profil.
  Damit kannst du dich wieder ohne Passwort einloggen und ein neues setzen.

**Der Server startet nicht**
→ Prüfe, ob die venv aktiv ist (`venv\Scripts\activate`) und ob alle Pakete installiert sind
  (`pip install -r requirements.txt`). Detailliertere Fehler erscheinen im Terminal-Fenster.

---

---

## Sentiment-Analyse

Nala analysiert die Stimmung jeder Nachricht automatisch – mit einem **deutschen BERT-Modell**
(`oliverguhr/german-sentiment-bert`), das speziell für deutsche Texte trainiert wurde.

**Das Modell erkennt drei Klassen:**
- `positive` – Freude, Lob, Zustimmung
- `negative` – Frust, Kritik, Unmut
- `neutral` – sachliche oder gemischte Aussagen

**GPU-Support:** Falls eine NVIDIA-GPU vorhanden ist, läuft das Modell auf CUDA – sonst auf der CPU.
Das Modell wird beim ersten Serverstart geladen und für alle weiteren Anfragen gecacht.
Sollte `torch` oder `transformers` nicht installiert sein, gibt Nala graceful `neutral (0.5)` zurück.

**Overnight-Auswertung:** Täglich um 04:30 wertet ein Hintergrund-Job alle Nachrichten der
letzten 24 Stunden aus und schreibt das BERT-Sentiment in die Datenbank. Die Ergebnisse sind
im Hel-Dashboard unter „Metriken" sichtbar (Toggle „BERT Sentiment" im Graph).

---

## Testing (Loki & Fenrir — Patch 93)

Zerberus hat zwei Test-Suiten auf Playwright-Basis:

- **Loki** (`zerberus/tests/test_loki.py`) — methodische E2E-Tests: Login, Chat-Input,
  Navigation, Hel-Dashboard, neuer Patch-91-Metrics-API-Envelope.
- **Fenrir** (`zerberus/tests/test_fenrir.py`) — Chaos-Agent: Prompt-Injection,
  XSS, SQL, Viewport-Wechsel, Rapid-Click.

**Voraussetzung:** Server läuft lokal auf `https://127.0.0.1:5000`, `loki` und
`fenrir` sind in `config.yaml` als Test-Profile angelegt (Patch 93).

```bash
# Beide Suiten mit HTML-Report
venv\Scripts\activate
pytest zerberus/tests/ -v --html=zerberus/tests/report/full_report.html --self-contained-html

# Nur Loki
pytest zerberus/tests/test_loki.py -v

# Nur Fenrir (Chaos)
pytest zerberus/tests/test_fenrir.py -v
```

Reports landen in `zerberus/tests/report/`. Patch 94 hat den Erstlauf gegen den
live Server gefahren — **32 passed, 0 skipped** in 47 s, kein 500er auf den 14
Chaos-Payloads. Patch 100 ergänzt `TestJavaScriptIntegrity` (Playwright
`pageerror`-Listener gegen Hel/Nala): **34 passed** in 54 s.

---

*Zerberus Pro 4.0 – Stand: 2026-04-27, Patch 169 (Self-Knowledge-RAG-Doku + Bug-Sweep: Bubble-Farben, RAG-Status-Lazy-Init, Cleaner-innerHTML-Guard)*
