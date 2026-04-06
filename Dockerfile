# Basis-Image mit Python 3.10
FROM python:3.10-slim

# Arbeitsverzeichnis
WORKDIR /app

# System-Abhängigkeiten (für einige Python-Pakete)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python-Abhängigkeiten kopieren und installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Restlichen Code kopieren
COPY . .

# Nicht-root Benutzer für Sicherheit
RUN useradd -m -u 1000 zerberus && chown -R zerberus:zerberus /app
USER zerberus

# Port freigeben
EXPOSE 5000

# Startbefehl (uvicorn mit reload deaktiviert, da wir im Container kein Live-Reload brauchen)
CMD ["uvicorn", "zerberus.main:app", "--host", "0.0.0.0", "--port", "5000"]