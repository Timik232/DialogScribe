"""Share lifecycle, payload caps and usage retention tests (Task 5).

Covers: identical 404 shape for unknown/revoked/expired links, owner-only
expiry/revoke controls, pre-migration share regression, payload cap
boundaries at save/update, and idempotent usage-event retention.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.auth import get_current_user
from gigaam_transcriber.database import Base, async_session_factory, engine
from gigaam_transcriber.models import SavedTranscription, UsageEvent, User
from gigaam_transcriber.usage import prune_usage_events
from routers.saved_transcriptions import MAX_SAVED_PAYLOAD_BYTES, _payload_size_bytes

EXPECTED_404_SHAPE = {"detail": "Shared transcription not found"}


@pytest.fixture(scope="module")
def client():
    mock_transcriber = MagicMock()
    with patch("api.GigaAMTranscriber", return_value=mock_transcriber):
        from api import app

        async def _create_tables():
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_create_tables())
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()


def _seed_user(role: str = "user") -> User:
    uid = uuid.uuid4().hex
    user = User(
        id=uid,
        email=f"{uid}@share-test.local",
        username=uid[:30],
        password_hash="x",
        role=role,
        is_active=True,
    )

    async def run():
        async with async_session_factory() as db:
            db.add(user)
            await db.commit()

    asyncio.run(run())
    return user


def _seed_transcription(
    user_id: str,
    share_id: str | None = None,
    share_expires_at: datetime | None = None,
    share_revoked_at: datetime | None = None,
    full_text: str = "Shared transcription body for lifecycle tests",
) -> str:
    tid = uuid.uuid4().hex

    async def run():
        async with async_session_factory() as db:
            db.add(
                SavedTranscription(
                    id=tid,
                    user_id=user_id,
                    title="Share test",
                    full_text=full_text,
                    duration=1.0,
                    share_id=share_id,
                    share_expires_at=share_expires_at,
                    share_revoked_at=share_revoked_at,
                )
            )
            await db.commit()

    asyncio.run(run())
    return tid


def _fetch_transcription(tid: str) -> SavedTranscription | None:

    async def run():
        async with async_session_factory() as db:
            result = await db.execute(
                __import__("sqlalchemy").select(SavedTranscription).where(
                    SavedTranscription.id == tid
                )
            )
            return result.scalar_one_or_none()

    return asyncio.run(run())


def _act_as(client, user: User | None) -> None:
    app = client.app
    if user is None:
        app.dependency_overrides.pop(get_current_user, None)
    else:
        app.dependency_overrides[get_current_user] = lambda: user


def _create_share(client, tid: str) -> str:
    resp = client.post(f"/api/saved-transcriptions/{tid}/share")
    assert resp.status_code == 200, resp.text
    return resp.json()["share_id"]


# ── Public access semantics ──────────────────────────────────────────


class TestPublicShareAccess:
    def test_valid_share_open_without_login(self, client):
        owner = _seed_user()
        tid = _seed_transcription(owner.id)
        _act_as(client, owner)
        share_id = _create_share(client, tid)

        _act_as(client, None)  # anonymous consumer
        resp = client.get(f"/api/share/{share_id}")
        assert resp.status_code == 200
        assert resp.json()["full_text"] == "Shared transcription body for lifecycle tests"

    def test_pre_migration_share_still_accessible(self, client):
        """Legacy row (share set, no expiry/revocation columns populated)."""
        owner = _seed_user()
        legacy_share_id = str(uuid.uuid4())
        _seed_transcription(
            owner.id,
            share_id=legacy_share_id,
            share_expires_at=None,
            share_revoked_at=None,
        )

        _act_as(client, None)
        resp = client.get(f"/api/share/{legacy_share_id}")
        assert resp.status_code == 200

    def test_unknown_revoked_expired_identical_404_shape(self, client):
        owner = _seed_user()
        revoked_share = str(uuid.uuid4())
        expired_share = str(uuid.uuid4())
        _seed_transcription(owner.id, share_id=revoked_share, share_revoked_at=datetime.utcnow())
        _seed_transcription(
            owner.id,
            share_id=expired_share,
            share_expires_at=datetime.utcnow() - timedelta(hours=1),
        )

        _act_as(client, None)
        unknown = client.get(f"/api/share/{uuid.uuid4()}")
        revoked = client.get(f"/api/share/{revoked_share}")
        expired = client.get(f"/api/share/{expired_share}")

        assert unknown.status_code == revoked.status_code == expired.status_code == 404
        assert unknown.json() == revoked.json() == expired.json() == EXPECTED_404_SHAPE


# ── Owner lifecycle controls ─────────────────────────────────────────


class TestShareLifecycleOwnership:
    def test_owner_revoke_then_generic_unavailable_then_unrevoke(self, client):
        owner = _seed_user()
        tid = _seed_transcription(owner.id)
        _act_as(client, owner)
        share_id = _create_share(client, tid)

        _act_as(client, None)
        assert client.get(f"/api/share/{share_id}").status_code == 200

        _act_as(client, owner)
        resp = client.post(f"/api/saved-transcriptions/{tid}/share/revoke")
        assert resp.status_code == 200
        assert resp.json()["share_revoked_at"] is not None

        _act_as(client, None)
        resp = client.get(f"/api/share/{share_id}")
        assert resp.status_code == 404
        assert resp.json() == EXPECTED_404_SHAPE

        _act_as(client, owner)
        resp = client.post(f"/api/saved-transcriptions/{tid}/share/unrevoke")
        assert resp.status_code == 200
        assert resp.json()["share_revoked_at"] is None

        _act_as(client, None)
        assert client.get(f"/api/share/{share_id}").status_code == 200

    def test_revoke_by_non_owner_404_no_state_change(self, client):
        owner = _seed_user()
        other = _seed_user()
        tid = _seed_transcription(owner.id)
        _act_as(client, owner)
        share_id = _create_share(client, tid)

        _act_as(client, other)
        resp = client.post(f"/api/saved-transcriptions/{tid}/share/revoke")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Transcription not found"}

        obj = _fetch_transcription(tid)
        assert obj.share_id == share_id
        assert obj.share_revoked_at is None
        assert obj.share_expires_at is None

    def test_expiry_via_iso_datetime(self, client):
        owner = _seed_user()
        tid = _seed_transcription(owner.id)
        _act_as(client, owner)
        share_id = _create_share(client, tid)

        expires_at = datetime.utcnow() + timedelta(minutes=30)
        resp = client.put(
            f"/api/saved-transcriptions/{tid}/share/expiry",
            json={"expires_at": expires_at.isoformat()},
        )
        assert resp.status_code == 200
        stored = datetime.fromisoformat(resp.json()["share_expires_at"])
        assert abs((stored - expires_at).total_seconds()) < 5

        _act_as(client, None)
        assert client.get(f"/api/share/{share_id}").status_code == 200

    def test_expiry_via_duration_and_clear(self, client):
        owner = _seed_user()
        tid = _seed_transcription(owner.id)
        _act_as(client, owner)
        _create_share(client, tid)

        resp = client.put(
            f"/api/saved-transcriptions/{tid}/share/expiry",
            json={"duration_seconds": 3600},
        )
        assert resp.status_code == 200
        stored = datetime.fromisoformat(resp.json()["share_expires_at"])
        assert timedelta(seconds=3590) < stored - datetime.utcnow() < timedelta(seconds=3610)

        resp = client.put(
            f"/api/saved-transcriptions/{tid}/share/expiry", json={}
        )
        assert resp.status_code == 200
        assert resp.json()["share_expires_at"] is None

    def test_expiry_rejections(self, client):
        owner = _seed_user()
        tid = _seed_transcription(owner.id)
        _act_as(client, owner)
        _create_share(client, tid)

        past = client.put(
            f"/api/saved-transcriptions/{tid}/share/expiry",
            json={"expires_at": (datetime.utcnow() - timedelta(hours=1)).isoformat()},
        )
        assert past.status_code == 422

        both = client.put(
            f"/api/saved-transcriptions/{tid}/share/expiry",
            json={"expires_at": (datetime.utcnow() + timedelta(hours=1)).isoformat(), "duration_seconds": 60},
        )
        assert both.status_code == 422

        bad_duration = client.put(
            f"/api/saved-transcriptions/{tid}/share/expiry",
            json={"duration_seconds": 0},
        )
        assert bad_duration.status_code == 422

    def test_expiry_on_unshared_transcription_404(self, client):
        owner = _seed_user()
        tid = _seed_transcription(owner.id)
        _act_as(client, owner)
        resp = client.put(
            f"/api/saved-transcriptions/{tid}/share/expiry",
            json={"duration_seconds": 60},
        )
        assert resp.status_code == 404

    def test_create_share_reactivates_revoked_link(self, client):
        owner = _seed_user()
        tid = _seed_transcription(owner.id)
        _act_as(client, owner)
        share_id = _create_share(client, tid)
        client.post(f"/api/saved-transcriptions/{tid}/share/revoke")

        resp = client.post(f"/api/saved-transcriptions/{tid}/share")
        assert resp.status_code == 200
        assert resp.json()["share_id"] == share_id
        assert resp.json()["share_revoked_at"] is None

    def test_delete_share_destroys_link_entirely(self, client):
        owner = _seed_user()
        tid = _seed_transcription(owner.id)
        _act_as(client, owner)
        share_id = _create_share(client, tid)

        resp = client.delete(f"/api/saved-transcriptions/{tid}/share")
        assert resp.status_code == 200

        _act_as(client, None)
        assert client.get(f"/api/share/{share_id}").json() == EXPECTED_404_SHAPE


# ── Payload caps ─────────────────────────────────────────────────────

CREATE_PAYLOAD_OVERHEAD = _payload_size_bytes(
    {"title": "cap", "full_text": "", "segments": None, "speaker_names": None}
)
UPDATE_PAYLOAD_OVERHEAD = _payload_size_bytes({"full_text": ""})


def _create_body_exact(target: int) -> dict:
    return {
        "title": "cap",
        "full_text": "x" * (target - CREATE_PAYLOAD_OVERHEAD),
        "duration": 60.0,
    }


class TestPayloadCaps:
    def test_create_at_limit_ok_one_byte_above_413(self, client):
        owner = _seed_user()
        _act_as(client, owner)

        ok = client.post("/api/saved-transcriptions", json=_create_body_exact(MAX_SAVED_PAYLOAD_BYTES))
        assert ok.status_code == 201, ok.text
        created_id = ok.json()["id"]
        obj = _fetch_transcription(created_id)
        assert obj is not None

        marker = uuid.uuid4().hex
        oversized = _create_body_exact(MAX_SAVED_PAYLOAD_BYTES + 1)
        oversized["title"] = f"cap{marker}"
        rejected = client.post("/api/saved-transcriptions", json=oversized)
        assert rejected.status_code == 413
        assert "too large" in rejected.json()["detail"].lower()

        async def count_rows():
            from sqlalchemy import func, select

            async with async_session_factory() as db:
                return (
                    await db.execute(
                        select(func.count()).select_from(SavedTranscription).where(
                            SavedTranscription.title == f"cap{marker}"
                        )
                    )
                ).scalar()

        assert asyncio.run(count_rows()) == 0

    def test_update_at_limit_ok_one_byte_above_413_no_change(self, client):
        owner = _seed_user()
        marker = uuid.uuid4().hex
        tid = _seed_transcription(owner.id, full_text=f"original {marker}")
        _act_as(client, owner)

        ok = client.put(
            f"/api/saved-transcriptions/{tid}",
            json={"full_text": "y" * (MAX_SAVED_PAYLOAD_BYTES - UPDATE_PAYLOAD_OVERHEAD)},
        )
        assert ok.status_code == 200

        rejected = client.put(
            f"/api/saved-transcriptions/{tid}",
            json={"full_text": "y" * (MAX_SAVED_PAYLOAD_BYTES + 1 - UPDATE_PAYLOAD_OVERHEAD)},
        )
        assert rejected.status_code == 413

        obj = _fetch_transcription(tid)
        assert obj.full_text.startswith("y" * 100)


# ── Usage retention ──────────────────────────────────────────────────


def _wipe_usage_events() -> None:
    async def run():
        async with async_session_factory() as db:
            await db.execute(UsageEvent.__table__.delete())
            await db.commit()

    asyncio.run(run())


def _seed_usage(user_id: str, event_type: str, created_at: datetime) -> str:
    eid = uuid.uuid4().hex

    async def run():
        async with async_session_factory() as db:
            db.add(UsageEvent(id=eid, user_id=user_id, event_type=event_type, created_at=created_at))
            await db.commit()

    asyncio.run(run())
    return eid


class TestUsageRetention:
    def test_prune_idempotent_and_scoped(self):
        _wipe_usage_events()
        user = _seed_user()
        now = datetime.utcnow()
        old_a = _seed_usage(user.id, "transcription", now - timedelta(days=40))
        old_b = _seed_usage(user.id, "llm_call", now - timedelta(days=35))
        recent = _seed_usage(user.id, "transcription", now - timedelta(days=5))

        async def run():
            async with async_session_factory() as db:
                first = await prune_usage_events(db, older_than_days=30, now=now)
                second = await prune_usage_events(db, older_than_days=30, now=now)
                remaining = (
                    await db.execute(UsageEvent.__table__.select())
                ).fetchall()
                users = (
                    await db.execute(User.__table__.select().where(User.id == user.id))
                ).fetchall()
                return first, second, remaining, users

        first, second, remaining, users = asyncio.run(run())

        assert first == 2
        assert second == 0
        assert [row.id for row in remaining] == [recent]
        assert len(users) == 1
        assert all(row.id not in (old_a, old_b) for row in remaining)

    def test_default_retention_uses_env_window(self):
        from gigaam_transcriber.usage import usage_retention_cutoff

        now = datetime(2026, 1, 1, 12, 0, 0)
        cutoff = usage_retention_cutoff(now=now)
        assert cutoff == now - timedelta(days=180)

        explicit = usage_retention_cutoff(older_than_days=7, now=now)
        assert explicit == now - timedelta(days=7)

    def test_admin_endpoint_prunes_and_non_admin_forbidden(self, client):
        _wipe_usage_events()
        admin = _seed_user(role="admin")
        user = _seed_user()
        now = datetime.utcnow()
        _seed_usage(user.id, "transcription", now - timedelta(days=100))
        _seed_usage(user.id, "llm_call", now - timedelta(days=3))

        _act_as(client, user)
        forbidden = client.delete("/api/admin/usage-events?older_than_days=30")
        assert forbidden.status_code == 403

        _act_as(client, admin)
        first = client.delete("/api/admin/usage-events?older_than_days=30")
        assert first.status_code == 200
        assert first.json() == {"deleted": 1, "older_than_days": 30}

        second = client.delete("/api/admin/usage-events?older_than_days=30")
        assert second.status_code == 200
        assert second.json()["deleted"] == 0

        _act_as(client, None)
