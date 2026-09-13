"""Real PostgreSQL/HTTP/worker contracts. Disposable DB only; every AI response is synthetic."""
import uuid
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException
from sqlalchemy import select, update
from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.document import Document, Page
from app.models.embedding_index import EmbeddingGeneration, EmbeddingItem
from app.models.transcription import Transcription
from app.models.record import Record
from app.models.mention import PersonMention
from app.models.job import Job
from app.modules.search import router as search_router, service as search_service
from app.modules.transcription.service import correct_text
from app.tasks import embedding_tasks
from .test_research import _tenant_token
from .test_tree import _auth


def vector(axis=0):
    data = [0.] * 1024; data[axis] = 1.; return data


async def setup(client, email="index-owner@example.com"):
    token = await _tenant_token(client, email, "Index workspace")
    headers = _auth(token); tid = uuid.UUID(decode_token(token, "access")["tid"])
    response = await client.post("/api/providers/credentials", headers=headers, json={"scope": "tenant", "provider_key": "ollama",
        "label": "Synthetic local", "base_url": "http://ollama:11434/v1", "model_default": "fixture-model"})
    assert response.status_code == 201, response.text
    credential = response.json()["id"]
    response = await client.put("/api/providers/bindings", headers=headers, json={"task_type": "embedding", "credential_id": credential, "model": "fixture-model"})
    assert response.status_code == 200, response.text
    return tid, headers, credential


async def source(tid, *, visibility="private", content="Alpha manuscript", with_record=True):
    did, pid, txid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        session.add(Document(id=did, tenant_id=tid, title="Synthetic source", visibility=visibility, doc_type="image_set",
            storage_bucket="fixture-only", storage_prefix=f"{tid}/{did}/", page_count=1))
        await session.flush()
        session.add(Page(id=pid, tenant_id=tid, document_id=did, visibility=visibility, page_no=1, storage_key="fixture-not-a-real-object"))
        await session.flush()
        session.add(Transcription(id=txid, tenant_id=tid, document_id=did, page_id=pid, page_no=1,
            visibility=visibility, engine="fixture", text=content, is_active=True, embedding=vector(1)))
        await session.flush()
        if with_record:
            record = Record(id=uuid.uuid4(), tenant_id=tid, document_id=did, page_id=pid, transcription_id=txid,
                visibility=visibility, record_type="baptism", extraction_engine="fixture", status="extracted")
            session.add(record); await session.flush()
            session.add(PersonMention(tenant_id=tid, record_id=record.id, visibility=visibility,
                role="principal", given="Alpha", surname="Test", name_raw="Alpha Test", norm_given="alpha", norm_surname="test"))
        await session.commit()
    return did, txid


async def build(client, headers, *, include_public=False, client_id=None):
    response = await client.post("/api/search/indexes", headers=headers, json={"client_id": str(client_id or uuid.uuid4()),
        "title": "Synthetic rebuild", "include_public": include_public, "authorize": True})
    assert response.status_code == 201, response.text
    return response.json()


async def activate(client, headers, gid, previous=None):
    generation = (await client.get(f"/api/search/indexes/{gid}", headers=headers)).json()
    response = await client.post(f"/api/search/indexes/{gid}/activate", headers=headers,
        json={"expected_version": generation["version"], "expected_active_id": previous, "reason": "Synthetic reviewed coverage"})
    assert response.status_code == 200, response.text
    return response.json()


