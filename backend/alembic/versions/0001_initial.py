"""initial: roles, RLS helpers, tenants/users/memberships

Revision ID: 0001
Revises:
Create Date: 2026-06-15

Establishes the multi-tenant foundation:
  * a restricted, NON-superuser ``app`` login role (so RLS is enforced at runtime);
  * STABLE helper functions reading the per-transaction GUCs;
  * core identity tables, with RLS on the tenant-scoped ``memberships`` table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    app_user = settings.app_db_user
    app_pw = settings.app_db_password.replace("'", "''")

    # ── RLS helper functions (NULLIF guards the empty-string == unset case) ──
    op.execute(
        "CREATE OR REPLACE FUNCTION app_current_user() RETURNS uuid LANGUAGE sql STABLE AS "
        "$$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid LANGUAGE sql STABLE AS "
        "$$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$;"
    )

    # ── Restricted application role + default privileges for future tables ──
    op.execute(
        f"""
        DO $$
        BEGIN
           IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{app_user}') THEN
              CREATE ROLE {app_user} LOGIN PASSWORD '{app_pw}'
                 NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
           END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {app_user};")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_user};"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {app_user};"
    )

    # ── Tables ──
    op.create_table(
        "tenants",
        _uuid_pk(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("plan", sa.String(32), server_default=sa.text("'free'"), nullable=False),
        sa.Column("status", sa.String(32), server_default=sa.text("'active'"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )
    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_server_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "refresh_tokens",
        _uuid_pk(),
        sa.Column("jti", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.UniqueConstraint("jti", name="uq_refresh_tokens_jti"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_refresh_tokens_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_table(
        "memberships",
        _uuid_pk(),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_memberships_tenant_id_tenants", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id_users", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "role IN ('tenant_admin','researcher','viewer')", name="ck_memberships_role"
        ),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    # Explicit grants (belt-and-suspenders alongside the default privileges above).
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, users, refresh_tokens, memberships TO {app_user};"
    )

    # ── RLS: memberships is tenant-scoped; a user may always see their own rows ──
    op.execute("ALTER TABLE memberships ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY memberships_isolation ON memberships "
        "USING (user_id = app_current_user() OR tenant_id = app_current_tenant()) "
        "WITH CHECK (user_id = app_current_user() OR tenant_id = app_current_tenant());"
    )


def downgrade() -> None:
    # Dev-only. The app role is intentionally left in place (default privileges depend
    # on it); drop it manually if truly tearing down.
    op.execute("DROP POLICY IF EXISTS memberships_isolation ON memberships;")
    op.drop_table("memberships")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
    op.drop_table("tenants")
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant();")
    op.execute("DROP FUNCTION IF EXISTS app_current_user();")
