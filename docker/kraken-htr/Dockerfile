# kraken-htr microservice — separate stack so kraken+torch (GBs) stay out of the gen_suite backend.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 KRAKEN_MODEL_DIR=/models

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip \
    && pip install "kraken>=5.2" "fastapi>=0.111" "uvicorn[standard]>=0.30" "pillow>=10.3"

WORKDIR /app
COPY app.py /app/app.py
VOLUME ["/models"]
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
