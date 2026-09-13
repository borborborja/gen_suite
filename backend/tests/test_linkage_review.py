"""Reviewed linkage writes are atomic, auditable and truly reversible."""
from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select

from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.document import Page
from app.models.match_candidate import MatchCandidate
from app.models.mention import PersonMention
from app.models.record import Record

from .test_extraction_records_api import _auth, _tenant_token, _upload_doc
from .test_tree_manager import _person


async def _candidate(client: AsyncClient, h: dict, person_id: str) -> str:
    document_id = await _upload_doc(client, h)
    tenant_id = uuid.UUID((await client.get("/api/auth/me", headers=h)).json()["active_tenant_id"])
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tenant_id)
        page = await session.scalar(select(Page).where(Page.document_id == uuid.UUID(document_id)))
        record = Record(
            tenant_id=tenant_id, document_id=uuid.UUID(document_id), page_id=page.id,
            record_type="baptism", date_raw="1880", date_year=1880, status="extracted",
            extraction_engine="test", summary="Bautismo de Juan Vega",
        )
        session.add(record)
        await session.flush()
        mention = PersonMention(tenant_id=tenant_id, record_id=record.id, role="principal",
                                given="Juan", surname="Vega", name_raw="Juan Vega")
        session.add(mention)
        await session.flush()
        candidate = MatchCandidate(
            tenant_id=tenant_id, tree_person_id=uuid.UUID(person_id), person_mention_id=mention.id,
            record_id=record.id, score=0.81, evidence={"signals": {}}, relation="self",
        )
        session.add(candidate)
        await session.commit()
        return str(candidate.id)


async def test_review_apply_is_idempotent_and_reverts_every_tree_write(client: AsyncClient):
    h = _auth(await _tenant_token(client))
    person_id = await _person(client, h, "Juan", "Vega", "M")
    candidate_id = await _candidate(client, h, person_id)

    preview = await client.post(f"/api/linkage/candidates/{candidate_id}/preview", headers=h)
    assert preview.status_code == 200, preview.text
    body = {"preview_token": preview.json()["preview_token"], "relative_mention_ids": []}
    key = "review-decision-1"
    applied = await client.post(f"/api/linkage/candidates/{candidate_id}/apply", json=body,
                                headers={**h, "Idempotency-Key": key})
    assert applied.status_code == 200, applied.text
    change_id = applied.json()["change_id"]

    # A network retry cannot create another event/citation.
    retry = await client.post(f"/api/linkage/candidates/{candidate_id}/apply", json=body,
                              headers={**h, "Idempotency-Key": key})
    assert retry.status_code == 200 and retry.json()["change_id"] == change_id
    detail = await client.get(f"/api/tree/persons/{person_id}", headers=h)
    assert any(event["type"] == "baptism" for event in detail.json()["events"])

    reverted = await client.post(f"/api/tree/changes/{change_id}/revert", headers=h)
    assert reverted.status_code == 200, reverted.text
    detail = await client.get(f"/api/tree/persons/{person_id}", headers=h)
    assert not any(event["type"] == "baptism" for event in detail.json()["events"])
    pending = await client.get("/api/linkage/candidates", params={"status_filter": "pending"}, headers=h)
    assert [row["id"] for row in pending.json()] == [candidate_id]
