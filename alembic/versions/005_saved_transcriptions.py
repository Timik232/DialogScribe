"""create saved_transcriptions table

Revision ID: 005_saved_transcriptions
Revises: 004_password_reset
Create Date: 2025-01-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005_saved_transcriptions"
down_revision: Union[str, None] = "004_password_reset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_transcriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("segments_json", sa.JSON(), nullable=True),
        sa.Column("speaker_names", sa.JSON(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("language", sa.String(10), nullable=False, server_default="ru"),
        sa.Column("share_id", sa.String(36), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_saved_transcriptions_share_id", "saved_transcriptions", ["share_id"])
    op.create_index("ix_saved_transcriptions_user_id", "saved_transcriptions", ["user_id"])


def downgrade() -> None:
    op.drop_table("saved_transcriptions")
