from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    doc_type: str
    visibility: str
    source_kind: str
    page_count: int
    rights_declaration: str | None
    image_policy: str
    may_contain_living: bool
    source_origin: str | None
    source_ref: str | None = None  # external origin (e.g. FamilySearch URL)
    derived_from_id: uuid.UUID | None = None  # parent document for a derived (compacted) one
    default_record_type: str | None
    pending_job_id: uuid.UUID | None = None  # rasterization job, when a PDF is still processing
    year_from: int | None
    year_to: int | None
    book_number: int | None = None       # ordinal within the parish series
    is_index: bool = False
    indexes_for_id: uuid.UUID | None = None
    created_at: datetime


class PageOut(BaseModel):
    id: uuid.UUID
    page_no: int
    folio_label: str | None = None  # the page's own printed/written number ("23v")
    kind: str = "record"            # record | index | cover | blank
    content_type: str | None
    width: int | None
    height: int | None
    byte_size: int | None
    source_ref: str | None = None  # exact image origin (e.g. FamilySearch ARK)


class PublishRequest(BaseModel):
    rights_declaration: str
