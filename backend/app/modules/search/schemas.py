from __future__ import annotations

import uuid

from pydantic import BaseModel


class SearchHit(BaseModel):
    transcription_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str | None
    page_no: int
    snippet: str | None
    score: float


class SuggestionOut(BaseModel):
    value: str
    count: int
    score: float


class RecordHit(BaseModel):
    record_id: uuid.UUID
    mention_id: uuid.UUID | None = None
    document_id: uuid.UUID
    document_title: str | None
    page_no: int | None
    record_type: str
    date_raw: str | None
    date_year: int | None
    place: str | None
    given: str | None
    surname: str | None
    role: str | None
    summary: str | None
    score: float = 0.0
