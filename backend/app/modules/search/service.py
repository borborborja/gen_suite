"""Search over transcriptions: keyword (FTS), semantic (pgvector), hybrid (RRF).

All queries run on a tenant session, so RLS already confines rows to the active tenant + public
documents; ``scope`` refines further. Hybrid fusion is done in Python over the top-N of each.
"""
from __future__ import annotations

import uuid
from fastapi import HTTPException

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from .indexes import FROM_SQL, LIVE_SQL, ITEM_JOIN, FRESH_SQL, validate_vectors

RRF_K = 60

_SCOPE_SQL = {
    "tenant": " AND t.tenant_id = :tid",
    "public": " AND t.visibility = 'public'",
    "all": "",
}

# Same scope refinement, but over the records alias `r` used by the structured search.
_REC_SCOPE_SQL = {
    "tenant": " AND r.tenant_id = :tid",
    "public": " AND r.visibility = 'public'",
    "all": "",
}


async def search_records(
    session: AsyncSession,
    *,
    given: str | None = None,
    surname: str | None = None,
    record_type: str | None = None,
    place: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    role: str | None = None,
    document_id: uuid.UUID | None = None,
    q: str | None = None,
    qvec: list[float] | None = None,
    fuzzy: bool = True,
    scope: str = "all",
    tenant_id: uuid.UUID | None = None,
    limit: int = 30,
    generation_id: uuid.UUID | None = None,
    query_call_id: str | None = None,
) -> list[dict]:
    """Structured search over extracted acts: person_mentions JOIN records, filtered by name/type/
    place/year/role/book, with optional free-text (FTS over the page transcription, operators
    honoured) or semantic ordering (mention embedding). When ``fuzzy`` (default), names/place tolerate
    HTR errors and spelling variants via pg_trgm similarity + Spanish phonetic keys. Each hit resolves
    to the document + page for the Visor."""
    from ..extraction.normalize import (
        block_key_given, block_key_surname, norm_given, norm_surname,
    )
    from ..tree.mapping import normalize_place

    conds: list[str] = []
    params: dict = {"tid": tenant_id, "limit": limit, "thr": 0.3}
    surname_fuzzy = False

    if surname and surname.strip():
        sn = norm_surname(surname)
        if fuzzy:
            surname_fuzzy = True
            conds.append(
                "(m.norm_surname ILIKE :sur_like OR similarity(m.norm_surname, :sur_norm) >= :thr "
                "OR m.block_key_surname = :sur_block)")
            params["sur_like"] = f"%{sn}%"
            params["sur_norm"] = sn
            params["sur_block"] = block_key_surname(surname)
        else:
            conds.append("m.norm_surname ILIKE :sur_like")
            params["sur_like"] = f"%{sn}%"
    if given and given.strip():
        gn = norm_given(given)
        if fuzzy:
            conds.append(
                "(m.norm_given ILIKE :giv_like OR similarity(m.norm_given, :giv_norm) >= :thr "
                "OR m.block_key_given = :giv_block)")
            params["giv_like"] = f"%{gn}%"
            params["giv_norm"] = gn
            params["giv_block"] = block_key_given(given)
        else:
            conds.append("m.norm_given ILIKE :giv_like")
            params["giv_like"] = f"%{gn}%"
    if record_type:
        conds.append("r.record_type = :rt")
        params["rt"] = record_type
    if role:
        conds.append("m.role = :role")
        params["role"] = role
    if document_id is not None:
        conds.append("r.document_id = :doc")
        params["doc"] = str(document_id)
    if place and place.strip():
        if fuzzy:
            conds.append(
                "(pl.name ILIKE :place OR pl.normalized_key ILIKE :placekey "
                "OR similarity(pl.name, :placeraw) >= :thr)")
            params["placeraw"] = place.strip()
        else:
            conds.append("(pl.name ILIKE :place OR pl.normalized_key ILIKE :placekey)")
        params["place"] = f"%{place.strip()}%"
        params["placekey"] = f"%{normalize_place(place)}%"
    if year_from is not None:
        conds.append("r.date_year >= :yf")
        params["yf"] = year_from
    if year_to is not None:
        conds.append("r.date_year <= :yt")
        params["yt"] = year_to

    use_fts = bool(q and q.strip()) and qvec is None
    use_vec = qvec is not None and bool(q and q.strip())
    if use_fts:
        conds.append("tr.tsv @@ websearch_to_tsquery('spanish', :q)")
        params["q"] = q
        select_score = "ts_rank(tr.tsv, websearch_to_tsquery('spanish', :q)) AS score"
        order_by = "score DESC"
    elif use_vec:
        if generation_id is None: raise HTTPException(409, "La consulta necesita una generación vectorial identificada.")
        validate_vectors([qvec], 1)
        params["qv"] = "[" + ",".join(repr(float(x)) for x in qvec) + "]"
        params["gid"] = generation_id
        conds.extend(["e.generation_id=:gid AND e.status='ready'", FRESH_SQL["mention"]])
        select_score = "1 - (e.embedding <=> (:qv)::vector) AS score"
        order_by = "e.embedding <=> (:qv)::vector"
    elif surname_fuzzy:
        # No free-text: surface the closest spellings first.
        select_score = "similarity(m.norm_surname, :sur_norm) AS score"
        order_by = "score DESC NULLS LAST"
    else:
        select_score = "0.0 AS score"
        order_by = "r.date_year DESC NULLS LAST"

    # superseded records are audit history (replaced after a correction + re-extract) — a search
    # hit on them would open the Visor on an act that no longer exists
    conds.append(LIVE_SQL["mention"])

    where = (" AND ".join(conds)) if conds else "TRUE"
    where += _REC_SCOPE_SQL.get(scope, "")

    sql = text(
        f"""
        SELECT m.id AS mention_id, r.id AS record_id, r.document_id, d.title,
               COALESCE(pg.page_no, tr.page_no) AS page_no,
               r.record_type, r.date_raw, r.date_year, pl.name AS place,
               m.given, m.surname, m.role, r.summary, r.page_id, r.transcription_id, r.transcription_end_id,
               {"e.id AS embedding_item_id,e.ai_call_id AS embedding_ai_call_id,e.source_hash," if use_vec else "NULL::uuid AS embedding_item_id,NULL::uuid AS embedding_ai_call_id,NULL::text AS source_hash,"}
               {select_score}
        FROM person_mentions m
        JOIN records r ON r.id = m.record_id
        JOIN documents d ON d.id = r.document_id
        LEFT JOIN places pl ON pl.id = r.place_id
        LEFT JOIN pages pg ON pg.id = r.page_id
        LEFT JOIN transcriptions tr ON tr.id = r.transcription_id
        LEFT JOIN transcriptions te ON te.id = r.transcription_end_id
        {"JOIN embedding_items e ON e.kind='mention' AND " + ITEM_JOIN['mention'] if use_vec else ""}
        WHERE {where}
        ORDER BY {order_by}
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, params)).all()
    return [
        {
            "record_id": r.record_id, "mention_id": r.mention_id, "document_id": r.document_id,
            "document_title": r.title, "page_no": r.page_no, "record_type": r.record_type,
            "date_raw": r.date_raw, "date_year": r.date_year, "place": r.place,
            "given": r.given, "surname": r.surname, "role": r.role, "summary": r.summary,
            "score": float(r.score) if r.score is not None else 0.0,
            "page_id": r.page_id, "transcription_id": r.transcription_id, "transcription_end_id": r.transcription_end_id,
            "index_generation_id": generation_id, "query_ai_call_id": query_call_id,
            "embedding_item_id": r.embedding_item_id, "embedding_ai_call_id": r.embedding_ai_call_id, "source_hash": r.source_hash,
        }
        for r in rows
    ]


async def suggest_terms(
    session: AsyncSession, *, field: str, q: str, limit: int = 8,
) -> list[dict]:
    """'Did you mean?' suggestions: distinct similar surnames/given names/places (trigram + Spanish
    phonetic), so the UI can offer Balsera→Balseras / Bidal→Vidal / Belmes→Belmez. RLS confines to
    the active tenant."""
    from ..extraction.normalize import (
        block_key_given, block_key_surname, norm_given, norm_surname,
    )
    from ..tree.mapping import normalize_place

    qn = (q or "").strip()
    if len(qn) < 2:
        return []
    params: dict = {"thr": 0.3, "limit": limit}

    if field == "surname":
        params["qn"] = norm_surname(qn)
        params["block"] = block_key_surname(qn)
        sql = text(
            """
            SELECT surname AS value, COUNT(*) AS n, MAX(similarity(norm_surname, :qn)) AS sim
            FROM person_mentions
            WHERE surname IS NOT NULL
              AND (similarity(norm_surname, :qn) >= :thr OR block_key_surname = :block)
            GROUP BY surname ORDER BY sim DESC, n DESC LIMIT :limit
            """
        )
    elif field == "given":
        params["qn"] = norm_given(qn)
        params["block"] = block_key_given(qn)
        sql = text(
            """
            SELECT given AS value, COUNT(*) AS n, MAX(similarity(norm_given, :qn)) AS sim
            FROM person_mentions
            WHERE given IS NOT NULL
              AND (similarity(norm_given, :qn) >= :thr OR block_key_given = :block)
            GROUP BY given ORDER BY sim DESC, n DESC LIMIT :limit
            """
        )
    elif field == "place":
        params["raw"] = qn
        params["like"] = f"%{normalize_place(qn)}%"
        sql = text(
            """
            SELECT name AS value, 0 AS n, similarity(name, :raw) AS sim
            FROM places
            WHERE similarity(name, :raw) >= :thr OR normalized_key ILIKE :like
            ORDER BY sim DESC LIMIT :limit
            """
        )
    else:
        return []

    rows = (await session.execute(sql, params)).all()
    return [
        {"value": r.value, "count": int(r.n), "score": float(r.sim) if r.sim is not None else 0.0}
        for r in rows if r.value
    ]


def _hit(row, score: float) -> dict:
    return {
        "transcription_id": row.id,
        "document_id": row.document_id,
        "document_title": row.title,
        "page_no": row.page_no,
        "snippet": row.snippet,
        "score": score,
        "page_id": getattr(row, "page_id", None),
        "index_generation_id": getattr(row, "index_generation_id", None),
        "embedding_item_id": getattr(row, "embedding_item_id", None),
        "embedding_ai_call_id": getattr(row, "embedding_ai_call_id", None),
        "source_hash": getattr(row, "source_hash", None),
    }


async def keyword_search(
    session: AsyncSession, q: str, scope: str, tenant_id: uuid.UUID, limit: int
) -> list[dict]:
    sql = text(
        f"""
        SELECT t.id, t.document_id, t.page_no, t.page_id, d.title,
               ts_rank(t.tsv, websearch_to_tsquery('spanish', :q)) AS rank,
               ts_headline('spanish', coalesce(t.text, ''),
                           websearch_to_tsquery('spanish', :q),
                           'MaxFragments=1, MinWords=3, MaxWords=14, StartSel=«, StopSel=»') AS snippet
        FROM transcriptions t
        JOIN documents d ON d.id = t.document_id
        WHERE t.tsv @@ websearch_to_tsquery('spanish', :q) AND t.is_active{_SCOPE_SQL[scope]}
        ORDER BY rank DESC
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, {"q": q, "tid": tenant_id, "limit": limit})).all()
    return [_hit(r, float(r.rank)) for r in rows]


