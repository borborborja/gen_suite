from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Optional

@dataclass
class ImageRecord:
    index: int
    iid: str
    ark: str
    filename: str
    status: str          # "pending" | "ok" | "restricted" | "error" | "skipped"
    error_message: str = ""
    file_size_bytes: int = 0
    download_url: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

@dataclass
class DownloadManifest:
    catalog_url: str
    film_dgs: str = ""
    collection_title: str = ""
    municipio: str = ""
    archivo: str = ""
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    finished_at: str = ""
    auth_mode: str = ""
    images: List[Dict] = field(default_factory=list)
    downloaded: int = 0
    restricted: int = 0
    errors: int = 0
    total_images: int = 0
    output_dir: str = ""
