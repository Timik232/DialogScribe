"""align user_settings.asr_provider server_default with DEFAULT_ASR_PROVIDER (litellm)

Changes only the server-side default for future rows; existing stored
mistral/litellm values are copied verbatim (batch_alter_table table rebuild
preserves data). Companion to ORM default in gigaam_transcriber/models.py.

Revision ID: 011_default_asr_provider_litellm
Revises: 010_refresh_sessions
Create Date: 2026-09-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "011_default_asr_provider_litellm"
down_revision: Union[str, None] = "010_refresh_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.alter_column(
            "asr_provider",
            existing_type=sa.String(length=100),
            server_default="litellm",
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.alter_column(
            "asr_provider",
            existing_type=sa.String(length=100),
            server_default="mistral",
            existing_nullable=False,
        )
