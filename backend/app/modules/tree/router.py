from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from . import editing, exporter, research, service
from .schemas import (
    CitationOut, DuplicatePair, ImportResult, PersonDetail, SearchHit, TreeGraph, TreeStats,
)

router = APIRouter(prefix="/tree", tags=["tree"])

_WRITE = (MembershipRole.tenant_admin.value, MembershipRole.researcher.value)


class PersonIn(BaseModel):
    given: str | None = None
    surname: str | None = None
    sex: str = "U"


class PersonPatch(BaseModel):
    sex: str | None = None
    given: str | None = None
    surname: str | None = None
    surname_prefix: str | None = None
    nickname: str | None = None
    notes: str | None = None


class EventIn(BaseModel):
    type: str
    date_raw: str | None = None
    place: str | None = None
    place_lat: float | None = None
    place_lng: float | None = None
    value: str | None = None


class RelativeIn(BaseModel):
    relation: str  # father | mother | parent | spouse | child
    relative_id: uuid.UUID | None = None  # link existing…
    given: str | None = None              # …or create new
    surname: str | None = None
    sex: str = "U"


@router.get("/fact-types")
async def fact_types() -> list[dict]:
    """GEDCOM-standard fact/event types for the edit UI's dropdown."""
    return editing.FACT_TYPES


