from __future__ import annotations

import uuid
import pytest

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.rls import set_rls_context
from app.db.session import SessionLocal
from app.models.membership import Membership
from app.settings import settings


async def test_storage_write_actions_are_append_only_and_tenant_parent_bound():
    from sqlalchemy.exc import DBAPIError
    tids = [uuid.uuid4(), uuid.uuid4()]; writes = [uuid.uuid4(), uuid.uuid4()]
    statement = "INSERT INTO storage_write_actions(tenant_id,write_id,created_by,request_hash,reason,released_bytes,evidence) VALUES (:tenant,:write,:actor,repeat('a',64),'Test cancellation',1,'{}')"
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.begin() as connection:
            for tid, wid in zip(tids, writes):
                values = {"tenant": tid, "write": wid, "actor": uuid.uuid4(), "slug": tid.hex}
                await connection.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:tenant,'Test',:slug)"), values)
                await connection.execute(text("INSERT INTO storage_quotas(tenant_id) VALUES (:tenant)"), values)
                await connection.execute(text("INSERT INTO storage_writes(id,tenant_id,kind,state,bucket,location_hash,storage_key,key_hash,reserved_bytes) VALUES (:write,:tenant,'put','cancelled','test',repeat('a',64),'test',repeat('b',64),1)"), values)
                await connection.execute(text(statement), values)
        async with SessionLocal() as db:
            await set_rls_context(db, tenant_id=tids[0])
            assert (await db.scalars(text("SELECT tenant_id FROM storage_write_actions"))).all() == [tids[0]]
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                assert not await db.scalar(text("SELECT has_table_privilege(current_user, 'storage_write_actions', :privilege)"), {"privilege": privilege})
            for sql, values, code in (
                (statement, {"tenant": tids[1], "write": writes[1], "actor": uuid.uuid4()}, "42501"),
                (statement, {"tenant": tids[0], "write": writes[1], "actor": uuid.uuid4()}, "23503"),
                ("UPDATE storage_write_actions SET reason='changed'", {}, "42501"),
                ("DELETE FROM storage_write_actions", {}, "42501"),
            ):
                with pytest.raises(DBAPIError) as error: await db.execute(text(sql), values)
                assert error.value.orig.sqlstate == code
                await db.rollback(); await set_rls_context(db, tenant_id=tids[0])
    finally: await admin.dispose()


@pytest.mark.parametrize("table", ["storage_quotas", "storage_writes"])
async def test_storage_quota_and_receipts_are_isolated_and_ai_response_parent_is_tenant_bound(table):
    from sqlalchemy.exc import DBAPIError
    tenants, scans, jobs, writes = ([uuid.uuid4(), uuid.uuid4()] for _ in range(4))
    parent = "INSERT INTO storage_quotas(tenant_id) VALUES (:tenant)"
    receipt = "INSERT INTO storage_writes(id,tenant_id,kind,state,bucket,location_hash,storage_key,key_hash,reserved_bytes) VALUES (:write,:tenant,'put','reserved','test',repeat('a',64),'test',repeat('b',64),1)"
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.begin() as connection:
            for tenant, scan, job, write in zip(tenants, scans, jobs, writes):
                values = dict(tenant=tenant, scan=scan, job=job, write=write, slug=tenant.hex)
                await connection.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:tenant,'Test',:slug)"), values)
                await connection.execute(text("INSERT INTO jobs(id,tenant_id,type,status) VALUES (:job,:tenant,'recount_storage','queued')"), values)
                await connection.execute(text("INSERT INTO storage_scans(id,tenant_id,job_id,buckets,location_hash,request_hash,created_by,reason) VALUES (:scan,:tenant,:job,'[]',repeat('a',64),repeat('b',64),:job,'Test')"), values)
                await connection.execute(text(parent), values)
                await connection.execute(text(receipt), values)
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tenants[0])
            assert (await session.scalars(text(f"SELECT tenant_id FROM {table}"))).all() == [tenants[0]]
            assert (await session.execute(text(f"UPDATE {table} SET tenant_id=tenant_id WHERE tenant_id=:other"), {"other": tenants[1]})).rowcount == 0
            assert (await session.execute(text(f"DELETE FROM {table} WHERE tenant_id=:other"), {"other": tenants[1]})).rowcount == 0
            with pytest.raises(DBAPIError) as error:
                await session.execute(text(parent if table == "storage_quotas" else receipt), {"tenant": tenants[1], "write": uuid.uuid4()})
            assert error.value.orig.sqlstate == "42501"
            await session.rollback(); await set_rls_context(session, tenant_id=tenants[0])
            if table == "storage_quotas":
                sql = "UPDATE storage_quotas SET scan_id=:scan WHERE tenant_id=:tenant"
            else:
                sql = "INSERT INTO ai_calls(tenant_id,response_write_id,task_type,operation_key,request_hash,profile,state,reserved_microusd) VALUES (:tenant,:write,'embedding',repeat('a',64),repeat('b',64),'{}','inflight',0)"
            with pytest.raises(DBAPIError) as error:
                await session.execute(text(sql), {"tenant": tenants[0], "scan": scans[1], "write": writes[1]})
            assert error.value.orig.sqlstate == "23503"
            await session.rollback()
    finally: await admin.dispose()


