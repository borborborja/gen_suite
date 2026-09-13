from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.document import Document
from ...models.tree_import import SourceDocument
from ...models.citation import Citation
from ...models.event import Event
from ...models.family import Family
from ...models.person import Name, Person
from ...models.research import (
    Assertion, Note, ObjectTag, Repository, ResearchProject, ResearchSearch, ResearchTask, Source, Tag,
)
from ...models.membership import Membership
from .schemas import (
    AssertionIn, AssertionOut, AssertionPatch, NoteIn, NoteOut, ProjectIn, ProjectOut,
    RepositoryIn, RepositoryOut, SearchIn, SearchOut, SourceIn, SourceOut, SourceDocumentOut, TagIn, TagOut, TaskIn, TaskOut, TaskPatch,
)

_TASK_STATES = {"open", "in_progress", "blocked", "done"}
_TASK_PRIORITIES = {"low", "medium", "high"}
_ASSERTION_STATES = {"proposed", "accepted", "disputed", "deprecated"}
_PROJECT_STATES = {"active", "paused", "completed", "archived"}
_SEARCH_RESULTS = {"found", "not_found", "inconclusive"}
_TARGET_MODELS = {
    "document": Document, "person": Person, "name": Name, "event": Event, "family": Family,
    "repository": Repository, "source": Source, "assertion": Assertion, "project": ResearchProject,
    "task": ResearchTask, "note": Note, "citation": Citation,
}


def _check(value: str, allowed: set[str], field: str) -> None:
    if value not in allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} inválido")


async def _user_is_member(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    """Assignments may only name someone who belongs to this workspace.

    Users are global identities; checking the user table alone would allow a task in one
    family workspace to expose the identifier of somebody from another one.
    """
    if user_id is not None and not await session.scalar(select(Membership.user_id).where(
        Membership.tenant_id == tenant_id, Membership.user_id == user_id
    )):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "miembro del espacio de trabajo no encontrado")


async def _validate_target(
    session: AsyncSession, target_type: str | None, target_id: uuid.UUID | None, *, field: str
) -> None:
    if target_type is None and target_id is None:
        return
    if not target_type or not target_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} necesita tipo e identificador")
    model = _TARGET_MODELS.get(target_type)
    if model is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"tipo de {field} no compatible")
    if not await session.get(model, target_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"destino de {field} no encontrado")


def repository_out(row: Repository) -> RepositoryOut:
    return RepositoryOut(id=row.id, name=row.name, url=row.url, address=row.address, notes=row.notes)


def source_out(row: Source, *, repository_name: str | None = None, document_title: str | None = None) -> SourceOut:
    return SourceOut(
        id=row.id, title=row.title, source_type=row.source_type, author=row.author,
        publication_info=row.publication_info, call_number=row.call_number, url=row.url,
        repository_id=row.repository_id, primary_document_id=row.primary_document_id, raw=row.raw,
        repository_name=repository_name, document_title=document_title,
    )


async def create_repository(session: AsyncSession, tenant_id: uuid.UUID, body: RepositoryIn) -> Repository:
    row = Repository(tenant_id=tenant_id, **body.model_dump())
    session.add(row)
    await session.flush()
    return row


async def list_repositories(session: AsyncSession) -> list[RepositoryOut]:
    rows = (await session.scalars(select(Repository).order_by(Repository.name))).all()
    return [repository_out(r) for r in rows]


async def create_source(session: AsyncSession, tenant_id: uuid.UUID, body: SourceIn) -> Source:
    if body.repository_id and not await session.get(Repository, body.repository_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "repositorio no encontrado")
    if body.primary_document_id and not await session.get(Document, body.primary_document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "documento no encontrado")
    row = Source(tenant_id=tenant_id, **body.model_dump())
    session.add(row)
    await session.flush()
    return row


async def list_sources(session: AsyncSession) -> list[SourceOut]:
    rows = (await session.execute(
        select(Source, Repository.name, Document.title)
        .outerjoin(Repository, Repository.id == Source.repository_id)
        .outerjoin(Document, Document.id == Source.primary_document_id)
        .order_by(Source.title)
    )).all()
    documents = {}
    for source_id, doc_id, title in (await session.execute(select(SourceDocument.source_id, Document.id, Document.title)
            .join(Document, (Document.id == SourceDocument.document_id) & (Document.tenant_id == SourceDocument.tenant_id))
            .order_by(Document.title, Document.id))).all():
        documents.setdefault(source_id, {})[doc_id] = title
    result = []
    for source, repository_name, document_title in rows:
        linked = documents.get(source.id, {})
        if source.primary_document_id and document_title:
            linked.setdefault(source.primary_document_id, document_title)
        value = source_out(source, repository_name=repository_name, document_title=document_title)
        value.documents = [SourceDocumentOut(id=identifier, title=title) for identifier, title in linked.items()]
        result.append(value)
    return result


