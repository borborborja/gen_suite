"""Super-discovery: reconstruct a family tree from the whole corpus of sacramental records.

Strategy — couple-anchored union-find:
  * Baptisms give (father, mother) + child(principal). Grouping baptisms by a *block-key couple key*
    (phonetic-folded father+mother names, optionally + place) collapses spelling variation and yields
    each couple's children = siblings, with their parents.
  * A marriage names the couple AND each spouse's parents; because a marriage couple's key equals the
    couple key of that pair's children's baptisms, the spouse mention unions into the same person —
    and being a 'child' of their own parents' couple, generations climb automatically.
  * Census households (head + spouse + children) reinforce co-residence when enabled.
  * Within a couple, children that share a normalized given name are the same person (so a baptism
    links to that person's later marriage), which is the cross-generation bridge.

Output is a JSON graph of proposed persons + families with provenance (mention/record ids) — the real
tree is untouched until the user merges. Reuses normalize + linkage helpers; no LLM.
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.mention import PersonMention
from ...models.record import Record
from ..extraction.normalize import block_key_given, block_key_surname, norm_given, norm_surname
from .service import ROLE_RELATION

_BAPTISM = {"baptism", "christening"}
_MARRIAGE = {"marriage"}
_CENSUS = {"census", "electoral_census"}


class _UF:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        root = x
        while self.p[root] != root:
            root = self.p[root]
        while self.p[x] != root:
            self.p[x], x = root, self.p[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _couple_key(fg, fs, mg, ms, place, conservative) -> tuple:
    k = (block_key_surname(fs), block_key_given(fg), block_key_surname(ms), block_key_given(mg))
    return k + ((str(place),) if (conservative and place) else ())


async def build_reconstruction(
    session: AsyncSession, tenant_id: uuid.UUID, *,
    conservative: bool = True, include_census: bool = False, link_to_tree: bool = True,
    on_progress=None,
) -> tuple[dict, dict]:
    types = set(_BAPTISM) | set(_MARRIAGE) | (set(_CENSUS) if include_census else set())

    rows = (await session.execute(
        select(
            PersonMention.id, PersonMention.given, PersonMention.surname, PersonMention.sex,
            PersonMention.role, PersonMention.record_id,
            Record.record_type, Record.date_year, Record.place_id,
        ).join(Record, Record.id == PersonMention.record_id)
        .where(Record.tenant_id == tenant_id, Record.record_type.in_(types))
    )).all()

    if on_progress:
        await on_progress({"phase": "loading", "done": 0, "total": len(rows)})

    # index by record
    by_record: dict[uuid.UUID, dict] = defaultdict(lambda: {"rt": None, "year": None, "place": None, "roles": defaultdict(list)})
    mdata: dict[str, dict] = {}
    for mid, given, surname, sex, role, rec_id, rt, year, place in rows:
        sid = str(mid)
        mdata[sid] = {"given": given, "surname": surname, "sex": sex, "role": role,
                      "record_id": str(rec_id), "rt": rt, "year": year}
        r = by_record[rec_id]
        r["rt"], r["year"], r["place"] = rt, year, place
        r["roles"][role].append(sid)

    uf = _UF()
    # couple_key -> {fathers, mothers, children, record_ids}
    couples: dict[tuple, dict] = defaultdict(lambda: {"fathers": set(), "mothers": set(), "children": set(), "records": set()})

    def _first(ids: list[str]) -> str | None:
        return ids[0] if ids else None

    def _reg_couple(father_ids, mother_ids, child_ids, place, rec_id):
        fi, mi = _first(father_ids), _first(mother_ids)
        if not fi and not mi:
            return
        fg = mdata[fi]["given"] if fi else None
        fs = mdata[fi]["surname"] if fi else None
        mg = mdata[mi]["given"] if mi else None
        ms = mdata[mi]["surname"] if mi else None
        ck = _couple_key(fg, fs, mg, ms, place, conservative)
        c = couples[ck]
        for x in father_ids:
            c["fathers"].add(x)
        for x in mother_ids:
            c["mothers"].add(x)
        for x in child_ids:
            c["children"].add(x)
        c["records"].add(str(rec_id))

    for rec_id, r in by_record.items():
        roles, rt, place = r["roles"], r["rt"], r["place"]
        if rt in _BAPTISM:
            _reg_couple(roles.get("father", []), roles.get("mother", []), roles.get("principal", []), place, rec_id)
        elif rt in _MARRIAGE:
            # the couple = principal + spouse (split by sex; default principal=husband)
            princ = roles.get("principal", [])
            spouse = roles.get("spouse", [])
            husb = [m for m in princ + spouse if mdata[m]["sex"] == "M"] or princ
            wife = [m for m in princ + spouse if mdata[m]["sex"] == "F"] or spouse
            _reg_couple(husb, wife, [], place, rec_id)
            # principal's parents (principal is their child)
            if (roles.get("father") or roles.get("mother")) and princ:
                _reg_couple(roles.get("father", []), roles.get("mother", []), princ, place, rec_id)
            # spouse's parents (spouse is their child)
            if (roles.get("spouse_father") or roles.get("spouse_mother")) and spouse:
                _reg_couple(roles.get("spouse_father", []), roles.get("spouse_mother", []), spouse, place, rec_id)
        elif rt in _CENSUS:
            head = roles.get("head", [])
            spouse = roles.get("spouse", [])
            husb = [m for m in head + spouse if mdata[m]["sex"] == "M"] or head
            wife = [m for m in head + spouse if mdata[m]["sex"] == "F"] or spouse
            kids = roles.get("son", []) + roles.get("daughter", []) + roles.get("child", [])
            _reg_couple(husb, wife, kids, place, rec_id)

    if on_progress:
        await on_progress({"phase": "clustering", "done": 0, "total": len(couples)})

    # Union same-role parents within each couple, and same-given children (a person's baptism links to
    # their later appearances as that couple's child).
    for c in couples.values():
        for grp in ("fathers", "mothers"):
            ids = list(c[grp])
            for x in ids[1:]:
                uf.union(ids[0], x)
        by_given: dict[str, list[str]] = defaultdict(list)
        for ch in c["children"]:
            by_given[norm_given(mdata[ch]["given"]) or ch].append(ch)
        for grp_ids in by_given.values():
            for x in grp_ids[1:]:
                uf.union(grp_ids[0], x)

    # Build person clusters
    clusters: dict[str, list[str]] = defaultdict(list)
    for sid in mdata:
        clusters[uf.find(sid)].append(sid)

    # Person key per cluster + aggregate attributes
    person_key: dict[str, str] = {}  # root -> p{n}
    persons: list[dict] = []
    for i, (root, members) in enumerate(sorted(clusters.items()), start=1):
        key = f"p{i}"
        person_key[root] = key
        givens = Counter(mdata[m]["given"] for m in members if mdata[m]["given"])
        surnames = Counter(mdata[m]["surname"] for m in members if mdata[m]["surname"])
        sexes = Counter()
        birth_year = None
        for m in members:
            rel_sex = ROLE_RELATION.get(mdata[m]["role"], ("other", None))[1]
            s = rel_sex or (mdata[m]["sex"] if mdata[m]["sex"] in ("M", "F") else None)
            if s:
                sexes[s] += 1
            if mdata[m]["rt"] in _BAPTISM and mdata[m]["role"] == "principal" and mdata[m]["year"]:
                birth_year = mdata[m]["year"] if birth_year is None else min(birth_year, mdata[m]["year"])
        persons.append({
            "key": key,
            "given": givens.most_common(1)[0][0] if givens else None,
            "surname": surnames.most_common(1)[0][0] if surnames else None,
            "sex": sexes.most_common(1)[0][0] if sexes else "U",
            "birth_year": birth_year,
            "death_year": None,
            "mention_ids": members,
            "record_ids": sorted({mdata[m]["record_id"] for m in members}),
        })

    # Families from couples
    families: list[dict] = []
    seen_fam: set[tuple] = set()
    for j, c in enumerate(couples.values(), start=1):
        husb = person_key[uf.find(next(iter(c["fathers"])))] if c["fathers"] else None
        wife = person_key[uf.find(next(iter(c["mothers"])))] if c["mothers"] else None
        children = sorted({person_key[uf.find(ch)] for ch in c["children"]})
        sig = (husb, wife, tuple(children))
        if (husb is None and wife is None) or sig in seen_fam:
            continue
        seen_fam.add(sig)
        families.append({
            "key": f"f{len(families) + 1}", "husband_key": husb, "wife_key": wife,
            "child_keys": children, "record_ids": sorted(c["records"]),
        })

    # Link to existing tree (dedup against the user's people)
    linked = 0
    if link_to_tree:
        from .service import _find_existing_person
        for p in persons:
            existing = await _find_existing_person(session, tenant_id, p["given"], p["surname"])
            if existing:
                p["existing_person_id"] = str(existing)
                linked += 1

    generations = _max_generations(persons, families)
    stats = {
        "persons": len(persons), "families": len(families),
        "generations": generations, "linked_to_existing": linked,
    }
    if on_progress:
        await on_progress({"phase": "done", "done": len(persons), "total": len(persons)})
    return {"persons": persons, "families": families}, stats


async def merge_reconstruction(
    session: AsyncSession, tenant_id: uuid.UUID, graph: dict, *,
    family_keys: list[str] | None = None,
) -> dict:
    """Materialize the proposed graph (all of it, or only ``family_keys``) into the real tree as
    INFERRED persons/names/families/events + Citations, deduping against existing tree people."""
    from ...models.citation import Citation
    from ...models.event import Event
    from ...models.family import Family, FamilyChild
    from ...models.person import Name, Person
    from .service import _find_existing_person

    persons = {p["key"]: p for p in graph.get("persons", [])}
    fams = graph.get("families", [])
    fam_sel = [f for f in fams if family_keys is None or f["key"] in family_keys]

    needed: set[str] = set()
    for f in fam_sel:
        for k in (f.get("husband_key"), f.get("wife_key"), *f.get("child_keys", [])):
            if k:
                needed.add(k)

    key_to_pid: dict[str, uuid.UUID] = {}
    made_p = made_f = 0
    for k in needed:
        p = persons.get(k)
        if not p:
            continue
        if p.get("existing_person_id"):
            key_to_pid[k] = uuid.UUID(p["existing_person_id"])
            continue
        existing = await _find_existing_person(session, tenant_id, p.get("given"), p.get("surname"))
        if existing:
            key_to_pid[k] = existing
            continue
        person = Person(tenant_id=tenant_id, sex=p.get("sex") or "U")
        session.add(person)
        await session.flush()
        session.add(Name(tenant_id=tenant_id, person_id=person.id, type="birth",
                         given=p.get("given"), surname=p.get("surname"), is_primary=True, is_inferred=True))
        rec_id = uuid.UUID(p["record_ids"][0]) if p.get("record_ids") else None
        session.add(Citation(tenant_id=tenant_id, target_type="person", target_id=person.id,
                             record_id=rec_id, note="Reconstruido del corpus (superdescubrimiento)"))
        if p.get("birth_year"):
            ev = Event(tenant_id=tenant_id, type="baptism", date_year=p["birth_year"],
                       is_inferred=True, subject_person_id=person.id)
            session.add(ev)
            await session.flush()
            session.add(Citation(tenant_id=tenant_id, target_type="event", target_id=ev.id, record_id=rec_id))
        key_to_pid[k] = person.id
        made_p += 1

    for f in fam_sel:
        hid = key_to_pid.get(f.get("husband_key")) if f.get("husband_key") else None
        wid = key_to_pid.get(f.get("wife_key")) if f.get("wife_key") else None
        child_ids = [key_to_pid[c] for c in f.get("child_keys", []) if c in key_to_pid]
        if hid is None and wid is None and not child_ids:
            continue
        fam = None
        if hid and wid:
            fam = await session.scalar(
                select(Family).where(Family.husband_id == hid, Family.wife_id == wid))
        if not fam:
            fam = Family(tenant_id=tenant_id, husband_id=hid, wife_id=wid)
            session.add(fam)
            await session.flush()
            made_f += 1
        for cid in child_ids:
            exists = await session.scalar(select(FamilyChild.person_id).where(
                FamilyChild.family_id == fam.id, FamilyChild.person_id == cid))
            if not exists:
                session.add(FamilyChild(tenant_id=tenant_id, family_id=fam.id, person_id=cid, relation="inferred"))
    await session.flush()
    return {"persons": made_p, "families": made_f}


def _max_generations(persons: list[dict], families: list[dict]) -> int:
    """Longest ancestor chain depth over the proposed graph (child → parents)."""
    parents_of: dict[str, list[str]] = defaultdict(list)
    for f in families:
        for ck in f["child_keys"]:
            for pk in (f["husband_key"], f["wife_key"]):
                if pk:
                    parents_of[ck].append(pk)
    memo: dict[str, int] = {}

    def depth(k: str, seen: frozenset) -> int:
        if k in memo:
            return memo[k]
        if k in seen:
            return 1
        best = 1
        for par in parents_of.get(k, []):
            best = max(best, 1 + depth(par, seen | {k}))
        memo[k] = best
        return best

    keys = {p["key"] for p in persons}
    return max((depth(k, frozenset()) for k in keys), default=0)
