"""AI provider registry: provider_credentials + task_provider_bindings + server-admin GUC

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-16

provider_credentials rows are readable (so the resolver can use a server key on a tenant's
behalf — the ciphertext is useless without the master key), but WRITING a server-scoped row
requires server-admin, enforced via the app.is_server_admin GUC.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE OR REPLACE FUNCTION app_is_server_admin() RETURNS boolean LANGUAGE sql STABLE AS "
        "$$ SELECT current_setting('app.is_server_admin', true) = 'true' $$;"
    )

    op.create_table(
        "provider_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True)),
        sa.Column("provider_key", sa.String(32), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("base_url", sa.String(256)),
        sa.Column("model_default", sa.String(128)),
        sa.Column("api_key_ciphertext", sa.LargeBinary()),
        sa.Column("api_key_nonce", sa.LargeBinary()),
        sa.Column("key_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_provider_credentials"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_provider_credentials_tenant_id_tenants", ondelete="CASCADE"),
        sa.CheckConstraint("scope IN ('server','tenant')", name="ck_provider_credentials_scope"),
        sa.CheckConstraint(
            "(scope = 'server' AND tenant_id IS NULL) OR (scope = 'tenant' AND tenant_id IS NOT NULL)",
            name="ck_provider_credentials_scope_tenant",
        ),
    )
    op.create_index("ix_provider_credentials_tenant_id", "provider_credentials", ["tenant_id"])

    op.create_table(
        "task_provider_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(24), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model", sa.String(128)),
        sa.Column("params", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_provider_bindings"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_task_provider_bindings_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["credential_id"], ["provider_credentials.id"], name="fk_task_provider_bindings_credential_id", ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "task_type", name="uq_task_provider_bindings_tenant_task"),
        sa.CheckConstraint(
            "task_type IN ('transcription','embedding','inference')",
            name="ck_task_provider_bindings_task_type",
        ),
    )
    op.create_index("ix_task_provider_bindings_tenant_id", "task_provider_bindings", ["tenant_id"])

    app_user = settings.app_db_user
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON provider_credentials, task_provider_bindings TO {app_user};"
    )

    # Credentials: readable (server rows + own-tenant rows); writes restricted.
    op.execute("ALTER TABLE provider_credentials ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY provider_credentials_isolation ON provider_credentials "
        "USING (scope = 'server' OR tenant_id = app_current_tenant()) "
        "WITH CHECK ("
        "  (scope = 'tenant' AND tenant_id = app_current_tenant()) "
        "  OR (scope = 'server' AND app_is_server_admin())"
        ");"
    )

    op.execute("ALTER TABLE task_provider_bindings ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY task_provider_bindings_isolation ON task_provider_bindings "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS task_provider_bindings_isolation ON task_provider_bindings;")
    op.execute("DROP POLICY IF EXISTS provider_credentials_isolation ON provider_credentials;")
    op.drop_table("task_provider_bindings")
    op.drop_table("provider_credentials")
    op.execute("DROP FUNCTION IF EXISTS app_is_server_admin();")
