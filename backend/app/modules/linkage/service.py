"""Linkage service: seed from a tree Person, retrieve candidate mentions by blocking, score
them transparently (scoring.py), persist MatchCandidates, and apply human decisions. The tree is
NEVER written except through ``confirm_candidate`` / ``accept_proposal`` (plan §4–5).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.citation import Citation
from ...models.event import Event
from ...models.family import Family, FamilyChild
from ...models.match_candidate import MatchCandidate
from ...models.mention import PersonMention
from ...models.person import Name, Person
from ...models.place import Place
from ...models.record import Record
from ..extraction.normalize import block_key_surname, norm_given, norm_surname, parse_age, split_name
from ..providers.service import (
    ProviderService, embed_texts, extract_structured_with_usage, record_usage)
from ..tree.service import get_person_detail
from .scoring import Candidate, ENQUEUE_FLOOR, Seed, score_candidate

# JSON schema for the LLM "same person?" adjudication (plan §4, ambiguous band only).
_ADJ_SCHEMA = {
    "type": "object",
    "properties": {
        "match": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["match", "confidence", "reasoning"],
}
_ADJ_SYSTEM = (
    "Eres un genealogista. Decide si la persona del árbol y la persona mencionada en el acta "
    "histórica son la MISMA. Responde sólo el JSON {match, confidence (0..1), reasoning} en español."
)


async def _adjudicate_band(session, tenant_id, seed: Seed, scored: list, max_llm: int = 5,
                           on_progress=None) -> None:
    """For the top candidates in the ambiguous band (scoring.LLM_BAND), ask an LLM to adjudicate.
    Graceful: if no inference provider is configured, leaves scores untouched (plan §4)."""
    band = [t for t in scored if t[3].get("needs_llm")]
    band.sort(key=lambda t: t[0].score, reverse=True)
    band = band[:max_llm]
    if not band:
        return
    try:
        rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type="inference")
    except Exception:
        return  # no LLM configured → keep heuristic scores
    seed_desc = (
        f"Árbol: {seed.given or ''} {seed.surname or ''}, nacimiento ~{seed.birth_year or '?'}, "
        f"padres/cónyuge conocidos: {', '.join(sorted(seed.parent_names | seed.spouse_names)) or '—'}."
    )
    total = len(band)
    tokens = {"prompt": 0, "completion": 0}
    for i, (mc, m, rec, result) in enumerate(band, start=1):
        if on_progress:
            await on_progress({"phase": "adjudicating", "done": i, "total": total})
        prompt = (
            f"{seed_desc}\nActa: {rec.record_type if rec else '?'} {rec.date_year if rec else ''} — "
            f"menciona a «{m.name_raw or ''}» como {m.role}. Resumen: {rec.summary if rec else ''}.\n"
            "¿Son la misma persona?"
        )
        try:
            out, usage = await asyncio.to_thread(
                extract_structured_with_usage, rc, prompt,
                schema=_ADJ_SCHEMA, system=_ADJ_SYSTEM, schema_name="adjudication",
            )
            tokens["prompt"] += usage.get("prompt", 0)
            tokens["completion"] += usage.get("completion", 0)
        except Exception:
            continue
        match = bool(out.get("match"))
        conf = float(out.get("confidence") or 0.0)
        result["llm"] = {"match": match, "confidence": conf, "reasoning": out.get("reasoning", "")}
        mc.method = "llm_adjudicated"
        # nudge the heuristic score toward the LLM verdict (kept transparent in evidence)
        mc.score = round(min(1.0, mc.score + 0.2 * conf) if match else max(0.0, mc.score - 0.2 * conf), 4)
        result["score"] = mc.score
        mc.evidence = result
    # Log the adjudication spend (best-effort; no job_id here — the caller's job context varies).
    if tokens["prompt"] or tokens["completion"]:
        await record_usage(session, tenant_id=tenant_id, job_id=None, task_type="linkage",
                           model=rc.model, prompt_tokens=tokens["prompt"],
                           completion_tokens=tokens["completion"])

# Which inferred Event a confirmed record implies for the principal (None = no person event).
RECORD_EVENT_TYPE = {
    "baptism": "baptism", "death": "death", "marriage": "marriage",
    "confirmation": "confirmation", "census": "census", "other": None,
}

# How a co-mentioned person relates to the focal person of the act, and the implied sex. Covers
# sacramental, census (head/son/daughter…), will (testator/heir/child) and military roles so that
# accepting a co-resident/relative from any document type builds the right tree link.
ROLE_RELATION = {
    # sacramental
    "father": ("parent", "M"), "mother": ("parent", "F"),
    "spouse": ("spouse", None), "spouse_father": ("other", "M"), "spouse_mother": ("other", "F"),
    "godfather": ("other", "M"), "godmother": ("other", "F"),
    "witness": ("other", None), "declarant": ("other", None), "officiant": ("other", None),
    "principal": ("self", None), "other": ("other", None), "relative": ("other", None),
    # census / household (relation is stated relative to the head)
    "head": ("self", None), "son": ("child", "M"), "daughter": ("child", "F"), "child": ("child", None),
    "sibling": ("sibling", None), "grandparent": ("other", None), "grandchild": ("other", None),
    "in_law": ("other", None), "servant": ("other", None), "lodger": ("other", None),
    # will / notarial / judicial / military
    "testator": ("self", None), "heir": ("child", None), "executor": ("other", None),
    "party": ("other", None), "notary": ("other", None),
    "defendant": ("self", None), "plaintiff": ("other", None), "judge": ("other", None),
    "victim": ("other", None), "soldier": ("self", None), "resident": ("self", None),
}


async def build_seed(session: AsyncSession, person_id: uuid.UUID) -> Seed:
    detail = await get_person_detail(session, person_id)
    primary = next((n for n in detail.names if n.is_primary), detail.names[0] if detail.names else None)
    birth = next((e.date_year for e in detail.events if e.type == "birth" and e.date_year), None)
    death = next((e.date_year for e in detail.events if e.type == "death" and e.date_year), None)
    place_key = None
    for e in detail.events:
        if e.place:
            place_key = e.place.strip().lower()
            break
    parent_names = {norm_surname(p.surname) for p in detail.parents if p.surname}
    parent_names |= {norm_surname(p.given) for p in detail.parents if p.given}
    spouse_names = {norm_surname(s.surname) for s in detail.spouses if s.surname}
    spouse_names |= {norm_surname(s.given) for s in detail.spouses if s.given}
    sibling_names = {norm_surname(s.surname) for s in detail.siblings if s.surname}
    sibling_names |= {norm_surname(s.given) for s in detail.siblings if s.given}
    return Seed(
        given=primary.given if primary else None,
        surname=primary.surname if primary else None,
        birth_year=birth, death_year=death, place_key=place_key,
        parent_names={n for n in parent_names if n},
        spouse_names={n for n in spouse_names if n},
        sibling_names={n for n in sibling_names if n},
    )


async def _co_mentions(session: AsyncSession, record_ids: set[uuid.UUID]) -> dict[uuid.UUID, list[PersonMention]]:
    if not record_ids:
        return {}
    rows = (
        await session.scalars(
            select(PersonMention).where(PersonMention.record_id.in_(record_ids))
        )
    ).all()
    out: dict[uuid.UUID, list[PersonMention]] = {}
    for m in rows:
        out.setdefault(m.record_id, []).append(m)
    return out


async def generate_candidates(
    session: AsyncSession, tenant_id: uuid.UUID, person_id: uuid.UUID, max_candidates: int = 50,
    adjudicate: bool = True, on_progress=None,
) -> int:
    """Blocking → scoring → (optional LLM adjudication of the ambiguous band) → persist pending
    MatchCandidates. Returns the number persisted. ``on_progress`` (async) receives phase events for
    live job progress."""
    seed = await build_seed(session, person_id)
    bk = block_key_surname(seed.surname)
    ns = norm_surname(seed.surname)
    if not bk and not ns:
        return 0

    conds = []
    if bk:
        conds.append(PersonMention.block_key_surname == bk)
    if ns:
        conds.append(PersonMention.norm_surname.ilike(f"%{ns}%"))
    mentions = list(
        (
            await session.scalars(
                select(PersonMention)
                .where(PersonMention.tenant_id == tenant_id, or_(*conds))
                .limit(max(max_candidates * 6, 60))
            )
        ).all()
    )

    # hybrid recall: union the phonetic/trigram blocking with semantic vector retrieval over
    # embedded mentions (plan §4). Graceful: no embedding provider or no embeddings → blocking only.
    vec_ids = await _vector_retrieve(session, tenant_id, seed, limit=max(max_candidates * 4, 40))
    if vec_ids:
        have = {m.id for m in mentions}
        extra = (
            await session.scalars(
                select(PersonMention).where(PersonMention.id.in_([i for i in vec_ids if i not in have]))
            )
        ).all()
        mentions.extend(extra)

    if not mentions:
        return 0

    if on_progress:
        await on_progress({"phase": "scoring", "done": 0, "total": len(mentions)})

    record_ids = {m.record_id for m in mentions}
    records = {
        r.id: r for r in (await session.scalars(select(Record).where(Record.id.in_(record_ids)))).all()
    }
    # never propose a mention from a superseded act (replaced after correction + re-extract)
    mentions = [m for m in mentions if records.get(m.record_id) is None or records[m.record_id].status != "superseded"]
    records = {rid: r for rid, r in records.items() if r.status != "superseded"}
    if not mentions:
        return 0
    place_keys = await _place_keys(session, {r.place_id for r in records.values() if r.place_id})
    co = await _co_mentions(session, record_ids)

    # avoid re-creating candidates that already exist for this person
    existing = set(
        (await session.scalars(
            select(MatchCandidate.person_mention_id).where(
                MatchCandidate.tree_person_id == person_id
            )
        )).all()
    )

    scored: list[tuple[MatchCandidate, PersonMention, Record | None, dict]] = []
    for m in mentions:
        if m.id in existing:
            continue
        rec = records.get(m.record_id)
        others = {
            norm_surname(x.surname) for x in co.get(m.record_id, []) if x.id != m.id and x.surname
        }
        others |= {
            norm_surname(x.given) for x in co.get(m.record_id, []) if x.id != m.id and x.given
        }
        cand = Candidate(
            given=m.given, surname=m.surname, role=m.role,
            record_year=rec.date_year if rec else None,
            record_place_key=place_keys.get(rec.place_id) if rec and rec.place_id else None,
            stated_age=parse_age(m.stated_age),
            address=m.address or (rec.address if rec else None),
            co_mention_names={o for o in others if o},
        )
        result = score_candidate(seed, cand)
        if result["score"] < ENQUEUE_FLOOR:
            continue
        mc = MatchCandidate(
            tenant_id=tenant_id, tree_person_id=person_id, person_mention_id=m.id,
            record_id=m.record_id, score=result["score"], evidence=result,
            status="pending", method="auto",
        )
        scored.append((mc, m, rec, result))

    if adjudicate:
        await _adjudicate_band(session, tenant_id, seed, scored, max_llm=5, on_progress=on_progress)

    scored.sort(key=lambda t: t[0].score, reverse=True)
    persisted = 0
    for mc, *_ in scored[:max_candidates]:
        session.add(mc)
        persisted += 1
    await session.flush()
    return persisted


_BAPTISM_TYPES = ("baptism", "christening")


async def _seed_couple(
    session: AsyncSession, tenant_id: uuid.UUID, person_id: uuid.UUID
) -> tuple[str | None, str | None, str | None, str | None, set[uuid.UUID]] | None:
    """The person's parent pair (father_given, father_surname, mother_given, mother_surname) plus the
    record ids that already belong to the person. Source of truth, in order: the father/mother
    co-mentions of the person's CONFIRMED baptism (exact names as written); else the tree parents."""
    own = (await session.scalars(
        select(PersonMention).where(
            PersonMention.tenant_id == tenant_id, PersonMention.resolved_person_id == person_id)
    )).all()
    own_record_ids = {m.record_id for m in own if m.record_id}
    if own_record_ids:
        recs = {r.id: r for r in (await session.scalars(
            select(Record).where(Record.id.in_(own_record_ids)))).all()}
        bap_ids = [rid for rid, r in recs.items() if r.record_type in _BAPTISM_TYPES]
        if bap_ids:
            co = await _co_mentions(session, set(bap_ids))
            for rid in bap_ids:
                f = next((x for x in co.get(rid, []) if x.role == "father"), None)
                mo = next((x for x in co.get(rid, []) if x.role == "mother"), None)
                if f or mo:
                    return ((f.given if f else None), (f.surname if f else None),
                            (mo.given if mo else None), (mo.surname if mo else None), own_record_ids)
    detail = await get_person_detail(session, person_id)
    dad = next((p for p in detail.parents if p.sex == "M"), None)
    mom = next((p for p in detail.parents if p.sex == "F"), None)
    if dad or mom:
        return ((dad.given if dad else None), (dad.surname if dad else None),
                (mom.given if mom else None), (mom.surname if mom else None), own_record_ids)
    return None


