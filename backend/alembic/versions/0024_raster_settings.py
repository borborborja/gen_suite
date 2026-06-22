"""Per-tenant rasterization quality settings (DPI, format, auto-split spreads)

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("raster_dpi", sa.Integer(), server_default=sa.text("300"), nullable=False))
    op.add_column("tenants", sa.Column("raster_format", sa.String(8), server_default=sa.text("'webp'"), nullable=False))
    op.add_column("tenants", sa.Column("raster_autosplit", sa.Boolean(), server_default=sa.text("true"), nullable=False))


def downgrade() -> None:
    op.drop_column("tenants", "raster_autosplit")
    op.drop_column("tenants", "raster_format")
    op.drop_column("tenants", "raster_dpi")