@pytest.mark.parametrize("table", ["storage_scans", "storage_inventory_items"])
async def test_storage_inventory_isolation_and_tenant_bound_parents(table):
    from sqlalchemy.exc import DBAPIError
    tenants, scans, jobs = ([uuid.uuid4(), uuid.uuid4()] for _ in range(3))
    spare_job = uuid.uuid4()
    parent = "INSERT INTO storage_scans(id,tenant_id,job_id,buckets,location_hash,request_hash,created_by,reason) VALUES (:scan,:tenant,:job,'[\"test-private\",\"test-public\"]',repeat('a',64),repeat('b',64),:job,'Test')"
    child = "INSERT INTO storage_inventory_items(tenant_id,scan_id,bucket_slot,kind,category,storage_key,version_id,byte_size,is_latest) VALUES (:tenant,:scan,0,'version','other','test','null',1,true)"
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.begin() as connection:
            for tid, sid, jid in zip(tenants, scans, jobs):
                values = dict(tenant=tid, scan=sid, job=jid, slug=tid.hex)
                await connection.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:tenant,'Test',:slug)"), values)
                await connection.execute(text("INSERT INTO jobs(id,tenant_id,type,status) VALUES (:job,:tenant,'inventory_storage','queued')"), values)
                await connection.execute(text(parent), values)
                await connection.execute(text(child), values)
            await connection.execute(text("INSERT INTO jobs(id,tenant_id,type,status) VALUES (:job,:tenant,'inventory_storage','queued')"), dict(job=spare_job, tenant=tenants[1]))
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tenants[0])
            assert (await session.scalars(text(f"SELECT tenant_id FROM {table}"))).all() == [tenants[0]]
            assert (await session.execute(text(f"UPDATE {table} SET tenant_id=tenant_id WHERE tenant_id=:other"), {"other": tenants[1]})).rowcount == 0
            assert (await session.execute(text(f"DELETE FROM {table} WHERE tenant_id=:other"), {"other": tenants[1]})).rowcount == 0
            statement = parent if table == "storage_scans" else child
            values = dict(tenant=tenants[1], scan=uuid.uuid4() if table == "storage_scans" else scans[1], job=spare_job)
            with pytest.raises(DBAPIError) as error: await session.execute(text(statement), values)
            assert error.value.orig.sqlstate == "42501"
            await session.rollback(); await set_rls_context(session, tenant_id=tenants[0])
            values["tenant"] = tenants[0]
            with pytest.raises(DBAPIError) as error: await session.execute(text(statement), values)
            assert error.value.orig.sqlstate == "23503"
            await session.rollback()
    finally: await admin.dispose()


@pytest.mark.parametrize("table", ["ingest_cleanups", "ingest_cleanup_objects"])
async def test_cleanup_receipts_enforce_tenant_and_parent_isolation(table):
    from sqlalchemy.exc import DBAPIError
    tenants = [uuid.uuid4(), uuid.uuid4()]
    sessions = [uuid.uuid4(), uuid.uuid4()]
    cleanups = [uuid.uuid4(), uuid.uuid4()]
    jobs = [uuid.uuid4(), uuid.uuid4()]
    create = "INSERT INTO ingest_cleanups(id,tenant_id,session_id,job_id,mode,phase,original_status,bucket,retained_reservation_bytes,preview_hash,request_hash,created_by,reason) VALUES (:cleanup,:tenant,:session,:job,'discard','inventory','uploading','test',0,repeat('a',64),repeat('b',64),:job,'Test')"
    child = "INSERT INTO ingest_cleanup_objects(tenant_id,cleanup_id,inventory_epoch,storage_key,version_id,byte_size,is_delete_marker) VALUES (:tenant,:cleanup,1,'test','null',1,false)"
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.begin() as conn:
            for tenant, sid, cid, jid in zip(tenants, sessions, cleanups, jobs):
                values = dict(tenant=tenant, session=sid, cleanup=cid, job=jid, slug=tenant.hex)
                await conn.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:tenant,'Test',:slug)"), values)
                await conn.execute(text("INSERT INTO jobs(id,tenant_id,type,status) VALUES (:job,:tenant,'cleanup_ingestion','queued')"), values)
                await conn.execute(text("INSERT INTO ingest_sessions(id,tenant_id,created_by,title,expected_files,reserved_bytes) VALUES (:session,:tenant,:job,'Test',1,1)"), values)
                await conn.execute(text(create), values)
                await conn.execute(text(child), values)
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tenants[0])
            assert (await session.scalars(text(f"SELECT tenant_id FROM {table}"))).all() == [tenants[0]]
            changed = await session.execute(text(f"UPDATE {table} SET tenant_id=tenant_id WHERE tenant_id=:other"), {"other": tenants[1]})
            assert changed.rowcount == 0
            insert = create if table == "ingest_cleanups" else child
            values = dict(tenant=tenants[1], session=sessions[1], cleanup=uuid.uuid4() if table == "ingest_cleanups" else cleanups[1], job=jobs[1])
            with pytest.raises(DBAPIError) as error: await session.execute(text(insert), values)
            assert error.value.orig.sqlstate == "42501"
            await session.rollback(); await set_rls_context(session, tenant_id=tenants[0])
            values["tenant"] = tenants[0]
            with pytest.raises(DBAPIError) as error: await session.execute(text(insert), values)
            assert error.value.orig.sqlstate == "23503"
            await session.rollback()
    finally: await admin.dispose()


