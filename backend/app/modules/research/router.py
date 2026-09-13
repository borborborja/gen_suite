from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from ..tree.audit import audited
from . import service
from .schemas import (
    AssertionIn, AssertionOut, AssertionPatch, NoteIn, NoteOut, ProjectIn, ProjectOut, SearchIn, SearchOut,
    RepositoryIn, RepositoryOut, SourceIn, SourceOut, TagIn, TagObjectIn, TagOut,
    TaskIn, TaskOut, TaskPatch,
)

router = APIRouter(prefix="/research", tags=["research"])
from .runs import router as runs_router
router.include_router(runs_router)
_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


@router.get("/repositories", response_model=list[RepositoryOut])
async def list_repositories(db: AsyncSession = Depends(get_tenant_db)) -> list[RepositoryOut]:
    return await service.list_repositories(db)


@router.post("/repositories", response_model=RepositoryOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_WRITE))])
async def create_repository(body: RepositoryIn, principal: Principal = Depends(get_current_principal),
                            db: AsyncSession = Depends(get_tenant_db)) -> RepositoryOut:
    async with audited(db, principal, action="repository_create", entity_type="repository", summary=f"Añadió el repositorio {body.name}"):
        row = await service.create_repository(db, principal.tenant_id, body)
    return service.repository_out(row)


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_tenant_db)) -> list[SourceOut]:
    return await service.list_sources(db)


@router.post("/sources", response_model=SourceOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_WRITE))])
async def create_source(body: SourceIn, principal: Principal = Depends(get_current_principal),
                        db: AsyncSession = Depends(get_tenant_db)) -> SourceOut:
    async with audited(db, principal, action="source_create", entity_type="source", summary=f"Añadió la fuente {body.title}"):
        row = await service.create_source(db, principal.tenant_id, body)
    return service.source_out(row)


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(db: AsyncSession = Depends(get_tenant_db)) -> list[ProjectOut]:
    return await service.list_projects(db)


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_WRITE))])
async def create_project(body: ProjectIn, principal: Principal = Depends(get_current_principal),
                         db: AsyncSession = Depends(get_tenant_db)) -> ProjectOut:
    async with audited(db, principal, action="research_project_create", entity_type="project", summary=f"Creó el proyecto {body.title}"):
        row = await service.create_project(db, principal.tenant_id, principal.user_id, body)
    return ProjectOut(id=row.id, title=row.title, description=row.description, question=row.question,
                      target_person_id=row.target_person_id, conclusion=row.conclusion,
                      status=row.status, created_by=row.created_by)


@router.get("/projects/{project_id}/searches", response_model=list[SearchOut])
async def list_searches(project_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> list[SearchOut]:
    return await service.list_searches(db, project_id)


@router.post("/projects/{project_id}/searches", response_model=SearchOut,
             status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles(*_WRITE))])
async def create_search(project_id: uuid.UUID, body: SearchIn,
                        principal: Principal = Depends(get_current_principal),
                        db: AsyncSession = Depends(get_tenant_db)) -> SearchOut:
    async with audited(db, principal, action="research_search_create", entity_type="project",
                       entity_id=project_id, summary="Documentó una búsqueda de investigación"):
        row = await service.create_search(db, principal.tenant_id, project_id, body)
    return service.search_out(row)


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(project_id: uuid.UUID | None = None, status: str | None = Query(default=None),
                     db: AsyncSession = Depends(get_tenant_db)) -> list[TaskOut]:
    return await service.list_tasks(db, project_id, status)


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_WRITE))])
async def create_task(body: TaskIn, principal: Principal = Depends(get_current_principal),
                      db: AsyncSession = Depends(get_tenant_db)) -> TaskOut:
    async with audited(db, principal, action="research_task_create", entity_type="task", summary=f"Creó la tarea {body.title}"):
        row = await service.create_task(db, principal.tenant_id, principal.user_id, body)
    return service.task_out(row)


