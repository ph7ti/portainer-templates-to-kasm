FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MANIFEST_PATH=/app/registry/backend_manifest.json \
    STATE_PATH=/var/lib/kasm-orchestrator/state.json

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator ./orchestrator
COPY registry/backend_manifest.json ./registry/backend_manifest.json

RUN mkdir -p /var/lib/kasm-orchestrator

ENTRYPOINT ["python", "-m", "orchestrator.orchestrator"]
