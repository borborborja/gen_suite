from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class ProposalIn(BaseModel):
    operation: str = Field(min_length=1, max_length=48)
    patch: dict
    target_type: str | None = Field(default=None, max_length=32)
    target_id: uuid.UUID | None = None
    rationale: str | None = None


class ProposalReview(BaseModel):
    status: str  # accepted | rejected
    reviewer_note: str | None = None


class ProposalOut(ProposalIn):
    id: uuid.UUID
    status: str
    submitted_by: uuid.UUID | None
    reviewed_by: uuid.UUID | None = None
    reviewer_note: str | None = None
    reviewed_at: datetime.datetime | None = None


class PublicationIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    person_ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)
    expires_at: datetime.datetime | None = None


class PublicationOut(BaseModel):
    id: uuid.UUID
    title: str
    slug: str
    status: str
    expires_at: datetime.datetime | None
    published_at: datetime.datetime | None
    share_path: str


class PublicationCreated(PublicationOut):
    access_token: str


class PublicTreeOut(BaseModel):
    title: str
    published_at: datetime.datetime | None
    payload: dict


class PublicShareToken(BaseModel):
    token: str = Field(min_length=32, max_length=512)
