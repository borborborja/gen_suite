from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class CreateApiKeyRequest(BaseModel):
    name: str
    scope: str = "read"  # read | write
    expires_days: int | None = None


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    scope: str
    role: str
    token_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class CreateApiKeyResponse(BaseModel):
    token: str  # shown once
    key: ApiKeyOut
