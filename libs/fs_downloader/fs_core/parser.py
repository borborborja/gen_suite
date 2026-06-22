import re
import urllib.parse
import logging
import requests
from typing import Optional

FS_BASE = "https://www.familysearch.org"
FS_API  = "https://www.familysearch.org/platform"

log = logging.getLogger("fs_downloader")

def parse_fs_url(url: str) -> dict:
    parsed = urllib.parse.urlparse(url)
    path   = parsed.path
    params = dict(urllib.parse.parse_qsl(parsed.query))
    result = {"url": url, "type": None, "id": None, "ark": None, "dgs": None}

    m = re.search(r"/ark:/(\d+/[^/?#\s]+)", url)
    if m:
        result["type"] = "ark"
        result["ark"]  = "ark:/" + m.group(1)
        result["id"]   = m.group(1)
        return result

    m = re.search(r"/search/catalog/(\d+)", path)
    if m:
        result["type"] = "catalog"
        result["id"]   = m.group(1)
        return result

    m = re.search(r"/search/film/(\d+)", path)
    if m:
        result["type"] = "film"
        result["dgs"]  = m.group(1)
        result["id"]   = m.group(1)
        return result

    m = re.search(r"/records/images/waypoints/([^/?#]+)", path)
    if m:
        result["type"] = "waypoint"
        result["id"]   = urllib.parse.unquote(m.group(1))
        return result

    dgs = params.get("dgsNum") or params.get("dgs")
    if dgs:
        result["type"] = "film"
        result["dgs"]  = dgs
        result["id"]   = dgs
        return result

    result["type"] = "unknown"
    return result

def resolve_first_image_ark(session: requests.Session, url_info: dict) -> Optional[str]:
    t   = url_info.get("type")
    id_ = url_info.get("id")

    if t == "ark":
        full_id = url_info.get("id", "")
        iid = full_id.split("/")[-1] if "/" in full_id else full_id
        return iid
    elif t == "catalog":
        return _resolve_catalog(session, id_)
    elif t == "film":
        return _resolve_film(session, id_)
    elif t == "waypoint":
        return _resolve_waypoint(session, id_)
    else:
        return _resolve_via_html(session, url_info["url"])

def _resolve_catalog(session, catalog_id):
    r = _safe_get(session, f"{FS_API}/records/collections/{catalog_id}")
    if r and r.status_code == 200:
        dgs = _find_dgs_in_json(r.json())
        if dgs: return _resolve_film(session, dgs)
    return _resolve_via_html(session, f"{FS_BASE}/search/catalog/{catalog_id}")

def _resolve_film(session, dgs):
    for url in [
        f"{FS_BASE}/records/images/waypoints/{urllib.parse.quote(dgs, safe='')}",
        f"{FS_BASE}/records/images/waypoints?dgsNum={dgs}",
    ]:
        r = _safe_get(session, url)
        if r and r.status_code == 200:
            try:
                iid = _extract_iid_from_waypoints(r.json())
                if iid: return iid
            except: pass
    return _resolve_via_html(session, f"{FS_BASE}/search/film/{dgs}")

def _resolve_waypoint(session, waypoint_id):
    r = _safe_get(session, f"{FS_BASE}/records/images/waypoints/{urllib.parse.quote(waypoint_id, safe='')}")
    if r and r.status_code == 200:
        try:
            return _extract_iid_from_waypoints(r.json())
        except: pass
    return waypoint_id

def _resolve_via_html(session, url):
    r = _safe_get(session, url, accept="text/html")
    if not r or r.status_code != 200: return None
    html = r.text
    patterns = [
        (r'"iid"\s*:\s*"([^"]+)"', "iid"),
        (r'"imageId"\s*:\s*"([^"]+)"', "imageId"),
        (r'waypoints/([A-Za-z0-9:_%.\-]+)', "waypoint"),
        (r'/ark:/61903/([^\"\'\?\s\#]+)', "ark"),
        (r'"dgsNum"\s*:\s*"?(\d{7,})"?', "dgsNum"),
    ]
    for pat, label in patterns:
        m = re.search(pat, html)
        if m:
            val = urllib.parse.unquote(m.group(1))
            if "dgs" in label or (val.isdigit() and len(val) >= 7):
                return _resolve_film(session, val)
            if label == "ark" and "/" in val: val = val.split("/")[-1]
            return val
    return None

def _find_dgs_in_json(data):
    # Simplified search for dgsNum in JSON response
    s = str(data)
    m = re.search(r"dgsNum['\"]: ['\"](\d+)['\"]", s)
    return m.group(1) if m else None

def _extract_iid_from_waypoints(data):
    # From the FS response format
    for group in data.get("groups", []):
        for waypoint in group.get("waypoints", []):
            iid = waypoint.get("imageId") or waypoint.get("iid")
            if iid: return iid
    return None

def _safe_get(session, url, accept="application/json"):
    try:
        return session.get(url, headers={"Accept": accept}, timeout=15)
    except:
        return None
