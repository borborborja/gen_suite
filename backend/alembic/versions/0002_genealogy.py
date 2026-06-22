"""genealogy graph: places, persons, names, families, family_children, events, gedcom_imports

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-15

All tables are tenant-scoped with RLS (tenant_id = app_current_tenant()). Grants to the app
role come from the default privileges established in 0001, plus explicit grants here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.settings import settings

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ["places", "persons", "names", "families", "family_children", "events", "gedcom_imports"]


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False
    )


def _tenant_fk() -> sa.Column:
    return sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "places",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("normalized_key", sa.String(512), nullable=False),
        sa.Column("lat", sa.Float()),
        sa.Column("lng", sa.Float()),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_places"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_places_tenant_id_tenants", ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "normalized_key", name="uq_places_tenant_norm"),
    )
    op.create_index("ix_places_tenant_id", "places", ["tenant_id"])

    op.create_table(
        "persons",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("gedcom_xref", sa.String(64)),
        sa.Column("sex", sa.String(1), server_default=sa.text("'U'"), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("raw", postgresql.JSONB()),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_persons"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_persons_tenant_id_tenants", ondelete="CASCADE"),
    )
    op.create_index("ix_persons_tenant_id", "persons", ["tenant_id"])
    op.create_index("ix_persons_gedcom_xref", "persons", ["gedcom_xref"])

    op.create_table(
        "names",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(16), server_default=sa.text("'birth'"), nullable=False),
        sa.Column("given", sa.String(255)),
        sa.Column("surname", sa.String(255)),
        sa.Column("surname_prefix", sa.String(64)),
        sa.Column("nickname", sa.String(128)),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_inferred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_names"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_names_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], name="fk_names_person_id_persons", ondelete="CASCADE"),
    )
    op.create_index("ix_names_tenant_id", "names", ["tenant_id"])
    op.create_index("ix_names_person_id", "names", ["person_id"])
    op.create_index("ix_names_surname", "names", ["surname"])

    op.create_table(
        "families",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("gedcom_xref", sa.String(64)),
        sa.Column("husband_id", postgresql.UUID(as_uuid=True)),
        sa.Column("wife_id", postgresql.UUID(as_uuid=True)),
        sa.Column("raw", postgresql.JSONB()),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_families"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_families_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["husband_id"], ["persons.id"], name="fk_families_husband_id_persons", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["wife_id"], ["persons.id"], name="fk_families_wife_id_persons", ondelete="SET NULL"),
    )
    op.create_index("ix_families_tenant_id", "families", ["tenant_id"])
    op.create_index("ix_families_gedcom_xref", "families", ["gedcom_xref"])
    op.create_index("ix_families_husband_id", "families", ["husband_id"])
    op.create_index("ix_families_wife_id", "families", ["wife_id"])

    op.create_table(
        "family_children",
        _tenant_fk(),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.String(16), server_default=sa.text("'birth'"), nullable=False),
        sa.Column("seq", sa.Integer()),
        sa.PrimaryKeyConstraint("family_id", "person_id", name="pk_family_children"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_family_children_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"], name="fk_family_children_family_id_families", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], name="fk_family_children_person_id_persons", ondelete="CASCADE"),
    )
    op.create_index("ix_family_children_tenant_id", "family_children", ["tenant_id"])
    op.create_index("ix_family_children_person_id", "family_children", ["person_id"])

    op.create_table(
        "events",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("type", sa.String(24), nullable=False),
        sa.Column("date_raw", sa.String(128)),
        sa.Column("date_year", sa.Integer()),
        sa.Column("value", sa.Text()),
        sa.Column("is_inferred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("place_id", postgresql.UUID(as_uuid=True)),
        sa.Column("subject_person_id", postgresql.UUID(as_uuid=True)),
        sa.Column("subject_family_id", postgresql.UUID(as_uuid=True)),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_events_tenant_id_tenants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], name="fk_events_place_id_places", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_person_id"], ["persons.id"], name="fk_events_subject_person_id_persons", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_family_id"], ["families.id"], name="fk_events_subject_family_id_families", ondelete="CASCADE"),
    )
    op.create_index("ix_events_tenant_id", "events", ["tenant_id"])
    op.create_index("ix_events_subject_person_id", "events", ["subject_person_id"])
    op.create_index("ix_events_subject_family_id", "events", ["subject_family_id"])

    op.create_table(
        "gedcom_imports",
        _uuid_pk(),
        _tenant_fk(),
        sa.Column("filename", sa.String(255)),
        sa.Column("char_encoding", sa.String(32)),
        sa.Column("individuals_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("families_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("raw_gedcom", sa.Text()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_gedcom_imports"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_gedcom_imports_tenant_id_tenants", ondelete="CASCADE"),
    )
    op.create_index("ix_gedcom_imports_tenant_id", "gedcom_imports", ["tenant_id"])

    # ── Grants + RLS (tenant_id == active tenant) ──
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(_TABLES)} TO {settings.app_db_user};"
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_isolation ON {table} "
            f"USING (tenant_id = app_current_tenant()) "
            f"WITH CHECK (tenant_id = app_current_tenant());"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table};")
    op.drop_table("gedcom_imports")
    op.drop_table("events")
    op.drop_table("family_children")
    op.drop_table("families")
    op.drop_table("names")
    op.drop_table("persons")
    op.drop_table("places")
