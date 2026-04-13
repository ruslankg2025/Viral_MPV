FROM python:3.11-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 1) shared viral_llm library
COPY Modules/shared/llm /opt/viral_llm
RUN pip install /opt/viral_llm

# 2) processor deps
COPY Modules/processor/requirements.txt .
RUN pip install -r requirements.txt

# 3) processor source
COPY Modules/processor/ .

RUN mkdir -p /media /db

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
