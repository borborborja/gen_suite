"""Worldwide place autocomplete via Nominatim (OpenStreetMap). Proxied server-side so we can set a
proper User-Agent and keep nomenclature consistent; returns a canonical name + lat/lng usable for a
future map. Free, no API key (be gentle: the UI debounces)."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ...core.deps import get_current_principal
from ...core.security import Principal

router = APIRouter(prefix="/geo", tags=["geo"])

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "gen_suite-genealogy/0.1 (research tool)"}


class GeoResult(BaseModel):
    name: str          # canonical short name (municipality)
    display_name: str  # full "Town, Province, Country"
    lat: float
    lng: float
    country: str | None = None
    type: str | None = None


async def geocode_one(q: str) -> tuple[float, float] | None:
    """Best-effort single lookup → (lat, lng). Used for batch-geocoding tree places."""
    if not q or not q.strip():
        return None
    params = {"q": q, "format": "jsonv2", "limit": "1", "accept-language": "es"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(_NOMINATIM, params=params, headers=_HEADERS)
            resp.raise_for_status()
            rows = resp.json()
    except Exception:
        return None
    if not rows:
        return None
    return float(rows[0]["lat"]), float(rows[0]["lon"])


@router.get("/search", response_model=list[GeoResult])
async def search(
    q: str = Query(min_length=2),
    limit: int = Query(6, ge=1, le=10),
    _: Principal = Depends(get_current_principal),
) -> list[GeoResult]:
    params = {
        "q": q, "format": "jsonv2", "addressdetails": "1", "limit": str(limit),
        "accept-language": "es", "featuretype": "settlement",
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(_NOMINATIM, params=params, headers=_HEADERS)
            resp.raise_for_status()
            rows = resp.json()
    except Exception:
        return []
    out: list[GeoResult] = []
    for r in rows:
        addr = r.get("address", {})
        name = (addr.get("city") or addr.get("town") or addr.get("village")
                or addr.get("municipality") or r.get("name") or r.get("display_name", "").split(",")[0])
        out.append(GeoResult(
            name=name, display_name=r.get("display_name", name),
            lat=float(r["lat"]), lng=float(r["lon"]),
            country=addr.get("country"), type=r.get("type"),
        ))
    return out
