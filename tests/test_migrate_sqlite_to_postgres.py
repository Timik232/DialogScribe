import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from gigaam_transcriber.database import Base
from gigaam_transcriber.models import *  # noqa: F401,F403

from tools.migrate_sqlite_to_postgres import (
    CHUNK_SIZE_OVERRIDES,
    DEFAULT_CHUNK_SIZE,
    chunk_ranges,
    chunk_size_for,
    coerce_row,
    get_table_counts_sync,
    main,
    plan_copy_tables,
    reflect_source,
    truncate_statement,
)

MEETING_PREP_DDL = """
CREATE TABLE meeting_prep_plans (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_data TEXT NOT NULL,
    catalog_data TEXT NOT NULL,
    result_markdown TEXT NOT NULL,
    model_used VARCHAR(100) NOT NULL,
    created_at DATETIME NOT NULL
)
"""

USERS_SQL = [
    (
        "u1",
        "ada@example.io",
        "ada",
        "hash1",
        "user",
        1,
        "2026-01-01T10:00:00",
        "2026-01-02T10:00:00",
    ),
    (
        "u2",
        "bob@example.io",
        "bob",
        "hash2",
        "admin",
        0,
        "2026-01-03T11:00:00",
        "2026-01-04T11:00:00",
    ),
]


def _insert_user(conn, row):
    conn.execute(
        text(
            "INSERT INTO users (id, email, username, password_hash, role, is_active, created_at, updated_at)"
            " VALUES (:id, :email, :username, :password_hash, :role, :is_active, :created_at, :updated_at)"
        ),
        dict(
            id=row[0],
            email=row[1],
            username=row[2],
            password_hash=row[3],
            role=row[4],
            is_active=row[5],
            created_at=row[6],
            updated_at=row[7],
        ),
    )


def make_source(path):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(MEETING_PREP_DDL))
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('012_user_settings_fk')"))
        for row in USERS_SQL:
            _insert_user(conn, row)
        conn.execute(
            text(
                "INSERT INTO user_settings (id, user_id, asr_provider, created_at, updated_at)"
                " VALUES ('s1', 'u1', 'litellm', '2026-01-01T12:00:00', '2026-01-01T12:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO templates (id, user_id, key, label, system_prompt, created_at, updated_at)"
                " VALUES ('t1', 'u1', 'sum', 'Summary', 'You summarise.', '2026-01-05T09:00:00', '2026-01-05T09:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO usage_events (id, user_id, event_type, value, metadata, created_at)"
                " VALUES ('e1', 'u1', 'transcribe', 1.5, '{\"audio_mb\": 12}', '2026-01-06T08:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO saved_transcriptions (id, user_id, title, full_text, segments_json, speaker_names,"
                " duration, language, created_at, updated_at)"
                " VALUES ('st1', 'u1', 'Meeting', 'full text here', '[{\"speaker\": \"S1\", \"text\": \"hi\"}]',"
                " '{\"S1\": \"Ada\"}', 12.5, 'ru', '2026-01-07T07:00:00', '2026-01-07T07:30:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO meeting_prep_plans VALUES ('mp1', 'u1', 'company', 'catalog', 'result', 'gpt-4.1', '2026-01-08T06:00:00')"
            )
        )
    return engine


def make_dest(path, with_meeting_prep=True, with_alembic=True):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        if with_meeting_prep:
            conn.execute(text(MEETING_PREP_DDL))
        if with_alembic:
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('012_user_settings_fk')"))
    return engine


@pytest.fixture()
def src_engine(tmp_path):
    engine = make_source(tmp_path / "src.db")
    yield engine
    engine.dispose()


@pytest.fixture()
def dest_engine(tmp_path):
    engine = make_dest(tmp_path / "dest.db")
    yield engine
    engine.dispose()


@pytest.fixture()
def src_url(tmp_path):
    return f"sqlite:///{tmp_path / 'src.db'}"


@pytest.fixture()
def dest_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path / 'dest.db'}"


def plan_for(engine):
    reflected, source_names = reflect_source(engine)
    return plan_copy_tables(Base.metadata, reflected, source_names)


class TestPlanOrder:
    def test_users_first_then_fk_dependents(self, src_engine):
        names = [t.name for t in plan_for(src_engine)]
        assert names[0] == "users"
        for dependent in ("refresh_sessions", "saved_transcriptions", "templates", "usage_events", "user_limits", "user_settings"):
            assert names.index("users") < names.index(dependent)

    def test_source_only_tables_appended_after_users(self, src_engine):
        names = [t.name for t in plan_for(src_engine)]
        assert "meeting_prep_plans" in names
        assert names.index("users") < names.index("meeting_prep_plans")
        assert names[-1] == "meeting_prep_plans"

    def test_alembic_version_and_sqlite_internals_excluded(self, src_engine):
        names = [t.name for t in plan_for(src_engine)]
        assert "alembic_version" not in names

    def test_extra_tables_get_generic_columns(self, src_engine):
        extra = next(t for t in plan_for(src_engine) if t.name == "meeting_prep_plans")
        assert [c.name for c in extra.columns] == [
            "id",
            "user_id",
            "company_data",
            "catalog_data",
            "result_markdown",
            "model_used",
            "created_at",
        ]
        assert [c.name for c in extra.primary_key.columns] == ["id"]

    def test_no_extras_when_source_has_only_orm_tables(self, tmp_path):
        engine = create_engine(f"sqlite:///{tmp_path / 'lean.db'}")
        Base.metadata.create_all(engine)
        try:
            names = [t.name for t in plan_for(engine)]
            assert names == [t.name for t in Base.metadata.sorted_tables]
        finally:
            engine.dispose()


