FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 1) shared viral_llm library (pre-built, меняется редко)
COPY Modules/shared/llm /opt/viral_llm
RUN pip install /opt/viral_llm

# 2) script deps
COPY Modules/script/requirements.txt .
RUN pip install -r requirements.txt

# 3) script source
COPY Modules/script/ .

RUN mkdir -p /db

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