@pytest.mark.parametrize("owner_field", ["research_run_id", "embedding_generation_id", "job_id"])
async def test_billing_authorizations_isolated_append_only_and_tenant_parent_bound(owner_field):
    from sqlalchemy.exc import DBAPIError
    tenants = [uuid.uuid4(), uuid.uuid4()]
    owners = [uuid.uuid4(), uuid.uuid4()]
    parents = {
        "job_id": "INSERT INTO jobs(id,tenant_id,type,status,params) VALUES (:owner,:tenant,'embedding','paused','{}')",
        "research_run_id": "INSERT INTO research_runs(id,tenant_id,title,created_by,status,version,budget_microusd,request_hash,config,profiles) VALUES (:owner,:tenant,'Test',:owner,'paused',1,0,repeat('a',64),'{}','{}')",
        "embedding_generation_id": "INSERT INTO embedding_generations(id,tenant_id,title,purpose,status,version,profile,space_key) VALUES (:owner,:tenant,'Test','full','ready',1,'{}',repeat('a',64))",
    }
    insert = f"INSERT INTO ai_billing_authorizations(tenant_id,{owner_field},task_type,version,base_profile_hash,budget_policy,request_hash,preview_hash,created_by,reason) VALUES (:tenant,:owner,'embedding',:version,repeat('a',64),'{{}}',repeat('b',64),repeat('c',64),:owner,'Test approval')"
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.begin() as conn:
            for tenant, owner in zip(tenants, owners):
                values = dict(tenant=tenant, owner=owner, version=1, slug=tenant.hex)
                await conn.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:tenant,'Test',:slug)"), values)
                await conn.execute(text(parents[owner_field]), values)
                await conn.execute(text(insert), values)
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tenants[0])
            assert (await session.scalars(text("SELECT tenant_id FROM ai_billing_authorizations"))).all() == [tenants[0]]
            # Own INSERT works, while arbitrary UPDATE, DELETE and TRUNCATE do not.
            await session.execute(text(insert), dict(tenant=tenants[0], owner=owners[0], version=2))
            await session.commit()
            for sql in ("UPDATE ai_billing_authorizations SET reason='tampered'", "DELETE FROM ai_billing_authorizations", "TRUNCATE ai_billing_authorizations"):
                await set_rls_context(session, tenant_id=tenants[0])
                with pytest.raises(DBAPIError) as error: await session.execute(text(sql))
                assert error.value.orig.sqlstate == "42501"
                await session.rollback()
            for tenant, owner, expected in ((tenants[1], owners[1], "42501"), (tenants[0], owners[1], "23503")):
                await set_rls_context(session, tenant_id=tenants[0])
                with pytest.raises(DBAPIError) as error:
                    await session.execute(text(insert), dict(tenant=tenant, owner=owner, version=3))
                assert error.value.orig.sqlstate == expected
                await session.rollback()
    finally:
        await admin.dispose()


