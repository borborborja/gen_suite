from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.interop import InteropConnection, SyncBaseline, SyncChange
from .mapper import DependencyMissing, apply_change, snapshot_objects
from .schemas import (
    BaselineIn, BaselineOut, ConnectionIn, ConnectionOut, SyncChangeIn, SyncChangeOut,
)

# These map to the complete Gramps object family, not merely GEDCOM's INDI/FAM subset.  The
# companion serializes each as an envelope; unknown extensions remain in payload.raw for round-trip.
GRAMPS_OBJECT_TYPES = frozenset({
    "person", "family", "event", "name", "place", "source", "citation", "repository", "media",
    "note", "tag", "address", "url", "attribute", "association", "lds_ordinance", "custom",
})
_PROVIDERS = {"gramps_desktop", "gramps_web"}


def connection_out(row: InteropConnection) -> ConnectionOut:
    return ConnectionOut(
        id=row.id, provider=row.provider, name=row.name, endpoint=row.endpoint, status=row.status,
        last_pulled_at=row.last_pulled_at, last_pushed_at=row.last_pushed_at,
    )


def baseline_out(row: SyncBaseline) -> BaselineOut:
    return BaselineOut(
        id=row.id, connection_id=row.connection_id, snapshot_hash=row.snapshot_hash,
        object_counts=row.object_counts, note=row.note, is_active=row.is_active, created_at=row.created_at,
    )


def change_out(row: SyncChange) -> SyncChangeOut:
    return SyncChangeOut(
        id=row.id, connection_id=row.connection_id, source_system=row.source_system,
        object_type=row.object_type, object_key=row.object_key, operation=row.operation,
        remote_revision=row.remote_revision, payload=row.payload, state=row.state,
        conflict_with_id=row.conflict_with_id, resolution_note=row.resolution_note,
        resolved_at=row.resolved_at,
    )


async def create_connection(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, body: ConnectionIn
) -> InteropConnection:
    if body.provider not in _PROVIDERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "proveedor de sincronización inválido")
    row = InteropConnection(tenant_id=tenant_id, created_by=user_id, **body.model_dump())
    session.add(row)
    await session.flush()
    return row


async def list_connections(session: AsyncSession) -> list[ConnectionOut]:
    return [connection_out(row) for row in (await session.scalars(
        select(InteropConnection).order_by(InteropConnection.created_at.desc())
    )).all()]


async def establish_baseline(
    session: AsyncSession, connection_id: uuid.UUID, user_id: uuid.UUID, body: BaselineIn
) -> SyncBaseline:
    if not await session.get(InteropConnection, connection_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conexión no encontrada")
    await session.execute(
        update(SyncBaseline).where(SyncBaseline.connection_id == connection_id, SyncBaseline.is_active.is_(True))
        .values(is_active=False)
    )
    connection = await session.get(InteropConnection, connection_id)
    row = SyncBaseline(
        tenant_id=connection.tenant_id, connection_id=connection_id, established_by=user_id,
        **body.model_dump(),
    )
    session.add(row)
    await session.flush()
    return row


async def list_baselines(session: AsyncSession, connection_id: uuid.UUID) -> list[BaselineOut]:
    return [baseline_out(row) for row in (await session.scalars(
        select(SyncBaseline).where(SyncBaseline.connection_id == connection_id)
        .order_by(SyncBaseline.created_at.desc())
    )).all()]


async def ingest_changes(
    session: AsyncSession, connection_id: uuid.UUID, changes: list[SyncChangeIn]
) -> list[SyncChangeOut]:
    connection = await session.get(InteropConnection, connection_id)
    if not connection:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conexión no encontrada")
    if connection.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "la conexión está pausada")
    baseline = await session.scalar(select(SyncBaseline.id).where(
        SyncBaseline.connection_id == connection_id, SyncBaseline.is_active.is_(True)
    ))
    if not baseline:
        raise HTTPException(status.HTTP_409_CONFLICT, "confirma una línea base antes de sincronizar")

    output: list[SyncChangeOut] = []
    for body in changes:
        if body.source_system not in _PROVIDERS or body.object_type not in GRAMPS_OBJECT_TYPES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "envoltorio de sincronización no compatible")
        if body.operation not in {"upsert", "tombstone"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "operación de sincronización inválida")
        # Two unprocessed peer edits to the same canonical object need an explicit human choice.
        rival = await session.scalar(select(SyncChange).where(
            SyncChange.object_key == body.object_key, SyncChange.object_type == body.object_type,
            SyncChange.source_system != body.source_system, SyncChange.state == "queued",
        ).order_by(SyncChange.created_at.desc()))
        row = SyncChange(
            tenant_id=connection.tenant_id, connection_id=connection.id, **body.model_dump(),
            state="tombstone" if body.operation == "tombstone" else "queued",
        )
        session.add(row)
        await session.flush()
        if rival:
            rival.state = "conflict"
            row.state, row.conflict_with_id = "conflict", rival.id
            rival.conflict_with_id = row.id
            await session.flush()
        output.append(change_out(row))
    connection.last_pushed_at = datetime.now(timezone.utc)
    await session.flush()
    return output


