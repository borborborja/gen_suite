from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class ConnectionIn(BaseModel):
    provider: str
    name: str = Field(min_length=1, max_length=128)
    endpoint: str | None = Field(default=None, max_length=1024)


class ConnectionOut(ConnectionIn):
    id: uuid.UUID
    status: str
    last_pulled_at: datetime.datetime | None
    last_pushed_at: datetime.datetime | None


class BaselineIn(BaseModel):
    snapshot_hash: str = Field(min_length=8, max_length=128)
    object_counts: dict[str, int]
    note: str | None = None


class BaselineOut(BaselineIn):
    id: uuid.UUID
    connection_id: uuid.UUID
    is_active: bool
    created_at: datetime.datetime


class SyncChangeIn(BaseModel):
    source_system: str
    object_type: str
    object_key: str = Field(min_length=1, max_length=255)
    operation: str = "upsert"
    remote_revision: str | None = Field(default=None, max_length=128)
    payload: dict | None = None


class SyncChangeOut(SyncChangeIn):
    id: uuid.UUID
    connection_id: uuid.UUID
    state: str
    conflict_with_id: uuid.UUID | None
    resolution_note: str | None
    resolved_at: datetime.datetime | None


class SyncChangeBatch(BaseModel):
    changes: list[SyncChangeIn] = Field(min_length=1, max_length=1000)


class ResolveChange(BaseModel):
    resolution: str  # accept_remote | reject_remote
    note: str | None = None


class ApplyResult(BaseModel):
    applied: int
    deferred: int
    tombstones: int


class SyncSnapshot(BaseModel):
    connection_id: uuid.UUID
    objects: list[dict]
