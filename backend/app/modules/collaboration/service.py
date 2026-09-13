from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.collaboration import ChangeProposal, Publication
from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.person import Name, Person
from ..research import service as research_service
from ..research.schemas import AssertionIn, NoteIn, SourceIn, TaskIn
from ..tree import editing
from .schemas import ProposalIn, ProposalOut, PublicationCreated, PublicationIn, PublicationOut


def proposal_out(row: ChangeProposal) -> ProposalOut:
    return ProposalOut(
        id=row.id, operation=row.operation, patch=row.patch, target_type=row.target_type,
        target_id=row.target_id, rationale=row.rationale, status=row.status,
        submitted_by=row.submitted_by, reviewed_by=row.reviewed_by,
        reviewer_note=row.reviewer_note, reviewed_at=row.reviewed_at,
    )


async def create_proposal(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, body: ProposalIn
) -> ChangeProposal:
    if body.operation not in {
        "create_person", "update_person", "add_event", "link_relative", "create_source",
        "create_assertion", "create_task", "create_note",
    }:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "operación de propuesta no compatible")
    row = ChangeProposal(tenant_id=tenant_id, submitted_by=user_id, **body.model_dump())
    session.add(row)
    await session.flush()
    return row


async def list_proposals(session: AsyncSession, proposal_status: str | None) -> list[ProposalOut]:
    query = select(ChangeProposal).order_by(ChangeProposal.created_at.desc())
    if proposal_status:
        if proposal_status not in {"pending", "accepted", "rejected", "withdrawn"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "estado de propuesta inválido")
        query = query.where(ChangeProposal.status == proposal_status)
    return [proposal_out(row) for row in (await session.scalars(query)).all()]


async def review_proposal(
    session: AsyncSession, proposal_id: uuid.UUID, reviewer_id: uuid.UUID, *, review_status: str,
    reviewer_note: str | None,
) -> ChangeProposal:
    if review_status not in {"accepted", "rejected"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "estado de revisión inválido")
    row = await session.get(ChangeProposal, proposal_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "propuesta no encontrada")
    if row.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "la propuesta ya fue resuelta")
    if row.submitted_by == reviewer_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "otra persona debe revisar la propuesta")
    if review_status == "accepted":
        await _apply_proposal(session, row, reviewer_id)
    row.status, row.reviewed_by = review_status, reviewer_id
    row.reviewer_note = (reviewer_note or "").strip() or None
    row.reviewed_at = datetime.now(timezone.utc)
    await session.flush()
    return row


def _proposal_target(row: ChangeProposal, key: str = "target_id") -> uuid.UUID:
    value = row.target_id or row.patch.get(key)
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "la propuesta no tiene un destino válido") from exc


