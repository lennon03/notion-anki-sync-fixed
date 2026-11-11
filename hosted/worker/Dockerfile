# Build from the repository root: the worker imports notion_sync_addon.
#   docker build -f hosted/worker/Dockerfile .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY hosted/worker/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY notion_sync_addon ./notion_sync_addon
COPY hosted/worker/notion_anki_worker ./notion_anki_worker

CMD ["python", "-m", "notion_anki_worker"]
