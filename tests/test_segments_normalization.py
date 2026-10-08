"""CQ-M8: saved-transcription segments normalize to ONE canonical list schema.

Legacy rows/requests may carry dict shapes; reads must always return a list.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from routers.saved_transcriptions import _normalize_segments
from tests.conftest import make_mock_user, setup_auth_override, clear_auth_override


# ── Pure normalization matrix (both legacy shapes + canonical) ────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        ([], []),
        ([{"text": "a", "start": 0.0, "end": 1.0}], [{"text": "a", "start": 0.0, "end": 1.0}]),
        ({}, []),
        ({"segments": [{"text": "a"}, {"text": "b"}]}, [{"text": "a"}, {"text": "b"}]),
        ({"0": {"text": "first"}, "2": {"text": "third"}, "1": {"text": "second"}},
         [{"text": "first"}, {"text": "second"}, {"text": "third"}]),
        ({"text": "single", "start": 0.0, "end": 2.0}, [{"text": "single", "start": 0.0, "end": 2.0}]),
        (({"text": "tuple-item"},), [{"text": "tuple-item"}]),
    ],
)
def test_normalize_segments_legacy_shapes(raw, expected):
    assert _normalize_segments(raw) == expected


def test_normalize_segments_is_copy_safe():
    legacy = {"0": {"text": "a"}}
    out = _normalize_segments(legacy)
    out.append({"text": "extra"})
    assert "extra" not in legacy["0"]


# ── Write path stores the canonical list (no [] -> {} coercion) ───────

def _mock_obj(**overrides):
    obj = MagicMock()
    obj.id = "st-1"
    obj.title = "T"
    obj.full_text = "text"
    obj.analysis_text = None
    obj.segments_json = []
    obj.speaker_names = {}
    obj.duration = 5.0
    obj.language = "ru"
    obj.share_id = None
    obj.share_expires_at = None
    obj.share_revoked_at = None
    obj.created_at = "2025-01-01T00:00:00"
    obj.updated_at = "2025-01-01T00:00:00"
    for k, v in overrides.items():
        setattr(obj, k, v)
    return obj


def test_save_empty_segments_stores_list_not_dict():
    from routers import saved_transcriptions as mod
    import api as api_mod

    app = api_mod.app
    setup_auth_override(app)

    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    captured = {}
    orig_cls = mod.SavedTranscription

    def factory(**kw):
        captured["stored"] = kw.get("segments_json")
        return _mock_obj(segments_json=kw.get("segments_json"))

    mod.SavedTranscription = factory
    app.dependency_overrides[mod.get_db] = lambda: (yield db)
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/saved-transcriptions",
                json={"full_text": "hello", "duration": 2.0, "segments": []},
            )
        assert resp.status_code == 201
        assert captured["stored"] == []
    finally:
        mod.SavedTranscription = orig_cls
        app.dependency_overrides.pop(mod.get_db, None)
        clear_auth_override(app)


def test_save_missing_segments_stores_list_not_dict():
    from routers import saved_transcriptions as mod
    import api as api_mod

    app = api_mod.app
    setup_auth_override(app)

    db = AsyncMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    captured = {}
    orig_cls = mod.SavedTranscription

    def factory(**kw):
        captured["stored"] = kw.get("segments_json")
        return _mock_obj(segments_json=kw.get("segments_json"))

    mod.SavedTranscription = factory
    app.dependency_overrides[mod.get_db] = lambda: (yield db)
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/saved-transcriptions",
                json={"full_text": "hello", "duration": 2.0},
            )
        assert resp.status_code == 201
        assert captured["stored"] == []
    finally:
        mod.SavedTranscription = orig_cls
        app.dependency_overrides.pop(mod.get_db, None)
        clear_auth_override(app)


# ── Read path: legacy DB rows come back as lists ─────────────────────

@pytest.mark.parametrize(
    "stored",
    [
        {},
        {"segments": [{"text": "a"}]},
        {"0": {"text": "first"}, "1": {"text": "second"}},
    ],
)
def test_get_transcription_normalizes_legacy_rows(stored):
    from routers import saved_transcriptions as mod
    import api as api_mod

    app = api_mod.app
    setup_auth_override(app)

    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = _mock_obj(segments_json=stored)
    db.execute = AsyncMock(return_value=result)

    app.dependency_overrides[mod.get_db] = lambda: (yield db)
    try:
        with TestClient(app) as client:
            resp = client.get("/api/saved-transcriptions/st-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["segments_json"] == _normalize_segments(stored)
        assert isinstance(body["segments_json"], list)
    finally:
        app.dependency_overrides.pop(mod.get_db, None)
        clear_auth_override(app)


def test_public_share_normalizes_legacy_rows():
    from routers import saved_transcriptions as mod
    import api as api_mod

    app = api_mod.app

    legacy = {"0": {"text": "first"}, "1": {"text": "second"}}
    db = AsyncMock()
    result = MagicMock()
    obj = _mock_obj(segments_json=legacy, share_id="share-1")
    result.scalar_one_or_none.return_value = obj
    db.execute = AsyncMock(return_value=result)

    app.dependency_overrides[mod.get_db] = lambda: (yield db)
    try:
        with TestClient(app) as client:
            resp = client.get("/api/share/share-1")
        assert resp.status_code == 200
        assert resp.json()["segments_json"] == [{"text": "first"}, {"text": "second"}]
    finally:
        app.dependency_overrides.pop(mod.get_db, None)
        clear_auth_override(app)
