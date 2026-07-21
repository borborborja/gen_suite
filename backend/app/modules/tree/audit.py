"""Change capture + revert for tree mutations.

Every mutating tree endpoint runs inside ``audited(...)``: SQLAlchemy session listeners
record row-images ({table, pk, before, after}) of whatever the operation touched, and a
``ChangeLog`` row is written alongside the change itself (same transaction). Reverting
applies the inverse images all-or-nothing; a revert is captured like any other change, so
it can itself be reverted.

Only ORM mutations are visible to the listeners — tree editing code must not use bulk
``update()/delete()`` statements for audited tables.
"""
from __future__ import annotations

import datetime
import uuid
from contextlib import asynccontextmanager

from fastapi import HTTPException, status
from sqlalchemy import event as sa_event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import get_history

from ...core.errors import AppError
from ...core.security import Principal
from ...models.change_log import ChangeLog
from ...models.citation import Citation
from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.person import Name, Person
from ...models.place import Place
from ...models.user import User
from .schemas import ChangeDetail, ChangeItem, ChangePage

TABLE_MODELS = {
    "persons": Person, "names": Name, "events": Event, "families": Family,
    "family_children": FamilyChild, "citations": Citation, "places": Place,
}
_MODEL_TABLES = {m: t for t, m in TABLE_MODELS.items()}
# FK-safe re-insert order (parents before dependents); deletes run in reverse.
TABLE_ORDER = ["persons", "families", "places", "names", "events", "family_children", "citations"]
_SKIP_COMPARE = {"created_at", "updated_at"}
MAX_ROWS = 20_000


def _ser(v):
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    return v


def _row_image(obj) -> dict:
    return {c.key: _ser(getattr(obj, c.key)) for c in sa_inspect(type(obj)).mapper.column_attrs}


def _pk_dict(obj) -> dict:
    mapper = sa_inspect(type(obj)).mapper
    return {c.name: _ser(getattr(obj, c.name)) for c in mapper.primary_key}


def _deser(model, key: str, v):
    if v is None:
        return None
    col = sa_inspect(model).mapper.columns.get(key)
    if col is None:
        return v
    from sqlalchemy import DateTime
    from sqlalchemy.dialects.postgresql import UUID as PGUUID
    if isinstance(col.type, PGUUID):
        return uuid.UUID(v)
    if isinstance(col.type, DateTime):
        return datetime.datetime.fromisoformat(v)
    return v


def _pk_tuple(model, pk: dict):
    mapper = sa_inspect(model).mapper
    vals = tuple(_deser(model, c.name, pk[c.name]) for c in mapper.primary_key)
    return vals[0] if len(vals) == 1 else vals


class Capture:
    def __init__(self) -> None:
        self.rows: list[dict] = []


@asynccontextmanager
async def capture_changes(session: AsyncSession):
    """Attach flush listeners that accumulate row-images of audited-table ORM mutations."""
    cap = Capture()
    sync = session.sync_session
    pending_new: list = []

    def before_flush(sess, ctx, instances) -> None:
        for obj in sess.deleted:
            if type(obj) in _MODEL_TABLES:
                cap.rows.append({"table": _MODEL_TABLES[type(obj)], "pk": _pk_dict(obj),
                                 "before": _row_image(obj), "after": None})
        for obj in sess.dirty:
            if type(obj) not in _MODEL_TABLES or not sess.is_modified(obj):
                continue
            after = _row_image(obj)
            before = dict(after)
            changed = False
            for attr in sa_inspect(type(obj)).mapper.column_attrs:
                hist = get_history(obj, attr.key)
                if hist.has_changes() and hist.deleted:
                    before[attr.key] = _ser(hist.deleted[0])
                    changed = True
            if changed:
                cap.rows.append({"table": _MODEL_TABLES[type(obj)], "pk": _pk_dict(obj),
                                 "before": before, "after": after})
        for obj in sess.new:
            if type(obj) in _MODEL_TABLES:
                pending_new.append(obj)

    def after_flush(sess, ctx) -> None:
        while pending_new:
            obj = pending_new.pop()
            cap.rows.append({"table": _MODEL_TABLES[type(obj)], "pk": _pk_dict(obj),
                             "before": None, "after": _row_image(obj)})

    sa_event.listen(sync, "before_flush", before_flush)
    sa_event.listen(sync, "after_flush", after_flush)
    try:
        yield cap
        await session.flush()  # capture anything still pending before detaching
    finally:
        sa_event.remove(sync, "before_flush", before_flush)
        sa_event.remove(sync, "after_flush", after_flush)