async def create_project(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, body: ProjectIn
) -> ResearchProject:
    _check(body.status, _PROJECT_STATES, "estado")
    if body.target_person_id and not await session.get(Person, body.target_person_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona objetivo no encontrada")
    row = ResearchProject(tenant_id=tenant_id, created_by=user_id, **body.model_dump())
    session.add(row)
    await session.flush()
    return row


async def list_projects(session: AsyncSession) -> list[ProjectOut]:
    rows = (await session.execute(
        select(ResearchProject, func.count(ResearchTask.id))
        .outerjoin(ResearchTask, (ResearchTask.project_id == ResearchProject.id) & (ResearchTask.status != "done"))
        .group_by(ResearchProject.id)
        .order_by(ResearchProject.status, ResearchProject.created_at.desc())
    )).all()
    return [
        ProjectOut(id=p.id, title=p.title, description=p.description, status=p.status,
                   question=p.question, target_person_id=p.target_person_id, conclusion=p.conclusion,
                   created_by=p.created_by, open_tasks=count)
        for p, count in rows
    ]


def search_out(row: ResearchSearch) -> SearchOut:
    return SearchOut(id=row.id, project_id=row.project_id, scope=row.scope, criteria=row.criteria,
                     result=row.result, notes=row.notes, created_at=row.created_at)


async def create_search(session: AsyncSession, tenant_id: uuid.UUID, project_id: uuid.UUID, body: SearchIn) -> ResearchSearch:
    if not await session.get(ResearchProject, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "proyecto no encontrado")
    _check(body.result, _SEARCH_RESULTS, "resultado")
    row = ResearchSearch(tenant_id=tenant_id, project_id=project_id, **body.model_dump())
    session.add(row)
    await session.flush()
    return row


async def list_searches(session: AsyncSession, project_id: uuid.UUID) -> list[SearchOut]:
    if not await session.get(ResearchProject, project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "proyecto no encontrado")
    rows = (await session.scalars(
        select(ResearchSearch).where(ResearchSearch.project_id == project_id)
        .order_by(ResearchSearch.created_at.desc())
    )).all()
    return [search_out(row) for row in rows]


async def _validate_task_refs(session: AsyncSession, tenant_id: uuid.UUID, body: TaskIn) -> None:
    if body.project_id and not await session.get(ResearchProject, body.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "proyecto no encontrado")
    await _user_is_member(session, tenant_id, body.assignee_user_id)
    await _validate_target(session, body.target_type, body.target_id, field="la tarea")
    _check(body.status, _TASK_STATES, "estado")
    _check(body.priority, _TASK_PRIORITIES, "prioridad")


def task_out(row: ResearchTask) -> TaskOut:
    return TaskOut(
        id=row.id, project_id=row.project_id, title=row.title, description=row.description,
        status=row.status, priority=row.priority, due_at=row.due_at, assignee_user_id=row.assignee_user_id,
        target_type=row.target_type, target_id=row.target_id, created_by=row.created_by,
    )


async def create_task(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, body: TaskIn
) -> ResearchTask:
    await _validate_task_refs(session, tenant_id, body)
    row = ResearchTask(tenant_id=tenant_id, created_by=user_id, **body.model_dump())
    session.add(row)
    await session.flush()
    return row


async def list_tasks(session: AsyncSession, project_id: uuid.UUID | None, status_filter: str | None) -> list[TaskOut]:
    query = select(ResearchTask).order_by(ResearchTask.due_at.is_(None), ResearchTask.due_at, ResearchTask.created_at.desc())
    if project_id:
        query = query.where(ResearchTask.project_id == project_id)
    if status_filter:
        _check(status_filter, _TASK_STATES, "estado")
        query = query.where(ResearchTask.status == status_filter)
    return [task_out(r) for r in (await session.scalars(query)).all()]


async def patch_task(session: AsyncSession, task_id: uuid.UUID, body: TaskPatch) -> ResearchTask:
    row = await session.get(ResearchTask, task_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "tarea no encontrada")
    values = body.model_dump(exclude_unset=True)
    if "project_id" in values and values["project_id"] is not None:
        if not await session.get(ResearchProject, values["project_id"]):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "proyecto no encontrado")
        row.project_id = values["project_id"]
    if body.clear_project:
        row.project_id = None
    if "assignee_user_id" in values and values["assignee_user_id"] is not None:
        await _user_is_member(session, row.tenant_id, values["assignee_user_id"])
        row.assignee_user_id = values["assignee_user_id"]
    if body.clear_assignee:
        row.assignee_user_id = None
    if "status" in values and values["status"] is not None:
        _check(values["status"], _TASK_STATES, "estado")
        row.status = values["status"]
    if "priority" in values and values["priority"] is not None:
        _check(values["priority"], _TASK_PRIORITIES, "prioridad")
        row.priority = values["priority"]
    for field in ("title", "description", "due_at", "target_type", "target_id"):
        if field in values:
            setattr(row, field, values[field])
    if body.clear_due_at:
        row.due_at = None
    if body.clear_target:
        row.target_type, row.target_id = None, None
    await _validate_target(session, row.target_type, row.target_id, field="la tarea")
    await session.flush()
    return row


