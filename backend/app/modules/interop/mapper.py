"""Projection between the lossless Gramps envelope and Gen Suite's native models.

The sync store is deliberately the source of truth for a peer's complete record.  This
module only projects the part that Gen Suite understands today; anything else remains
in :class:`SyncObject.payload` and is returned unchanged to the companion.  That keeps
an address, media reference, LDS ordinance or extension from disappearing merely
because the web application does not render it yet.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.citation import Citation
from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.interop import InteropConnection, SyncChange, SyncObject
from ...models.person import Name, Person
from ...models.place import Place
from ...models.research import Repository, Source
from ..tree.mapping import extract_year, normalize_place


class DependencyMissing(Exception):
    """The peer sent a valid reference before the object it refers to."""


def _text(value: object, size: int | None = None) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    if not result:
        return None
    return result[:size] if size else result


def _uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (TypeError, ValueError, AttributeError):
        return None


async def _peer_object(session: AsyncSession, connection_id: uuid.UUID, object_type: str, object_key: object) -> SyncObject | None:
    key = _text(object_key, 255)
    if not key:
        return None
    return await session.scalar(select(SyncObject).where(
        SyncObject.connection_id == connection_id,
        SyncObject.object_type == object_type,
        SyncObject.object_key == key,
    ))


async def _local_id(session: AsyncSession, connection_id: uuid.UUID, object_type: str, object_key: object) -> uuid.UUID | None:
    peer = await _peer_object(session, connection_id, object_type, object_key)
    if peer and peer.local_id and not peer.is_tombstone:
        return peer.local_id
    return None


async def _upsert_sync_object(session: AsyncSession, change: SyncChange) -> SyncObject:
    row = await _peer_object(session, change.connection_id, change.object_type, change.object_key)
    if not row:
        row = SyncObject(
            tenant_id=change.tenant_id, connection_id=change.connection_id,
            object_type=change.object_type, object_key=change.object_key,
        )
        session.add(row)
    row.payload = change.payload
    row.remote_revision = change.remote_revision
    row.is_tombstone = change.operation == "tombstone"
    await session.flush()
    return row


async def _project_person(session: AsyncSession, peer: SyncObject, payload: dict) -> None:
    person = await session.get(Person, peer.local_id) if peer.local_id else None
    names = payload.get("names") if isinstance(payload.get("names"), list) else []
    primary = next((n for n in names if isinstance(n, dict) and n.get("primary")), None)
    primary = primary or next((n for n in names if isinstance(n, dict)), {})
    given = _text(payload.get("given") if "given" in payload else primary.get("given"), 255)
    surname = _text(payload.get("surname") if "surname" in payload else primary.get("surname"), 255)
    sex = (_text(payload.get("sex"), 1) or "U").upper()
    sex = sex if sex in {"M", "F"} else "U"
    privacy = _text(payload.get("privacy_status"), 16) or "unknown"
    privacy = privacy if privacy in {"unknown", "living", "deceased", "public", "private"} else "unknown"
    if not person:
        person = Person(tenant_id=peer.tenant_id, sex=sex, notes=_text(payload.get("notes")),
                        privacy_status=privacy, raw=payload.get("raw"))
        session.add(person)
        await session.flush()
        peer.local_type, peer.local_id = "person", person.id
    else:
        person.sex, person.notes, person.privacy_status = sex, _text(payload.get("notes")), privacy
        person.raw = payload.get("raw")
    existing = await session.scalar(select(Name).where(Name.person_id == person.id).order_by(Name.is_primary.desc()))
    if not existing:
        session.add(Name(tenant_id=peer.tenant_id, person_id=person.id, type="birth", given=given,
                         surname=surname, is_primary=True))
    else:
        existing.given, existing.surname = given, surname
    await session.flush()


async def _project_name(session: AsyncSession, peer: SyncObject, payload: dict) -> None:
    """Project an independently-addressable Gramps name without collapsing aliases."""
    person_key = payload.get("person_key")
    person_id = await _local_id(session, peer.connection_id, "person", person_key)
    if person_key and not person_id:
        raise DependencyMissing("falta la persona del nombre")
    if not person_id:
        return
    row = await session.get(Name, peer.local_id) if peer.local_id else None
    if not row:
        row = Name(tenant_id=peer.tenant_id, person_id=person_id)
        session.add(row)
        await session.flush()
        peer.local_type, peer.local_id = "name", row.id
    row.person_id = person_id
    row.type = _text(payload.get("type"), 16) or "birth"
    row.given = _text(payload.get("given"), 255)
    row.surname = _text(payload.get("surname"), 255)
    row.surname_prefix = _text(payload.get("surname_prefix"), 64)
    row.nickname = _text(payload.get("nickname"), 128)
    row.is_inferred = bool(payload.get("is_inferred", False))
    row.is_primary = bool(payload.get("primary", payload.get("is_primary", False)))
    if row.is_primary:
        for other in (await session.scalars(select(Name).where(
            Name.person_id == person_id, Name.id != row.id, Name.is_primary.is_(True)
        ))).all():
            other.is_primary = False
    await session.flush()


async def _project_place(session: AsyncSession, peer: SyncObject, payload: dict) -> None:
    place = await session.get(Place, peer.local_id) if peer.local_id else None
    name = _text(payload.get("name") or payload.get("title"), 512)
    if not name:
        return  # valid Gramps extension: retain the envelope but do not invent a flat place name
    normalized = normalize_place(name)[:512]
    if not place:
        place = await session.scalar(select(Place).where(
            Place.tenant_id == peer.tenant_id, Place.normalized_key == normalized
        ))
    if not place:
        place = Place(tenant_id=peer.tenant_id, name=name, normalized_key=normalized)
        session.add(place)
        await session.flush()
    place.name = name
    place.lat = payload.get("lat") if isinstance(payload.get("lat"), (int, float)) else None
    place.lng = payload.get("lng") if isinstance(payload.get("lng"), (int, float)) else None
    place.place_type = _text(payload.get("place_type"), 24)
    if "parent_key" in payload:
        parent_id = await _local_id(session, peer.connection_id, "place", payload.get("parent_key"))
        if payload.get("parent_key") and not parent_id:
            raise DependencyMissing("falta el lugar superior")
        if parent_id == place.id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "un lugar no puede ser su propio superior")
        place.parent_id = parent_id
    peer.local_type, peer.local_id = "place", place.id
    await session.flush()


async def _project_repository(session: AsyncSession, peer: SyncObject, payload: dict) -> None:
    row = await session.get(Repository, peer.local_id) if peer.local_id else None
    name = _text(payload.get("name") or payload.get("title"), 255)
    if not name:
        return
    if not row:
        row = Repository(tenant_id=peer.tenant_id, name=name)
        session.add(row)
        await session.flush()
        peer.local_type, peer.local_id = "repository", row.id
    row.name, row.url = name, _text(payload.get("url"), 1024)
    row.address, row.notes = _text(payload.get("address")), _text(payload.get("notes"))
    await session.flush()


async def _project_source(session: AsyncSession, peer: SyncObject, payload: dict) -> None:
    row = await session.get(Source, peer.local_id) if peer.local_id else None
    title = _text(payload.get("title"), 512)
    if not title:
        return
    repository_id = await _local_id(session, peer.connection_id, "repository", payload.get("repository_key"))
    if payload.get("repository_key") and not repository_id:
        raise DependencyMissing("falta el repositorio de la fuente")
    if not row:
        row = Source(tenant_id=peer.tenant_id, title=title)
        session.add(row)
        await session.flush()
        peer.local_type, peer.local_id = "source", row.id
    row.title = title
    row.source_type = _text(payload.get("source_type"), 32) or "other"
    row.author = _text(payload.get("author"), 255)
    row.publication_info = _text(payload.get("publication_info"))
    row.call_number = _text(payload.get("call_number"), 255)
    row.url = _text(payload.get("url"), 1024)
    row.repository_id = repository_id
    row.raw = payload.get("raw")
    await session.flush()


async def _project_family(session: AsyncSession, peer: SyncObject, payload: dict) -> None:
    row = await session.get(Family, peer.local_id) if peer.local_id else None
    husband_key = payload.get("husband_key", payload.get("father_key"))
    wife_key = payload.get("wife_key", payload.get("mother_key"))
    husband_id = await _local_id(session, peer.connection_id, "person", husband_key)
    wife_id = await _local_id(session, peer.connection_id, "person", wife_key)
    if husband_key and not husband_id or wife_key and not wife_id:
        raise DependencyMissing("faltan personas de la familia")
    if not row:
        row = Family(tenant_id=peer.tenant_id)
        session.add(row)
        await session.flush()
        peer.local_type, peer.local_id = "family", row.id
    row.husband_id, row.wife_id, row.raw = husband_id, wife_id, payload.get("raw")
    child_keys = payload.get("child_keys", payload.get("children", []))
    if child_keys is None:
        child_keys = []
    if not isinstance(child_keys, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "los hijos de familia deben ser una lista")
    children: list[uuid.UUID] = []
    for key in child_keys:
        child_id = await _local_id(session, peer.connection_id, "person", key)
        if not child_id:
            raise DependencyMissing("falta un hijo de la familia")
        children.append(child_id)
    current = (await session.scalars(select(FamilyChild).where(FamilyChild.family_id == row.id))).all()
    for membership in current:
        if membership.person_id not in children:
            await session.delete(membership)
    for sequence, child_id in enumerate(children):
        membership = next((item for item in current if item.person_id == child_id), None)
        if membership:
            membership.seq = sequence
        else:
            session.add(FamilyChild(tenant_id=peer.tenant_id, family_id=row.id, person_id=child_id, seq=sequence))
    await session.flush()


async def _project_event(session: AsyncSession, peer: SyncObject, payload: dict) -> None:
    row = await session.get(Event, peer.local_id) if peer.local_id else None
    person_key, family_key = payload.get("person_key"), payload.get("family_key")
    person_id = await _local_id(session, peer.connection_id, "person", person_key)
    family_id = await _local_id(session, peer.connection_id, "family", family_key)
    if person_key and not person_id or family_key and not family_id:
        raise DependencyMissing("falta el sujeto del evento")
    if not person_id and not family_id:
        # A standalone event is still safely retained in the generic projection.
        return
    place_id = await _local_id(session, peer.connection_id, "place", payload.get("place_key"))
    if payload.get("place_key") and not place_id:
        raise DependencyMissing("falta el lugar del evento")
    if not row:
        row = Event(tenant_id=peer.tenant_id, type="event")
        session.add(row)
        await session.flush()
        peer.local_type, peer.local_id = "event", row.id
    date_raw = _text(payload.get("date_raw"), 128)
    row.type = _text(payload.get("type"), 24) or "event"
    row.date_raw, row.date_year = date_raw, extract_year(date_raw)
    row.value = _text(payload.get("value"))
    row.is_inferred = bool(payload.get("is_inferred", False))
    row.place_id, row.subject_person_id, row.subject_family_id = place_id, person_id, family_id
    await session.flush()


async def _project_citation(session: AsyncSession, peer: SyncObject, payload: dict) -> None:
    row = await session.get(Citation, peer.local_id) if peer.local_id else None
    target_type = _text(payload.get("target_type"), 16)
    target_key = payload.get("target_key")
    if not target_type or not target_key:
        return
    target_id = await _local_id(session, peer.connection_id, target_type, target_key)
    if not target_id:
        raise DependencyMissing("falta el objeto citado")
    source_id = await _local_id(session, peer.connection_id, "source", payload.get("source_key"))
    if payload.get("source_key") and not source_id:
        raise DependencyMissing("falta la fuente de la cita")
    if not row:
        row = Citation(tenant_id=peer.tenant_id, target_type=target_type, target_id=target_id)
        session.add(row)
        await session.flush()
        peer.local_type, peer.local_id = "citation", row.id
    row.target_type, row.target_id, row.source_id = target_type, target_id, source_id
    row.locator = payload.get("locator") if isinstance(payload.get("locator"), dict) else None
    row.quote, row.quality, row.note = (_text(payload.get("quote")), _text(payload.get("quality"), 16),
                                        _text(payload.get("note")))
    await session.flush()


async def apply_change(session: AsyncSession, change: SyncChange) -> None:
    """Persist the envelope and project it when possible.  No destructive local delete occurs."""
    peer = await _upsert_sync_object(session, change)
    if change.operation == "tombstone":
        return
    payload = change.payload or {}
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "la carga de sincronización debe ser un objeto")
    projector = {
        "person": _project_person, "name": _project_name, "place": _project_place, "repository": _project_repository,
        "source": _project_source, "family": _project_family, "event": _project_event,
        "citation": _project_citation,
    }.get(change.object_type)
    if projector:
        await projector(session, peer, payload)
    await session.flush()


async def snapshot_objects(session: AsyncSession, connection_id: uuid.UUID) -> list[dict]:
    """Return a deterministic, full local snapshot consumable by the Companion.

    Native rows are emitted with the peer's original identifier when known.  Unknown
    Gramps records stay as their stored envelopes, ensuring lossless round trips.
    """
    connection = await session.get(InteropConnection, connection_id)
    if not connection:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conexión no encontrada")
    peer_rows = (await session.scalars(select(SyncObject).where(SyncObject.connection_id == connection_id))).all()
    peer_by_local = {(r.local_type, r.local_id): r for r in peer_rows if r.local_type and r.local_id}
    output: list[dict] = []

    async def remote_key(object_type: str, local_id: uuid.UUID | None) -> str | None:
        if not local_id:
            return None
        row = peer_by_local.get((object_type, local_id))
        if not row:
            # An object born in Gen Suite needs a durable peer identity *before* the Companion
            # sends the snapshot back. Otherwise its echoed update would look like a new person.
            row = SyncObject(
                tenant_id=connection.tenant_id, connection_id=connection_id,
                object_type=object_type, object_key=f"gensuite:{local_id}",
                local_type=object_type, local_id=local_id,
            )
            session.add(row)
            await session.flush()
            peer_by_local[(object_type, local_id)] = row
            peer_rows.append(row)
        return row.object_key

    persons = (await session.scalars(select(Person).order_by(Person.id))).all()
    for p in persons:
        names = (await session.scalars(select(Name).where(Name.person_id == p.id).order_by(Name.is_primary.desc(), Name.id))).all()
        output.append({"object_type": "person", "object_key": await remote_key("person", p.id), "operation": "upsert",
                       "payload": {"sex": p.sex, "notes": p.notes, "privacy_status": p.privacy_status, "raw": p.raw,
                                   "names": [{"given": n.given, "surname": n.surname, "primary": n.is_primary} for n in names]}})
        for name in names:
            output.append({"object_type": "name", "object_key": await remote_key("name", name.id), "operation": "upsert",
                           "payload": {"person_key": await remote_key("person", p.id), "type": name.type,
                                       "given": name.given, "surname": name.surname,
                                       "surname_prefix": name.surname_prefix, "nickname": name.nickname,
                                       "primary": name.is_primary, "is_inferred": name.is_inferred}})
    places = (await session.scalars(select(Place).order_by(Place.id))).all()
    for place in places:
        output.append({"object_type": "place", "object_key": await remote_key("place", place.id), "operation": "upsert",
                       "payload": {"name": place.name, "lat": place.lat, "lng": place.lng, "place_type": place.place_type,
                                   "parent_key": await remote_key("place", place.parent_id)}})
    repositories = (await session.scalars(select(Repository).order_by(Repository.id))).all()
    for repository in repositories:
        output.append({"object_type": "repository", "object_key": await remote_key("repository", repository.id), "operation": "upsert",
                       "payload": {"name": repository.name, "url": repository.url, "address": repository.address, "notes": repository.notes}})
    sources = (await session.scalars(select(Source).order_by(Source.id))).all()
    for source in sources:
        output.append({"object_type": "source", "object_key": await remote_key("source", source.id), "operation": "upsert",
                       "payload": {"title": source.title, "source_type": source.source_type, "author": source.author,
                                   "publication_info": source.publication_info, "call_number": source.call_number, "url": source.url,
                                   "repository_key": await remote_key("repository", source.repository_id), "raw": source.raw}})
    families = (await session.scalars(select(Family).order_by(Family.id))).all()
    for family in families:
        child_ids = (await session.scalars(select(FamilyChild.person_id).where(FamilyChild.family_id == family.id).order_by(FamilyChild.seq, FamilyChild.person_id))).all()
        output.append({"object_type": "family", "object_key": await remote_key("family", family.id), "operation": "upsert",
                       "payload": {"husband_key": await remote_key("person", family.husband_id),
                                   "wife_key": await remote_key("person", family.wife_id),
                                   "child_keys": [await remote_key("person", child_id) for child_id in child_ids], "raw": family.raw}})
    events = (await session.scalars(select(Event).order_by(Event.id))).all()
    for event in events:
        output.append({"object_type": "event", "object_key": await remote_key("event", event.id), "operation": "upsert",
                       "payload": {"type": event.type, "date_raw": event.date_raw, "value": event.value,
                                   "is_inferred": event.is_inferred, "person_key": await remote_key("person", event.subject_person_id),
                                   "family_key": await remote_key("family", event.subject_family_id),
                                   "place_key": await remote_key("place", event.place_id)}})
    citations = (await session.scalars(select(Citation).order_by(Citation.id))).all()
    for citation in citations:
        output.append({"object_type": "citation", "object_key": await remote_key("citation", citation.id), "operation": "upsert",
                       "payload": {"target_type": citation.target_type,
                                   "target_key": await remote_key(citation.target_type, citation.target_id),
                                   "source_key": await remote_key("source", citation.source_id),
                                   "locator": citation.locator, "quote": citation.quote, "quality": citation.quality, "note": citation.note}})
    represented = {r.id for r in peer_rows if r.local_id}
    for peer in peer_rows:
        if peer.id in represented:
            continue
        output.append({"object_type": peer.object_type, "object_key": peer.object_key,
                       "operation": "tombstone" if peer.is_tombstone else "upsert",
                       "remote_revision": peer.remote_revision, "payload": peer.payload})
    return output
