"""add analysis_text to saved_transcriptions

Revision ID: 006_add_analysis_text
Revises: 005_saved_transcriptions
Create Date: 2025-01-06 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006_add_analysis_text"
down_revision: Union[str, None] = "005_saved_transcriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("saved_transcriptions") as batch_op:
        batch_op.add_column(sa.Column("analysis_text", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("saved_transcriptions") as batch_op:
        batch_op.drop_column("analysis_text")
