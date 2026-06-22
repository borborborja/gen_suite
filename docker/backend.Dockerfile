# Backend image — also used by the ARQ worker and the one-shot migrate service
# (same image, different command). Tesseract is added in Phase 4 (M1 transcription).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl tesseract-ocr tesseract-ocr-spa fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/ /app/
RUN pip install --upgrade pip && pip install -e ".[dev]"

# Vendored FamilySearch downloader (fs_core), installed editable. The libros2pdf OCR helpers were
# absorbed into app/modules/transcription/ocr_engines.py, so they no longer need a vendored package.
COPY libs/ /libs/
RUN pip install -e /libs/fs_downloader

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