@pytest.mark.parametrize("table", ["embedding_generations", "embedding_items", "embedding_index_events"])
async def test_generation_tables_and_cross_tenant_parents_are_isolated(table):
    from sqlalchemy.exc import DBAPIError
    tenants = [uuid.uuid4(), uuid.uuid4()]
    generations = [uuid.uuid4(), uuid.uuid4()]
    create = "INSERT INTO embedding_generations(id,tenant_id,title,purpose,status,version,profile,space_key) VALUES (:generation,:tenant,'Test','full','building',1,'{}',repeat('a',64))"
    inserts = {
        "embedding_generations": create,
        "embedding_items": "INSERT INTO embedding_items(tenant_id,generation_id,kind,source_id,source_tenant_id,document_id,input_text,input_fields,source_hash,job_id,status) VALUES (:tenant,:generation,'transcription',:source,:tenant,:source,'Test','{}',repeat('a',64),:source,'pending')",
        "embedding_index_events": "INSERT INTO embedding_index_events(tenant_id,generation_id,kind,detail) VALUES (:tenant,:generation,'test','{}')",
    }
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.begin() as conn:
            for tenant, gid in zip(tenants, generations):
                values = dict(tenant=tenant, generation=gid, source=uuid.uuid4(), slug=tenant.hex)
                await conn.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:tenant,'Index',:slug)"), values)
                await conn.execute(text(create), values)
                if table != "embedding_generations": await conn.execute(text(inserts[table]), values)
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tenants[0])
            assert (await session.scalars(text(f"SELECT tenant_id FROM {table}"))).all() == [tenants[0]]
            changed = await session.execute(text(f"UPDATE {table} SET tenant_id=tenant_id WHERE tenant_id=:tenant"), {"tenant": tenants[1]})
            assert changed.rowcount == 0
            values = dict(tenant=tenants[1], generation=uuid.uuid4() if table == "embedding_generations" else generations[1], source=uuid.uuid4())
            with pytest.raises(DBAPIError) as error: await session.execute(text(inserts[table]), values)
            assert error.value.orig.sqlstate == "42501"
            await session.rollback()
            if table != "embedding_generations":
                await set_rls_context(session, tenant_id=tenants[0]); values["tenant"] = tenants[0]
                with pytest.raises(DBAPIError) as error: await session.execute(text(inserts[table]), values)
                assert error.value.orig.sqlstate == "23503"
                await session.rollback()
    finally: await admin.dispose()


@pytest.mark.parametrize("table", ["research_runs", "research_run_stages", "research_run_events"])
async def test_research_execution_tables_and_parent_relationships_are_isolated(table):
    from sqlalchemy.exc import DBAPIError
    tenants = [uuid.uuid4(), uuid.uuid4()]
    run_ids = [uuid.uuid4(), uuid.uuid4()]
    admin = create_async_engine(settings.admin_database_url)
    insert_run = "INSERT INTO research_runs(id,tenant_id,title,created_by,status,version,budget_microusd,request_hash,config,profiles) VALUES (:run,:tenant,'Test',:actor,'planned',1,0,repeat('a',64),'{}','{}')"
    inserts = {
        "research_runs": insert_run,
        "research_run_stages": "INSERT INTO research_run_stages(tenant_id,run_id,position,kind,title,status,attempts) VALUES (:tenant,:run,:position,'pdf','Test','pending',0)",
        "research_run_events": "INSERT INTO research_run_events(tenant_id,run_id,kind,detail) VALUES (:tenant,:run,'test','{}')",
    }
    try:
        async with admin.begin() as conn:
            for tenant, run_id in zip(tenants, run_ids):
                values = {"tenant": tenant, "run": run_id, "actor": uuid.uuid4(), "position": 0}
                await conn.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:tenant,'Run',:slug)"), {**values, "slug": tenant.hex})
                await conn.execute(text(insert_run), values)
                if table != "research_runs": await conn.execute(text(inserts[table]), values)
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tenants[0])
            assert (await session.scalars(text(f"SELECT tenant_id FROM {table}"))).all() == [tenants[0]]
            # A fresh position avoids a uniqueness error masking the parent-tenant FK.
            values = {"tenant": tenants[1], "run": uuid.uuid4() if table == "research_runs" else run_ids[1], "actor": uuid.uuid4(), "position": 1}
            with pytest.raises(DBAPIError) as error:
                await session.execute(text(inserts[table]), values)
            assert error.value.orig.sqlstate == "42501"
            await session.rollback()
            if table != "research_runs":
                await set_rls_context(session, tenant_id=tenants[0])
                values["tenant"] = tenants[0]
                with pytest.raises(DBAPIError) as error:
                    await session.execute(text(inserts[table]), values)
                assert error.value.orig.sqlstate == "23503"
                await session.rollback()
            await set_rls_context(session, tenant_id=tenants[0])
            with pytest.raises(DBAPIError) as error:
                await session.execute(text("INSERT INTO ai_calls(tenant_id,research_run_id,task_type,operation_key,request_hash,profile,state,reserved_microusd) VALUES (:tenant,:run,'test',repeat('a',64),repeat('b',64),'{}','inflight',1)"),
                                      {"tenant": tenants[0], "run": run_ids[1]})
            assert error.value.orig.sqlstate == "23503"
            await session.rollback()
    finally:
        await admin.dispose()


