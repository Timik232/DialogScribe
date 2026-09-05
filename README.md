# DialogScribe

[DialogScribe](https://github.com/Timik232/DialogScribe) — веб-приложение для транскрипции аудио и видео с анализом на базе LLM.

Транскрипция выполняется через **Mistral Voxtral API**, диаризация спикеров — через **pyannote** (локально, GPU), аналитика (саммари, майндмапы, инсайты, чат) — через **OpenAI-совместимый API**.

## Возможности

- **Транскрипция** аудио и видео через Mistral Voxtral (облачная ASR)
- **Диаризация спикеров** через pyannote/speaker-diarization-3.1 (локально, NVIDIA GPU)
- **Аналитика на базе LLM**: саммари, майндмапы, инсайты, чат с транскрипцией
- **Веб-интерфейс** на SvelteKit с авторизацией пользователей
- **OpenAI-совместимый API** — можно подключать любую совместимую модель (gpt-4.1, gpt-4o-mini и др.)
- **Экспорт** в TXT, JSON, SRT, VTT, DOCX
- **Шаблоны** промптов и автопотоки (autoflow)
- **Отслеживание использования** с лимитами для пользователей
- **Vault Agent** для безопасного управления секретами

## Архитектура

```
┌─────────────────────────────────────────────┐
│  Frontend (SvelteKit SPA)                    │
│  ↳ Статический билд, отдаётся FastAPI        │
├─────────────────────────────────────────────┤
│  Backend (FastAPI)                           │
│  ↳ /api/transcribe — транскрипция            │
│  ↳ /api/summary, /api/mindmap, /api/insights │
│  ↳ /api/chat, /api/models                   │
│  ↳ /v1/audio/transcriptions (OpenAI-формат)  │
│  ↳ Auth, Admin, Exports, Templates, Usage    │
├─────────────────────────────────────────────┤
│  Mistral Voxtral API (ASR)    ← облако       │
│  OpenAI-compatible LLM API    ← облако       │
│  pyannote diarization         ← локально, GPU│
├─────────────────────────────────────────────┤
│  SQLite + Alembic миграции                   │
│  HashiCorp Vault Agent (секреты)             │
└─────────────────────────────────────────────┘
```

## Запуск через Docker

### Требования

- Docker + Docker Compose
- NVIDIA GPU + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- HuggingFace токен с доступом к моделям pyannote

### Настройка HuggingFace (для диаризации)

1. Создайте токен на [HuggingFace](https://huggingface.co/settings/tokens) (права "Read")
2. Примите условия использования моделей:
   - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
   - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
   - [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)

### Конфигурация

```bash
cp .env.example .env
```

Отредактируйте `.env`:

```env
# ASR — Mistral Voxtral
MISTRAL_API_KEY=your_mistral_api_key_here
ASR_URL=https://api.mistral.ai
ASR_MODEL=voxtral-mini-latest

# Диаризация — pyannote (локально)
HF_TOKEN=your_huggingface_token_here

# LLM — OpenAI-совместимый API
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4.1
LLM_MODELS=gpt-4.1,gpt-4o-mini
LLM_API_KEY=your_openai_api_key_here

# API-ключ для авторизации (опционально)
API_KEY=your-api-key-here

# Лимит загрузки (МБ, по умолчанию 1024 — единое значение с docker-compose, фронтендом и кодом)
MAX_UPLOAD_SIZE_MB=1024
```

### Сборка и запуск

**Production:**

```bash
docker compose build
docker compose up -d
```

Приложение будет доступно на `http://localhost:7860`.

**Dev-режим (порт 7860 на хост, без GPU, без Vault):**

```bash
docker compose -f docker-compose.dev.yaml up -d --build
```

Для запуска тестов (900+ тестов, не требуют GPU/HF-токен):

```bash
HOME=/tmp PYTHONUSERBASE=$HOME/.local JWT_SECRET=ci-test-only python -m pytest -q -m "not slow and not requires_gpu and not requires_hf_token and not requires_model"
```

### Vault Agent

Для управления секретами используется HashiCorp Vault Agent (sidecar-контейнер). Конфигурация находится в `vault/`. Секреты (GRADIO_USERNAME, GRADIO_PASSWORD и др.) инжектируются через entrypoint.sh при старте.

## API Endpoints

| Endpoint | Описание |
|----------|----------|
| `POST /v1/audio/transcriptions` | OpenAI-совместимая транскрипция — **выключена по умолчанию** (503; включается `V1_API_ENABLED=true` + `API_KEY`) |
| `POST /api/transcribe` | Транскрипция с расширенными параметрами |
| `POST /api/summary` | Генерация саммари (LLM) |
| `POST /api/mindmap` | Генерация майндмапа (LLM) |
| `POST /api/insights` | Извлечение инсайтов (LLM) |
| `POST /api/chat` | Чат с контекстом транскрипции |
| `WS /api/live-hints/ws` | Live-подсказки в реальном времени (WebSocket, JWT auth via query param; ошибки протокола — close-коды 44xx) |
| `WS /api/autoflow/ws` | Автопоток транскрипции+аналитики (WebSocket, JWT via query param; лимиты — terminal-событие `limit_exceeded`) |
| `GET/PUT /api/settings/asr-provider` | Настройка ASR-провайдера пользователя (mistral/litellm, по умолчанию litellm) |
| `GET /api/models` | Список доступных LLM-моделей |
| `/api/saved-transcriptions` | CRUD сохранённых транскрипций + share-ссылки (`/api/share/{share_id}` — публичный доступ без auth) |
| `POST /api/meeting-prep` | Подготовка к встрече (LLM) |
| Auth routes | Регистрация, логин, refresh/logout, восстановление пароля (rate-limited) |
| Admin routes | Управление пользователями, лимитами |
| Export routes | Экспорт в TXT, JSON, SRT, VTT, DOCX, PDF |

Ошибки API возвращают стабильный код + `correlation_id` (заголовок `X-Correlation-ID` на каждом ответе) — детали остаются в серверных логах.

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `MISTRAL_API_KEY` | Ключ API Mistral для ASR | — |
| `ASR_URL` | URL API Mistral | `https://api.mistral.ai` |
| `ASR_MODEL` | Модель ASR | `voxtral-mini-latest` |
| `HF_TOKEN` | HuggingFace токен (pyannote) | — |
| `LLM_BASE_URL` | URL OpenAI-совместимого API | `https://api.openai.com/v1` |
| `LLM_MODEL` | Модель LLM по умолчанию | `gpt-4.1` |
| `LLM_MODELS` | Список доступных моделей (через запятую) | `gpt-4.1,gpt-4o-mini` |
| `LLM_API_KEY` | Ключ API для LLM | — |
| `API_KEY` | Bearer-токен для API-авторизации | — |
| `MAX_UPLOAD_SIZE_MB` | Макс. размер загрузки (МБ) | `1024` |
| `DATABASE_URL` | URL базы данных (SQLite) | — |
| `ADMIN_EMAIL` | Email администратора (начальная загрузка) | — |
| `ADMIN_PASSWORD` | Пароль администратора | — |
| `SMTP_HOST` | SMTP-сервер для email | `smtp.mail.ru` |
| `SMTP_PORT` | Порт SMTP | `465` |

## Структура проекта

```
DialogScribe/
├── api.py                     # Точка входа FastAPI
├── Dockerfile                 # Multi-stage: frontend build + Python runtime
├── docker-compose.yaml        # vault-agent + dialogscribe-web
├── frontend/                  # SvelteKit SPA
│   ├── src/                   # Компоненты, роуты, стили
│   └── build/                 # Статический билд (генерируется)
├── routers/                   # FastAPI роуты
│   ├── transcription.py       # Транскрипция
│   ├── analysis.py            # Саммари, майндмап, инсайты, чат
│   ├── auth.py                # Авторизация
│   ├── admin.py               # Админ-панель
│   ├── exports.py             # Экспорт
│   ├── templates.py           # Шаблоны промптов
│   ├── autoflow.py            # Автопотоки
│   ├── live_hints.py          # Подсказки в реальном времени
│   ├── usage.py               # Отслеживание использования
│   ├── saved_transcriptions.py# Сохранённые транскрипции
│   └── settings.py            # Настройки пользователя (ASR-провайдер)
├── gigaam_transcriber/        # Ядро транскрипции
│   ├── transcriber.py         # GigaAMTranscriber → MistralASRClient
│   ├── diarization.py         # pyannote диаризация
│   ├── mistral_client.py      # Клиент Mistral Voxtral API
│   └── ...
├── alembic/                   # Миграции БД
├── vault/                     # Конфигурация Vault Agent
├── data/                      # SQLite база данных (runtime)
└── tests/                     # Тесты
```

## Лицензия

MIT License