class TestChunks:
    def test_empty(self):
        assert chunk_ranges(0, 200) == []

    def test_single_partial_chunk(self):
        assert chunk_ranges(5, 200) == [(0, 5)]

    def test_exact_multiple(self):
        assert chunk_ranges(400, 200) == [(0, 200), (200, 200)]

    def test_remainder_chunk(self):
        assert chunk_ranges(450, 200) == [(0, 200), (200, 200), (400, 50)]

    def test_size_overrides(self):
        assert CHUNK_SIZE_OVERRIDES == {"saved_transcriptions": 20}
        assert chunk_size_for("saved_transcriptions") == 20
        assert chunk_size_for("users") == DEFAULT_CHUNK_SIZE == 200


class TestCoerceRow:
    def test_iso_string_datetimes_become_datetime(self):
        users = Base.metadata.tables["users"]
        row = coerce_row(
            users,
            {
                "id": "u1",
                "email": "a@a.io",
                "username": "ada",
                "password_hash": "h",
                "role": "user",
                "is_active": 1,
                "approved_by": None,
                "approved_at": "2026-01-01T09:00:00",
                "reset_token_hash": None,
                "reset_token_expires": None,
                "created_at": "2026-01-01T10:00:00",
                "updated_at": "2026-01-02T10:00:00",
            },
        )
        assert row["created_at"] == datetime(2026, 1, 1, 10, 0, 0)
        assert row["approved_at"] == datetime(2026, 1, 1, 9, 0, 0)
        assert row["reset_token_expires"] is None
        assert row["id"] == "u1"

    def test_datetime_objects_pass_through(self):
        users = Base.metadata.tables["users"]
        dt = datetime(2026, 2, 3, 4, 5, 6)
        row = coerce_row(users, {"id": "u1", "created_at": dt, "updated_at": dt, "is_active": True})
        assert row["created_at"] is dt
        assert row["updated_at"] is dt

    def test_int_bools_become_bool(self):
        limits = Base.metadata.tables["user_limits"]
        base = {
            "id": "l1",
            "user_id": "u1",
            "limit_type": "hours",
            "max_value": 10.0,
            "period": "monthly",
            "enabled": 0,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        assert coerce_row(limits, base)["enabled"] is False
        assert coerce_row(limits, {**base, "enabled": 1})["enabled"] is True
        assert coerce_row(limits, {**base, "enabled": None})["enabled"] is None

    def test_json_strings_become_parsed_objects(self):
        saved = Base.metadata.tables["saved_transcriptions"]
        row = coerce_row(saved, {"id": "st1", "segments_json": "[{\"s\": \"S1\"}]", "speaker_names": "{\"S1\": \"Ada\"}"})
        assert row["segments_json"] == [{"s": "S1"}]
        assert row["speaker_names"] == {"S1": "Ada"}

    def test_json_objects_pass_through(self):
        usage = Base.metadata.tables["usage_events"]
        row = coerce_row(usage, {"id": "e1", "metadata": {"audio_mb": 12}})
        assert row["metadata"] == {"audio_mb": 12}
        assert coerce_row(usage, {"id": "e1", "metadata": None})["metadata"] is None

    def test_text_and_floats_untouched(self):
        saved = Base.metadata.tables["saved_transcriptions"]
        row = coerce_row(saved, {"id": "st1", "full_text": "raw text", "duration": 12.5})
        assert row["full_text"] == "raw text"
        assert row["duration"] == 12.5


class TestSyncCounting:
    def test_counts_match_fixture(self, src_engine):
        plan = plan_for(src_engine)
        counts = get_table_counts_sync(src_engine, plan)
        assert counts["users"] == 2
        assert counts["user_settings"] == 1
        assert counts["templates"] == 1
        assert counts["usage_events"] == 1
        assert counts["saved_transcriptions"] == 1
        assert counts["meeting_prep_plans"] == 1
        assert counts["user_limits"] == 0
        assert counts["refresh_sessions"] == 0


class TestTruncateStatement:
    def test_single_cascade_statement_with_all_tables(self, src_engine):
        names = [t.name for t in plan_for(src_engine)]
        stmt = truncate_statement(names)
        assert stmt.startswith("TRUNCATE TABLE ")
        assert stmt.endswith(" CASCADE")
        body = stmt[len("TRUNCATE TABLE ") : -len(" CASCADE")]
        assert body.split(", ") == [f'"{n}"' for n in names]


class TestMain:
    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self, src_engine, dest_engine, src_url, dest_url, capsys):
        rc = await main(["--source", src_url, "--dest", dest_url])
        assert rc == 0
        out = capsys.readouterr().out
        assert "dry run" in out
        assert "saved_transcriptions" in out
        assert "meeting_prep_plans" in out
        assert "alembic" in out
        with dest_engine.connect() as conn:
            for table in ("users", "saved_transcriptions", "meeting_prep_plans"):
                assert conn.scalar(text(f"SELECT COUNT(*) FROM {table}")) == 0

    @pytest.mark.asyncio
    async def test_aborts_when_dest_missing_table(self, tmp_path, src_engine, src_url, capsys):
        dest = make_dest(tmp_path / "partial.db", with_meeting_prep=False)
        try:
            rc = await main(["--source", src_url, "--dest", f"sqlite+aiosqlite:///{tmp_path / 'partial.db'}", "--yes"])
            assert rc == 2
            assert "meeting_prep_plans" in capsys.readouterr().out
        finally:
            dest.dispose()

    @pytest.mark.asyncio
    async def test_refuses_non_empty_dest_without_truncate(self, src_engine, dest_engine, src_url, dest_url, capsys):
        with dest_engine.begin() as conn:
            _insert_user(conn, ("zz", "zz@example.io", "zz", "h", "user", 1, "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
        rc = await main(["--source", src_url, "--dest", dest_url, "--yes"])
        assert rc == 2
        out = capsys.readouterr().out
        assert "not empty" in out
        assert "users=1" in out
        with dest_engine.connect() as conn:
            assert conn.scalar(text("SELECT COUNT(*) FROM users")) == 1

    @pytest.mark.asyncio
    async def test_copy_preserves_rows_ids_timestamps_and_json(self, src_engine, dest_engine, src_url, dest_url, capsys):
        rc = await main(["--source", src_url, "--dest", dest_url, "--yes"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "[users] src=2 dest_before=0 copied=2 dest_after=2" in out
        assert "[verify] users: PASS" in out
        assert "verification PASSED" in out

        with dest_engine.connect() as conn:
            user = conn.execute(
                select(Base.metadata.tables["users"]).where(Base.metadata.tables["users"].c.id == "u1")
            ).mappings().one()
            assert user["email"] == "ada@example.io"
            assert user["is_active"] in (True, 1)
            assert user["created_at"] == datetime(2026, 1, 1, 10, 0, 0)

            saved = conn.execute(select(Base.metadata.tables["saved_transcriptions"])).mappings().one()
            assert saved["id"] == "st1"
            assert saved["full_text"] == "full text here"
            assert saved["duration"] == 12.5
            assert (saved["segments_json"] == [{"speaker": "S1", "text": "hi"}]) or (
                saved["segments_json"] == json.dumps([{"speaker": "S1", "text": "hi"}])
            )

            usage = conn.execute(select(Base.metadata.tables["usage_events"])).mappings().one()
            assert usage["metadata"] == {"audio_mb": 12} or usage["metadata"] == '{"audio_mb": 12}'

            prep = conn.execute(text("SELECT id, user_id, created_at FROM meeting_prep_plans")).mappings().one()
            assert prep["id"] == "mp1"
            assert prep["user_id"] == "u1"

            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "012_user_settings_fk"

    @pytest.mark.asyncio
    async def test_paginates_large_tables_in_chunks(self, src_engine, dest_engine, src_url, dest_url, capsys):
        with src_engine.begin() as conn:
            for i in range(250):
                conn.execute(
                    text(
                        "INSERT INTO templates (id, user_id, key, label, system_prompt, created_at, updated_at)"
                        " VALUES (:id, 'u1', :key, 'L', 'P', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
                    ),
                    {"id": f"bulk-{i:03d}", "key": f"k{i}"},
                )
        rc = await main(["--source", src_url, "--dest", dest_url, "--yes"])
        assert rc == 0
        assert "[templates] src=251" in capsys.readouterr().out
        with dest_engine.connect() as conn:
            assert conn.scalar(text("SELECT COUNT(*) FROM templates")) == 251
            assert conn.scalar(text("SELECT COUNT(*) FROM templates WHERE id LIKE 'bulk-%'")) == 250

    @pytest.mark.asyncio
    async def test_truncate_wipes_dest_before_copy(self, src_engine, dest_engine, src_url, dest_url, capsys):
        with dest_engine.begin() as conn:
            _insert_user(conn, ("zz", "zz@example.io", "zz", "h", "user", 1, "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
        rc = await main(["--source", src_url, "--dest", dest_url, "--yes", "--truncate"])
        assert rc == 0
        with dest_engine.connect() as conn:
            ids = conn.execute(text("SELECT id FROM users ORDER BY id")).scalars().all()
            assert ids == ["u1", "u2"]

    @pytest.mark.asyncio
    async def test_async_dest_engine_url_accepted(self, src_engine, dest_engine, tmp_path):
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dest.db'}")
        try:
            from tools.migrate_sqlite_to_postgres import get_table_counts

            plan = plan_for(src_engine)
            counts = await get_table_counts(engine, plan)
            assert counts["users"] == 0
            assert counts["meeting_prep_plans"] == 0
        finally:
            await engine.dispose()