async def test_ai_ledger_isolated_for_reads_inserts_and_updates():
    from sqlalchemy.exc import DBAPIError
    tenants = [uuid.uuid4(), uuid.uuid4()]
    admin = create_async_engine(settings.admin_database_url)
    insert = "INSERT INTO ai_calls(tenant_id,task_type,operation_key,request_hash,profile,state,reserved_microusd) VALUES (:tenant,'test',:key,repeat('a',64),'{}','inflight',123)"
    try:
        async with admin.begin() as conn:
            for tenant in tenants:
                await conn.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:tenant,'Ledger',:slug)"), {"tenant": tenant, "slug": tenant.hex})
                await conn.execute(text(insert), {"tenant": tenant, "key": tenant.hex})
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tenants[0])
            assert (await session.scalars(text("SELECT tenant_id FROM ai_calls"))).all() == [tenants[0]]
            result = await session.execute(text("UPDATE ai_calls SET reserved_microusd=0 WHERE tenant_id=:tenant"), {"tenant": tenants[1]})
            assert result.rowcount == 0
            with pytest.raises(DBAPIError) as error:
                await session.execute(text(insert), {"tenant": tenants[1], "key": uuid.uuid4().hex})
            assert error.value.orig.sqlstate == "42501"
            await session.rollback()
            await set_rls_context(session)  # No tenant: even a known call identifier is invisible.
            assert (await session.scalars(text("SELECT id FROM ai_calls"))).all() == []
    finally:
        await admin.dispose()


@pytest.mark.parametrize("table", ["ingest_sessions", "ingest_books", "ingest_files"])
async def test_resumable_manifest_tables_enforce_tenant_and_parent_isolation(table):
    from sqlalchemy.exc import DBAPIError
    tenants = [uuid.uuid4(), uuid.uuid4()]
    identifiers = {tenant: {name: uuid.uuid4() for name in ("session", "book", "file")} for tenant in tenants}
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.begin() as conn:
            for tenant, ids in identifiers.items():
                values = {"tenant": tenant, **ids, "user": uuid.uuid4(), "slug": tenant.hex}
                await conn.execute(text("INSERT INTO tenants(id,name,slug) VALUES (:tenant,'Ingest',:slug)"), values)
                await conn.execute(text("INSERT INTO ingest_sessions(id,tenant_id,created_by,title,expected_files,reserved_bytes) VALUES (:session,:tenant,:user,'Privado',1,10)"), values)
                await conn.execute(text("INSERT INTO ingest_books(id,tenant_id,session_id,group_key,title,kind,options) VALUES (:book,:tenant,:session,'group','Libro','image_set','{}')"), values)
                await conn.execute(text("INSERT INTO ingest_files(id,tenant_id,session_id,book_id,relative_path,path_hash,position,byte_size,last_modified) VALUES (:file,:tenant,:session,:book,'a.jpg',repeat('a',64),1,10,0)"), values)
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=tenants[0])
            visible = (await session.scalars(text(f"SELECT tenant_id FROM {table}"))).all()
            assert visible == [tenants[0]]
            values = {"tenant": tenants[1], **identifiers[tenants[1]], "new": uuid.uuid4(), "user": uuid.uuid4()}
            statements = {
                "ingest_sessions": "INSERT INTO ingest_sessions(id,tenant_id,created_by,title,expected_files,reserved_bytes) VALUES (:new,:tenant,:user,'Prohibido',1,10)",
                "ingest_books": "INSERT INTO ingest_books(id,tenant_id,session_id,group_key,title,kind,options) VALUES (:new,:tenant,:session,'other','Prohibido','image_set','{}')",
                "ingest_files": "INSERT INTO ingest_files(id,tenant_id,session_id,book_id,relative_path,path_hash,position,byte_size,last_modified) VALUES (:new,:tenant,:session,:book,'b.jpg',repeat('b',64),2,10,0)",
            }
            with pytest.raises(DBAPIError) as error:
                await session.execute(text(statements[table]), values)
            assert error.value.orig.sqlstate == "42501"
            await session.rollback()
            if table != "ingest_sessions":
                await set_rls_context(session, tenant_id=tenants[0])
                values["tenant"] = tenants[0]  # Own row cannot point at another tenant's parent.
                with pytest.raises(DBAPIError) as error:
                    await session.execute(text(statements[table]), values)
                assert error.value.orig.sqlstate == "23503"
                await session.rollback()
    finally:
        await admin.dispose()


