from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DiscoverRequest(BaseModel):
    person_id: uuid.UUID
    max_candidates: int = 50


class MentionOut(BaseModel):
    id: uuid.UUID
    role: str
    name_raw: str | None
    given: str | None
    surname: str | None


class RecordOut(BaseModel):
    id: uuid.UUID
    record_type: str
    date_raw: str | None
    date_year: int | None
    summary: str | None
    parish_raw: str | None
    transcription_id: uuid.UUID | None
    page_id: uuid.UUID | None
    document_id: uuid.UUID | None = None
    page_no: int | None = None
    folio_label: str | None = None
    confidence: float | None
    mentions: list[MentionOut] = []


class TreePersonOut(BaseModel):
    id: uuid.UUID
    given: str | None
    surname: str | None
    birth_year: int | None
    death_year: int | None


class CandidateOut(BaseModel):
    id: uuid.UUID
    tree_person_id: uuid.UUID
    person_mention_id: uuid.UUID
    record_id: uuid.UUID | None
    score: float
    score_kind: str = "heuristic"
    score_version: str = "v1"
    status: str
    method: str
    relation: str = "self"  # self | sibling
    evidence: dict | None
    record: RecordOut | None = None
    mention: MentionOut | None = None
    tree_person: TreePersonOut | None = None
    created_at: datetime


class DecisionOut(BaseModel):
    id: uuid.UUID
    status: str
    resolved_person_id: uuid.UUID | None = None
    created_inferred: list[uuid.UUID] = []
    change_id: uuid.UUID | None = None


class ProposalOut(BaseModel):
    mention_id: uuid.UUID
    role: str
    name_raw: str | None
    given: str | None
    surname: str | None
    suggested_relation: str


class AcceptedOut(BaseModel):
    person_id: uuid.UUID
    mention_id: uuid.UUID


class DecisionActionOut(BaseModel):
    kind: str
    mention_id: uuid.UUID
    name: str
    relation: str
    role: str | None = None
    required: bool


class CandidatePreviewOut(BaseModel):
    candidate_id: uuid.UUID
    status: str
    relation: str
    tree_person_id: uuid.UUID
    actions: list[DecisionActionOut]
    updated_at: datetime
    preview_token: str


class CandidateApplyIn(BaseModel):
    preview_token: str
    relative_mention_ids: list[uuid.UUID] = []
    # JSON object keys are mention UUIDs and values are explicitly selected tree persons.
    existing_person_ids: dict[uuid.UUID, uuid.UUID] = {}
    # Used only for a sibling candidate; omitting it explicitly creates a new person.
    candidate_person_id: uuid.UUID | None = None


class CandidateApplyOut(DecisionOut):
    change_id: uuid.UUID
    created_or_linked_person_ids: list[uuid.UUID]
