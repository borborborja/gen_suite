"""tenants.home_person_id: the tree viewer's default 'home' person

Persists which person the árbol opens on by default (set from the UI). FK to persons, SET NULL on
delete. Tenants is a global table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("home_person_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_tenants_home_person_id_persons", "tenants", "persons",
        ["home_person_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_tenants_home_person_id_persons", "tenants", type_="foreignkey")
    op.drop_column("tenants", "home_person_id")
