"""Kinship calculator: given two persons of the tenant's tree, find the shortest
genealogical path between them (parent/child/spouse hops) and name the relationship in
Spanish (padre, tía abuela, primo segundo, cuñada…). Runs in memory — a family tree of
tens of thousands of edges loads in milliseconds on an RLS-scoped session."""
from __future__ import annotations

import uuid
from collections import deque

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.family import Family, FamilyChild
from ...models.person import Person
from .schemas import KinshipStep, RelationshipOut, SearchHit
from .service import _summaries

_ANCESTORS = ["padre|madre|progenitor(a)", "abuelo|abuela", "bisabuelo|bisabuela",
              "tatarabuelo|tatarabuela"]
_DESCENDANTS = ["hijo|hija", "nieto|nieta", "bisnieto|bisnieta", "tataranieto|tataranieta"]
_UNCLES = ["tío|tía", "tío abuelo|tía abuela", "tío bisabuelo|tía bisabuela"]
_NEPHEWS = ["sobrino|sobrina", "sobrino nieto|sobrina nieta", "sobrino bisnieto|sobrina bisnieta"]
_COUSIN_ORD = ["hermano|hermana", "segundo|segunda", "tercero|tercera", "cuarto|cuarta",
               "quinto|quinta", "sexto|sexta"]


def _g(pair: str, sex: str) -> str:
    """Pick the gendered form from "masc|fem" (or "masc|fem|neutral") by sex."""
    parts = pair.split("|")
    if sex == "F":
        return parts[1]
    if sex == "M" or len(parts) < 3:
        return parts[0]
    return parts[2]


class _Graph:
    def __init__(self) -> None:
        self.parents: dict[uuid.UUID, set[uuid.UUID]] = {}
        self.children: dict[uuid.UUID, set[uuid.UUID]] = {}
        self.spouses: dict[uuid.UUID, set[uuid.UUID]] = {}

    def neighbors(self, pid: uuid.UUID):
        for p in self.parents.get(pid, ()):  # noqa: UP028 — clarity over yield-from
            yield p, "parent"
        for c in self.children.get(pid, ()):
            yield c, "child"
        for s in self.spouses.get(pid, ()):
            yield s, "spouse"


async def _load_graph(session: AsyncSession) -> _Graph:
    g = _Graph()
    fams = (await session.execute(select(Family.id, Family.husband_id, Family.wife_id))).all()
    parents_by_family: dict[uuid.UUID, list[uuid.UUID]] = {}
    for fid, husb, wife in fams:
        parents_by_family[fid] = [p for p in (husb, wife) if p]
        if husb and wife:
            g.spouses.setdefault(husb, set()).add(wife)
            g.spouses.setdefault(wife, set()).add(husb)
    for fid, child in (await session.execute(
            select(FamilyChild.family_id, FamilyChild.person_id))).all():
        for parent in parents_by_family.get(fid, ()):
            g.parents.setdefault(child, set()).add(parent)
            g.children.setdefault(parent, set()).add(child)
    return g


def _ancestors(g: _Graph, start: uuid.UUID) -> dict[uuid.UUID, int]:
    """Minimum generation distance from ``start`` up to each ancestor (start itself = 0)."""
    dist = {start: 0}
    frontier = deque([start])
    while frontier:
        cur = frontier.popleft()
        for p in g.parents.get(cur, ()):
            if p not in dist:
                dist[p] = dist[cur] + 1
                frontier.append(p)
    return dist


def _blood(g: _Graph, a: uuid.UUID, b: uuid.UUID) -> tuple[int, int] | None:
    """(gens from a up to nearest common ancestor, same for b) — None if no blood relation."""
    anc_a, anc_b = _ancestors(g, a), _ancestors(g, b)
    common = anc_a.keys() & anc_b.keys()
    if not common:
        return None
    best = min(common, key=lambda c: (anc_a[c] + anc_b[c], max(anc_a[c], anc_b[c])))
    return anc_a[best], anc_b[best]


def _ordinal_label(base: list[str], n: int, sex: str, far: str) -> str:
    if 1 <= n <= len(base):
        return _g(base[n - 1], sex)
    return far


