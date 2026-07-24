"""Consistency checker: cross-checks dates and kinship in the whole tree and reports
impossible or suspicious situations (born after dying, 8-year-old mothers…) in Spanish.

On-demand and in-memory: four bulk queries into dicts, then O(n) pure-Python rules —
a tree of tens of thousands of persons resolves in well under a second. Records without
a usable ``date_year`` are silently skipped by each rule."""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.event import Event
from ...models.family import Family, FamilyChild
from .service import _summaries

MOTHER_MIN, MOTHER_MAX = 12, 55
FATHER_MIN = 14
SPOUSE_MIN = 12
MAX_AGE = 110


async def get_consistency(session: AsyncSession) -> dict:
    births: dict[uuid.UUID, int] = {}
    deaths: dict[uuid.UUID, int] = {}
    for pid, etype, year in (await session.execute(
            select(Event.subject_person_id, Event.type, Event.date_year).where(
                Event.type.in_(("birth", "death")), Event.date_year.is_not(None),
                Event.subject_person_id.is_not(None)).order_by(Event.date_year))).all():
        (births if etype == "birth" else deaths).setdefault(pid, year)

    marriages: dict[uuid.UUID, int] = {}
    for fid, year in (await session.execute(
            select(Event.subject_family_id, Event.date_year).where(
                Event.type == "marriage", Event.date_year.is_not(None),
                Event.subject_family_id.is_not(None)).order_by(Event.date_year))).all():
        marriages.setdefault(fid, year)

    fams = (await session.execute(
        select(Family.id, Family.husband_id, Family.wife_id))).all()
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    for fid, child in (await session.execute(
            select(FamilyChild.family_id, FamilyChild.person_id))).all():
        children.setdefault(fid, []).append(child)

    issues: list[dict] = []

    def add(code: str, severity: str, person: uuid.UUID, message: str,
            related: uuid.UUID | None = None, family: uuid.UUID | None = None) -> None:
        issues.append({"code": code, "severity": severity, "person_id": person,
                       "related_person_id": related, "family_id": family, "message": message})

    this_year = datetime.date.today().year
    for pid, by in births.items():
        dy = deaths.get(pid)
        if dy is not None and dy < by:
            add("birth_after_death", "error", pid,
                f"consta nacido en {by} pero fallecido antes, en {dy}")
        elif dy is None and this_year - by > MAX_AGE:
            add("alive_over_110", "warning", pid,
                f"nacido en {by} y sin defunción: tendría {this_year - by} años")

    for fid, husb, wife in fams:
        marr = marriages.get(fid)
        for spouse, other in ((husb, wife), (wife, husb)):
            sb = births.get(spouse) if spouse else None
            if spouse and marr is not None and sb is not None and marr - sb < SPOUSE_MIN:
                add("spouse_too_young", "error", spouse, related=other, family=fid,
                    message=f"casado/a en {marr} con solo {marr - sb} años")
        for child in children.get(fid, []):
            cb = births.get(child)
            if cb is None:
                continue
            if marr is not None and cb < marr:
                add("child_before_marriage", "warning", child, family=fid,
                    message=f"nacido en {cb}, antes del matrimonio de sus padres ({marr})")
            mother_death = deaths.get(wife) if wife else None
            if mother_death is not None and cb > mother_death + 1:
                add("child_after_mother_death", "error", child, related=wife, family=fid,
                    message=f"nacido en {cb}, después de la muerte de su madre ({mother_death})")
            for role, parent, min_age, max_age in (
                ("padre", husb, FATHER_MIN, None),
                ("madre", wife, MOTHER_MIN, MOTHER_MAX),
            ):
                pb = births.get(parent) if parent else None
                if pb is None:
                    continue
                age = cb - pb
                if age < 0:
                    add("child_older_than_parent", "error", child, related=parent, family=fid,
                        message=f"nacido en {cb}, antes que su {role} ({pb})")
                elif age < min_age:
                    add("parent_too_young", "error", parent, related=child, family=fid,
                        message=f"habría sido {role} con {age} años")
                elif max_age is not None and age > max_age:
                    add("mother_too_old", "warning", parent, related=child, family=fid,
                        message=f"habría sido madre con {age} años")

    # resolve names and prefix them into the message
    ids = {i["person_id"] for i in issues} | {i["related_person_id"] for i in issues if i["related_person_id"]}
    summaries = await _summaries(session, ids)

    def name(pid: uuid.UUID | None) -> str:
        s = summaries.get(pid) if pid else None
        return " ".join(x for x in ((s.given, s.surname) if s else ()) if x) or "(sin nombre)"

    out = []
    for i in sorted(issues, key=lambda i: (i["severity"] != "error", i["code"])):
        out.append({
            **{k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in i.items()},
            "person_name": name(i["person_id"]),
            "related_person_name": name(i["related_person_id"]) if i["related_person_id"] else None,
            "message": f"{name(i['person_id'])}: {i['message']}",
        })
    counts: dict[str, int] = {}
    for i in out:
        counts[i["code"]] = counts.get(i["code"], 0) + 1
    return {"issues": out, "counts": counts, "checked_at_year": this_year}
