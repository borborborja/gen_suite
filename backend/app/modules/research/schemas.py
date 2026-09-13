from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class RepositoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str | None = Field(default=None, max_length=1024)
    address: str | None = None
    notes: str | None = None


class RepositoryOut(RepositoryIn):
    id: uuid.UUID


class SourceIn(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    source_type: str = Field(default="document", max_length=32)
    author: str | None = Field(default=None, max_length=255)
    publication_info: str | None = None
    call_number: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=1024)
    repository_id: uuid.UUID | None = None
    primary_document_id: uuid.UUID | None = None
    raw: dict | None = None


class SourceDocumentOut(BaseModel):
    id: uuid.UUID
    title: str


class SourceOut(SourceIn):
    id: uuid.UUID
    repository_name: str | None = None
    document_title: str | None = None
    documents: list[SourceDocumentOut] = Field(default_factory=list)


class ProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    question: str | None = None
    target_person_id: uuid.UUID | None = None
    conclusion: str | None = None
    status: str = "active"


class ProjectOut(ProjectIn):
    id: uuid.UUID
    created_by: uuid.UUID | None = None
    open_tasks: int = 0


class SearchIn(BaseModel):
    scope: str = Field(min_length=1)
    criteria: str = Field(min_length=1)
    result: str = "not_found"
    notes: str | None = None


class SearchOut(SearchIn):
    id: uuid.UUID
    project_id: uuid.UUID
    created_at: datetime.datetime


class TaskIn(BaseModel):
    project_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status: str = "open"
    priority: str = "medium"
    due_at: datetime.datetime | None = None
    assignee_user_id: uuid.UUID | None = None
    target_type: str | None = Field(default=None, max_length=32)
    target_id: uuid.UUID | None = None


class TaskPatch(BaseModel):
    project_id: uuid.UUID | None = None
    clear_project: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    due_at: datetime.datetime | None = None
    clear_due_at: bool = False
    assignee_user_id: uuid.UUID | None = None
    clear_assignee: bool = False
    target_type: str | None = Field(default=None, max_length=32)
    target_id: uuid.UUID | None = None
    clear_target: bool = False


class TaskOut(TaskIn):
    id: uuid.UUID
    created_by: uuid.UUID | None = None


class AssertionIn(BaseModel):
    subject_type: str = Field(min_length=1, max_length=32)
    subject_id: uuid.UUID
    predicate: str = Field(min_length=1, max_length=64)
    value: dict
    state: str = "proposed"
    confidence: int | None = Field(default=None, ge=0, le=100)


class AssertionPatch(BaseModel):
    state: str | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)
    value: dict | None = None


class AssertionOut(AssertionIn):
    id: uuid.UUID
    created_by: uuid.UUID | None = None


class NoteIn(BaseModel):
    subject_type: str = Field(min_length=1, max_length=32)
    subject_id: uuid.UUID
    text: str = Field(min_length=1)
    format: str = "plain"


class NoteOut(NoteIn):
    id: uuid.UUID


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    color: str | None = Field(default=None, max_length=16)


class TagOut(TagIn):
    id: uuid.UUID


class TagObjectIn(BaseModel):
    target_type: str = Field(min_length=1, max_length=32)
    target_id: uuid.UUID
