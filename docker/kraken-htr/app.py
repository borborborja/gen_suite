"""kraken-htr — a thin FastAPI wrapper around Kraken's Python API (plan §6).

Deployed as its OWN homelab stack (kraken + torch are gigabytes; they must not bloat the gen_suite
backend image). gen_suite calls this over HTTP at the engine's base_url. Contract:

    POST /htr  {"image": <base64>, "model": <optional .mlmodel name>}
            -> {"text": str, "lines": [{"bbox":[x,y,w,h], "text":str, "conf":float}], "confidence": float}

Models live in /models (shared by volume with eScriptorium so corrected pages can retrain them).
"""
from __future__ import annotations

import base64
import io
import os
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

MODEL_DIR = os.environ.get("KRAKEN_MODEL_DIR", "/models")
DEFAULT_MODEL = os.environ.get("KRAKEN_DEFAULT_MODEL", "default.mlmodel")

app = FastAPI(title="kraken-htr", version="0.1.0")


class HtrRequest(BaseModel):
    image: str  # base64-encoded image bytes
    model: str | None = None


@lru_cache(maxsize=8)
def _load_model(name: str):
    from kraken.lib import models  # imported lazily so the module imports without kraken installed

    path = os.path.join(MODEL_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError(name)
    return models.load_any(path)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_dir": MODEL_DIR}


@app.post("/htr")
def htr(req: HtrRequest) -> dict:
    from kraken import blla, rpred
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(base64.b64decode(req.image))).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"bad image: {exc}") from exc

    try:
        net = _load_model(req.model or DEFAULT_MODEL)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"model not found: {exc}") from exc

    baseline_seg = blla.segment(img)  # line/baseline segmentation
    lines: list[dict] = []
    confs: list[float] = []
    for record in rpred.rpred(net, img, baseline_seg):
        conf = float(sum(record.confidences) / len(record.confidences)) if record.confidences else 0.0
        box = list(getattr(record, "line", {}).get("bbox", []) or [])
        lines.append({"bbox": box, "text": str(record), "conf": conf})
        confs.append(conf)

    return {
        "text": "\n".join(ln["text"] for ln in lines),
        "lines": lines,
        "confidence": (sum(confs) / len(confs)) if confs else 0.0,
    }
