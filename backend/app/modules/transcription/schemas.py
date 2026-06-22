from __future__ import annotations

import uuid

from pydantic import BaseModel


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


class VersionPairOut(BaseModel):
    page_no: int
    active: TranscriptionOut | None
    candidate: TranscriptionOut | None


class ReconcileRequest(BaseModel):
    mode: str  # substitute | mix | manual
    criterion: str | None = None  # for mix: frequency | llm
    keep_history: bool = False  # keep the old transcription as an archived version
    choices: dict[str, str] | None = None  # manual: page_no -> "old" | "new" | edited text