@pytest.mark.parametrize("table", ["task_dispatches", "document_exports", "processing_checkpoints", "tree_exports", "tree_imports", "source_documents"])
async def test_preservation_tables_are_tenant_only(table):
    """Public document visibility never makes private export/outbox/checkpoint rows public."""
    from sqlalchemy.exc import DBAPIError
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    ids = {}
    admin = create_async_engine(settings.admin_database_url)
    try:
        async with admin.begin() as conn:
            for tenant in (t1, t2):
                doc, job, transcription = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                ids[tenant] = (doc, job, transcription)
                await conn.execute(text("INSERT INTO tenants (id,name,slug) VALUES (:id,'RLS',:slug)"), {"id": tenant, "slug": tenant.hex})
                await conn.execute(text("INSERT INTO documents (id,tenant_id,title,doc_type,visibility,storage_bucket,storage_prefix) VALUES (:id,:tenant,'Prueba','image_set','public','test',:prefix)"),
                                   {"id": doc, "tenant": tenant, "prefix": f"{tenant}/{doc}/"})
                await conn.execute(text("INSERT INTO jobs (id,tenant_id,type) VALUES (:id,:tenant,'test')"), {"id": job, "tenant": tenant})
                await conn.execute(text("INSERT INTO transcriptions (id,tenant_id,document_id,page_no,engine) VALUES (:id,:tenant,:doc,1,'test')"), {"id": transcription, "tenant": tenant, "doc": doc})
                if table == "task_dispatches":
                    stmt = "INSERT INTO task_dispatches(job_id,tenant_id,function,payload) VALUES (:job,:tenant,'transcribe_document','{}')"
                elif table == "document_exports":
                    stmt = "INSERT INTO document_exports(tenant_id,document_id,kind,storage_key,checksum,byte_size,manifest) VALUES (:tenant,:doc,'pdf','test','abc',1,'{}')"
                elif table == "tree_exports":
                    stmt = "INSERT INTO tree_exports(id,tenant_id,kind,storage_key,checksum,byte_size,report) VALUES (:job,:tenant,'gedcom7',cast(cast(:tenant AS uuid) AS text)||'/tree-exports/'||cast(cast(:job AS uuid) AS text)||'/test.ged',repeat('a',64),1,'{}')"
                elif table == "tree_imports":
                    stmt = "INSERT INTO tree_imports(id,tenant_id,client_id,kind,filename,storage_key,checksum,byte_size,report) VALUES (:job,:tenant,:job,'gedcom7','test.ged',cast(cast(:tenant AS uuid) AS text)||'/tree-imports/'||cast(cast(:job AS uuid) AS text)||'/test.ged',repeat('a',64),1,'{}')"
                elif table == "source_documents":
                    await conn.execute(text("INSERT INTO research_sources(id,tenant_id,title) VALUES (:id,:tenant,'Fuente sintética')"), {"id": job, "tenant": tenant})
                    stmt = "INSERT INTO source_documents(tenant_id,source_id,document_id) VALUES (:tenant,:job,:doc)"
                else:
                    stmt = "INSERT INTO processing_checkpoints(tenant_id,transcription_id,job_id,stage,records_count) VALUES (:tenant,:transcription,:job,'extraction',0)"
                await conn.execute(text(stmt), {"tenant": tenant, "doc": doc, "job": job, "transcription": transcription})
        async with SessionLocal() as session:
            await set_rls_context(session, tenant_id=t1)
            visible = (await session.scalars(text(f"SELECT tenant_id FROM {table}"))).all()
            assert visible == [t1]
            await session.rollback()
            await set_rls_context(session, tenant_id=t1)
            with pytest.raises(DBAPIError) as error:
                await session.execute(text(stmt), {"tenant": t2, "doc": ids[t1][0], "job": ids[t1][1], "transcription": ids[t1][2]})
            assert error.value.orig.sqlstate == "42501"
            await session.rollback()
            if table == "tree_imports":
                await set_rls_context(session, tenant_id=t1)
                with pytest.raises(DBAPIError) as immutable:
                    await session.execute(text("UPDATE tree_imports SET report=jsonb_build_object('changed',true) WHERE tenant_id=:tenant"), {"tenant": t1})
                assert immutable.value.orig.sqlstate == "P0001"
                await session.rollback()
                await set_rls_context(session, tenant_id=t1)
                with pytest.raises(DBAPIError) as mismatch:
                    await session.execute(text("UPDATE tree_imports SET apply_job_id=:job WHERE tenant_id=:tenant"), {"job": ids[t2][1], "tenant": t1})
                assert mismatch.value.orig.sqlstate == "23503"
                await session.rollback()
            if table == "source_documents":
                for values in ({"tenant": t1, "job": ids[t2][1], "doc": ids[t1][0]}, {"tenant": t1, "job": ids[t1][1], "doc": ids[t2][0]}):
                    await set_rls_context(session, tenant_id=t1)
                    with pytest.raises(DBAPIError) as mismatch:
                        await session.execute(text(stmt), values)
                    assert mismatch.value.orig.sqlstate == "23503"
                    await session.rollback()
            if table == "tree_exports":
                # Default schema privileges must not let the app change a reviewed file.
                await set_rls_context(session, tenant_id=t1)
                with pytest.raises(DBAPIError) as immutable:
                    await session.execute(text("UPDATE tree_exports SET report='{}' WHERE tenant_id=:tenant"), {"tenant": t1})
                assert immutable.value.orig.sqlstate == "42501"
                await session.rollback()
                other_job = uuid.uuid4()
                async with admin.begin() as conn:
                    await conn.execute(text("INSERT INTO jobs(id,tenant_id,type) VALUES (:id,:tenant,'test')"), {"id": other_job, "tenant": t2})
                await set_rls_context(session, tenant_id=t1)
                with pytest.raises(DBAPIError) as mismatch:
                    await session.execute(text(stmt), {"job": other_job, "tenant": t1})
                assert mismatch.value.orig.sqlstate == "23503"
                await session.rollback()
    finally:
        await admin.dispose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, email: str) -> str:
    r = await client.post("/api/auth/register", json={"email": email, "password": "supersecret123"})
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


