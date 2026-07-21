from __future__ import annotations

import uuid

from pydantic import BaseModel


class ImportResult(BaseModel):
    import_id: uuid.UUID
    individuals: int
    families: int
    places: int
    events: int
    encoding: str


class TreePerson(BaseModel):
    id: uuid.UUID
    given: str | None
    surname: str | None
    sex: str
    birth_year: int | None
    death_year: int | None
    has_documents: bool = False  # populated in Phase 2/7 overlays
    deduction_count: int = 0  # populated in Phase 7


class TreeFamily(BaseModel):
    id: uuid.UUID
    husband_id: uuid.UUID | None
    wife_id: uuid.UUID | None
    child_ids: list[uuid.UUID]


class TreeGraph(BaseModel):
    focus: uuid.UUID
    persons: list[TreePerson]
    families: list[TreeFamily]


class NameOut(BaseModel):
    type: str
    given: str | None
    surname: str | None
    surname_prefix: str | None
    nickname: str | None
    is_primary: bool
    is_inferred: bool


class EventOut(BaseModel):
    id: uuid.UUID
    type: str
    date_raw: str | None
    date_year: int | None
    place: str | None
    place_lat: float | None = None
    place_lng: float | None = None
    value: str | None
    is_inferred: bool


class RelatedPerson(BaseModel):
    id: uuid.UUID
    given: str | None
    surname: str | None
    sex: str
    birth_year: int | None
    death_year: int | None
    relation: str | None = None


class PersonDetail(BaseModel):
    id: uuid.UUID
    sex: str
    notes: str | None = None
    names: list[NameOut]
    events: list[EventOut]
    parents: list[RelatedPerson]
    spouses: list[RelatedPerson]
    children: list[RelatedPerson]
    siblings: list[RelatedPerson] = []  # other children of the family/families where this person is a child


class SearchHit(BaseModel):
    id: uuid.UUID
    given: str | None
    surname: str | None
    birth_year: int | None
    death_year: int | None


class TreeStats(BaseModel):
    persons: int
    families: int
    events: int
    places: int


class PersonRow(BaseModel):
    id: uuid.UUID
    given: str | None
    surname: str | None
    sex: str
    birth_year: int | None
    death_year: int | None


class PersonPage(BaseModel):
    total: int
    items: list[PersonRow]


class KinshipStep(BaseModel):
    person: SearchHit
    step: str | None = None  # e.g. "madre", "hijo", "esposa" — None on the first node


class RelationshipOut(BaseModel):
    related: bool
    label: str  # what B is of A, in Spanish ("prima segunda", "suegro"…)
    path: list[KinshipStep]


class DuplicatePair(BaseModel):
    a: SearchHit
    b: SearchHit
    score: float
    reason: str


class CitationOut(BaseModel):
    id: uuid.UUID
    note: str | None = None
    target_type: str
    document_id: uuid.UUID | None = None
    document_title: str | None = None
    page_no: int | None = None
    record_type: str | None = None
    date_raw: str | None = None
    summary: str | None = None