async def _apply_proposal(session: AsyncSession, row: ChangeProposal, reviewer_id: uuid.UUID) -> None:
    """Apply a whitelisted proposal as an inferred, fully audited tree/research mutation.

    The router wraps this call in ``audited`` so approval and the concrete rows land in one
    ChangeLog entry. There is intentionally no generic JSON-patch executor here.
    """
    patch = row.patch
    try:
        if row.operation == "create_person":
            person = await editing.create_person(
                session, row.tenant_id, given=patch.get("given"), surname=patch.get("surname"),
                sex=patch.get("sex", "U"), inferred=True,
            )
            row.target_type, row.target_id = "person", person.id
            return
        if row.operation == "update_person":
            person_id = _proposal_target(row)
            allowed = {key: patch.get(key) for key in (
                "sex", "given", "surname", "surname_prefix", "nickname", "notes", "privacy_status"
            ) if key in patch}
            if not allowed:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "la propuesta no contiene cambios")
            await editing.update_person(session, row.tenant_id, person_id, **allowed)
            row.target_type, row.target_id = "person", person_id
            return
        if row.operation == "add_event":
            person_id = _proposal_target(row, "person_id")
            if not patch.get("type"):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "falta el tipo de hecho")
            event = await editing.add_event(
                session, row.tenant_id, person_id, type=patch["type"], date_raw=patch.get("date_raw"),
                place=patch.get("place"), place_lat=patch.get("place_lat"), place_lng=patch.get("place_lng"),
                value=patch.get("value"), inferred=True,
            )
            row.target_type, row.target_id = "event", event.id
            return
        if row.operation == "link_relative":
            person_id = _proposal_target(row, "person_id")
            relative_id = patch.get("relative_id")
            if not relative_id or not patch.get("relation"):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "faltan los datos de parentesco")
            try:
                relative_uuid = uuid.UUID(str(relative_id))
            except ValueError as exc:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "pariente inválido") from exc
            await editing.link_relative(session, row.tenant_id, person_id, relative_uuid, patch["relation"])
            row.target_type, row.target_id = "person", person_id
            return
        if row.operation == "create_source":
            source = await research_service.create_source(session, row.tenant_id, SourceIn.model_validate(patch))
            row.target_type, row.target_id = "source", source.id
            return
        actor_id = row.submitted_by or reviewer_id
        if row.operation == "create_assertion":
            assertion = await research_service.create_assertion(
                session, row.tenant_id, actor_id, AssertionIn.model_validate(patch)
            )
            row.target_type, row.target_id = "assertion", assertion.id
            return
        if row.operation == "create_task":
            task = await research_service.create_task(session, row.tenant_id, actor_id, TaskIn.model_validate(patch))
            row.target_type, row.target_id = "task", task.id
            return
        if row.operation == "create_note":
            note = await research_service.create_note(session, row.tenant_id, NoteIn.model_validate(patch))
            row.target_type, row.target_id = "note", note.id
            return
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "los datos de la propuesta no son válidos") from exc
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "operación de propuesta no compatible")


async def withdraw_proposal(session: AsyncSession, proposal_id: uuid.UUID, user_id: uuid.UUID) -> ChangeProposal:
    row = await session.get(ChangeProposal, proposal_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "propuesta no encontrada")
    if row.submitted_by != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "solo quien propuso el cambio puede retirarlo")
    if row.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "la propuesta ya fue resuelta")
    row.status = "withdrawn"
    await session.flush()
    return row


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:72] or "arbol"
    return f"{normalized}-{secrets.token_urlsafe(6).lower()}"


def publication_out(row: Publication) -> PublicationOut:
    return PublicationOut(
        id=row.id, title=row.title, slug=row.slug, status=row.status, expires_at=row.expires_at,
        published_at=row.published_at, share_path=f"/p/{row.slug}",
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _public_payload(session: AsyncSession, person_ids: list[uuid.UUID]) -> dict:
    requested = set(person_ids)
    persons = (await session.scalars(select(Person).where(Person.id.in_(requested)))).all()
    if len(persons) != len(requested):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "una persona seleccionada no existe")
    visible = {p.id for p in persons if p.privacy_status in {"deceased", "public"}}
    if not visible:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "no hay personas publicables; marca los registros históricos como fallecidos o públicos",
        )
    names = {row.person_id: row for row in (await session.scalars(
        select(Name).where(Name.person_id.in_(visible), Name.is_primary.is_(True))
    )).all()}
    events = (await session.scalars(select(Event).where(Event.subject_person_id.in_(visible)))).all()
    by_person: dict[uuid.UUID, list[dict]] = {pid: [] for pid in visible}
    for event in events:
        by_person[event.subject_person_id].append({
            "type": event.type, "date_raw": event.date_raw, "date_year": event.date_year, "value": event.value,
        })
    public_people = []
    for person in persons:
        if person.id not in visible:
            continue
        name = names.get(person.id)
        public_people.append({
            "id": str(person.id), "given": name.given if name else None,
            "surname": name.surname if name else None, "sex": person.sex, "events": by_person[person.id],
        })

    # A family relation can expose a redacted person. Include an edge only if all its members are
    # in the explicit, public selection.
    families = (await session.scalars(select(Family).where(
        Family.husband_id.in_(visible) | Family.wife_id.in_(visible)
    ))).all()
    family_rows: list[dict] = []
    for family in families:
        children = list((await session.scalars(
            select(FamilyChild.person_id).where(FamilyChild.family_id == family.id)
        )).all())
        members = {member for member in (family.husband_id, family.wife_id, *children) if member}
        if members and members <= visible:
            family_rows.append({
                "id": str(family.id), "husband_id": str(family.husband_id) if family.husband_id else None,
                "wife_id": str(family.wife_id) if family.wife_id else None,
                "child_ids": [str(child) for child in children],
            })
    return {
        "schema": "gen-suite-public-tree/v1", "persons": public_people, "families": family_rows,
        "redacted_people": len(requested - visible),
    }


