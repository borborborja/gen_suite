"""Restrict connector_credentials SELECT to server-admins (H2)

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-21

Previously the read policy was ``USING (true)`` so any authenticated session could list the
(encrypted) operator secrets. Tighten it to ``app_is_server_admin()`` to match the write policy.
The download worker reads these on the operator's behalf and now sets the server-admin GUC for
that query (see app/tasks/fs_tasks.py); the management API already runs server-admins with the
GUC set.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS connector_credentials_read ON connector_credentials;")
    op.execute(
        "CREATE POLICY connector_credentials_read ON connector_credentials FOR SELECT "
        "USING (app_is_server_admin());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS connector_credentials_read ON connector_credentials;")
    op.execute(
        "CREATE POLICY connector_credentials_read ON connector_credentials FOR SELECT USING (true);"
    )
