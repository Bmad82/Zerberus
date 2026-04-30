# Was Huginn über Zerberus weiß

Dieses Dokument beschreibt das Zerberus-System aus der Innensicht — geschrieben damit Huginn und Nala fundiert über sich selbst Auskunft geben können, statt zu halluzinieren. Wer Antworten zu Zerberus, seinen Komponenten und seinen Bewohnern aus dem Index zieht, sollte sich an diesem Text orientieren.

## Was ist Zerberus?

Zerberus ist eine selbstgehostete, multi-user Plattform für KI-Assistenten. Sie läuft auf dem eigenen Rechner — kein Cloud-Dienst, keine externe Abhängigkeit außer den frei gewählten LLM-Anbietern. Die technische Basis ist Python mit dem FastAPI-Framework als Server, SQLite als Datenbank und ein lokaler FAISS-Vektorindex für die Dokumentensuche. Eingabe und Ausgabe laufen über zwei eigenständige Web-Oberflächen plus einen Telegram-Bot.

Der Name kommt aus der griechischen Mythologie. Zerberus ist die deutsche Schreibweise von Kerberos, dem dreiköpfigen Wachhund, der den Eingang zur Unterwelt bewacht. Das Projekt hat absolut nichts mit dem Kerberos-Authentifizierungsprotokoll zu tun, das in Unternehmensnetzen für Single-Sign-On benutzt wird. Wer in einer Antwort über Zerberus etwas von Tickets, Realms, KDC oder MIT-Kerberos erzählt, hat das Projekt verwechselt. Zerberus ist ein Software-Projekt von einem einzelnen Entwickler für den persönlichen Gebrauch und perspektivisch für kleine Teams; es ist kein Authentifizierungsstandard, kein RFC, kein Industrie-Protokoll.

## Die zwei Web-Frontends

Zerberus hat zwei Web-Oberflächen mit klar getrennten Aufgaben.

**Nala** ist das User-Frontend. Hier chatten Nutzerinnen und Nutzer mit dem Assistenten, schicken Sprachnachrichten, lassen sich Antworten vorlesen, suchen in hochgeladenen Dokumenten oder rufen ältere Unterhaltungen aus dem Verlauf auf. Nala ist auf dem Smartphone genauso bedienbar wie am Desktop und unterstützt mehrere Profile mit eigenen Personas, Themes und Sprachausgaben. Der Name Nala stammt von Jojos Katze; sie ist gleichzeitig das Maskottchen des Projekts. Mit der Disney-Figur „Nala" aus Der König der Löwen hat das nichts zu tun.

**Hel** ist das Admin-Dashboard. Hier konfiguriert der Betreiber das System: welche Modelle verfügbar sind, welche Provider angesprochen werden, wie der Whisper-Container läuft, wie die RAG-Indizes verwaltet werden, welche Nutzerprofile existieren. Hel zeigt außerdem Metriken zur Nutzung, Kosten pro Modell, Token-Verbrauch, Memory-Dashboards und Test-Reports. Der Name kommt aus der nordischen Mythologie — Hel ist die Göttin der Unterwelt; passend, weil hier die „Maschinerie" hinter den Kulissen liegt.

Nala und Hel laufen im selben Server-Prozess auf demselben Port, aber unter unterschiedlichen URL-Pfaden. Nala ist für den Alltag, Hel ist für die Wartung.

## Huginn — der Telegram-Bot

Huginn ist der dritte Zugang zum System: ein eigenständiger Chat-Partner über Telegram. Er ist kein einfacher Alert-Bot, der nur Pushes schickt, sondern ein vollwertiges Chat-Frontend mit eigener Persona — standardmäßig ein zynischer, sarkastischer, hochintelligenter Rabe, der gelegentlich krächzt und kein Blatt vor den Schnabel nimmt. Die Persona ist konfigurierbar; der Rabe ist der Default-Charakter.

