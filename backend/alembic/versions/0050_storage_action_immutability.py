"""Revoca los permisos heredados por defecto de 0001 sobre la auditoría de reservas."""
from alembic import op
from app.settings import settings

revision = "0050"
down_revision = "0049"
branch_labels = depends_on = None


def upgrade():
    role = '"' + settings.app_db_user.replace('"', '""') + '"'
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON storage_write_actions FROM {role}, PUBLIC")
    op.execute("CREATE POLICY storage_write_actions_no_update ON storage_write_actions AS RESTRICTIVE FOR UPDATE USING (false) WITH CHECK (false)")
    op.execute("CREATE POLICY storage_write_actions_no_delete ON storage_write_actions AS RESTRICTIVE FOR DELETE USING (false)")


def downgrade():
    op.execute("DROP POLICY storage_write_actions_no_delete ON storage_write_actions")
    op.execute("DROP POLICY storage_write_actions_no_update ON storage_write_actions")
    role = '"' + settings.app_db_user.replace('"', '""') + '"'
    op.execute(f"GRANT UPDATE, DELETE ON storage_write_actions TO {role}")
