"""retain publication selections and revalidate snapshots at publication time

Revision ID: 0033
Revises: 0032
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "publications",
        sa.Column("person_ids", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    # Existing snapshots had no retained intent. Reconstruct the visible IDs conservatively; a
    # subsequent refresh can only make the snapshot smaller, never add unselected people.
    op.execute(
        "UPDATE publications SET person_ids = COALESCE(("
        " SELECT jsonb_agg(item->>'id') FROM jsonb_array_elements(COALESCE(payload->'persons', '[]'::jsonb)) AS item"
        "), '[]'::jsonb)"
    )


def downgrade() -> None:
    op.drop_column("publications", "person_ids")