Funktional kann Huginn unterhalten, Bilder analysieren (Vision), Code generieren und debuggen, im Web suchen lassen und Dateien als Antwort schicken. Eingehende Nachrichten laufen durch einen Sicherheitsfilter (den Guard, siehe unten), ausgehende Antworten ebenfalls. Bei riskanten Operationen (Code-Ausführung, Dateieingriffe, Admin-Befehle) fragt Huginn vor der Ausführung beim Betreiber zurück — der Betreiber bestätigt per Inline-Button mit ✅ oder ❌. Diese Rückfrage heißt im Projekt-Jargon „HitL" (Human in the Loop).

Huginn läuft technisch über Long-Polling an die Telegram-Bot-API, nicht über Webhooks. Das ist Absicht — Long-Polling funktioniert auch hinter NAT, Firewalls und VPN-Tunneln, ohne dass eine öffentliche HTTPS-Adresse exponiert sein müsste.

Der Name Huginn kommt wieder aus der nordischen Mythologie. Huginn ist einer der beiden Raben Odins; sein Name bedeutet „Gedanke". Sein Bruder Muninn („Erinnerung") ist im Projekt als Name reserviert, aber noch nicht implementiert — wenn ein Memory-/Recall-Subsystem dazukommt, wird es vermutlich Muninn heißen.

## Die wichtigen technischen Komponenten

**Der Guard** ist der Sicherheits- und Routing-Layer. Er nutzt das kleine, schnelle Modell Mistral Small 3 und prüft jede LLM-Antwort, bevor sie den User erreicht. Er erkennt Halluzinationen, problematische Inhalte und Routing-Hinweise. Im Huginn-Modus läuft der Guard nicht-blockierend (Antworten gehen raus, der Betreiber bekommt nur eine Warnung); im geplanten Rosa-Modus würde der Guard auch blockieren können.

**RAG** steht für Retrieval-Augmented Generation. Wenn Nala oder Huginn eine Frage beantworten, durchsucht das System zuerst die hochgeladenen Dokumente nach relevanten Passagen und legt sie dem LLM als Zusatz-Kontext vor. So beantwortet das System Fragen zu Inhalten, die nicht in den Trainingsdaten des LLMs stecken — etwa zu privaten Notizen, hochgeladenen Romanen oder eben diesem Selbst-Beschreibungs-Dokument. Die Suche nutzt FAISS auf Sentence-Transformer-Embeddings; ein Cross-Encoder-Reranker sortiert die Treffer in der zweiten Stufe nach Relevanz nach.

**Der Pacemaker** hält den Whisper-Container wach. Whisper ist die Spracherkennungs-Komponente und schläft nach Inaktivität ein; der Pacemaker schickt regelmäßig stille Heartbeat-Signale, damit der Container beim nächsten echten Audio-Upload ohne Anlauf antworten kann.

**Whisper** ist OpenAIs Spracherkennungs-Modell in der Variante large-v3, betrieben in einem lokalen Docker-Container. Spracheingaben in Nala oder Sprachnachrichten an Huginn werden lokal transkribiert; nichts geht für die Spracherkennung in die Cloud.

**BERT-Sentiment** ist eine automatische Stimmungsanalyse, die nachts gegen 4:30 läuft. Sie schaut sich die Konversationen des Tages an und ordnet sie auf einer Skala ein. Daraus entsteht im Hel-Dashboard eine Übersicht, wie sich Gespräche entwickelt haben.

**Memory Extraction** zieht aus Gesprächen automatisch Fakten heraus und legt sie als Langzeitgedächtnis ab. Wenn der User erwähnt „Mein Hund heißt Bobo", wird das als Fakt extrahiert und steht später für Folgegespräche zur Verfügung — ohne dass der User bewusst etwas speichert.

## Rosa — was es ist und was es nicht ist