async def generate_family_candidates(
    session: AsyncSession, tenant_id: uuid.UUID, person_id: uuid.UUID, max_candidates: int = 50,
    on_progress=None,
) -> int:
    """Find the person's SIBLING SET: other baptisms naming the SAME parent pair (couple-key). Each is
    surfaced as a pending candidate with relation='sibling'; confirming one attaches the sibling to the
    person's parent-family and materializes/confirms the parents. The strongest path to the parents."""
    from .reconstruct import _couple_key  # local import avoids a module cycle

    couple = await _seed_couple(session, tenant_id, person_id)
    if not couple:
        return 0  # no parents nor confirmed baptism → cannot form a couple key
    fg, fs, mg, ms, own_record_ids = couple
    key = _couple_key(fg, fs, mg, ms, None, False)
    fbk, mbk = block_key_surname(fs), block_key_surname(ms)
    if not fbk and not mbk:
        return 0

    pconds = []
    if fbk:
        pconds.append(PersonMention.block_key_surname == fbk)
    if mbk:
        pconds.append(PersonMention.block_key_surname == mbk)
    parent_mentions = (await session.scalars(
        select(PersonMention).where(
            PersonMention.tenant_id == tenant_id,
            PersonMention.role.in_(("father", "mother")), or_(*pconds))
    )).all()
    cand_record_ids = {m.record_id for m in parent_mentions if m.record_id} - own_record_ids
    if not cand_record_ids:
        return 0

    recs = {r.id: r for r in (await session.scalars(
        select(Record).where(Record.id.in_(cand_record_ids), Record.record_type.in_(_BAPTISM_TYPES)))).all()}
    if not recs:
        return 0
    co = await _co_mentions(session, set(recs.keys()))
    place_keys = await _place_keys(session, {r.place_id for r in recs.values() if r.place_id})

    seed = await build_seed(session, person_id)
    # inject the discovered parents so relational_corroboration fires even if they aren't in the tree yet
    seed.parent_names = {n for n in (
        norm_surname(fs), norm_given(fg), norm_surname(ms), norm_given(mg)) if n}

    # One candidate per (person, mention) — DB unique constraint. A generic /discover may already
    # hold a PENDING self-candidate on the same mention (person X ≟ act of Y); when the couple-key
    # proves that act is a SIBLING's baptism, upgrade that row in place instead of skipping it
    # (skipping would let a weak self-match permanently block the sibling interpretation).
    existing_rows = (await session.scalars(
        select(MatchCandidate).where(MatchCandidate.tree_person_id == person_id))).all()
    upgradable = {mc.person_mention_id: mc for mc in existing_rows
                  if mc.status == "pending" and mc.relation != "sibling"}
    existing = {mc.person_mention_id for mc in existing_rows}

    scored: list[MatchCandidate] = []
    upgraded = 0
    for rid, rec in recs.items():
        ms_list = co.get(rid, [])
        f = next((x for x in ms_list if x.role == "father"), None)
        mo = next((x for x in ms_list if x.role == "mother"), None)
        rkey = _couple_key((f.given if f else None), (f.surname if f else None),
                           (mo.given if mo else None), (mo.surname if mo else None), None, False)
        if rkey != key:
            continue  # different parents → not a sibling
        principal = next((x for x in ms_list if x.role == "principal"), None)
        if not principal:
            continue
        if principal.id in existing:
            mc = upgradable.get(principal.id)
            if mc is not None:
                mc.relation = "sibling"
                ev = dict(mc.evidence or {})
                ev["sibling_couple_key"] = key
                mc.evidence = ev
                upgraded += 1
            continue
        others = {norm_surname(x.surname) for x in ms_list if x.id != principal.id and x.surname}
        others |= {norm_surname(x.given) for x in ms_list if x.id != principal.id and x.given}
        cand = Candidate(
            given=principal.given, surname=principal.surname, role="principal",
            record_year=rec.date_year,
            record_place_key=place_keys.get(rec.place_id) if rec.place_id else None,
            stated_age=parse_age(principal.stated_age),
            address=principal.address or rec.address,
            co_mention_names={o for o in others if o},
        )
        result = score_candidate(seed, cand)
        if result["score"] < ENQUEUE_FLOOR:
            continue
        scored.append(MatchCandidate(
            tenant_id=tenant_id, tree_person_id=person_id, person_mention_id=principal.id,
            record_id=rid, score=result["score"], evidence=result,
            status="pending", method="auto", relation="sibling",
        ))

    scored.sort(key=lambda m: m.score, reverse=True)
    keep = scored[:max_candidates]
    for mc in keep:
        session.add(mc)
    await session.flush()
    return len(keep) + upgraded


