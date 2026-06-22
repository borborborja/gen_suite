import time
import re
import logging
import urllib.parse
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import asdict

import requests
from .models import ImageRecord, DownloadManifest

try:
    import piexif
except ImportError:
    piexif = None

FS_BASE = "https://www.familysearch.org"
FS_API  = "https://www.familysearch.org/platform"

log = logging.getLogger("fs_downloader")

def get_image_metadata(session: requests.Session, iid: str,
                       seek: str = "current") -> Optional[dict]:
    encoded = urllib.parse.quote(iid, safe="")
    url = f"{FS_API}/records/images/{encoded}?seek={seek}"
    try:
        resp = session.get(url, headers={"Accept": "application/json"}, timeout=15)
    except:
        return None

    if resp.status_code == 200:
        try:
            data = resp.json()
            for sd in data.get("sourceDescriptions", []):
                rights = sd.get("rights", [])
                if any("restricted=true" in str(r) for r in rights):
                    links = sd.get("links", {})
                    if not any(k in links for k in ("image-download", "image", "download")):
                        return {"_restricted": True, "_iid": iid, "_status": 200}
            return data
        except:
            return None
    elif resp.status_code == 403:
        return {"_restricted": True, "_iid": iid, "_status": 403}
    elif resp.status_code == 404:
        return {"_end": True, "_iid": iid}
    elif resp.status_code == 401:
        return {"_unauth": True, "_iid": iid}
    else:
        return {"_error": True, "_iid": iid, "_status": resp.status_code}

def extract_download_url(metadata: dict) -> Optional[str]:
    if not metadata or any(metadata.get(k) for k in ("_restricted","_error","_end","_unauth")):
        return None
    for sd in metadata.get("sourceDescriptions", []):
        links = sd.get("links", {})
        for key in ("image-download", "download", "image", "image-stream-image-dist"):
            href = links.get(key, {}).get("href", "")
            if href: return href
    return None

def extract_next_iid(metadata: dict) -> Optional[str]:
    if not metadata: return None
    for key in ("next", "image-next"):
        href = metadata.get("links", {}).get(key, {}).get("href", "")
        if href: return _iid_from_href(href)
    return None

def _iid_from_href(href: str) -> str:
    path = urllib.parse.urlparse(href).path
    return path.split("/")[-1]

def extract_ark(metadata: dict) -> str:
    for sd in metadata.get("sourceDescriptions", []):
        about = sd.get("about", "")
        if "ark:" in about: return about
    return ""

def check_image_downloadable(metadata: dict) -> Tuple[bool, str]:
    if not metadata: return False, "sin metadatos"
    if metadata.get("_restricted"): return False, "restringida"
    if metadata.get("_unauth"): return False, "sesión expirada"
    if metadata.get("_end"): return False, "fin del catálogo"
    if metadata.get("_error"): return False, f"HTTP {metadata.get('_status')}"
    return True, "ok"

def download_image_binary(session: requests.Session, url: str,
                          filepath: Path, timeout: int = 45) -> Tuple[bool, str, int]:
    try:
        r = session.get(url, stream=True, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            size = 0
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(32768):
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
            return True, "", size
        return False, f"HTTP {r.status_code}", 0
    except Exception as e:
        return False, str(e), 0

def embed_exif_metadata(filepath: Path, manifest: DownloadManifest, record: Dict):
    if piexif is None: return
    try:
        title = manifest.collection_title or "FamilySearch Catalog"
        dgs = manifest.film_dgs or "N/A"
        ark = record.get("ark", "")
        idx = record.get("index", 0)
        description = f"{title} | DGS: {dgs} | Page: {idx}"
        comment = f"FamilySearch ARK: {ark} | DGS: {dgs} Index: {idx}"
        try:
            exif_dict = piexif.load(str(filepath))
        except:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        exif_dict["0th"][piexif.ImageIFD.ImageDescription] = description.encode("utf-8")
        exif_dict["Exif"][piexif.ExifIFD.UserComment] = b"ASCII\x00\x00\x00" + comment.encode("utf-8")
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, str(filepath))
    except Exception as e:
        log.debug(f"Error EXIF: {e}")

def fetch_catalog_title(session: requests.Session, url_info: dict) -> Optional[str]:
    cat_id = url_info.get("id")
    if not cat_id or url_info.get("type") != "catalog": return None
    try:
        r = session.get(f"{FS_BASE}/search/catalog/{cat_id}", timeout=15)
        if r.status_code == 200:
            m_h1 = re.search(r"<h1[^>]*>(.*?)</h1>", r.text, re.S | re.I)
            if m_h1:
                return re.sub(r"<[^>]+>", "", m_h1.group(1)).strip()
    except: pass
    return None
