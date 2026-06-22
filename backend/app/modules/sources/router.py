"""GET /sources — external genealogical sources applicable to a query, with ready-to-open search
URLs (filled + URL-encoded from the given name/place/years)."""
from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...core.deps import get_current_principal
from ...core.security import Principal
from .catalog import CATEGORIES, for_region

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceOut(BaseModel):
    key: str
    name: str
    category: str
    category_label: str
    region: str | None
    note: str | None
    url: str


def _fill(template: str, **vals: str) -> str:
    out = template
    for k, v in vals.items():
        out = out.replace("{" + k + "}", quote_plus(v or ""))
    return out


@router.get("", response_model=list[SourceOut])
async def list_sources(
    given: str = "", surname: str = "", place: str = "",
    year_from: str = "", year_to: str = "", region: str | None = None, country: str = "ES",
    _: Principal = Depends(get_current_principal),
) -> list[SourceOut]:
    q = " ".join(p for p in [given, surname, place] if p).strip()
    out: list[SourceOut] = []
    for s in for_region(country, region):
        url = _fill(s["url"], given=given, surname=surname, place=place,
                    year_from=year_from, year_to=year_to, q=q)
        out.append(SourceOut(
            key=s["key"], name=s["name"], category=s["category"],
            category_label=CATEGORIES.get(s["category"], s["category"]),
            region=s["region"], note=s.get("note"), url=url,
        ))
    return out
