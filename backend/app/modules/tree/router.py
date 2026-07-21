from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from ...core.deps import get_current_principal, get_tenant_db, require_roles
from ...core.security import Principal
from ...models.membership import MembershipRole
from . import audit, citations, editing, exporter, kinship, places, research, service
from .audit import audited
from .schemas import (
    ChangeDetail, ChangePage, CitationIn, CitationOut, CitationPatch, DuplicatePair,
    FamilyOut, ImportResult, PersonDetail, PersonPage, PlaceDetail, PlaceEventsPage,
    PlacePage, PlacePatch, RelationshipOut, SearchHit, TreeGraph, TreeStats,
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
    """GEDCOM-standard fact/event types for the edit UI's dropdowns, tagged by scope."""
    return ([{**t, "scope": "person"} for t in editing.FACT_TYPES]
            + [{**t, "scope": "family"} for t in editing.FAMILY_FACT_TYPES])


@router.post("/persons", dependencies=[Depends(require_roles(*_WRITE))])
async def create_person(
    body: PersonIn, principal: Principal = Depends(get_current_principal),
    db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    label = " ".join(x for x in (body.given, body.surname) if x) or "(sin nombre)"
    async with audited(db, principal, action="person_create", entity_type="person",
                       summary=f"Creó a {label}"):
        p = await editing.create_person(db, principal.tenant_id, given=body.given,
                                        surname=body.surname, sex=body.sex)
    return {"id": str(p.id)}


@router.patch("/persons/{person_id}", response_model=PersonDetail, dependencies=[Depends(require_roles(*_WRITE))])
async def update_person(
    person_id: uuid.UUID, body: PersonPatch,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> PersonDetail:
    async with audited(db, principal, action="person_update", entity_type="person",
                       entity_id=person_id, summary="Editó la identidad de una persona"):
        await editing.update_person(db, principal.tenant_id, person_id, sex=body.sex, given=body.given,
                                    surname=body.surname, surname_prefix=body.surname_prefix,
                                    nickname=body.nickname, notes=body.notes)
    return await service.get_person_detail(db, person_id)


@router.post("/persons/{person_id}/events", dependencies=[Depends(require_roles(*_WRITE))])
async def add_event(
    person_id: uuid.UUID, body: EventIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    async with audited(db, principal, action="event_add", entity_type="person",
                       entity_id=person_id, summary=f"Añadió un hecho ({body.type})"):
        ev = await editing.add_event(db, principal.tenant_id, person_id, type=body.type, date_raw=body.date_raw,
                                     place=body.place, place_lat=body.place_lat, place_lng=body.place_lng, value=body.value)
    return {"id": str(ev.id)}


@router.patch("/events/{event_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def edit_event(
    event_id: uuid.UUID, body: EventIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    async with audited(db, principal, action="event_edit", entity_type="event",
                       entity_id=event_id, summary=f"Editó un hecho ({body.type})"):
        ev = await editing.edit_event(db, principal.tenant_id, event_id, type=body.type,
                                      date_raw=body.date_raw, place=body.place, place_lat=body.place_lat,
                                      place_lng=body.place_lng, value=body.value)
    return {"id": str(ev.id)}


@router.delete("/events/{event_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def delete_event(
    event_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    async with audited(db, principal, action="event_delete", entity_type="event",
                       entity_id=event_id, summary="Borró un hecho"):
        await editing.delete_event(db, event_id)
    return {"deleted": str(event_id)}


@router.post("/persons/{person_id}/relatives", dependencies=[Depends(require_roles(*_WRITE))])
async def add_relative(
    person_id: uuid.UUID, body: RelativeIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    async with audited(db, principal, action="relative_add", entity_type="person",
                       entity_id=person_id, summary=f"Añadió un pariente ({body.relation})"):
        if body.relative_id:
            await editing.link_relative(db, principal.tenant_id, person_id, body.relative_id, body.relation)
            return {"id": str(body.relative_id)}
        rel = await editing.add_relative(db, principal.tenant_id, person_id, relation=body.relation,
                                         given=body.given, surname=body.surname, sex=body.sex)
    return {"id": str(rel.id)}


@router.get("/persons/{person_id}/families", response_model=list[FamilyOut])
async def person_families(
    person_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db),
) -> list[FamilyOut]:
    """Las familias (parejas) de una persona, con sus hechos compartidos."""
    return await service.get_person_families(db, person_id)


@router.post("/families/{family_id}/events", dependencies=[Depends(require_roles(*_WRITE))])
async def add_family_event(
    family_id: uuid.UUID, body: EventIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Añade un hecho de pareja (matrimonio, divorcio…) sobre la familia, como en GEDCOM."""
    async with audited(db, principal, action="family_event_add", entity_type="family",
                       entity_id=family_id, summary=f"Añadió un hecho de pareja ({body.type})"):
        ev = await editing.add_family_event(db, principal.tenant_id, family_id, type=body.type,
                                            date_raw=body.date_raw, place=body.place,
                                            place_lat=body.place_lat, place_lng=body.place_lng,
                                            value=body.value)
    return {"id": str(ev.id)}


@router.post("/citations", dependencies=[Depends(require_roles(*_WRITE))])
async def create_citation(
    body: CitationIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Cita manual: vincula una persona o un hecho con un documento/página de la biblioteca."""
    async with audited(db, principal, action="citation_add", entity_type="citation",
                       summary="Añadió una fuente"):
        cit = await citations.create_citation(
            db, principal.tenant_id, target_type=body.target_type, target_id=body.target_id,
            document_id=body.document_id, page_no=body.page_no, note=body.note)
    return {"id": str(cit.id)}


@router.patch("/citations/{citation_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def update_citation(
    citation_id: uuid.UUID, body: CitationPatch,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    async with audited(db, principal, action="citation_update", entity_type="citation",
                       entity_id=citation_id, summary="Editó una fuente"):
        cit = await citations.update_citation(db, citation_id, document_id=body.document_id,
                                              page_no=body.page_no, note=body.note)
    return {"id": str(cit.id)}


@router.delete("/citations/{citation_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def delete_citation(
    citation_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    async with audited(db, principal, action="citation_delete", entity_type="citation",
                       entity_id=citation_id, summary="Borró una fuente"):
        await citations.delete_citation(db, citation_id)
    return {"deleted": str(citation_id)}


@router.delete("/persons/{person_id}/relatives/{relative_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def unlink_relative(
    person_id: uuid.UUID, relative_id: uuid.UUID, relation: str = Query(...),
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    async with audited(db, principal, action="relative_unlink", entity_type="person",
                       entity_id=person_id, summary=f"Desvinculó un pariente ({relation})"):
        await editing.unlink_relative(db, principal.tenant_id, person_id, relative_id, relation)
    return {"unlinked": str(relative_id)}


@router.delete("/persons/{person_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def delete_person(
    person_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    async with audited(db, principal, action="person_delete", entity_type="person",
                       entity_id=person_id, summary="Eliminó una persona (con sus nombres, hechos y parentescos)"):
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
    async with audited(db, principal, action="person_merge", entity_type="person",
                       entity_id=keep_id, summary="Fusionó dos personas duplicadas"):
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


@router.get("/persons", response_model=PersonPage)
async def list_persons(
    q: str | None = Query(None),
    surname: str | None = Query(None),
    sort: str = Query("name", pattern="^(name|birth|death)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
) -> PersonPage:
    """Directorio de personas del árbol: paginado, ordenable y filtrable (vista Lista)."""
    return await service.list_persons(
        db, q=q, surname=surname, sort=sort, order=order, page=page, page_size=page_size)


@router.get("/relationship", response_model=RelationshipOut)
async def relationship(
    a: uuid.UUID = Query(...), b: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_tenant_db),
) -> RelationshipOut:
    """Parentesco entre dos personas: etiqueta en español + cadena de pasos (calculadora)."""
    return await kinship.get_relationship(db, a, b)


class MergePlaceIn(BaseModel):
    into_id: uuid.UUID


@router.get("/changes", response_model=ChangePage)
async def list_changes(
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
) -> ChangePage:
    """Historial de cambios del árbol (quién, qué, cuándo)."""
    return await audit.list_changes(db, page=page, page_size=page_size)


@router.get("/changes/{change_id}", response_model=ChangeDetail)
async def change_detail(change_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> ChangeDetail:
    """Detalle de un cambio con sus filas antes/después (para pintar el diff)."""
    return await audit.get_change(db, change_id)


@router.post("/changes/{change_id}/revert", dependencies=[Depends(require_roles(*_WRITE))])
async def revert_change(
    change_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Deshace un cambio aplicando su inversa (todo o nada; 409 si los datos ya cambiaron)."""
    change = await audit.revert_change(db, principal, change_id)
    return {"reverted": str(change.id)}


@router.get("/places", response_model=PlacePage)
async def list_places(
    q: str | None = Query(None),
    sort: str = Query("name", pattern="^(name|events)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
) -> PlacePage:
    """Directorio de lugares del árbol con nº de eventos, padre y nº de hijos."""
    return await places.list_places(db, q=q, sort=sort, order=order, page=page, page_size=page_size)


@router.get("/places/{place_id}", response_model=PlaceDetail)
async def place_detail(place_id: uuid.UUID, db: AsyncSession = Depends(get_tenant_db)) -> PlaceDetail:
    return await places.get_place(db, place_id)


@router.get("/places/{place_id}/events", response_model=PlaceEventsPage)
async def place_events(
    place_id: uuid.UUID, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
) -> PlaceEventsPage:
    return await places.list_place_events(db, place_id, page=page, page_size=page_size)


@router.patch("/places/{place_id}", dependencies=[Depends(require_roles(*_WRITE))])
async def update_place(
    place_id: uuid.UUID, body: PlacePatch,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Renombrar, tipar, mover en la jerarquía o fijar coordenadas de un lugar."""
    async with audited(db, principal, action="place_update", entity_type="place",
                       entity_id=place_id, summary="Editó un lugar"):
        pl = await places.update_place(db, place_id, name=body.name, place_type=body.place_type,
                                       parent_id=body.parent_id, clear_parent=body.clear_parent,
                                       lat=body.lat, lng=body.lng)
    return {"id": str(pl.id)}


@router.post("/places/{place_id}/merge", dependencies=[Depends(require_roles(*_WRITE))])
async def merge_place(
    place_id: uuid.UUID, body: MergePlaceIn,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Fusiona un lugar duplicado dentro de otro (los eventos e hijos se repuntan)."""
    async with audited(db, principal, action="place_merge", entity_type="place",
                       entity_id=body.into_id, summary="Fusionó dos lugares"):
        pl = await places.merge_place(db, place_id, body.into_id)
    return {"id": str(pl.id)}


@router.post("/places/{place_id}/geocode", dependencies=[Depends(require_roles(*_WRITE))])
async def geocode_place(
    place_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal), db: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Geocodifica el lugar usando su jerarquía como contexto (name, padre, país)."""
    async with audited(db, principal, action="place_geocode", entity_type="place",
                       entity_id=place_id, summary="Geocodificó un lugar"):
        pl = await places.geocode_place(db, place_id)
    return {"id": str(pl.id), "lat": pl.lat, "lng": pl.lng}


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
