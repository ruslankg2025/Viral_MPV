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

# Stub-фикстура: 1-секундный чёрный mp4 ~10 KB. На dev можно подменить
# реальным виральным роликом через volume-mount в compose.override.
RUN mkdir -p /app/fixtures /media /db && \
    ffmpeg -hide_banner -loglevel error \
        -f lavfi -i color=c=black:s=240x320:d=1 \
        -c:v libx264 -pix_fmt yuv420p -t 1 \
        -y /app/fixtures/test_fixture.mp4

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
