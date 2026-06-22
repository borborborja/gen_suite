"""Transparent match scoring (plan §4). Every signal is a pure function returning [0,1] plus a
short human reason; ``score_candidate`` combines them with fixed weights and keeps the per-signal
breakdown so the review UI can show *why we think it's them*. No DB, no LLM here — unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import jellyfish

from ..extraction.normalize import norm_given, norm_surname, spanish_phonetic

# Relational corroboration is the strongest signal; names are repetitive so weight them below it.
WEIGHTS = {"name": 0.30, "date": 0.20, "place": 0.15, "relational": 0.35}
ENQUEUE_FLOOR = 0.45  # below this we don't persist a candidate
LLM_BAND = (0.45, 0.70)  # ambiguous range where LLM adjudication is worth the cost


@dataclass
class Seed:
    """The tree person we are searching for, plus the relatives we already know."""
    given: str | None
    surname: str | None
    birth_year: int | None = None
    death_year: int | None = None
    place_key: str | None = None  # Place.normalized_key
    address: str | None = None  # known domicile (census co-residence)
    parent_names: set[str] = field(default_factory=set)  # norm_surname-ish tokens of known parents
    spouse_names: set[str] = field(default_factory=set)
    sibling_names: set[str] = field(default_factory=set)  # known siblings — corroborate when co-named (padrino/testigo)


@dataclass
class Candidate:
    """A mention being scored, plus the other people named in the same record."""
    given: str | None
    surname: str | None
    role: str
    record_year: int | None = None
    record_place_key: str | None = None
    stated_age: int | None = None  # parsed age → implied birth year
    address: str | None = None  # domicile stated in the record
    co_mention_names: set[str] = field(default_factory=set)  # norm tokens of other mentions in the act


def name_sim(seed: Seed, cand: Candidate) -> tuple[float, str]:
    sg, cg = norm_given(seed.given), norm_given(cand.given)
    ss, cs = norm_surname(seed.surname), norm_surname(cand.surname)
    g = jellyfish.jaro_winkler_similarity(sg, cg) if sg and cg else 0.0
    s = jellyfish.jaro_winkler_similarity(ss, cs) if ss and cs else 0.0
    base = 0.45 * g + 0.55 * s if (sg and ss) else (g or s)
    # phonetic agreement of the surname is a strong corroborator of ibérico spelling variants
    if ss and cs and spanish_phonetic(ss.split()[0]) == spanish_phonetic(cs.split()[0]):
        base = min(1.0, base + 0.1)
    reason = f"nombre «{cand.given or ''} {cand.surname or ''}».strip() ~ árbol (JW {base:.2f})"
    return round(base, 4), reason.replace(".strip()", "")


# Plausible age gap between the seed and a co-mentioned relative, by role in the record.
def date_plausibility(seed: Seed, cand: Candidate) -> tuple[float, str]:
    if cand.record_year is None:
        return 0.5, "sin fecha en el acta"
    # a stated age pins the candidate's birth year directly (census/military/death records)
    if cand.stated_age is not None and seed.birth_year:
        implied = cand.record_year - cand.stated_age
        diff = abs(implied - seed.birth_year)
        if diff <= 2:
            return 0.95, f"edad {cand.stated_age} en {cand.record_year} → nacido ~{implied} (encaja con {seed.birth_year})"
        if diff <= 6:
            return 0.7, f"edad implica ~{implied} (±{diff} de {seed.birth_year})"
        return 0.15, f"edad implica ~{implied}, lejos de {seed.birth_year}"
    expected = seed.birth_year if seed.birth_year else None
    if expected is None and seed.death_year:
        expected = seed.death_year - 50  # crude fallback
    if expected is None:
        return 0.5, "sin año esperado en el árbol"
    # principal of a baptism ≈ born that year; a parent ≈ 20–60y earlier; spouse close.
    if cand.role in ("father", "mother", "spouse_father", "spouse_mother"):
        lo, hi = expected + 18, expected + 65
        ok = lo <= cand.record_year <= hi
        return (0.9 if ok else 0.2), f"acta {cand.record_year} vs nacimiento ~{expected} (rol {cand.role})"
    diff = abs(cand.record_year - expected)
    if diff <= 3:
        return 0.9, f"{cand.record_year} encaja con ~{expected}"
    if diff <= 12:
        return 0.6, f"{cand.record_year} ≈ ~{expected} (±{diff})"
    if diff <= 30:
        return 0.3, f"{cand.record_year} lejos de ~{expected}"
    return 0.1, f"{cand.record_year} incompatible con ~{expected}"


def place_proximity(seed: Seed, cand: Candidate) -> tuple[float, str]:
    # same domicile is the strongest place signal (census co-residence); a clearly different one is
    # a negative signal, so when both addresses are known we decide here rather than fall through.
    if seed.address and cand.address:
        sa, ca = norm_surname(seed.address), norm_surname(cand.address)
        if sa and ca:
            if sa == ca:
                return 1.0, "mismo domicilio"
            if jellyfish.jaro_winkler_similarity(sa, ca) >= 0.88:
                return 0.8, "domicilio muy similar"
            return 0.3, "domicilio distinto"
    if not seed.place_key or not cand.record_place_key:
        return 0.5, "lugar desconocido"
    if seed.place_key == cand.record_place_key:
        return 1.0, "misma parroquia/municipio"
    a, b = seed.place_key, cand.record_place_key
    sim = jellyfish.jaro_winkler_similarity(a, b)
    if sim >= 0.85:
        return 0.7, "municipio muy similar"
    return 0.2, "lugar distinto"


def relational_corroboration(seed: Seed, cand: Candidate) -> tuple[float, str]:
    """Do the OTHER people in this act match the relatives the tree already knows? The single
    most discriminating signal: a baptism whose father/mother match the known parents ≈ certainty."""
    known = {n for n in (seed.parent_names | seed.spouse_names | seed.sibling_names) if n}
    if not known or not cand.co_mention_names:
        return 0.5, "sin parientes que comparar"
    hits = 0
    for k in known:
        for c in cand.co_mention_names:
            if k and c and jellyfish.jaro_winkler_similarity(k, c) >= 0.88:
                hits += 1
                break
    if hits >= 2:
        return 1.0, f"{hits} parientes coinciden en el acta"
    if hits == 1:
        return 0.8, "un pariente coincide en el acta"
    return 0.25, "ningún pariente coincide"


@dataclass
class MentionView:
    """A corpus mention reduced to the fields needed for within-corpus entity resolution (M4)."""
    given: str | None
    surname: str | None
    year: int | None = None
    role: str | None = None
    origin: str | None = None
    co_names: set[str] = field(default_factory=set)  # norm names of others in the same act


# Plausible span (years) between two acts naming the same person across a lifetime.
COREF_LIFESPAN = 95
COREF_FLOOR = 0.62  # below this, not considered the same corpus person


def coref_score(a: MentionView, b: MentionView) -> dict:
    """Score whether two corpus mentions refer to the SAME real person (independent of the tree).
    Gated on name: repetitive names ("Joan Vidal") need corroboration from shared relatives /
    origin / date plausibility. Pure — unit-tested."""
    ag, bg = norm_given(a.given), norm_given(b.given)
    asu, bsu = norm_surname(a.surname), norm_surname(b.surname)
    g = jellyfish.jaro_winkler_similarity(ag, bg) if ag and bg else 0.0
    su = jellyfish.jaro_winkler_similarity(asu, bsu) if asu and bsu else 0.0
    # the SAME person needs the same given name (after Latin fold) — a shared surname is just a
    # family, not an identity. Different given names ⇒ different people, regardless of surname.
    if ag and bg and g < 0.80:
        return {"score": 0.0, "same": False,
                "signals": {"name": round(g, 4), "shared_relatives": 0, "date": 0.0, "origin": 0.0}}
    name = 0.45 * g + 0.55 * su if (ag and asu) else (g or su)
    if asu and bsu and spanish_phonetic(asu.split()[0]) == spanish_phonetic(bsu.split()[0]):
        name = min(1.0, name + 0.08)

    # shared relatives across the two acts (the strongest corroborator)
    shared = 0
    for x in a.co_names:
        for y in b.co_names:
            if x and y and jellyfish.jaro_winkler_similarity(x, y) >= 0.9:
                shared += 1
                break
    rel = 1.0 if shared >= 2 else 0.7 if shared == 1 else 0.4

    if a.year is not None and b.year is not None:
        gap = abs(a.year - b.year)
        date = 1.0 if gap <= COREF_LIFESPAN else max(0.0, 1 - (gap - COREF_LIFESPAN) / 40)
    else:
        date = 0.6

    origin = 1.0 if (a.origin and b.origin and norm_surname(a.origin) == norm_surname(b.origin)) else 0.5

    # name is a hard gate: different names are never the same person here
    total = 0.0 if name < 0.78 else round(0.45 * name + 0.30 * rel + 0.15 * date + 0.10 * origin, 4)
    return {
        "score": total,
        "same": total >= COREF_FLOOR,
        "signals": {
            "name": round(name, 4), "shared_relatives": shared,
            "date": round(date, 4), "origin": round(origin, 4),
        },
    }


def score_candidate(seed: Seed, cand: Candidate) -> dict:
    signals = {
        "name": name_sim(seed, cand),
        "date": date_plausibility(seed, cand),
        "place": place_proximity(seed, cand),
        "relational": relational_corroboration(seed, cand),
    }
    total = sum(WEIGHTS[k] * v for k, (v, _) in signals.items())
    return {
        "score": round(total, 4),
        "signals": {k: {"value": v, "weight": WEIGHTS[k], "reason": r} for k, (v, r) in signals.items()},
        "needs_llm": LLM_BAND[0] <= total <= LLM_BAND[1],
    }