@asynccontextmanager
async def audited(session: AsyncSession, principal: Principal, *, action: str,
                  entity_type: str | None = None, entity_id: uuid.UUID | None = None,
                  summary: str | None = None, revert_of: uuid.UUID | None = None):
    """Run a mutation capturing its row-images and persist them as one ChangeLog entry."""
    async with capture_changes(session) as cap:
        yield cap
    if len(cap.rows) > MAX_ROWS:
        raise AppError(409, "El cambio afecta a demasiadas filas para registrarse en el "
                            "historial; hazlo por partes", code="change_too_big")
    if cap.rows:
        session.add(ChangeLog(
            tenant_id=principal.tenant_id, actor_user_id=principal.user_id, action=action,
            entity_type=entity_type, entity_id=entity_id, summary=summary,
            rows=cap.rows, revert_of=revert_of,
        ))
        await session.flush()


async def list_changes(session: AsyncSession, *, page: int = 1, page_size: int = 50) -> ChangePage:
    total = await session.scalar(select(func.count()).select_from(ChangeLog)) or 0
    rows = (
        await session.execute(
            select(ChangeLog, User.email)
            .outerjoin(User, User.id == ChangeLog.actor_user_id)
            .order_by(ChangeLog.created_at.desc())
            .limit(page_size).offset((page - 1) * page_size)
        )
    ).all()
    return ChangePage(total=total, items=[
        ChangeItem(
            id=c.id, action=c.action, entity_type=c.entity_type, entity_id=c.entity_id,
            summary=c.summary, actor_email=email, created_at=c.created_at,
            reverted_at=c.reverted_at, revert_of=c.revert_of, rows_count=len(c.rows),
        )
        for c, email in rows
    ])


async def get_change(session: AsyncSession, change_id: uuid.UUID) -> ChangeDetail:
    row = (await session.execute(
        select(ChangeLog, User.email).outerjoin(User, User.id == ChangeLog.actor_user_id)
        .where(ChangeLog.id == change_id))).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cambio no encontrado")
    c, email = row
    return ChangeDetail(
        id=c.id, action=c.action, entity_type=c.entity_type, entity_id=c.entity_id,
        summary=c.summary, actor_email=email, created_at=c.created_at,
        reverted_at=c.reverted_at, revert_of=c.revert_of, rows_count=len(c.rows), rows=c.rows,
    )


def _conflict(n: int) -> AppError:
    return AppError(409, "No se puede revertir: los datos han cambiado desde entonces "
                         f"({n} fila{'s' if n != 1 else ''} en conflicto)", code="revert_conflict")


async def revert_change(session: AsyncSession, principal: Principal, change_id: uuid.UUID) -> ChangeLog:
    change = await session.get(ChangeLog, change_id)
    if not change:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "cambio no encontrado")
    if change.reverted_at:
        raise AppError(409, "Este cambio ya fue revertido", code="already_reverted")

    inserts = [r for r in change.rows if r["after"] is None]           # were deleted → re-insert
    updates = [r for r in change.rows if r["before"] and r["after"]]   # restore before
    deletes = [r for r in change.rows if r["before"] is None]          # were created → delete

    conflicts = 0
    async with audited(session, principal, action="revert", entity_type=change.entity_type,
                       entity_id=change.entity_id, revert_of=change.id,
                       summary=f"Revirtió: {change.summary or change.action}"):
        # IntegrityError can surface at any autoflush (session.get flushes pending work),
        # e.g. re-inserting a row whose FK target no longer exists → conflict, not a 500.
        try:
            # 1. resurrect deleted rows, parents first
            for r in sorted(inserts, key=lambda r: TABLE_ORDER.index(r["table"])):
                model = TABLE_MODELS[r["table"]]
                if await session.get(model, _pk_tuple(model, r["pk"])):
                    conflicts += 1
                    continue
                session.add(model(**{k: _deser(model, k, v) for k, v in r["before"].items()}))
            # 2. restore updated rows to their before-image
            for r in updates:
                model = TABLE_MODELS[r["table"]]
                obj = await session.get(model, _pk_tuple(model, r["pk"]))
                if obj is None:
                    conflicts += 1
                    continue
                current = _row_image(obj)
                if any(current.get(k) != r["after"].get(k)
                       for k in r["after"] if k not in _SKIP_COMPARE):
                    conflicts += 1
                    continue
                for k, v in r["before"].items():
                    if k not in _SKIP_COMPARE:
                        setattr(obj, k, _deser(model, k, v))
            # 3. remove created rows, dependents first
            for r in sorted(deletes, key=lambda r: -TABLE_ORDER.index(r["table"])):
                model = TABLE_MODELS[r["table"]]
                obj = await session.get(model, _pk_tuple(model, r["pk"]))
                if obj is None:
                    conflicts += 1
                    continue
                await session.delete(obj)
            if conflicts:
                raise _conflict(conflicts)
            await session.flush()
        except IntegrityError as exc:
            raise _conflict(1) from exc

    change.reverted_at = datetime.datetime.now(datetime.timezone.utc)
    await session.flush()
    return change
