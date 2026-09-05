# Образ сервиса montage: FastAPI-очередь + детерминированный оркестратор поверх
# инструментов OpenMontage (движок; Rule Zero). torch НЕ ставим (CPU-путь).
#
# ⚠️ Парити движка: на проде OpenMontage лежит РАСПАКОВКОЙ (не git), а образ
# клонирует пиннутый коммит из GitHub. После сборки ОБЯЗАТЕЛЕН in-container smoke
# (тот же клип, что 2026-09-02), чтобы поведение инструментов совпало с
# провалидированным. Если разойдётся — вендорить дерево OpenMontage с прода.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENMONTAGE_ROOT=/opt/OpenMontage \
    HF_HOME=/montage/hf

# Пин движка OpenMontage (github calesthio/OpenMontage). Менять осознанно.
ARG OPENMONTAGE_COMMIT=cd9f3c1f03368be87b140af494914b8ee4e3c7a4

# Системные зависимости:
#  ffmpeg              — рендер/энкод (video_compose, normalize, loudnorm)
#  git                 — клон движка на пиннутый коммит
#  libgl1/libglib2.0-0 — рантайм OpenCV (mediapipe/auto_reframe/visual_qa)
#  libgomp1            — OpenMP (ctranslate2/onnxruntime)
#  libsndfile1         — sounddevice (тянет mediapipe)
# node/шрифты Manrope+Bebas — НЕ здесь: нужны на A5 (remotion b-roll) и A3
# (ASS-субтитры/хук). Добавим в этих фазах, чтобы не раздувать A0-образ.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git ca-certificates \
        libgl1 libglib2.0-0 libgomp1 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 1) Движок OpenMontage на пиннутом коммите
RUN git clone https://github.com/calesthio/OpenMontage.git "$OPENMONTAGE_ROOT" \
    && git -C "$OPENMONTAGE_ROOT" checkout "$OPENMONTAGE_COMMIT" \
    && rm -rf "$OPENMONTAGE_ROOT/.git"

# 2) Базовые зависимости OpenMontage (CPU; requirements-gpu.txt с torch пропускаем)
RUN pip install -r "$OPENMONTAGE_ROOT/requirements.txt"

# 3) Наши доп.зависимости + CPU-зависимости инструментов (точные пины)
WORKDIR /app
COPY Modules/montage/requirements-montage.txt .
RUN pip install -r requirements-montage.txt

# 4) Код сервиса + оркестратор (+ будущие custom-tools в Modules/montage/tools/,
#    которые на A1+ будем копировать в $OPENMONTAGE_ROOT/tools/)
COPY Modules/montage/ .

# Тома монтируются в compose: /db (SQLite), /montage (сырьё+результаты)
RUN mkdir -p /db /montage

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
