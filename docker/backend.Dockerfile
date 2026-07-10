# Backend image — also used by the ARQ worker and the one-shot migrate service
# (same image, different command). Includes tesseract for the local OCR engine.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg tesseract-ocr tesseract-ocr-spa fonts-dejavu-core \
    # PostgreSQL 16 client (pg_dump) for the optional DB→S3 backup, from the PGDG repo so the dump
    # version matches the PG16 server (an older pg_dump refuses to dump a newer server).
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
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
