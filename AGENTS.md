# AGENTS.md — DialogScribe Project Conventions

## Build & Deploy

### Сборка фронтенда

**НИКОГДА не используй `npm run build` напрямую.**

Сборка проекта — через Docker:
```bash
docker compose build
```

Это собирает фронтенд и бэкенд вместе в Docker-образ. Прямой вызов `npm run build` не учитывает контейнеризацию и может дать неработающий результат.

### Docker Build

**НИКОГДА не используй `--no-cache`.**

```bash
# Правильно:
docker compose build

# НЕПРАВИЛЬНО (слишком долго, бессмысленно):
docker compose build --no-cache
```

Кэш Docker нужен для скорости сборки. `--no-cache` пересоздаёт ВСЕ слои, включая установку pip-пакетов и модели — это десятки минут впустую.