async def vector_search(
    session: AsyncSession, qvec: list[float], scope: str, tenant_id: uuid.UUID, limit: int,
    *, generation_id: uuid.UUID | None = None, query_call_id: str | None = None,
) -> list[dict]:
    if generation_id is None: raise HTTPException(409, "La consulta necesita una generación vectorial identificada.")
    validate_vectors([qvec], 1)
    qv = "[" + ",".join(repr(float(x)) for x in qvec) + "]"
    sql = text(
        f"""
        SELECT t.id, t.document_id, t.page_no, t.page_id, d.title,
               e.generation_id AS index_generation_id, e.id AS embedding_item_id, e.ai_call_id AS embedding_ai_call_id, e.source_hash,
               1 - (e.embedding <=> (:qv)::vector) AS sim,
               left(coalesce(t.text, ''), 180) AS snippet
        FROM {FROM_SQL['transcription']}
        JOIN embedding_items e ON e.kind='transcription' AND {ITEM_JOIN['transcription']}
        WHERE e.tenant_id=:tid AND e.generation_id=:gid AND e.status='ready' AND {LIVE_SQL['transcription']} AND {FRESH_SQL['transcription']}{_SCOPE_SQL[scope]}
        ORDER BY e.embedding <=> (:qv)::vector
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, {"qv": qv, "tid": tenant_id, "gid": generation_id, "limit": limit})).all()
    return [{**_hit(r, float(r.sim)), "query_ai_call_id": query_call_id} for r in rows]


async def hybrid_search(
    session: AsyncSession, q: str, qvec: list[float], scope: str, tenant_id: uuid.UUID, limit: int,
    *, generation_id: uuid.UUID | None = None, query_call_id: str | None = None,
) -> list[dict]:
    keyword = await keyword_search(session, q, scope, tenant_id, 50)
    vector = await vector_search(session, qvec, scope, tenant_id, 50, generation_id=generation_id, query_call_id=query_call_id)
    fused: dict[uuid.UUID, dict] = {}
    for ranked in (keyword, vector):
        for rank, hit in enumerate(ranked, start=1):
            entry = fused.setdefault(hit["transcription_id"], {**hit, "score": 0.0})
            entry["score"] += 1.0 / (RRF_K + rank)
            if hit.get("embedding_item_id"):
                entry.update({key: hit[key] for key in ("index_generation_id", "embedding_item_id", "embedding_ai_call_id", "query_ai_call_id", "source_hash")})
            if hit["snippet"] and not entry.get("snippet"):
                entry["snippet"] = hit["snippet"]
    return sorted(fused.values(), key=lambda h: h["score"], reverse=True)[:limit]
