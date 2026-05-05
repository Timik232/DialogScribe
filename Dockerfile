# Stage 1: Build SvelteKit frontend
FROM node:20-slim AS frontend-build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime (production)
FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libffi-dev \
    shared-mime-info \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

ARG HF_TOKEN
ENV HF_TOKEN=${HF_TOKEN}
RUN python -c "from pyannote.audio import Pipeline; Pipeline.from_pretrained('pyannote/speaker-diarization-3.1', use_auth_token='${HF_TOKEN}')" || true

COPY gigaam_transcriber/ ./gigaam_transcriber/
COPY pyproject.toml ./
COPY routers/ ./routers/
COPY api.py ./
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Copy built frontend from Stage 1
COPY --from=frontend-build /app/frontend/build /app/frontend/build

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health'); print('healthy')" || exit 1

CMD ["python", "api.py"]
