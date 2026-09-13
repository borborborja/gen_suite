from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TranscribeRequest(BaseModel):
    document_id: uuid.UUID
    # Optional per-job provider override; if omitted, the tenant's "transcription" binding is used.
    engine: str | None = None
    model: str | None = None
    credential_id: uuid.UUID | None = None
    api_key: str | None = None
    base_url: str | None = None
    # OCR options
    prompt: str | None = None
    lang: str = "spa"
    psm: int = 6
    # Re-recognition: write new transcriptions as candidates (is_active=false) for pages that already
    # have an active one, so the user can reconcile (substitute / mix / manual) afterwards.
    replace: bool = False


class TranscriptionOut(BaseModel):
    id: uuid.UUID
    page_no: int
    engine: str
    model: str | None
    text: str | None
    status: str


class CorrectionRequest(BaseModel):
    text: str = Field(max_length=500_000)


class ExpectedVersion(BaseModel):
    active_id: uuid.UUID | None
    candidate_id: uuid.UUID | None


class VersionPairOut(BaseModel):
    page_no: int
    active: TranscriptionOut | None
    candidate: TranscriptionOut | None


class TranscriptionRevisionOut(TranscriptionOut):
    is_active: bool
    supersedes_id: uuid.UUID | None
    reviewed_by: uuid.UUID | None
    created_at: datetime


class ReconcileRequest(BaseModel):
    mode: str  # substitute | mix | manual
    criterion: str | None = None  # for mix: frequency | llm
    keep_history: bool = True  # compatibility field; evidence history is always preserved
    choices: dict[str, str] | None = None  # manual: page_no -> "old" | "new" | edited text
    expected_versions: dict[str, ExpectedVersion]
