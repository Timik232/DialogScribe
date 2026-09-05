"""One-shot data migration: SQLite -> PostgreSQL for DialogScribe.

The dest schema must already exist at alembic head (run `alembic upgrade head`
with DATABASE_URL pointing at Postgres before this script). Copies every row
of every user table, preserving PK ids, timestamps and all column values
verbatim. alembic_version is compared but never copied.

Usage:
    python tools/migrate_sqlite_to_postgres.py \
        --source sqlite:///data/dialogscribe.db \
        --dest postgresql+asyncpg://user:pass@host:5432/dbname \
        [--truncate] [--yes]

Without --yes the script prints a dry-run plan and exits 0.
Exit codes: 0 = ok / dry run, 1 = verification failed, 2 = aborted before copy.
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.sql.ddl import sort_tables

from gigaam_transcriber.database import Base
from gigaam_transcriber.models import *  # noqa: F401,F403

DEFAULT_CHUNK_SIZE = 200
# saved_transcriptions rows carry multi-MB TEXT payloads -> smaller chunks
CHUNK_SIZE_OVERRIDES = {"saved_transcriptions": 20}
SKIP_TABLES = {"alembic_version"}  # compared at the end, never copied
SQLITE_INTERNAL_PREFIX = "sqlite_"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Copy all DialogScribe data from SQLite to PostgreSQL (dest schema must already be at alembic head)."
    )
    parser.add_argument("--source", required=True, help="source sync SQLAlchemy URL, e.g. sqlite:///data/dialogscribe.db")
    parser.add_argument("--dest", required=True, help="dest async SQLAlchemy URL, e.g. postgresql+asyncpg://user:pass@host:5432/dbname")
    parser.add_argument("--truncate", action="store_true", help="TRUNCATE all dest tables before copying")
    parser.add_argument("--yes", action="store_true", help="actually write data (default: dry run)")
    return parser.parse_args(argv)


def is_internal_table(name):
    return name in SKIP_TABLES or name.startswith(SQLITE_INTERNAL_PREFIX)


def chunk_size_for(table_name):
    return CHUNK_SIZE_OVERRIDES.get(table_name, DEFAULT_CHUNK_SIZE)


def chunk_ranges(total, size):
    return [(offset, min(size, total - offset)) for offset in range(0, total, size)]


def _generic_type(coltype):
    # sqlite-reflected types can carry dialect-specific bind processors
    # (e.g. sqlite DATETIME storage format) that break asyncpg inserts;
    # rebuild every extra table with dialect-neutral generic types.
    if isinstance(coltype, Boolean):
        return Boolean()
    if isinstance(coltype, DateTime):
        return DateTime()
    if isinstance(coltype, JSON):
        return JSON()
    if isinstance(coltype, Float):
        return Float()
    if isinstance(coltype, Numeric):
        return Numeric()
    if isinstance(coltype, LargeBinary):
        return LargeBinary()
    if isinstance(coltype, Integer):
        return Integer()
    if isinstance(coltype, String) and coltype.length is not None:
        return String(coltype.length)
    return Text()


def _genericize_table(reflected_table):
    out = Table(reflected_table.name, MetaData())
    for col in reflected_table.columns:
        out.append_column(Column(col.name, _generic_type(col.type), primary_key=col.primary_key, nullable=col.nullable))
    return out


def plan_copy_tables(metadata, reflected, source_names):
    # ORM tables in Base.metadata.sorted_tables order (FK-safe), then source-only
    # tables (no ORM mapping, e.g. meeting_prep_plans) topologically sorted by
    # their reflected FKs and rebuilt with generic column types.
    plan = [t for t in metadata.sorted_tables if t.name in source_names and not is_internal_table(t.name)]
    extras = [
        reflected.tables[name]
        for name in source_names
        if not is_internal_table(name) and name not in metadata.tables
    ]
    plan.extend(_genericize_table(t) for t in sort_tables(extras))
    return plan


def _coerce_value(coltype, value):
    if value is None:
        return None
    if isinstance(coltype, Boolean):
        return bool(value)
    if isinstance(coltype, DateTime):
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    if isinstance(coltype, JSON):
        return json.loads(value) if isinstance(value, (str, bytes)) else value
    return value


def coerce_row(table, row):
    return {col.name: _coerce_value(col.type, row.get(col.name)) for col in table.columns}


def truncate_statement(table_names):
    return "TRUNCATE TABLE " + ", ".join(f'"{name}"' for name in table_names) + " CASCADE"


def _pk_column(table):
    cols = list(table.primary_key.columns)
    if len(cols) != 1:
        raise SystemExit(f"table {table.name}: only single-column primary keys are supported")
    return cols[0]


def reflect_source(engine):
    reflected = MetaData()
    reflected.reflect(bind=engine)
    return reflected, set(inspect(engine).get_table_names())


def get_table_counts_sync(engine, tables):
    with engine.connect() as conn:
        return {t.name: conn.scalar(select(func.count()).select_from(t)) for t in tables}


def get_table_stats_sync(engine, tables):
    with engine.connect() as conn:
        stats = {}
        for t in tables:
            pk = _pk_column(t)
            count, lo, hi = conn.execute(select(func.count(), func.min(pk), func.max(pk)).select_from(t)).one()
            stats[t.name] = (int(count), lo, hi)
        return stats


def fetch_batch(engine, table, pk, last_value, limit):
    q = select(table).order_by(pk.asc())
    if last_value is not None:
        q = q.where(pk > last_value)
    with engine.connect() as conn:
        return [dict(m) for m in conn.execute(q.limit(limit)).mappings()]


def get_alembic_version_sync(engine):
    if "alembic_version" not in set(inspect(engine).get_table_names()):
        return None
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


async def get_table_counts(engine, tables):
    async with engine.connect() as conn:
        return {t.name: await conn.scalar(select(func.count()).select_from(t)) for t in tables}


async def get_table_stats(engine, tables):
    async with engine.connect() as conn:
        stats = {}
        for t in tables:
            pk = _pk_column(t)
            count, lo, hi = (await conn.execute(select(func.count(), func.min(pk), func.max(pk)).select_from(t))).one()
            stats[t.name] = (int(count), lo, hi)
        return stats


async def dest_missing_tables(engine, tables):
    async with engine.connect() as conn:
        names = await conn.run_sync(lambda conn_: set(inspect(conn_).get_table_names()))
    return [t.name for t in tables if t.name not in names]


async def get_alembic_version(engine):
    async with engine.connect() as conn:

        def _query(conn_):
            if "alembic_version" not in set(inspect(conn_).get_table_names()):
                return None
            return conn_.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()

        return await conn.run_sync(_query)


async def truncate_dest(engine, tables):
    if engine.dialect.name == "postgresql":
        async with engine.begin() as conn:
            await conn.execute(text(truncate_statement([t.name for t in tables])))
    else:
        async with engine.begin() as conn:
            for t in reversed(tables):
                await conn.execute(t.delete())


async def copy_table(sync_engine, dest_engine, table):
    pk = _pk_column(table)
    chunk = chunk_size_for(table.name)
    last = None
    copied = 0
    async with dest_engine.begin() as conn:
        while True:
            rows = await asyncio.to_thread(fetch_batch, sync_engine, table, pk, last, chunk)
            if not rows:
                break
            await conn.execute(table.insert(), [coerce_row(table, row) for row in rows])
            copied += len(rows)
            last = rows[-1][pk.name]
    return copied


async def verify(sync_engine, dest_engine, tables):
    src = await asyncio.to_thread(get_table_stats_sync, sync_engine, tables)
    dst = await get_table_stats(dest_engine, tables)
    all_ok = True
    for t in tables:
        ok = src[t.name] == dst[t.name]
        all_ok = all_ok and ok
        print(f"[verify] {t.name}: {'PASS' if ok else 'FAIL'} src(count,min,max)={src[t.name]} dest={dst[t.name]}")
    return all_ok


async def main(argv=None):
    args = parse_args(argv)
    sync_engine = create_engine(args.source)
    dest_engine = create_async_engine(args.dest)

    reflected, source_names = await asyncio.to_thread(reflect_source, sync_engine)
    tables = plan_copy_tables(Base.metadata, reflected, source_names)

    missing = await dest_missing_tables(dest_engine, tables)
    if missing:
        print(f"ABORT: dest is missing tables {missing}; run `alembic upgrade head` on the dest database first")
        return 2

    src_counts = await asyncio.to_thread(get_table_counts_sync, sync_engine, tables)
    dest_counts = await get_table_counts(dest_engine, tables)
    src_version = await asyncio.to_thread(get_alembic_version_sync, sync_engine)
    dest_version = await get_alembic_version(dest_engine)

    print(f"source: {args.source}")
    print(f"dest:   {args.dest}")
    print("plan (FK-safe order):")
    for t in tables:
        print(f"  {t.name:<22} src={src_counts[t.name]:>8}  dest={dest_counts[t.name]:>8}  chunk={chunk_size_for(t.name)}")
    print(f"alembic: src={src_version!r} dest={dest_version!r}")

    non_empty = {t.name: dest_counts[t.name] for t in tables if dest_counts[t.name] > 0}
    if not args.yes:
        hint = ", --truncate to wipe dest first" if non_empty else ""
        print(f"dry run: no rows written (pass --yes to copy{hint})")
        await dest_engine.dispose()
        return 0

    if non_empty and not args.truncate:
        print("ABORT: dest tables are not empty: " + ", ".join(f"{name}={count}" for name, count in non_empty.items()))
        print("Pass --truncate to wipe dest tables before copying.")
        return 2

    if args.truncate:
        await truncate_dest(dest_engine, tables)
        print(f"truncated {len(tables)} dest tables")

    for t in tables:
        started = time.monotonic()
        before = dest_counts[t.name]
        copied = await copy_table(sync_engine, dest_engine, t)
        after = await get_table_counts(dest_engine, [t])
        print(f"[{t.name}] src={src_counts[t.name]} dest_before={before} copied={copied} dest_after={after[t.name]} ({time.monotonic() - started:.1f}s)")

    if src_version is not None and dest_version is not None:
        if src_version != dest_version:
            print(f"WARNING: alembic versions differ: src={src_version!r} dest={dest_version!r} (not modified; both should be at head)")
    else:
        print(f"NOTE: alembic_version not comparable: src={src_version!r} dest={dest_version!r}")

    ok = await verify(sync_engine, dest_engine, tables)
    print("verification " + ("PASSED" if ok else "FAILED"))
    await dest_engine.dispose()
    sync_engine.dispose()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
