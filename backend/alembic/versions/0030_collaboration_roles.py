"""collaboration roles: owner/admin/editor/contributor/viewer/guest

Revision ID: 0030
Revises: 0029
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROLES = "'owner','admin','editor','contributor','viewer','guest'"


def upgrade() -> None:
    # Preserve a single accountable owner for every existing workspace.  Other previous
    # tenant_admin memberships become admins; the former researcher role becomes editor.
    op.execute(
        "WITH ranked AS ("
        " SELECT id, row_number() OVER (PARTITION BY tenant_id ORDER BY created_at, id) AS position"
        " FROM memberships WHERE role = 'tenant_admin'"
        ") UPDATE memberships m SET role = CASE WHEN ranked.position = 1 THEN 'owner' ELSE 'admin' END"
        " FROM ranked WHERE m.id = ranked.id"
    )
    op.execute("UPDATE memberships SET role = 'editor' WHERE role = 'researcher'")
    op.execute("UPDATE api_keys SET role = 'admin' WHERE role = 'tenant_admin'")
    op.execute("UPDATE api_keys SET role = 'editor' WHERE role = 'researcher'")
    op.drop_constraint("ck_memberships_role", "memberships", type_="check")
    op.create_check_constraint("ck_memberships_role", "memberships", f"role IN ({_ROLES})")


def downgrade() -> None:
    # Contributor/guest grants cannot be expressed by the old model.  Conservatively reduce
    # them to viewer; every owner/admin becomes the former tenant administrator.
    op.drop_constraint("ck_memberships_role", "memberships", type_="check")
    op.execute("UPDATE memberships SET role = 'tenant_admin' WHERE role IN ('owner', 'admin')")
    op.execute("UPDATE memberships SET role = 'researcher' WHERE role = 'editor'")
    op.execute("UPDATE memberships SET role = 'viewer' WHERE role IN ('contributor', 'guest')")
    op.execute("UPDATE api_keys SET role = 'tenant_admin' WHERE role IN ('owner', 'admin')")
    op.execute("UPDATE api_keys SET role = 'researcher' WHERE role = 'editor'")
    op.execute("UPDATE api_keys SET role = 'viewer' WHERE role IN ('contributor', 'guest')")
    op.create_check_constraint(
        "ck_memberships_role", "memberships", "role IN ('tenant_admin','researcher','viewer')"
    )
