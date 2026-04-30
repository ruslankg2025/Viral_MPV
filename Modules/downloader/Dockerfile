FROM python:3.11-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY Modules/downloader/requirements.txt .
RUN pip install -r requirements.txt

COPY Modules/downloader/ .

# Test fixture для stub-режима (используется при STUB_MODE=true)
COPY ["Залетевший роллик.mp4", "/app/fixtures/test_fixture.mp4"]

RUN mkdir -p /media /db

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
