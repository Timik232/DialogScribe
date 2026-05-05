"""create templates table

Revision ID: 003_templates_table
Revises: 002_approval_fields
Create Date: 2025-01-03 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003_templates_table"
down_revision: Union[str, None] = "002_approval_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("emoji", sa.String(10), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("user_prompt_template", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "key", name="uq_templates_user_key"),
    )
    op.create_index("ix_templates_user_id", "templates", ["user_id"])


def downgrade() -> None:
    op.drop_table("templates")
