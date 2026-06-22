"""Client for the ``kraken-htr`` microservice (plan §6).

Kraken (+torch) is NOT installed in the backend image — it would add gigabytes. Instead a thin
FastAPI service wraps Kraken's Python API and is deployed as a separate homelab stack; gen_suite
calls it over HTTP at the engine's ``base_url``. Contract:

    POST {base_url}/htr   body: {"image": <base64 jpeg>, "model": <optional .mlmodel name>}
    200  -> {"text": str, "lines": [{"bbox": [x,y,w,h], "text": str, "conf": float}], "confidence": float}

This module only does the HTTP call + parsing; the line geometry can later populate
``Record.region_bbox`` for the review UI.
"""
from __future__ import annotations

import base64

import httpx


class KrakenError(RuntimeError):
    pass


def htr_via_kraken(
    jpeg: bytes, *, model: str | None, base_url: str | None, timeout: float = 120.0
) -> str:
    """Transcribe one page image via the kraken-htr microservice. Returns the plain text
    (full-page, lines joined). Raises KrakenError on transport/protocol failure."""
    if not base_url:
        raise KrakenError("kraken engine requires base_url pointing at the kraken-htr service")
    payload = {"image": base64.b64encode(jpeg).decode("ascii")}
    if model:
        payload["model"] = model
    url = base_url.rstrip("/") + "/htr"
    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise KrakenError(f"kraken-htr request failed: {exc}") from exc
    text = data.get("text")
    if text is None and isinstance(data.get("lines"), list):
        text = "\n".join(ln.get("text", "") for ln in data["lines"])
    if not isinstance(text, str):
        raise KrakenError("kraken-htr response missing 'text'")
    return text.strip()