async def create_publication(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, body: PublicationIn
) -> PublicationCreated:
    # Capabilities are intentionally short-lived unless a workspace explicitly
    # refreshes/re-publishes them; unlisted does not mean harmless if forwarded.
    expires_at = _aware(body.expires_at) or (datetime.now(timezone.utc) + timedelta(days=30))
    if expires_at and expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "la caducidad debe estar en el futuro")
    token = secrets.token_urlsafe(32)
    row = Publication(
        tenant_id=tenant_id, title=body.title.strip(), slug=_slug(body.title),
        access_token_hash=_hash(token), expires_at=expires_at, created_by=user_id,
        person_ids=[str(person_id) for person_id in body.person_ids],
        payload=await _public_payload(session, body.person_ids),
    )
    session.add(row)
    await session.flush()
    return PublicationCreated(**publication_out(row).model_dump(), access_token=token)


async def list_publications(session: AsyncSession) -> list[PublicationOut]:
    return [publication_out(row) for row in (await session.scalars(
        select(Publication).order_by(Publication.created_at.desc())
    )).all()]


async def publish_publication(session: AsyncSession, publication_id: uuid.UUID) -> Publication:
    row = await session.get(Publication, publication_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "publicación no encontrada")
    if row.status == "revoked":
        raise HTTPException(status.HTTP_409_CONFLICT, "una publicación revocada debe crearse de nuevo")
    if row.expires_at and row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_409_CONFLICT, "la publicación ya ha caducado")
    try:
        selected = [uuid.UUID(str(person_id)) for person_id in row.person_ids]
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "la publicación no conserva una selección válida") from exc
    row.payload = await _public_payload(session, selected)
    row.status, row.published_at = "published", datetime.now(timezone.utc)
    await session.flush()
    return row


async def refresh_publication(session: AsyncSession, publication_id: uuid.UUID) -> Publication:
    row = await session.get(Publication, publication_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "publicación no encontrada")
    if row.status == "revoked":
        raise HTTPException(status.HTTP_409_CONFLICT, "una publicación revocada debe crearse de nuevo")
    try:
        selected = [uuid.UUID(str(person_id)) for person_id in row.person_ids]
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "la publicación no conserva una selección válida") from exc
    row.payload = await _public_payload(session, selected)
    await session.flush()
    return row


async def revoke_publication(session: AsyncSession, publication_id: uuid.UUID) -> Publication:
    row = await session.get(Publication, publication_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "publicación no encontrada")
    row.status = "revoked"
    await session.flush()
    return row


async def get_public_tree(session: AsyncSession, slug: str, token: str) -> Publication:
    row = await session.scalar(select(Publication).where(Publication.slug == slug))
    now = datetime.now(timezone.utc)
    if (
        not row
        or row.status != "published"
        or (row.expires_at and _aware(row.expires_at) <= now)
        or not hmac.compare_digest(row.access_token_hash, _hash(token))
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "enlace no encontrado")
    return row
