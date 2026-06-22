import requests
import json
import logging
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Reusing constants from the original script
FS_BASE = "https://www.familysearch.org"
FS_API  = "https://www.familysearch.org/platform"
DEFAULT_SESSION_FILE = "fs_session.json"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.familysearch.org/",
}

log = logging.getLogger("fs_downloader")

def build_session(retries: int = 3) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries, backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"], raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update(BROWSER_HEADERS)
    return session

def authenticate_from_cookies_file(session: requests.Session, cookies_file: str) -> bool:
    path = Path(cookies_file)
    if not path.exists():
        log.error(f"Archivo no encontrado: {cookies_file}")
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            cookies_data = json.load(f)
    except json.JSONDecodeError as e:
        log.error(f"JSON inválido: {e}")
        return False

    if isinstance(cookies_data, dict):
        cookies_data = [{"name": k, "value": v, "domain": ".familysearch.org"}
                        for k, v in cookies_data.items()]

    fssessionid = None
    count = 0
    for c in cookies_data:
        if not isinstance(c, dict) or not c.get("name") or not c.get("value"):
            continue
        domain = c.get("domain", ".familysearch.org")
        session.cookies.set(c["name"], c["value"], domain=domain)
        count += 1
        if c["name"] == "fssessionid":
            fssessionid = c["value"]

    if not fssessionid:
        log.error("No se encontró 'fssessionid' en las cookies.")
        return False

    session.headers["Authorization"] = f"Bearer {fssessionid}"
    log.info(f"✅ {count} cookies cargadas")
    return True

def load_saved_session(session: requests.Session, session_file: str) -> bool:
    path = Path(session_file)
    if not path.exists():
        return False
    try:
        with open(path) as f:
            data = json.load(f)
        fssessionid = data.get("fssessionid")
        cookies = data.get("cookies", [])
        if not fssessionid:
            return False
        for c in cookies:
            session.cookies.set(c["name"], c["value"],
                                domain=c.get("domain", ".familysearch.org"))
        session.headers["Authorization"] = f"Bearer {fssessionid}"
        return True
    except Exception as e:
        log.error(f"Error cargando sesión: {e}")
        return False

def verify_session(session: requests.Session) -> bool:
    resp = _safe_get(session, f"{FS_API}/users/current", accept="application/json", timeout=15)
    if resp and resp.status_code == 200:
        return True
    return False

def _safe_get(session, url, accept="application/json", timeout=30):
    try:
        return session.get(url, headers={"Accept": accept}, timeout=timeout)
    except Exception as e:
        log.warning(f"Error GET {url}: {e}")
        return None
