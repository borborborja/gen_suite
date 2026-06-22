"""Shared GEDCOM tag <-> domain mappings used by the importer and exporter."""
from __future__ import annotations

import re

PERSON_EVENT_TAGS = {
    "BIRT", "CHR", "BAPM", "DEAT", "BURI", "CREM", "ADOP", "RESI", "OCCU", "EDUC", "RELI",
    "CENS", "IMMI", "EMIG", "NATU", "GRAD", "RETI", "EVEN", "FACT", "TITL", "CONF", "ORDN",
    "PROB", "WILL", "DSCR", "NATI", "NCHI", "NMR", "CAST", "PROP", "IDNO", "SSN",
}
FAMILY_EVENT_TAGS = {"MARR", "DIV", "ENGA", "MARB", "MARC", "MARL", "ANUL", "DIVF", "CENS", "RESI", "EVEN"}

PERSON_MAPPED = {"NAME", "SEX", "FAMC", "FAMS"} | PERSON_EVENT_TAGS
FAMILY_MAPPED = {"HUSB", "WIFE", "CHIL"} | FAMILY_EVENT_TAGS
ALL_EVENT_TAGS = PERSON_EVENT_TAGS | FAMILY_EVENT_TAGS

# Events whose primary value lives on the event line itself (e.g. "1 OCCU Farmer").
VALUE_EVENT_TAGS = {
    "OCCU", "TITL", "RELI", "EDUC", "DSCR", "CAST", "NATI", "PROP", "IDNO", "SSN", "FACT",
    "EVEN", "NCHI", "NMR",
}

TAG2TYPE = {
    "BIRT": "birth", "CHR": "christening", "BAPM": "baptism", "DEAT": "death", "BURI": "burial",
    "CREM": "cremation", "MARR": "marriage", "DIV": "divorce", "ENGA": "engagement",
    "RESI": "residence", "OCCU": "occupation", "EDUC": "education", "RELI": "religion",
    "CENS": "census", "IMMI": "immigration", "EMIG": "emigration", "NATU": "naturalization",
    "ADOP": "adoption", "GRAD": "graduation", "RETI": "retirement", "TITL": "title",
    "CONF": "confirmation", "ORDN": "ordination", "PROB": "probate", "WILL": "will",
    "EVEN": "event", "FACT": "fact",
}
TYPE2TAG = {v: k for k, v in TAG2TYPE.items()}

_YEAR_RE = re.compile(r"(\d{3,4})")
_NAME_RE = re.compile(r"^(?P<given>[^/]*)/(?P<surname>[^/]*)/?(?P<suffix>.*)$")


def normalize_place(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def extract_year(date_raw: str | None) -> int | None:
    if not date_raw:
        return None
    m = _YEAR_RE.search(date_raw)
    return int(m.group(1)) if m else None
