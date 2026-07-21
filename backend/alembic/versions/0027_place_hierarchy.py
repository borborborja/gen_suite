"""places.parent_id + place_type — jerarquía de lugares (pueblo → provincia → país)

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("places", sa.Column(
        "parent_id", sa.dialects.postgresql.UUID(as_uuid=True),
        sa.ForeignKey("places.id", ondelete="SET NULL"), nullable=True))
    op.add_column("places", sa.Column("place_type", sa.String(24), nullable=True))
    op.create_index("ix_places_parent_id", "places", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_places_parent_id", table_name="places")
    op.drop_column("places", "place_type")
    op.drop_column("places", "parent_id")