async def _materialize_parents(
    session: AsyncSession, tenant_id: uuid.UUID, family: Family, record_id: uuid.UUID | None
) -> None:
    """Create/link the father & mother persons (from a baptism's father/mother mentions) into ``family``
    (husband_id/wife_id), reusing existing tree people by name. Idempotent — skips slots already filled.
    This is how confirming a sibling set materializes/confirms the parents."""
    if not record_id:
        return
    co = (await session.scalars(
        select(PersonMention).where(
            PersonMention.record_id == record_id,
            PersonMention.role.in_(("father", "mother")))
    )).all()
    for pm in co:
        slot = "husband_id" if pm.role == "father" else "wife_id"
        if getattr(family, slot):
            continue
        given = pm.given or split_name(pm.name_raw)[0] or None
        surname = pm.surname or split_name(pm.name_raw)[1] or None
        if not (given or surname):
            continue
        pid = await _find_existing_person(session, tenant_id, given, surname)
        if not pid:
            parent = Person(tenant_id=tenant_id, sex="M" if pm.role == "father" else "F")
            session.add(parent)
            await session.flush()
            session.add(Name(
                tenant_id=tenant_id, person_id=parent.id, type="birth",
                given=given, surname=surname, is_primary=True, is_inferred=True))
            pid = parent.id
            session.add(Citation(
                tenant_id=tenant_id, target_type="person", target_id=pid,
                record_id=record_id, person_mention_id=pm.id,
                note="Padre/madre materializado del conjunto de hermanos"))
        setattr(family, slot, pid)
        if pm.resolved_person_id is None:
            pm.resolved_person_id = pid
    await session.flush()