async def _require_active_baseline(session: AsyncSession, connection_id: uuid.UUID) -> InteropConnection:
    connection = await session.get(InteropConnection, connection_id)
    if not connection:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conexión no encontrada")
    baseline = await session.scalar(select(SyncBaseline.id).where(
        SyncBaseline.connection_id == connection_id, SyncBaseline.is_active.is_(True)
    ))
    if not baseline:
        raise HTTPException(status.HTTP_409_CONFLICT, "confirma una línea base antes de sincronizar")
    return connection


async def apply_pending_changes(session: AsyncSession, connection_id: uuid.UUID) -> dict[str, int]:
    """Apply non-conflicting peer envelopes in dependency order.

    A valid family/event/citation can arrive before its references.  It remains queued
    and will be retried by the next call instead of being silently discarded or guessed.
    """
    await _require_active_baseline(session, connection_id)
    rows = (await session.scalars(select(SyncChange).where(
        SyncChange.connection_id == connection_id,
        SyncChange.state.in_(("queued", "tombstone")),
    ).order_by(SyncChange.created_at, SyncChange.id))).all()
    applied = tombstones = 0
    pending = list(rows)
    while pending:
        made_progress = False
        next_round: list[SyncChange] = []
        for row in pending:
            try:
                await apply_change(session, row)
            except DependencyMissing:
                next_round.append(row)
                continue
            row.state = "tombstone" if row.operation == "tombstone" else "applied"
            row.resolved_at = datetime.now(timezone.utc)
            applied += row.operation == "upsert"
            tombstones += row.operation == "tombstone"
            made_progress = True
        if not made_progress:
            pending = next_round
            break
        pending = next_round
    await session.flush()
    return {"applied": applied, "deferred": len(pending), "tombstones": tombstones}


async def get_snapshot(session: AsyncSession, connection_id: uuid.UUID) -> list[dict]:
    connection = await _require_active_baseline(session, connection_id)
    if connection.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "la conexión está pausada")
    objects = await snapshot_objects(session, connection_id)
    connection.last_pulled_at = datetime.now(timezone.utc)
    await session.flush()
    return objects


async def list_changes(
    session: AsyncSession, connection_id: uuid.UUID, state_filter: str | None
) -> list[SyncChangeOut]:
    if not await session.get(InteropConnection, connection_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conexión no encontrada")
    query = select(SyncChange).where(SyncChange.connection_id == connection_id).order_by(SyncChange.created_at.desc())
    if state_filter:
        if state_filter not in {"queued", "conflict", "applied", "rejected", "tombstone"}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "estado de cambio inválido")
        query = query.where(SyncChange.state == state_filter)
    return [change_out(row) for row in (await session.scalars(query)).all()]


async def resolve_change(
    session: AsyncSession, change_id: uuid.UUID, user_id: uuid.UUID, resolution: str, note: str | None,
) -> SyncChange:
    if resolution not in {"accept_remote", "reject_remote"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "resolución inválida")
    row = await session.get(SyncChange, change_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cambio no encontrado")
    if row.state not in {"queued", "conflict", "tombstone"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "el cambio ya fue resuelto")
    if resolution == "accept_remote":
        try:
            await apply_change(session, row)
        except DependencyMissing as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, f"cambio pendiente de dependencia: {exc}") from exc
        row.state = "tombstone" if row.operation == "tombstone" else "applied"
    else:
        row.state = "rejected"
    row.resolution_note, row.resolved_by, row.resolved_at = (note or "").strip() or None, user_id, datetime.now(timezone.utc)
    if row.conflict_with_id:
        rival = await session.get(SyncChange, row.conflict_with_id)
        if rival and rival.state == "conflict":
            rival.state = "rejected" if resolution == "accept_remote" else "queued"
            rival.resolved_by, rival.resolved_at = user_id, datetime.now(timezone.utc)
    await session.flush()
    return row