@router.patch("/tasks/{task_id}", response_model=TaskOut, dependencies=[Depends(require_roles(*_WRITE))])
async def patch_task(task_id: uuid.UUID, body: TaskPatch, principal: Principal = Depends(get_current_principal),
                     db: AsyncSession = Depends(get_tenant_db)) -> TaskOut:
    async with audited(db, principal, action="research_task_update", entity_type="task", entity_id=task_id, summary="Actualizó una tarea de investigación"):
        row = await service.patch_task(db, task_id, body)
    return service.task_out(row)


@router.get("/assertions", response_model=list[AssertionOut])
async def list_assertions(subject_type: str | None = None, subject_id: uuid.UUID | None = None,
                          db: AsyncSession = Depends(get_tenant_db)) -> list[AssertionOut]:
    return await service.list_assertions(db, subject_type, subject_id)


@router.post("/assertions", response_model=AssertionOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_WRITE))])
async def create_assertion(body: AssertionIn, principal: Principal = Depends(get_current_principal),
                           db: AsyncSession = Depends(get_tenant_db)) -> AssertionOut:
    async with audited(db, principal, action="assertion_create", entity_type="assertion", summary=f"Propuso la afirmación {body.predicate}"):
        row = await service.create_assertion(db, principal.tenant_id, principal.user_id, body)
    return service.assertion_out(row)


@router.patch("/assertions/{assertion_id}", response_model=AssertionOut,
              dependencies=[Depends(require_roles(*_WRITE))])
async def patch_assertion(assertion_id: uuid.UUID, body: AssertionPatch, principal: Principal = Depends(get_current_principal),
                          db: AsyncSession = Depends(get_tenant_db)) -> AssertionOut:
    async with audited(db, principal, action="assertion_update", entity_type="assertion", entity_id=assertion_id, summary="Actualizó una afirmación"):
        row = await service.patch_assertion(db, assertion_id, body)
    return service.assertion_out(row)


@router.get("/notes", response_model=list[NoteOut])
async def list_notes(subject_type: str, subject_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> list[NoteOut]:
    return await service.list_notes(db, subject_type, subject_id)


@router.post("/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_WRITE))])
async def create_note(body: NoteIn, principal: Principal = Depends(get_current_principal),
                      db: AsyncSession = Depends(get_tenant_db)) -> NoteOut:
    async with audited(db, principal, action="research_note_create", entity_type="note", summary="Añadió una nota de investigación"):
        row = await service.create_note(db, principal.tenant_id, body)
    return NoteOut(id=row.id, subject_type=row.subject_type, subject_id=row.subject_id, text=row.text, format=row.format)


@router.get("/tags", response_model=list[TagOut])
async def list_tags(db: AsyncSession = Depends(get_tenant_db)) -> list[TagOut]:
    return await service.list_tags(db)


@router.post("/tags", response_model=TagOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_roles(*_WRITE))])
async def create_tag(body: TagIn, principal: Principal = Depends(get_current_principal),
                     db: AsyncSession = Depends(get_tenant_db)) -> TagOut:
    async with audited(db, principal, action="research_tag_create", entity_type="tag", summary=f"Creó la etiqueta {body.name}"):
        row = await service.create_tag(db, principal.tenant_id, body)
    return TagOut(id=row.id, name=row.name, color=row.color)


@router.post("/tags/{tag_id}/objects", status_code=status.HTTP_204_NO_CONTENT,
             dependencies=[Depends(require_roles(*_WRITE))])
async def tag_object(tag_id: uuid.UUID, body: TagObjectIn, principal: Principal = Depends(get_current_principal),
                     db: AsyncSession = Depends(get_tenant_db)) -> None:
    async with audited(db, principal, action="research_tag_apply", entity_type=body.target_type, entity_id=body.target_id, summary="Aplicó una etiqueta"):
        await service.tag_object(db, principal.tenant_id, tag_id, body.target_type, body.target_id)
