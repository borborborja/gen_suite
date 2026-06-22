"""Vendored FamilySearch core (extracted from fs_downloader). Reused verbatim by the gated
FamilySearch connector; only the storage sink (disk → MinIO) is adapted at the suite layer."""
from .auth import (
    authenticate_from_cookies_file,
    build_session,
    load_saved_session,
    verify_session,
)
from .downloader import (
    download_image_binary,
    extract_ark,
    extract_download_url,
    extract_next_iid,
    fetch_catalog_title,
    get_image_metadata,
)
from .models import DownloadManifest, ImageRecord
from .parser import parse_fs_url, resolve_first_image_ark

__all__ = [
    "build_session",
    "authenticate_from_cookies_file",
    "load_saved_session",
    "verify_session",
    "parse_fs_url",
    "resolve_first_image_ark",
    "get_image_metadata",
    "extract_download_url",
    "extract_next_iid",
    "extract_ark",
    "download_image_binary",
    "fetch_catalog_title",
    "ImageRecord",
    "DownloadManifest",
]