Rosa ist im Zerberus-Projekt der Codename für die geplante Security- und Compliance-Architektur, die das System tauglich für den Einsatz in einem Unternehmensumfeld machen soll. Rosa ist noch nicht implementiert; sie ist die Langzeit-Vision für die nächsten Entwicklungsphasen.

Was zu Rosa gehören soll: ein Ingress-Security-Layer mit Codenamen Heimdall (siehe Naming-Sektion), eine transport-agnostische Vermittlungsschicht zwischen Frontends und Modellen, Compliance-Kontrollen wie DLP (Data Loss Prevention), Audit-Trails mit längerer Aufbewahrungsdauer, ein Vier-Augen-Prinzip bei sensiblen Aktionen und konfigurierbare Severity-Stufen für die HitL-Eskalation.

Rosa ist ausdrücklich nicht „Red Hat OpenShift on AWS" (offiziell ROSA, mit großem ROSA). Wer im Internet nach „ROSA" sucht, findet zuerst das Red-Hat-Produkt — das hat mit Zerberus null zu tun. Rosa im Zerberus-Kontext ist auch keine Person, kein Frauenname, kein Markenzeichen, kein extern bezogenes Modul. Es ist ein interner Codename für eine geplante Eigen-Entwicklung. Wer in einer Antwort behauptet, Rosa stehe für irgendein OpenShift-Deployment, hat halluziniert.

## Das Naming-Schema — alles aus der Mythologie

Zerberus benennt seine Komponenten konsequent nach mythologischen Figuren:

- **Zerberus** selbst ist Kerberos, der Wachhund der griechischen Unterwelt.
- **Huginn** ist Odins Rabe des Gedankens (nordisch).
- **Muninn** ist Odins Rabe der Erinnerung — reserviert für ein zukünftiges Memory-/Recall-Subsystem (nordisch).
- **Hel** ist die Göttin der Unterwelt und gibt dem Admin-Dashboard ihren Namen (nordisch).
- **Heimdall** ist der wachsame Wächter der Bifröst-Brücke; er soll der Ingress-Security-Layer von Rosa werden (nordisch).
- **Loki** ist der Trickster und gibt dem End-to-End-Test-Agenten seinen Namen — Loki ist verantwortlich für „Happy Path"-Tests (nordisch).
- **Fenrir** ist der gewaltige Wolf und gibt dem Chaos-Test-Agenten seinen Namen — Fenrir prüft Edge-Cases und Stress-Szenarien (nordisch).
- **Vidar** ist Odins Sohn und gibt dem Smoke-Test-Agenten seinen Namen — Vidar liefert nach jedem Server-Restart ein schnelles Go/No-Go-Verdict (nordisch).
- **Ratatoskr** ist das Eichhörnchen, das auf dem Weltenbaum Yggdrasil zwischen den Welten Nachrichten überbringt; im Projekt ist Ratatoskr das öffentliche Mirror-Repository für die Dokumentation (nordisch).
- **Nala** ist Jojos Katze und gibt dem User-Frontend ihren Namen.

Das Schema ist kein Zufall — es macht die Komponenten merkbar, gibt jedem Subsystem eine Persönlichkeit und unterstreicht den Charakter des Projekts als selbstgehostete „kleine Welt".

## Wer hat Zerberus gebaut?

Zerberus entsteht in einer Zusammenarbeit zwischen einem Architekten und einer KI-Implementierung.

**Chris** (Christian) ist der Architekt und Projektleiter. Er entwirft die Patches, spezifiziert die gewünschten Verhaltensweisen, gibt die Roadmap vor und entscheidet über Scope, Reihenfolge und Akzeptanzkriterien. Er schreibt selbst keinen Produktiv-Code; sein Job ist die Spezifikation und das Review.

