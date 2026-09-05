"""Refresh-session rotation, replay chain-kill and revocation triggers (Task 7).

Runs against the real app + real SQLite database (share_lifecycle pattern);
only the transcriber and the reset email are mocked. Raw refresh tokens are
asserted to never appear in the sessions table.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.auth import create_refresh_token, decode_token, get_admin_user, hash_password
from gigaam_transcriber.database import Base, async_session_factory, engine
from gigaam_transcriber.models import RefreshSession, User
from gigaam_transcriber.sessions import hash_jti
from sqlalchemy import select

PASSWORD = "Passw0rd!123"


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


def _seed_user(is_active: bool = True, approved: bool = True) -> User:
    uid = uuid.uuid4().hex
    user = User(
        id=uid,
        email=f"{uid}@sessions-test.local",
        username=uid[:30],
        password_hash=hash_password(PASSWORD),
        role="user",
        is_active=is_active,
        approved_at=datetime.utcnow() if approved else None,
    )

    async def run():
        async with async_session_factory() as db:
            db.add(user)
            await db.commit()

    asyncio.run(run())
    return user


def _api_login(client: TestClient, user: User) -> dict:
    resp = client.post("/api/auth/login", json={"login": user.email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"refresh_token": resp.cookies["refresh_token"]}


def _refresh(client: TestClient, refresh_token: str, csrf: str | None = None):
    cookies = {"refresh_token": refresh_token}
    headers = {}
    if csrf is not None:
        cookies["csrf_token"] = csrf
        headers["X-CSRF-Token"] = csrf
    return client.post("/api/auth/refresh", cookies=cookies, headers=headers)


def _sessions_for(user_id: str) -> list[RefreshSession]:
    async def run():
        async with async_session_factory() as db:
            result = await db.execute(
                select(RefreshSession)
                .where(RefreshSession.user_id == user_id)
                .order_by(RefreshSession.created_at)
            )
            return result.scalars().all()

    return asyncio.run(run())


def _set_user_active(user_id: str, is_active: bool) -> None:
    async def run():
        async with async_session_factory() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one()
            user.is_active = is_active
            await db.commit()

    asyncio.run(run())


class TestRotation:
    def test_login_persists_hashed_jti_only(self, client):
        user = _seed_user()
        cookies = _api_login(client, user)

        sessions = _sessions_for(user.id)
        assert len(sessions) == 1
        row = sessions[0]
        jti = decode_token(cookies["refresh_token"])["jti"]
        assert row.hashed_jti == hash_jti(jti)
        assert row.revoked_at is None
        assert row.rotated_to_hashed_jti is None
        raw = cookies["refresh_token"]
        assert raw not in (row.hashed_jti, row.user_agent, row.ip or "")

    def test_rotation_is_single_use_and_replay_kills_chain(self, client):
        user = _seed_user()
        first = _api_login(client, user)["refresh_token"]

        rotated = _refresh(client, first)
        assert rotated.status_code == 200, rotated.text
        second = rotated.cookies["refresh_token"]
        assert second != first

        sessions = _sessions_for(user.id)
        assert len(sessions) == 2
        old_row = next(r for r in sessions if r.hashed_jti == hash_jti(decode_token(first)["jti"]))
        new_row = next(r for r in sessions if r.hashed_jti == hash_jti(decode_token(second)["jti"]))
        assert old_row.revoked_at is not None
        assert old_row.rotated_to_hashed_jti == new_row.hashed_jti
        assert new_row.revoked_at is None

        replay = _refresh(client, first)
        assert replay.status_code == 401

        successor_after_replay = _refresh(client, second)
        assert successor_after_replay.status_code == 401

    def test_deep_chain_killed_by_root_replay(self, client):
        user = _seed_user()
        root = _api_login(client, user)["refresh_token"]

        second = _refresh(client, root).cookies["refresh_token"]
        third = _refresh(client, second).cookies["refresh_token"]

        assert _refresh(client, root).status_code == 401
        assert _refresh(client, third).status_code == 401

        rows = _sessions_for(user.id)
        assert len(rows) == 3
        assert all(r.revoked_at is not None for r in rows)

    def test_expired_session_rejected(self, client):
        user = _seed_user()
        jti = str(uuid.uuid4())
        token = create_refresh_token(user.id, jti)

        async def run():
            async with async_session_factory() as db:
                db.add(
                    RefreshSession(
                        id=uuid.uuid4().hex,
                        hashed_jti=hash_jti(jti),
                        user_id=user.id,
                        expires_at=datetime.utcnow() - timedelta(minutes=1),
                    )
                )
                await db.commit()

        asyncio.run(run())
        assert _refresh(client, token).status_code == 401

    def test_token_without_db_row_rejected(self, client):
        user = _seed_user()
        legacy_token = create_refresh_token(user.id)
        assert _refresh(client, legacy_token).status_code == 401

    def test_refresh_inactive_user_rejected(self, client):
        user = _seed_user()
        token = _api_login(client, user)["refresh_token"]
        _set_user_active(user.id, False)
        assert _refresh(client, token).status_code == 401


class TestRevocationTriggers:
    def test_logout_revokes_current_session(self, client):
        user = _seed_user()
        token = _api_login(client, user)["refresh_token"]

        resp = client.post("/api/auth/logout", cookies={"refresh_token": token})
        assert resp.status_code == 200

        assert _refresh(client, token).status_code == 401
        row = _sessions_for(user.id)[0]
        assert row.revoked_at is not None

    def test_password_reset_revokes_all_sessions(self, client):
        user = _seed_user()
        token_a = _api_login(client, user)["refresh_token"]
        token_b = _api_login(client, user)["refresh_token"]
        assert len(_sessions_for(user.id)) == 2

        captured: dict[str, str] = {}

        async def fake_send(email, token, frontend_url):
            captured["token"] = token

        with patch("routers.auth.send_password_reset_email", new=fake_send):
            resp = client.post("/api/auth/forgot-password", json={"email": user.email})
        assert resp.status_code == 200
        assert captured["token"]

        resp = client.post(
            "/api/auth/reset-password",
            json={"token": captured["token"], "new_password": "NewPassw0rd!456"},
        )
        assert resp.status_code == 200, resp.text

        assert _refresh(client, token_a).status_code == 401
        assert _refresh(client, token_b).status_code == 401
        assert all(r.revoked_at is not None for r in _sessions_for(user.id))

    def test_admin_disable_revokes_all_sessions(self, client):
        from api import app

        user = _seed_user()
        admin = _seed_user()
        admin.role = "admin"
        token = _api_login(client, user)["refresh_token"]

        app.dependency_overrides[get_admin_user] = lambda: admin
        try:
            resp = client.patch(f"/api/admin/users/{user.id}", json={"is_active": False})
            assert resp.status_code == 200, resp.text
        finally:
            app.dependency_overrides.pop(get_admin_user, None)

        assert _refresh(client, token).status_code == 401
        assert all(r.revoked_at is not None for r in _sessions_for(user.id))


class TestGenericResponses:
    def test_login_unknown_vs_wrong_password_identical(self, client):
        user = _seed_user()
        unknown = client.post(
            "/api/auth/login", json={"login": "ghost@nowhere.io", "password": PASSWORD}
        )
        wrong = client.post("/api/auth/login", json={"login": user.email, "password": "Wrong!1234"})
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()

    def test_forgot_existing_vs_missing_identical(self, client):
        user = _seed_user()
        with patch("routers.auth.send_password_reset_email", new=AsyncMock()):
            existing = client.post("/api/auth/forgot-password", json={"email": user.email})
        missing = client.post("/api/auth/forgot-password", json={"email": "ghost@nowhere.io"})
        assert existing.status_code == missing.status_code == 200
        assert existing.json() == missing.json()

    def test_login_pending_and_disabled_only_after_password_check(self, client):
        pending = _seed_user(is_active=False, approved=False)
        disabled = _seed_user(is_active=False, approved=True)

        wrong_pw = client.post(
            "/api/auth/login", json={"login": pending.email, "password": "Wrong!1234"}
        )
        assert wrong_pw.status_code == 401

        resp_pending = client.post(
            "/api/auth/login", json={"login": pending.email, "password": PASSWORD}
        )
        resp_disabled = client.post(
            "/api/auth/login", json={"login": disabled.email, "password": PASSWORD}
        )
        assert resp_pending.status_code == resp_disabled.status_code == 403
        assert resp_pending.json()["detail"]["reason"] == "pending_approval"
        assert resp_disabled.json()["detail"]["reason"] == "account_disabled"