async def test_tenant_member_lists_are_isolated(client: AsyncClient):
    a = await _register(client, "a@example.com")
    b = await _register(client, "b@example.com")

    t1 = (await client.post("/api/tenants", json={"name": "Familia A"}, headers=_auth(a))).json()
    t2 = (await client.post("/api/tenants", json={"name": "Familia B"}, headers=_auth(b))).json()

    # Switch each user into their own tenant.
    a_t1 = (await client.post(f"/api/auth/switch/{t1['id']}", headers=_auth(a))).json()["access_token"]
    b_t2 = (await client.post(f"/api/auth/switch/{t2['id']}", headers=_auth(b))).json()["access_token"]

    members_a = await client.get("/api/tenants/members", headers=_auth(a_t1))
    members_b = await client.get("/api/tenants/members", headers=_auth(b_t2))
    assert {m["email"] for m in members_a.json()} == {"a@example.com"}
    assert {m["email"] for m in members_b.json()} == {"b@example.com"}

    # A cannot switch into B's tenant.
    forbidden = await client.post(f"/api/auth/switch/{t2['id']}", headers=_auth(a))
    assert forbidden.status_code == 403


async def test_rls_policy_blocks_cross_tenant_rows():
    """Direct DB proof: with T1 context, the app role never sees T2's membership rows."""
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()

    admin = create_async_engine(settings.admin_database_url)
    async with admin.begin() as conn:
        for tid, name, slug in [(t1, "T1", f"t1-{t1.hex[:6]}"), (t2, "T2", f"t2-{t2.hex[:6]}")]:
            await conn.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
                {"id": tid, "n": name, "s": slug},
            )
        for uid, email in [(u1, f"{u1.hex[:6]}@e.com"), (u2, f"{u2.hex[:6]}@e.com")]:
            await conn.execute(
                text("INSERT INTO users (id, email, password_hash) VALUES (:id, :e, 'x')"),
                {"id": uid, "e": email},
            )
        for tid, uid in [(t1, u1), (t2, u2)]:
            await conn.execute(
                text(
                    "INSERT INTO memberships (tenant_id, user_id, role) "
                    "VALUES (:t, :u, 'owner')"
                ),
                {"t": tid, "u": uid},
            )
    await admin.dispose()

    async with SessionLocal() as session:
        await set_rls_context(session, user_id=u1, tenant_id=t1, role="owner")
        visible = (await session.scalars(select(Membership.tenant_id))).all()

    assert t2 not in visible
    assert set(visible) == {t1}


async def test_rls_policy_covers_change_log():
    """change_log (historial del árbol) queda confinado por tenant: ni SELECT cruzado ni
    INSERT con tenant ajeno (WITH CHECK)."""
    from app.models.change_log import ChangeLog

    t1, t2 = uuid.uuid4(), uuid.uuid4()
    admin = create_async_engine(settings.admin_database_url)
    async with admin.begin() as conn:
        for tid, name, slug in [(t1, "T1", f"t1-{t1.hex[:6]}"), (t2, "T2", f"t2-{t2.hex[:6]}")]:
            await conn.execute(
                text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
                {"id": tid, "n": name, "s": slug},
            )
        for tid in (t1, t2):
            await conn.execute(
                text("INSERT INTO change_log (tenant_id, action, rows) "
                     "VALUES (:t, 'person_create', '[]'::jsonb)"),
                {"t": tid},
            )
    await admin.dispose()

    async with SessionLocal() as session:
        await set_rls_context(session, user_id=uuid.uuid4(), tenant_id=t1, role="owner")
        visible = (await session.scalars(select(ChangeLog.tenant_id))).all()
        assert set(visible) == {t1}

        session.add(ChangeLog(tenant_id=t2, action="person_create", rows=[]))
        try:
            await session.flush()
            raised = False
        except Exception:
            raised = True
        await session.rollback()
        assert raised, "WITH CHECK debería impedir insertar change_log de otro tenant"


