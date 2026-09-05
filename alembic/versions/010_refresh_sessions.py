"""refresh session table for revocable rotated tokens

Revision ID: 010_refresh_sessions
Revises: 009_share_lifecycle
Create Date: 2026-09-05 00:00:00.000000

Additive-only migration: creates the ``refresh_sessions`` table backing
single-use refresh-token rotation (Task 7). Only SHA-256 hashes of token
``jti`` values are stored — never raw tokens. Existing rows are unaffected;
refresh tokens issued before this migration have no row and are rejected
at ``/api/auth/refresh`` (clients simply re-authenticate).
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010_refresh_sessions"
down_revision: str | None = "009_share_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("hashed_jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("rotated_to_hashed_jti", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_sessions_hashed_jti", "refresh_sessions", ["hashed_jti"], unique=True)
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_hashed_jti", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
