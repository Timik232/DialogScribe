"""add approved_by and approved_at to users

Revision ID: 002_approval_fields
Revises: 001_initial
Create Date: 2025-01-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_approval_fields"
down_revision: Union[str, None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("approved_by", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key("fk_users_approved_by", "users", ["approved_by"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_approved_by", type_="foreignkey")
        batch_op.drop_column("approved_at")
        batch_op.drop_column("approved_by")
