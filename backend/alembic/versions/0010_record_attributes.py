"""generic record attributes: address, household grouping, occupation, type-specific JSONB

Makes the extraction model carry any genealogical document — not just sacramental acts. Census/
padrón co-residence needs a real ``address`` + ``household_key`` (so people at the same domicile
group into a household); wills/trials/military/residence records keep their type-specific fields in
``Record.attributes`` (JSONB) so new document types need no further migration. ``occupation`` and
``address`` on the mention feed age/co-residence linkage signals.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("records", sa.Column("address", sa.String(256)))
    op.add_column("records", sa.Column("household_key", sa.String(128)))
    op.add_column("records", sa.Column("attributes", postgresql.JSONB()))
    op.create_index("ix_records_household_key", "records", ["household_key"])
    op.add_column("person_mentions", sa.Column("occupation", sa.String(128)))
    op.add_column("person_mentions", sa.Column("address", sa.String(256)))


def downgrade() -> None:
    op.drop_column("person_mentions", "address")
    op.drop_column("person_mentions", "occupation")
    op.drop_index("ix_records_household_key", table_name="records")
    op.drop_column("records", "attributes")
    op.drop_column("records", "household_key")
    op.drop_column("records", "address")
