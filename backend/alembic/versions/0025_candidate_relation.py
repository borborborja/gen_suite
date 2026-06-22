"""MatchCandidate.relation (self|sibling) for family/sibling discovery

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("match_candidates", sa.Column(
        "relation", sa.String(16), server_default=sa.text("'self'"), nullable=False))


def downgrade() -> None:
    op.drop_column("match_candidates", "relation")
