"""add user_settings.user_id FK to users (CQ-M7)

Backward-compatible: the table is rebuilt via batch_alter_table with the new
FK constraint; existing data is copied verbatim. Orphaned user_settings rows
(user_id with no matching users row) are deleted first — they are unreachable
through the API (settings lookup is always by an authenticated user_id) and
would violate the new constraint.

Revision ID: 012_user_settings_fk
Revises: 011_default_asr_provider_litellm
Create Date: 2026-09-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "012_user_settings_fk"
down_revision: Union[str, None] = "011_default_asr_provider_litellm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK_NAME = "fk_user_settings_user_id_users"


def upgrade() -> None:
    op.execute("DELETE FROM user_settings WHERE user_id NOT IN (SELECT id FROM users)")
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.create_foreign_key(_FK_NAME, "users", ["user_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.drop_constraint(_FK_NAME, type_="foreignkey")
