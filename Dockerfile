# syntax=docker/dockerfile:1

# One image for the API, the ingestion worker and the one-shot database setup (docker-compose.yml).
#
#   docker build -t attendance-ai:local .
#
# Layers go from least to most often changed: OS packages, Python dependencies, embedding models,
# the built UI, then the application source. A source-only change rebuilds only the last layers.

ARG PYTHON_IMAGE=python:3.12-slim-trixie
ARG NODE_IMAGE=node:22-slim
ARG UV_VERSION=0.8.18

# uv, pinned. A named stage, because COPY --from cannot expand build arguments.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------- Stage 1: the React UI (Vite, base /ui/) ----------
FROM ${NODE_IMAGE} AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: the runtime image ----------
FROM ${PYTHON_IMAGE} AS runtime

# Tesseract OCR for scanned PDFs. The package depends on its English and OSD language data.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

# The unprivileged user the containers run as.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    MODEL_CACHE_DIR=/opt/models

WORKDIR /app

# Third-party dependencies only, without the dev group. Rebuilt only when pyproject.toml or uv.lock
# change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project

# FastEmbed models, downloaded now into a folder that is not a volume: the API and the worker load
# the same files from the image, without downloading at start-up. Keep these names in line with
# embedding_model, sparse_model and rerank_model in src/attendance_ai/core/config.py; a model missing
# here is downloaded by each container at first use. The folder belongs to the runtime user: the
# Hugging Face cache writes some index files readable by their owner only.
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
ARG SPARSE_MODEL=Qdrant/bm25
ARG RERANK_MODEL=Xenova/ms-marco-MiniLM-L-6-v2
RUN python - <<'PY' && chown -R app:app "$MODEL_CACHE_DIR"
import os
from pathlib import Path

from fastembed import SparseTextEmbedding, TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder

cache_dir = os.environ["MODEL_CACHE_DIR"]
TextEmbedding(model_name=os.environ["EMBEDDING_MODEL"], cache_dir=cache_dir)
SparseTextEmbedding(model_name=os.environ["SPARSE_MODEL"], cache_dir=cache_dir)
TextCrossEncoder(model_name=os.environ["RERANK_MODEL"], cache_dir=cache_dir)

# fastembed takes a model from the cache only if the model's file is there, and BM25 has none: it
# names the placeholder "mock.file". Without an empty one, BM25 would need the Hugging Face API on
# every start.
for snapshot in Path(cache_dir).glob("models--Qdrant--bm25/snapshots/*"):
    (snapshot / "mock.file").touch()
PY

# The built UI, which the API serves at /ui/ from /app/frontend/dist.
COPY --from=ui /ui/dist ./frontend/dist

# The application. It must sit at /app/src/attendance_ai: config.py derives PROJECT_ROOT (/app) from
# its own location, and finds the seed file, the UI and var/ from there.
COPY pyproject.toml uv.lock alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY sample_data ./sample_data
COPY src ./src

# Install the project itself in editable mode (uv's default for this project): the venv points at
# /app/src rather than copying the package into site-packages, which would move PROJECT_ROOT.
# Then create the uploads folder; a new named volume mounted there takes over its owner.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev \
 && mkdir -p var/storage \
 && chown -R app:app var

USER 10001:10001

EXPOSE 8000

# The API by default; docker-compose.yml gives the worker and the setup job their own commands.
CMD ["uvicorn", "attendance_ai.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
