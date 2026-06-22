from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class JobOut(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    progress: dict | None
    result: dict | None
    error: str | None
    document_id: uuid.UUID | None = None  # from params, lets the UI reconnect a job to its document
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
