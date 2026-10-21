"""background transcription jobs (docs/design/background-jobs.md)

Adds ``transcription_jobs``: one row per server-side transcription job.
The row is the source of truth for a job's lifecycle — created by
``POST /api/jobs``, advanced by the processing task, readable by
``GET /api/jobs/{id}`` polling — so a client disconnect (screen off,
app killed) never loses the work or its result.

Revision ID: 013_transcription_jobs
Revises: 012_user_settings_fk
Create Date: 2026-10-17
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "013_transcription_jobs"
down_revision: str | None = "012_user_settings_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transcription_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("input_filename", sa.String(length=255), nullable=True),
        sa.Column("input_path", sa.String(length=512), nullable=False),
        sa.Column("input_size_bytes", sa.Integer(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("saved_transcription_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_transcription_jobs_user_id", "transcription_jobs", ["user_id"])
    op.create_index("ix_transcription_jobs_status", "transcription_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_transcription_jobs_status", table_name="transcription_jobs")
    op.drop_index("ix_transcription_jobs_user_id", table_name="transcription_jobs")
    op.drop_table("transcription_jobs")
