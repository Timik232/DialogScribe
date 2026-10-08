"""add share expiry and revocation columns

Revision ID: 009_share_lifecycle
Revises: 008_asr_provider_settings
Create Date: 2025-01-08 00:00:00.000000

Additive-only migration: adds nullable ``share_expires_at`` and
``share_revoked_at`` to ``saved_transcriptions``. Existing rows get NULL
(non-expiring, not revoked) so every previously issued public link keeps
working until its owner explicitly changes the share state.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009_share_lifecycle"
down_revision: str | None = "008_asr_provider_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("saved_transcriptions") as batch_op:
        batch_op.add_column(sa.Column("share_expires_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("share_revoked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("saved_transcriptions") as batch_op:
        batch_op.drop_column("share_revoked_at")
        batch_op.drop_column("share_expires_at")
