"""Tests: LLM title generation (gigaam_transcriber.titles) and its wiring
into the two save paths (manual POST /saved-transcriptions and server-side
auto-persist after a finished transcription)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import gigaam_transcriber.titles as titles_module
from gigaam_transcriber.titles import (
    _clean_title,
    generate_title,
    generate_title_sync,
)
from tests.conftest import setup_auth_override


class _FakeLLM:
    def __init__(self, reply=None, exc=None):
        self.reply = reply
        self.exc = exc
        self.calls = []

    def call(self, system_prompt, user_text, **kwargs):
        self.calls.append((system_prompt, user_text, kwargs))
        if self.exc:
            raise self.exc
        return self.reply


# ── _clean_title ────────────────────────────────────────────────────


def test_clean_title_basic_normalization():
    # multiline answer → one line; surrounding quotes/dot stripped
    raw = "«Планируем запуск продукта:\nбюджет и дедлайн»."
    assert _clean_title(raw) == "Планируем запуск продукта: бюджет и дедлайн"


def test_clean_title_empty_and_none():
    assert _clean_title(None) is None
    assert _clean_title("") is None
    assert _clean_title("   ") is None
    assert _clean_title("«»") is None


def test_clean_title_truncates_to_limit():
    long = "суть " * 100
    cleaned = _clean_title(long)
    assert cleaned is not None
    assert len(cleaned) <= titles_module.TITLE_MAX_LEN


# ── generate_title_sync ─────────────────────────────────────────────


def test_generate_title_sync_ok():
    fake = _FakeLLM(reply="  Обсуждение релиза и бюджета  ")
    out = generate_title_sync("Иван: обсудим релиз? Мария: да, бюджет 500.", client=fake)
    assert out == "Обсуждение релиза и бюджета"
    system_prompt, user_text, kwargs = fake.calls[0]
    assert "заголовок" in system_prompt.lower() or "заголовк" in system_prompt
    assert "релиз" in user_text
    assert kwargs.get("model_override") == titles_module.DEFAULT_TITLE_MODEL
    assert kwargs.get("max_tokens") == titles_module.TITLE_MAX_TOKENS


def test_generate_title_sync_truncates_input():
    fake = _FakeLLM(reply="Суть")
    long_text = "а" * (titles_module.TITLE_MAX_INPUT_CHARS + 5000)
    generate_title_sync(long_text, client=fake)
    _, user_text, _ = fake.calls[0]
    assert len(user_text) == titles_module.TITLE_MAX_INPUT_CHARS


def test_generate_title_sync_llm_failure_returns_none():
    fake = _FakeLLM(exc=RuntimeError("gateway down"))
    assert generate_title_sync("текст диалога", client=fake) is None


def test_generate_title_sync_empty_text_returns_none():
    fake = _FakeLLM(reply="Суть")
    assert generate_title_sync("", client=fake) is None
    assert fake.calls == []


# ── generate_title (async wrapper) ──────────────────────────────────


@pytest.mark.asyncio
async def test_generate_title_returns_llm_title():
    with patch.object(titles_module, "generate_title_sync", return_value="Суть звонка"):
        assert await generate_title("текст") == "Суть звонка"


@pytest.mark.asyncio
async def test_generate_title_timeout_returns_none(monkeypatch):
    monkeypatch.setattr(titles_module, "TITLE_TIMEOUT_SECONDS", 0.05)

    def slow(_text):
        import time

        time.sleep(0.3)
        return "поздно"

    monkeypatch.setattr(titles_module, "generate_title_sync", slow)
    assert await generate_title("текст") is None


# ── wiring: POST /saved-transcriptions ──────────────────────────────


def _capture_db():
    db = AsyncMock()
    saved = []

    # db.add — синхронный метод AsyncSession, поэтому обычный MagicMock
    db.add = MagicMock(side_effect=lambda obj: saved.append(obj))
    db.refresh = AsyncMock(side_effect=lambda obj: None)
    return db, saved


@pytest.mark.asyncio
async def test_create_without_title_uses_llm(monkeypatch):
    from fastapi.testclient import TestClient

    from gigaam_transcriber.database import get_db
    from routers.saved_transcriptions import router
    from fastapi import FastAPI

    db, saved = _capture_db()
    llm = AsyncMock(return_value="Согласование плана релиза")
    monkeypatch.setattr("routers.saved_transcriptions.generate_title", llm)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    setup_auth_override(app)
    with TestClient(app) as client:
        resp = client.post(
            "/api/saved-transcriptions",
            json={"full_text": "Иван: когда релиз? Мария: в декабре.", "duration": 61.0},
        )
    assert resp.status_code == 201
    assert resp.json()["title"] == "Согласование плана релиза"
    llm.assert_awaited_once()
    assert saved and saved[0].title == "Согласование плана релиза"


@pytest.mark.asyncio
async def test_create_with_explicit_title_skips_llm(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gigaam_transcriber.database import get_db
    from routers.saved_transcriptions import router

    db, saved = _capture_db()
    llm = AsyncMock(return_value="не должно использоваться")
    monkeypatch.setattr("routers.saved_transcriptions.generate_title", llm)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    setup_auth_override(app)
    with TestClient(app) as client:
        resp = client.post(
            "/api/saved-transcriptions",
            json={
                "full_text": "текст",
                "duration": 10.0,
                "title": "  Мой заголовок  ",
            },
        )
    assert resp.status_code == 201
    assert resp.json()["title"] == "Мой заголовок"
    assert llm.await_count == 0


@pytest.mark.asyncio
async def test_create_llm_failure_falls_back_to_default(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gigaam_transcriber.database import get_db
    from routers.saved_transcriptions import router

    db, saved = _capture_db()
    llm = AsyncMock(return_value=None)
    monkeypatch.setattr("routers.saved_transcriptions.generate_title", llm)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    setup_auth_override(app)
    with TestClient(app) as client:
        resp = client.post(
            "/api/saved-transcriptions",
            json={"full_text": "текст", "duration": 65.0},
        )
    assert resp.status_code == 201
    # default: "dd.mm.yyyy hh:mm — 1m 5s"
    assert resp.json()["title"].endswith("— 1m 5s")


# ── wiring: server-side auto-persist ────────────────────────────────


@pytest.mark.asyncio
async def test_persist_uses_llm_title(monkeypatch):
    from routers._persistence import persist_transcription_result

    db, saved = _capture_db()
    llm = AsyncMock(return_value="Договорились о новом поставщике")
    monkeypatch.setattr("routers._persistence.generate_title", llm)

    result = {
        "text": "Оператор: звонок записывается. …",
        "duration": 120.0,
        "segments": [],
    }
    await persist_transcription_result(db, "user-1", result, origin="upload",
                                       filename="zvonok.mp3")
    llm.assert_awaited_once()
    assert saved and saved[0].title == "Договорились о новом поставщике"


@pytest.mark.asyncio
async def test_persist_llm_failure_falls_back(monkeypatch):
    from routers._persistence import persist_transcription_result

    db, saved = _capture_db()
    llm = AsyncMock(return_value=None)
    monkeypatch.setattr("routers._persistence.generate_title", llm)

    result = {"text": "текст", "duration": 30.0, "segments": []}
    await persist_transcription_result(db, "user-1", result, origin="upload",
                                       filename="rec.mp3")
    assert saved
    # fallback keeps the filename stem for recognisability
    assert "rec" in saved[0].title
