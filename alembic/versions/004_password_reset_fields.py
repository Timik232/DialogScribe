"""add reset_token_hash and reset_token_expires to users

Revision ID: 004_password_reset
Revises: 003_templates_table
Create Date: 2025-01-04 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004_password_reset"
down_revision: Union[str, None] = "003_templates_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("reset_token_hash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("reset_token_expires", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_users_reset_token_hash", ["reset_token_hash"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_reset_token_hash")
        batch_op.drop_column("reset_token_expires")
        batch_op.drop_column("reset_token_hash")