@router.post("/persons", dependencies=[Depends(require_roles(*_WRITE))])
async def create_person(
    body: PersonIn, principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    p = await editing.create_person(db, principal.tenant_id, given=body.given, surname=body.surname, sex=body.sex)
    return {"id": str(p.id)}


@router.patch("/persons/{person_id}", response_model=PersonDetail, dependencies=[Depends(require_roles(*_WRITE))])
async def update_person(
    person_id: uuid.UUID, body: PersonPatch,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> PersonDetail:
    await editing.update_person(db, principal.tenant_id, person_id, sex=body.sex, given=body.given,
                                surname=body.surname, surname_prefix=body.surname_prefix,
                                nickname=body.nickname, notes=body.notes)
    return await service.get_person_detail(db, person_id)


@router.post("/persons/{person_id}/events", dependencies=[Depends(require_roles(*_WRITE))])
async def add_event(
    person_id: uuid.UUID, body: EventIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    ev = await editing.add_event(db, principal.tenant_id, person_id, type=body.type, date_raw=body.date_raw,
                                 place=body.place, place_lat=body.place_lat, place_lng=body.place_lng, value=body.value)
    return {"id": str(ev.id)}


@router.patch("/events/{event_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def edit_event(
    event_id: uuid.UUID, body: EventIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    ev = await editing.edit_event(db, principal.tenant_id, event_id, type=body.type,
                                  date_raw=body.date_raw, place=body.place, place_lat=body.place_lat,
                                  place_lng=body.place_lng, value=body.value)
    return {"id": str(ev.id)}


@router.delete("/events/{event_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def delete_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> dict:
    await editing.delete_event(db, event_id)
    return {"deleted": str(event_id)}


@router.post("/persons/{person_id}/relatives", dependencies=[Depends(require_roles(*_WRITE))])
async def add_relative(
    person_id: uuid.UUID, body: RelativeIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    if body.relative_id:
        await editing.link_relative(db, principal.tenant_id, person_id, body.relative_id, body.relation)
        return {"id": str(body.relative_id)}
    rel = await editing.add_relative(db, principal.tenant_id, person_id, relation=body.relation,
                                     given=body.given, surname=body.surname, sex=body.sex)
    return {"id": str(rel.id)}


@router.delete("/persons/{person_id}/relatives/{relative_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def unlink_relative(
    person_id: uuid.UUID, relative_id: uuid.UUID, relation: str = Query(...),
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    await editing.unlink_relative(db, principal.tenant_id, person_id, relative_id, relation)
    return {"unlinked": str(relative_id)}


@router.delete("/persons/{person_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def delete_person(
    person_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    await editing.delete_person(db, principal.tenant_id, person_id)
    return {"deleted": str(person_id)}


class MergeIn(BaseModel):
    dup_id: uuid.UUID


@router.get("/duplicates", response_model=list[DuplicatePair])
async def duplicates(
    limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_tenant_db),
) -> list[DuplicatePair]:
    """Suggested duplicate persons in the tree (phonetic blocking + name/date scoring)."""
    return await service.find_duplicates(db, limit)


@router.post("/persons/{keep_id}/merge", response_model=PersonDetail, dependencies=[Depends(require_roles(*_WRITE))])
async def merge_persons(
    keep_id: uuid.UUID, body: MergeIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> PersonDetail:
    await editing.merge_persons(db, principal.tenant_id, keep_id, body.dup_id)
    return await service.get_person_detail(db, keep_id)


@router.post(
    "/import/gedcom",
    response_model=ImportResult,
    dependencies=[Depends(require_roles(*_WRITE))],
)
async def import_gedcom(
    file: UploadFile = File(...),
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> ImportResult:
    data = await file.read()
    return await service.import_gedcom_file(
        db, principal.tenant_id, principal.user_id, file.filename, data
    )


@router.post("/geocode-places", dependencies=[Depends(require_roles(*_WRITE))])
async def geocode_places(
    limit: int = Query(40, ge=1, le=200), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Resolve coordinates for tree places that lack them (for the life map). Rate-limited."""
    return await service.geocode_places(db, limit)


class HomeOut(BaseModel):
    person_id: uuid.UUID | None


class HomeIn(BaseModel):
    person_id: uuid.UUID | None = None


@router.get("/home", response_model=HomeOut)
async def get_home(
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> HomeOut:
    return HomeOut(person_id=await service.get_home(db, principal.tenant_id))


@router.put("/home", response_model=HomeOut, dependencies=[Depends(require_roles(*_WRITE))])
async def set_home(
    body: HomeIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> HomeOut:
    await service.set_home(db, principal.tenant_id, body.person_id)
    return HomeOut(person_id=body.person_id)


@router.get("/stats", response_model=TreeStats)
async def stats(db: AsyncSession = Depends(get_tenant_db)) -> TreeStats:
    return await service.get_stats(db)


@router.get("/persons/search", response_model=list[SearchHit])
async def search(
    q: str | None = Query(None),
    given: str | None = Query(None),
    surname: str | None = Query(None),
    year_from: int | None = Query(None),
    year_to: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[SearchHit]:
    return await service.search_persons(
        db, q, limit, given=given, surname=surname, year_from=year_from, year_to=year_to)


@router.get("/roots", response_model=list[SearchHit])
async def roots(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[SearchHit]:
    return await service.get_roots(db, limit)


@router.get("/persons/{person_id}", response_model=PersonDetail)
async def person_detail(
    person_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)
) -> PersonDetail:
    return await service.get_person_detail(db, person_id)


@router.get("/persons/{person_id}/gaps")
async def person_gaps(
    person_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[dict]:
    """Research suggestions: which source would likely fill a hole (missing parent, birth, marriage)."""
    return await research.get_gaps(db, principal.tenant_id, person_id)


@router.get("/persons/{person_id}/citations", response_model=list[CitationOut])
async def person_citations(
    person_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)
) -> list[CitationOut]:
    """The source evidence behind a person — for the ficha's 'Fuentes' tab."""
    return await service.get_person_citations(db, person_id)


@router.get("/persons/{person_id}/subtree", response_model=TreeGraph)
async def subtree(
    person_id: uuid.UUID,
    depth: int = Query(3, ge=1, le=6),
    db: AsyncSession = Depends(get_tenant_db),
) -> TreeGraph:
    return await service.get_subgraph(db, person_id, depth)


@router.get("/export/gedcom")
async def export_gedcom(
    principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> PlainTextResponse:
    text = await exporter.export_gedcom(db, principal.tenant_id)
    return PlainTextResponse(
        text, headers={"Content-Disposition": 'attachment; filename="gen_suite_export.ged"'}
    )