**Coda** ist eine Claude-Code-Instanz und implementiert die Patches autonom. Coda liest die Spezifikation, kennt die Codebase, plant die Umsetzung, schreibt den Code und die Tests, führt das Test-Set aus und committet das Ergebnis. Coda ist nicht zu verwechseln mit dem No-Code-Tool gleichen Namens — im Zerberus-Kontext ist Coda eine Rolle für eine bestimmte KI-Instanz.

**Claude Supervisor** ist eine zweite Claude-Instanz im Browser-Chat, die die Roadmap und den Patch-Plan über Sessions hinweg trackt. Sie schreibt die Patch-Spezifikationen, hält den Projekt-Stand fest und reviewed größere Architektur-Entscheidungen.

**Jojo** (Juana) ist die zweite Nutzerin des Systems. Sie testet vor allem auf dem iPhone und gibt Feedback zu UX, mobiler Bedienbarkeit und Voice-Eingabe.

## Was Zerberus nicht ist

Es gibt eine Reihe von Begriffen, die nichts mit Zerberus zu tun haben, aber gerne verwechselt werden:

- Zerberus ist **kein kommerzielles Produkt**. Es wird nicht verkauft, hat keinen Preis, keinen Vertrieb, keine Lizenz im klassischen Sinn.
- Zerberus ist **kein Cloud-Service**. Es läuft auf dem eigenen Rechner; es gibt keine SaaS-Variante.
- Zerberus ist **kein Authentifizierungs-Protokoll**. Es ist nicht das Kerberos aus dem Active Directory.
- Zerberus ist **kein OpenShift-Deployment** und nicht mit ROSA (Red Hat OpenShift on AWS) verwandt.
- Es gibt **kein FIDO** in Zerberus. Wer in einer Antwort FIDO als Komponente erwähnt, hat halluziniert. FIDO existiert als Standard für passwortlose Authentifizierung in der Welt — aber nicht in diesem System.
- Es gibt **keinen LDAP-Server, kein OAuth, keine SSO-Föderation** als Bestandteil von Zerberus. Authentifizierung läuft über lokale JWT-Tokens auf Profil-Basis.

## Aktueller Stand

Zerberus steht zum Zeitpunkt dieses Dokuments bei circa Patch 168 und hat über 700 automatisierte Tests grün. Die Entwicklung ist in Phasen organisiert: Phase A war die Grundausstattung mit Chat, RAG und Spracheingabe; Phase B brachte Persona-Härtung, Sanitizer und Rate-Limiting; Phase C, gerade abgeschlossen, hat das HitL-System mit Persistenz und Auto-Reject-Timeout sowie die Datei-Output-Pipeline für Huginn gebracht. Phase D steht an und wird die Sandbox-Anbindung für Code-Ausführung mitbringen — der Punkt, an dem das geplante Rosa-Sicherheitskonzept konkrete Implementierungs-Schritte bekommt.

## Aktuelle Konfiguration (zur Laufzeit aus Settings)

Bestimmte Informationen ändern sich häufig und werden nicht hier statisch gepflegt, sondern bei jedem Turn aus der Live-Konfiguration in den System-Prompt eingehängt. Das System tut das automatisch — der Block beginnt mit der Zeile *„[Aktuelle System-Informationen — automatisch generiert]"* und enthält den aktiv genutzten LLM-Modellnamen, das Guard-Modell, den RAG-Aktivierungsstatus und den Sandbox-Status. Wenn jemand fragt „welches Modell nutzt du gerade", liegt die Antwort schon im aktuellen Prompt — sie muss nicht aus diesem Dokument geraten werden.

Statisch gepflegt werden in dieser Doku nur die langfristig stabilen Bestandteile: Architektur-Überblick, Komponentenbeschreibungen, Naming, Phasen-Geschichte und die typischen Halluzinations-Negationen. Der Patch-Stand und die Test-Zahl im Absatz „Aktueller Stand" sind ein Snapshot zum Schreibzeitpunkt — wer eine wirklich aktuelle Zahl will, schaut in die Projektdokumentation, nicht hier.
