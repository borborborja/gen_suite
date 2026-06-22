"""Enforce one active transcription per page (M8)

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-21

A concurrent re-recognition (or the earlier libros2pdf-failure incident) could leave more than
one ``is_active`` transcription for the same page. First collapse any existing duplicates to a
single active row — preferring a real transcription over an ``error`` row, then the most recent —
then add a partial unique index so the invariant holds going forward.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Deactivate all but the best active row per (document_id, page_no).
    op.execute(
        """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY document_id, page_no
                ORDER BY (status = 'error') ASC, created_at DESC
            ) AS rn
            FROM transcriptions
            WHERE is_active
        )
        UPDATE transcriptions t
        SET is_active = false
        FROM ranked r
        WHERE t.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_active_transcription_per_page "
        "ON transcriptions (document_id, page_no) WHERE is_active"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_active_transcription_per_page")