async def test_build_failure_resume_activation_rollback_profile_and_revision_checks(client):
    tid, headers, credential = await setup(client)
    did, txid = await source(tid)
    cid = uuid.uuid4()
    first = await build(client, headers, client_id=cid)
    assert (await build(client, headers, client_id=cid))["id"] == first["id"]
    assert (await client.get("/api/search/indexes/active", headers=headers)).json()["id"] is None
    transport = AsyncMock(side_effect=lambda rc, texts: ([vector() for _ in texts], {"prompt": 1, "completion": 0}))
    with patch.object(embedding_tasks, "embed_texts_with_usage", transport):
        await embedding_tasks.reembed_corpus({}, tenant_id=tid, job_id=first["job_id"])
    ready = await activate(client, headers, first["id"])
    assert ready["searchable"] == {"transcription": 1, "mention": 1}
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        assert list((await session.get(Transcription, txid)).embedding) == vector(1)  # Never erased/overwritten.
    await client.put("/api/providers/bindings", headers=headers, json={"task_type": "embedding", "credential_id": credential, "model": "next-model"})
    query = AsyncMock(return_value=([vector()], {"prompt": 1, "completion": 0}))
    with patch.object(search_router, "embed_texts_with_usage", query):
        found = await client.get("/api/search", headers=headers, params={"q": "Alpha", "mode": "semantic"})
    assert found.status_code == 200, found.text
    assert found.json()[0]["index_generation_id"] == first["id"]
    assert query.call_args.args[0].model == "fixture-model"  # Changed binding cannot corrupt query space.
    records = await client.get("/api/search/records", headers=headers, params={"surname": "Test"})
    assert records.status_code == 200 and len(records.json()) == 1, records.text
    second = await build(client, headers)
    with patch.object(embedding_tasks, "embed_texts_with_usage", AsyncMock(side_effect=HTTPException(402, "Synthetic budget stop"))):
        await embedding_tasks.reembed_corpus({}, tenant_id=tid, job_id=second["job_id"])
    failed = (await client.get(f"/api/search/indexes/{second['id']}", headers=headers)).json()
    assert failed["status"] == "error" and failed["counts"]["pending"] > 0
    assert (await client.get("/api/search/indexes/active", headers=headers)).json()["id"] == first["id"]
    resumed = await client.post(f"/api/search/indexes/{second['id']}/retry", headers=headers,
        json={"expected_version": failed["version"], "reason": "Synthetic issue resolved"})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["job_id"] == second["job_id"]
    with patch.object(embedding_tasks, "embed_texts_with_usage", transport):
        await embedding_tasks.reembed_corpus({}, tenant_id=tid, job_id=second["job_id"])
    await activate(client, headers, second["id"], first["id"])
    await activate(client, headers, first["id"], second["id"])  # Atomic rollback uses retained original vectors.
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=tid)
        await correct_text(session, txid, "Corrected manuscript", tenant_id=tid)
        await session.commit()
    with patch.object(search_router, "embed_texts_with_usage", query):
        after = await client.get("/api/search", headers=headers, params={"q": "Alpha", "mode": "semantic"})
    assert after.status_code == 200 and after.json() == []  # Old revision cannot be a new discovery.
    records = await client.get("/api/search/records", headers=headers, params={"surname": "Test"})
    assert records.status_code == 200 and records.json() == []


async def test_public_capture_remains_private_to_index_owner_and_respects_revocation(client):
    owner, headers, _ = await setup(client)
    other, other_headers, _ = await setup(client, "index-other@example.com")
    public_doc, public_tx = await source(other, visibility="public", with_record=False)
    private_doc, _ = await source(other, visibility="private", with_record=False)
    generation = await build(client, headers, include_public=True)
    with patch.object(embedding_tasks, "embed_texts_with_usage", AsyncMock(side_effect=lambda rc, texts: ([vector() for _ in texts], {}))):
        await embedding_tasks.reembed_corpus({}, tenant_id=owner, job_id=generation["job_id"])
    await activate(client, headers, generation["id"])
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=owner)
        items = (await session.scalars(select(EmbeddingItem))).all()
        assert {item.document_id for item in items} == {public_doc}
        hits = await search_service.vector_search(session, vector(), "public", owner, 10, generation_id=uuid.UUID(generation["id"]))
        assert [hit["document_id"] for hit in hits] == [public_doc]
    assert (await client.get(f"/api/search/indexes/{generation['id']}", headers=other_headers)).status_code == 404
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=other)
        await session.execute(update(Document).where(Document.id == public_doc).values(visibility="private"))
        await session.execute(update(Transcription).where(Transcription.id == public_tx).values(visibility="private"))
        await session.commit()
    async with SessionLocal() as session:
        await set_rls_context(session, tenant_id=owner)
        assert await search_service.vector_search(session, vector(), "all", owner, 10, generation_id=uuid.UUID(generation["id"])) == []