async def test_rls_policy_covers_new_research_collaboration_and_sync_tables():
    """Every new tenant table is invisible outside its workspace (not just its API route)."""
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    ids = {name: uuid.uuid4() for name in (
        "repo1", "repo2", "tag1", "tag2", "project1", "project2", "connection1", "connection2",
        "search1", "search2",
    )}
    admin = create_async_engine(settings.admin_database_url)
    async with admin.begin() as conn:
        for tid, name, slug in [(t1, "T1", f"t1-{t1.hex[:6]}"), (t2, "T2", f"t2-{t2.hex[:6]}")]:
            await conn.execute(text("INSERT INTO tenants (id, name, slug) VALUES (:id, :n, :s)"),
                               {"id": tid, "n": name, "s": slug})
        for uid, email in [(u1, f"{u1.hex[:6]}@e.com"), (u2, f"{u2.hex[:6]}@e.com")]:
            await conn.execute(text("INSERT INTO users (id, email, password_hash) VALUES (:id, :e, 'x')"),
                               {"id": uid, "e": email})
        for tid, uid in ((t1, u1), (t2, u2)):
            await conn.execute(text("INSERT INTO memberships (tenant_id, user_id, role) VALUES (:t, :u, 'owner')"),
                               {"t": tid, "u": uid})
        for tid, repo, tag, project, connection in (
            (t1, ids["repo1"], ids["tag1"], ids["project1"], ids["connection1"]),
            (t2, ids["repo2"], ids["tag2"], ids["project2"], ids["connection2"]),
        ):
            await conn.execute(text("INSERT INTO repositories (id, tenant_id, name) VALUES (:id, :t, 'Archivo')"),
                               {"id": repo, "t": tid})
            await conn.execute(text("INSERT INTO research_sources (tenant_id, title, repository_id) VALUES (:t, 'Fuente', :r)"),
                               {"t": tid, "r": repo})
            await conn.execute(text("INSERT INTO research_notes (tenant_id, subject_type, subject_id, text) VALUES (:t, 'person', :s, 'nota')"),
                               {"t": tid, "s": uuid.uuid4()})
            await conn.execute(text("INSERT INTO research_tags (id, tenant_id, name) VALUES (:id, :t, 'etiqueta')"),
                               {"id": tag, "t": tid})
            await conn.execute(text("INSERT INTO research_object_tags (tenant_id, tag_id, target_type, target_id) VALUES (:t, :tag, 'person', :target)"),
                               {"t": tid, "tag": tag, "target": uuid.uuid4()})
            await conn.execute(text("INSERT INTO research_projects (id, tenant_id, title) VALUES (:id, :t, 'Proyecto')"),
                               {"id": project, "t": tid})
            await conn.execute(text("INSERT INTO research_tasks (tenant_id, project_id, title) VALUES (:t, :p, 'Tarea')"),
                               {"t": tid, "p": project})
            await conn.execute(text("INSERT INTO research_assertions (tenant_id, subject_type, subject_id, predicate, value) VALUES (:t, 'person', :s, 'name', '{}'::jsonb)"),
                               {"t": tid, "s": uuid.uuid4()})
            search_id = ids["search1"] if tid == t1 else ids["search2"]
            await conn.execute(text("INSERT INTO research_searches (id, tenant_id, project_id, scope, criteria, result) VALUES (:id, :t, :p, 'corpus', 'apellido=Vega', 'not_found')"),
                               {"id": search_id, "t": tid, "p": project})
            await conn.execute(text("INSERT INTO mutation_requests (tenant_id, idempotency_key, request_hash, response) VALUES (:t, :k, repeat('a', 64), '{}'::jsonb)"),
                               {"t": tid, "k": f"request-{tid.hex}"})
            await conn.execute(text("INSERT INTO change_proposals (tenant_id, operation, patch) VALUES (:t, 'create_person', '{}'::jsonb)"),
                               {"t": tid})
            await conn.execute(text("INSERT INTO publications (tenant_id, title, slug, access_token_hash, person_ids) VALUES (:t, 'Árbol', :slug, repeat('0', 64), '[]'::jsonb)"),
                               {"t": tid, "slug": f"tree-{tid.hex}"})
            await conn.execute(text("INSERT INTO interop_connections (id, tenant_id, provider, name) VALUES (:id, :t, 'gramps_desktop', 'Equipo')"),
                               {"id": connection, "t": tid})
            await conn.execute(text("INSERT INTO sync_baselines (tenant_id, connection_id, snapshot_hash, object_counts) VALUES (:t, :c, repeat('a', 64), '{}'::jsonb)"),
                               {"t": tid, "c": connection})
            await conn.execute(text("INSERT INTO sync_changes (tenant_id, connection_id, source_system, object_type, object_key, operation) VALUES (:t, :c, 'gramps_desktop', 'person', 'I1', 'upsert')"),
                               {"t": tid, "c": connection})
            await conn.execute(text("INSERT INTO sync_objects (tenant_id, connection_id, object_type, object_key) VALUES (:t, :c, 'person', 'I1')"),
                               {"t": tid, "c": connection})
    await admin.dispose()

    tables = (
        "repositories", "research_sources", "research_notes", "research_tags", "research_object_tags",
        "research_projects", "research_tasks", "research_assertions", "research_searches", "mutation_requests", "change_proposals", "publications",
        "interop_connections", "sync_baselines", "sync_changes", "sync_objects",
    )
    async with SessionLocal() as session:
        await set_rls_context(session, user_id=u1, tenant_id=t1, role="owner")
        for table in tables:
            visible = (await session.scalars(text(f"SELECT tenant_id FROM {table}"))).all()
            assert set(visible) == {t1}, table
