"""connector_credentials (operator secrets, e.g. FamilySearch cookies)

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-16

Rows are readable (encrypted) so the worker can use them on a tenant's behalf, but only
server-admins may write — enforced via two RLS policies (read-all / write-server-admin).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connector_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("connector", sa.String(32), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("secret_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_connector_credentials"),
    )
    op.create_index("ix_connector_credentials_connector", "connector_credentials", ["connector"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON connector_credentials TO {settings.app_db_user};")
    op.execute("ALTER TABLE connector_credentials ENABLE ROW LEVEL SECURITY;")
    # Readable by any authenticated session (ciphertext only); writes require server-admin.
    op.execute(
        "CREATE POLICY connector_credentials_read ON connector_credentials FOR SELECT USING (true);"
    )
    op.execute(
        "CREATE POLICY connector_credentials_write ON connector_credentials FOR ALL "
        "USING (app_is_server_admin()) WITH CHECK (app_is_server_admin());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS connector_credentials_write ON connector_credentials;")
    op.execute("DROP POLICY IF EXISTS connector_credentials_read ON connector_credentials;")
    op.drop_table("connector_credentials")