def assertion_out(row: Assertion) -> AssertionOut:
    return AssertionOut(
        id=row.id, subject_type=row.subject_type, subject_id=row.subject_id, predicate=row.predicate,
        value=row.value, state=row.state, confidence=row.confidence, created_by=row.created_by,
    )


async def create_assertion(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, body: AssertionIn
) -> Assertion:
    _check(body.state, _ASSERTION_STATES, "estado")
    await _validate_target(session, body.subject_type, body.subject_id, field="la afirmación")
    row = Assertion(tenant_id=tenant_id, created_by=user_id, **body.model_dump())
    session.add(row)
    await session.flush()
    return row


async def list_assertions(session: AsyncSession, subject_type: str | None, subject_id: uuid.UUID | None) -> list[AssertionOut]:
    query = select(Assertion).order_by(Assertion.created_at.desc())
    if subject_type:
        query = query.where(Assertion.subject_type == subject_type)
    if subject_id:
        query = query.where(Assertion.subject_id == subject_id)
    return [assertion_out(row) for row in (await session.scalars(query)).all()]


async def patch_assertion(session: AsyncSession, assertion_id: uuid.UUID, body: AssertionPatch) -> Assertion:
    row = await session.get(Assertion, assertion_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "afirmación no encontrada")
    if body.state is not None:
        _check(body.state, _ASSERTION_STATES, "estado")
        row.state = body.state
    if body.confidence is not None:
        row.confidence = body.confidence
    if body.value is not None:
        row.value = body.value
    await session.flush()
    return row


async def create_note(session: AsyncSession, tenant_id: uuid.UUID, body: NoteIn) -> Note:
    await _validate_target(session, body.subject_type, body.subject_id, field="la nota")
    row = Note(tenant_id=tenant_id, **body.model_dump())
    session.add(row)
    await session.flush()
    return row


async def list_notes(session: AsyncSession, subject_type: str, subject_id: uuid.UUID) -> list[NoteOut]:
    rows = (await session.scalars(
        select(Note).where(Note.subject_type == subject_type, Note.subject_id == subject_id).order_by(Note.created_at)
    )).all()
    return [NoteOut(id=n.id, subject_type=n.subject_type, subject_id=n.subject_id, text=n.text, format=n.format) for n in rows]


async def create_tag(session: AsyncSession, tenant_id: uuid.UUID, body: TagIn) -> Tag:
    row = Tag(tenant_id=tenant_id, name=body.name.strip(), color=body.color)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "la etiqueta ya existe") from exc
    return row


async def list_tags(session: AsyncSession) -> list[TagOut]:
    rows = (await session.scalars(select(Tag).order_by(Tag.name))).all()
    return [TagOut(id=t.id, name=t.name, color=t.color) for t in rows]


async def tag_object(session: AsyncSession, tenant_id: uuid.UUID, tag_id: uuid.UUID, target_type: str, target_id: uuid.UUID) -> None:
    if not await session.get(Tag, tag_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "etiqueta no encontrada")
    await _validate_target(session, target_type, target_id, field="la etiqueta")
    row = ObjectTag(tenant_id=tenant_id, tag_id=tag_id, target_type=target_type, target_id=target_id)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "la etiqueta ya está aplicada") from exc