def _blood_label(g: _Graph, a: uuid.UUID, b: uuid.UUID, da: int, db: int, sex: str) -> str:
    """Name what *b* is of *a* given generation distances to their nearest common ancestor."""
    if da == 0 and db == 0:
        return "la misma persona"
    if db == 0:  # b is a direct ancestor of a
        return _ordinal_label(_ANCESTORS, da, sex, f"ancestro directo ({da} generaciones)")
    if da == 0:  # b is a direct descendant of a
        return _ordinal_label(_DESCENDANTS, db, sex,
                              f"descendiente directo ({db} generaciones)")
    if da == 1 and db == 1:
        shared = g.parents.get(a, set()) & g.parents.get(b, set())
        if len(shared) == 1 and (len(g.parents.get(a, ())) > 1 or len(g.parents.get(b, ())) > 1):
            return _g("medio hermano|media hermana", sex)
        return _g("hermano|hermana", sex)
    if db == 1:  # b is a sibling of a's ancestor
        return _ordinal_label(_UNCLES, da - 1, sex, f"tío/a lejano/a ({da} generaciones)")
    if da == 1:  # b descends from a's sibling
        return _ordinal_label(_NEPHEWS, db - 1, sex, f"sobrino/a lejano/a ({db} generaciones)")
    if da == db:
        grade = _ordinal_label(_COUSIN_ORD, da - 1, sex, f"de {da - 1}.º grado")
        return f"{_g('primo|prima', sex)} {grade}"
    m, diff = min(da, db), abs(da - db)
    grade = _ordinal_label(_COUSIN_ORD, m - 1, sex, f"de {m - 1}.º grado")
    return (f"{_g('primo|prima', sex)} {grade} "
            f"con {diff} generaci{'ón' if diff == 1 else 'ones'} de diferencia")


def _shortest_path(g: _Graph, a: uuid.UUID, b: uuid.UUID) -> list[tuple[uuid.UUID, str | None]]:
    """BFS over typed edges → [(person, step-from-previous)] from a to b; [] if disconnected."""
    if a == b:
        return [(a, None)]
    prev: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    seen = {a}
    frontier = deque([a])
    while frontier:
        cur = frontier.popleft()
        for nxt, step in g.neighbors(cur):
            if nxt in seen:
                continue
            seen.add(nxt)
            prev[nxt] = (cur, step)
            if nxt == b:
                path: list[tuple[uuid.UUID, str | None]] = [(b, prev[b][1])]
                node = b
                while node != a:
                    node = prev[node][0]
                    path.append((node, prev[node][1] if node in prev else None))
                path.reverse()
                return path
            frontier.append(nxt)
    return []


_STEP_LABEL = {"parent": "padre|madre|progenitor(a)", "child": "hijo|hija",
               "spouse": "esposo|esposa|cónyuge"}


async def get_relationship(session: AsyncSession, a_id: uuid.UUID, b_id: uuid.UUID) -> RelationshipOut:
    for pid in (a_id, b_id):
        if not await session.get(Person, pid):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")

    g = await _load_graph(session)
    raw_path = _shortest_path(g, a_id, b_id)
    path_ids = {pid for pid, _ in raw_path} | {a_id, b_id}
    summaries = await _summaries(session, path_ids)

    def hit(pid: uuid.UUID) -> SearchHit:
        s = summaries.get(pid)
        return SearchHit(id=pid, given=s.given if s else None, surname=s.surname if s else None,
                         birth_year=s.birth_year if s else None,
                         death_year=s.death_year if s else None)

    def sex(pid: uuid.UUID) -> str:
        s = summaries.get(pid)
        return s.sex if s else "U"

    path = [
        KinshipStep(person=hit(pid), step=_g(_STEP_LABEL[step], sex(pid)) if step else None)
        for pid, step in raw_path
    ]
    b_sex = sex(b_id)

    if a_id == b_id:
        return RelationshipOut(related=True, label="la misma persona", path=path)

    blood = _blood(g, a_id, b_id)
    if blood:
        label = _blood_label(g, a_id, b_id, blood[0], blood[1], b_sex)
        return RelationshipOut(related=True, label=label, path=path)

    # Affinity (through marriage), one spouse hop at either end of a blood line.
    if b_id in g.spouses.get(a_id, set()):
        return RelationshipOut(related=True, label=_g("esposo|esposa|cónyuge", b_sex), path=path)
    for s in g.spouses.get(a_id, ()):  # b blood-related to a's spouse
        r = _blood(g, s, b_id)
        if not r:
            continue
        ds, dbb = r
        if (ds, dbb) == (1, 0):
            label = _g("suegro|suegra", b_sex)
        elif (ds, dbb) == (1, 1):
            label = _g("cuñado|cuñada", b_sex)
        elif (ds, dbb) == (0, 1):
            label = _g("hijastro|hijastra", b_sex)
        else:
            label = f"{_blood_label(g, s, b_id, ds, dbb, b_sex)} de su cónyuge"
        return RelationshipOut(related=True, label=label, path=path)
    for r_id in g.spouses.get(b_id, ()):  # b is the spouse of a blood relative of a
        r = _blood(g, a_id, r_id)
        if not r:
            continue
        da, dr = r
        if (da, dr) == (0, 1):
            label = _g("yerno|nuera", b_sex)
        elif (da, dr) == (1, 1):
            label = _g("cuñado|cuñada", b_sex)
        elif (da, dr) == (1, 0):
            label = _g("padrastro|madrastra", b_sex)
        else:
            label = f"cónyuge de su {_blood_label(g, a_id, r_id, da, dr, sex(r_id))}"
        return RelationshipOut(related=True, label=label, path=path)

    if path:
        return RelationshipOut(related=True, label="parentesco lejano (por alianzas)", path=path)
    return RelationshipOut(related=False, label="sin parentesco conocido", path=[])
