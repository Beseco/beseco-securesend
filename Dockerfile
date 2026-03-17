FROM python:3.12-slim

WORKDIR /app

# System-Abhängigkeiten
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python-Abhängigkeiten installieren
COPY cloud/requirements.txt cloud/requirements.txt
RUN pip install --no-cache-dir -r cloud/requirements.txt \
    && pip install --no-cache-dir requests msal

# Projektdateien kopieren
COPY core/ core/
COPY cloud/ cloud/
COPY run_cloud.py run_cloud.py

# Daten-Verzeichnis für SQLite-DB
RUN mkdir -p /data && chmod 777 /data

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["python3", "run_cloud.py"]