async def _confirm_sibling(
    session: AsyncSession, mc: MatchCandidate, user_id: uuid.UUID
) -> MatchCandidate:
    """Confirm a 'sibling' candidate: create the sibling person, attach to the seed person's
    parent-family, and materialize/confirm the shared parents from the record."""
    mention = await session.get(PersonMention, mc.person_mention_id)
    record = await session.get(Record, mc.record_id) if mc.record_id else None
    page_id = record.page_id if record else None

    given = (mention.given if mention else None) or (split_name(mention.name_raw)[0] if mention else None) or None
    surname = (mention.surname if mention else None) or (split_name(mention.name_raw)[1] if mention else None) or None
    sex = mention.sex if (mention and mention.sex in ("M", "F")) else "U"

    existing_id = await _find_existing_person(session, mc.tenant_id, given, surname)
    if existing_id:
        sibling = await session.get(Person, existing_id)
    else:
        sibling = Person(tenant_id=mc.tenant_id, sex=sex)
        session.add(sibling)
        await session.flush()
        session.add(Name(
            tenant_id=mc.tenant_id, person_id=sibling.id, type="birth",
            given=given, surname=surname, is_primary=True, is_inferred=True))

    family = await _parent_family(session, mc.tenant_id, mc.tree_person_id)
    await _add_child(session, mc.tenant_id, family.id, sibling.id)
    await _materialize_parents(session, mc.tenant_id, family, mc.record_id)

    if mention:
        mention.resolved_person_id = sibling.id
        mention.match_status = "confirmed"

    if record:
        ev_type = RECORD_EVENT_TYPE.get(record.record_type)
        if ev_type:
            event = Event(
                tenant_id=mc.tenant_id, type=ev_type, date_raw=record.date_raw,
                date_year=record.date_year, place_id=record.place_id, is_inferred=True,
                subject_person_id=sibling.id)
            session.add(event)
            await session.flush()
            session.add(Citation(
                tenant_id=mc.tenant_id, target_type="event", target_id=event.id,
                record_id=mc.record_id, person_mention_id=mc.person_mention_id,
                match_candidate_id=mc.id, page_id=page_id))
    session.add(Citation(
        tenant_id=mc.tenant_id, target_type="person", target_id=sibling.id,
        record_id=mc.record_id, person_mention_id=mc.person_mention_id,
        match_candidate_id=mc.id, page_id=page_id,
        note="Hermano/a descubierto por padres compartidos"))

    mc.status = "confirmed"
    mc.decided_by = user_id
    mc.decided_at = datetime.now(timezone.utc)
    await session.flush()
    return mc


