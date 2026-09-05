# Stage 1: Build SvelteKit frontend
FROM node@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS frontend-build

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime (production)
FROM python@sha256:fd76ade0c607f27677bc04be3c60749f400eedc941d9e72967e19a4cedff80c2

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

COPY gigaam_transcriber/ ./gigaam_transcriber/
COPY pyproject.toml ./
COPY routers/ ./routers/
COPY api.py ./
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY scripts/entrypoint_secrets.py /app/scripts/entrypoint_secrets.py

# Copy built frontend from Stage 1
COPY --from=frontend-build /app/frontend/build /app/frontend/build

RUN groupadd -g 10003 secrets \
    && useradd -u 10001 -g 10003 -m -d /home/appuser appuser \
    && mkdir -p /app/data /home/appuser/.cache \
    && chown -R 10001:10003 /app /home/appuser

# Model caches (pyannote uses torch.hub cache, speechbrain/hf use HF_HOME) live
# under /home/appuser/.cache — mount a named volume on the whole directory so
# warm caches survive restarts and enable offline start.
ENV HF_HOME=/home/appuser/.cache/huggingface
USER 10001

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/health'); print('healthy')" || exit 1

CMD ["python", "api.py"]