async def _vector_retrieve(
    session: AsyncSession, tenant_id: uuid.UUID, seed: Seed, limit: int
) -> list[uuid.UUID]:
    """Semantic recall over embedded person_mentions (cosine), cloning search/service.vector_search.
    Graceful no-op when no embedding provider is configured or no mentions are embedded yet."""
    # cheap pre-check: are there any embedded mentions at all?
    any_emb = await session.scalar(
        select(PersonMention.id).where(
            PersonMention.tenant_id == tenant_id, PersonMention.embedding.is_not(None)
        ).limit(1)
    )
    if not any_emb:
        return []
    seed_text = " ".join(filter(None, [seed.given, seed.surname, seed.place_key])).strip()
    if not seed_text:
        return []
    try:
        rc = await ProviderService(session).resolve(tenant_id=tenant_id, task_type="embedding")
        qvec = (await asyncio.to_thread(embed_texts, rc, [seed_text]))[0]
    except Exception:
        return []
    qv = "[" + ",".join(repr(float(x)) for x in qvec) + "]"
    sql = sa_text(
        """
        SELECT id FROM person_mentions
        WHERE tenant_id = :tid AND embedding IS NOT NULL
        ORDER BY embedding <=> (:qv)::vector
        LIMIT :limit
        """
    )
    rows = (await session.execute(sql, {"tid": tenant_id, "qv": qv, "limit": limit})).all()
    return [r.id for r in rows]


async def _place_keys(session: AsyncSession, place_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    place_ids = {p for p in place_ids if p}
    if not place_ids:
        return {}
    rows = (await session.execute(
        select(Place.id, Place.normalized_key).where(Place.id.in_(place_ids))
    )).all()
    return {pid: key for pid, key in rows}


async def list_candidates(
    session: AsyncSession, person_id: uuid.UUID | None, status_filter: str | None,
    limit: int = 100, offset: int = 0,
):
    stmt = select(MatchCandidate)
    if person_id:
        stmt = stmt.where(MatchCandidate.tree_person_id == person_id)
    if status_filter:
        stmt = stmt.where(MatchCandidate.status == status_filter)
    stmt = stmt.order_by(MatchCandidate.score.desc()).limit(limit).offset(offset)
    return list((await session.scalars(stmt)).all())


async def confirm_candidate(
    session: AsyncSession, candidate_id: uuid.UUID, user_id: uuid.UUID
) -> MatchCandidate:
    mc = await session.get(MatchCandidate, candidate_id)
    if not mc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate not found")
    if mc.status != "pending":
        return mc
    if getattr(mc, "relation", "self") == "sibling":
        return await _confirm_sibling(session, mc, user_id)
    mention = await session.get(PersonMention, mc.person_mention_id)
    if mention:
        mention.resolved_person_id = mc.tree_person_id
        mention.match_status = "confirmed"
    mc.status = "confirmed"
    mc.decided_by = user_id
    mc.decided_at = datetime.now(timezone.utc)
    record = await session.get(Record, mc.record_id) if mc.record_id else None
    page_id = record.page_id if record else None

    # inferred Event for the principal (e.g. a baptism record → a baptism event), with its Citation
    if record:
        ev_type = RECORD_EVENT_TYPE.get(record.record_type)
        # Create the inferred event whenever the record type implies one — even if date/place weren't
        # extracted (a baptism with no date is still a baptism fact, with its source).
        if ev_type:
            event = Event(
                tenant_id=mc.tenant_id, type=ev_type, date_raw=record.date_raw,
                date_year=record.date_year, place_id=record.place_id, is_inferred=True,
                subject_person_id=mc.tree_person_id,
            )
            session.add(event)
            await session.flush()
            session.add(Citation(
                tenant_id=mc.tenant_id, target_type="event", target_id=event.id,
                record_id=mc.record_id, person_mention_id=mc.person_mention_id,
                match_candidate_id=mc.id, page_id=page_id,
            ))

    # provenance: cite the record as a source for this tree person
    session.add(Citation(
        tenant_id=mc.tenant_id, target_type="person", target_id=mc.tree_person_id,
        record_id=mc.record_id, person_mention_id=mc.person_mention_id,
        match_candidate_id=mc.id, page_id=page_id,
    ))
    await session.flush()
    return mc


async def _find_existing_person(
    session: AsyncSession, tenant_id: uuid.UUID, given: str | None, surname: str | None
) -> uuid.UUID | None:
    """Match a name against existing tree people (normalized given+surname) so accepting a relative
    that's already in the tree corroborates it (Citation) instead of creating a duplicate (plan §4)."""
    ng, ns = norm_given(given), norm_surname(surname)
    if not ns:
        return None
    rows = (
        await session.execute(
            select(Name.person_id, Name.given, Name.surname)
            .where(Name.tenant_id == tenant_id, Name.is_primary.is_(True),
                   Name.surname.ilike(f"%{surname}%"))
        )
    ).all()
    for pid, g, sn in rows:
        if norm_surname(sn) == ns and (not ng or norm_given(g) == ng):
            return pid
    return None


async def list_proposals(session: AsyncSession, candidate_id: uuid.UUID) -> list[PersonMention]:
    """After a candidate is confirmed, the OTHER people named in the same act become proposals to
    add to the tree as inferred (plan §4). Excludes the principal mention and already-resolved ones."""
    mc = await session.get(MatchCandidate, candidate_id)
    if not mc or not mc.record_id:
        return []
    rows = (
        await session.scalars(
            select(PersonMention).where(
                PersonMention.record_id == mc.record_id,
                PersonMention.id != mc.person_mention_id,
                PersonMention.resolved_person_id.is_(None),
            )
        )
    ).all()
    return list(rows)


async def _parent_family(session: AsyncSession, tenant_id: uuid.UUID, child_id: uuid.UUID) -> Family:
    """The family in which ``child_id`` is a child, creating an empty one if none exists yet."""
    fam_id = await session.scalar(
        select(FamilyChild.family_id).where(FamilyChild.person_id == child_id).limit(1)
    )
    if fam_id:
        return await session.get(Family, fam_id)
    family = Family(tenant_id=tenant_id)
    session.add(family)
    await session.flush()
    session.add(FamilyChild(tenant_id=tenant_id, family_id=family.id, person_id=child_id, relation="inferred"))
    return family


async def _add_child(session: AsyncSession, tenant_id: uuid.UUID, family_id: uuid.UUID, child_id: uuid.UUID) -> None:
    exists = await session.scalar(
        select(FamilyChild.person_id).where(
            FamilyChild.family_id == family_id, FamilyChild.person_id == child_id
        )
    )
    if not exists:
        session.add(FamilyChild(tenant_id=tenant_id, family_id=family_id, person_id=child_id, relation="inferred"))


async def _spouse_family(session: AsyncSession, tenant_id: uuid.UUID, person_id: uuid.UUID) -> Family:
    fam = await session.scalar(
        select(Family).where(or_(Family.husband_id == person_id, Family.wife_id == person_id)).limit(1)
    )
    if fam:
        return fam
    family = Family(tenant_id=tenant_id)
    session.add(family)
    await session.flush()
    return family


async def accept_proposal(
    session: AsyncSession, tenant_id: uuid.UUID, candidate_id: uuid.UUID,
    mention_id: uuid.UUID, user_id: uuid.UUID,
) -> Person:
    """Write a co-mentioned relative into the tree as an INFERRED person (Person + primary Name with
    is_inferred=True) linked to the confirmed principal, with a Citation back to the source. The
    new person becomes a future seed (the discovery flywheel). Never auto-merges existing people."""
    mc = await session.get(MatchCandidate, candidate_id)
    if not mc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate not found")
    if mc.status != "confirmed":
        raise HTTPException(status.HTTP_409_CONFLICT, "confirm the candidate before adding relatives")
    mention = await session.get(PersonMention, mention_id)
    if not mention or mention.record_id != mc.record_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mention not part of this act")
    if mention.resolved_person_id:
        return await session.get(Person, mention.resolved_person_id)

    relation, implied_sex = ROLE_RELATION.get(mention.role, ("other", None))
    given = mention.given or split_name(mention.name_raw)[0] or None
    surname = mention.surname or split_name(mention.name_raw)[1] or None
    sex = implied_sex or (mention.sex if mention.sex in ("M", "F") else "U")

    # dedup: if this relative already exists in the tree, corroborate it instead of duplicating
    existing_id = await _find_existing_person(session, tenant_id, given, surname)
    if existing_id:
        person = await session.get(Person, existing_id)
    else:
        person = Person(tenant_id=tenant_id, sex=sex)
        session.add(person)
        await session.flush()
        session.add(Name(
            tenant_id=tenant_id, person_id=person.id, type="birth",
            given=given, surname=surname, is_primary=True, is_inferred=True,
        ))

    principal_id = mc.tree_person_id
    if relation == "parent":
        family = await _parent_family(session, tenant_id, principal_id)
        if person.id not in (family.husband_id, family.wife_id):  # idempotent
            if sex == "F" and family.wife_id is None:
                family.wife_id = person.id
            elif family.husband_id is None:
                family.husband_id = person.id
            elif family.wife_id is None:
                family.wife_id = person.id
    elif relation == "spouse":
        family = await _spouse_family(session, tenant_id, principal_id)
        if person.id not in (family.husband_id, family.wife_id):  # idempotent
            if family.husband_id in (None, principal_id) and sex != "F":
                family.husband_id = family.husband_id or principal_id
                family.wife_id = person.id if family.wife_id is None else family.wife_id
            else:
                family.wife_id = family.wife_id or principal_id
                family.husband_id = person.id if family.husband_id is None else family.husband_id
    elif relation == "child":
        # new person is a child of the focal person → child of the focal person's couple-family
        family = await _spouse_family(session, tenant_id, principal_id)
        if family.husband_id is None and family.wife_id is None:
            family.husband_id = principal_id
        await _add_child(session, tenant_id, family.id, person.id)
    elif relation == "sibling":
        # new person shares the focal person's parents → child of the focal person's parent-family
        family = await _parent_family(session, tenant_id, principal_id)
        await _add_child(session, tenant_id, family.id, person.id)

    mention.resolved_person_id = person.id
    mention.match_status = "confirmed"
    note = (
        f"Corroborado por el acta como «{mention.role}» de la persona confirmada"
        if existing_id else
        f"Inferido del acta como «{mention.role}» de la persona confirmada"
    )
    session.add(Citation(
        tenant_id=tenant_id, target_type="person", target_id=person.id,
        record_id=mc.record_id, person_mention_id=mention.id, match_candidate_id=mc.id,
        page_id=(await session.get(Record, mc.record_id)).page_id if mc.record_id else None,
        note=note,
    ))
    await session.flush()
    return person


async def reject_candidate(
    session: AsyncSession, candidate_id: uuid.UUID, user_id: uuid.UUID
) -> MatchCandidate:
    mc = await session.get(MatchCandidate, candidate_id)
    if not mc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "candidate not found")
    if mc.status == "pending":
        mc.status = "rejected"
        mc.decided_by = user_id
        mc.decided_at = datetime.now(timezone.utc)
        mention = await session.get(PersonMention, mc.person_mention_id)
        if mention and mention.match_status != "confirmed":
            mention.match_status = "rejected"
    await session.flush()
    return mc
