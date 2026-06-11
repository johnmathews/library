# Library image: FastAPI backend + the built Vue frontend.
# The same image serves both the `api` and `worker` services; docker-compose
# overrides the command for the worker.

# --- Frontend stage: build the Vue SPA ---
FROM node:22-slim AS frontend

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# `npm run build` type-checks (vue-tsc) and builds (vite) into dist/.
RUN npm run build

# --- Build stage: install dependencies and the project with uv ---
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install third-party dependencies first for better layer caching.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Then install the project itself.
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY migrations/ migrations/
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# --- Runtime stage: slim image, non-root user ---
FROM python:3.13-slim

# OCR system dependencies (per OCRmyPDF docs): tesseract + nld/eng tessdata,
# ghostscript (PDF/A output), unpaper (--clean), pngquant (--optimize >= 2).
# OpenCV is the headless build, so no GUI/libGL packages are needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-nld \
        tesseract-ocr-eng \
        ghostscript \
        unpaper \
        pngquant \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --create-home app \
    && mkdir -p /data && chown app:app /data

COPY --from=builder --chown=app:app /app /app
# The built SPA; served by the API process itself (library.app._mount_spa,
# found via the LIBRARY_FRONTEND_DIST default `frontend/dist` under /app).
COPY --from=frontend --chown=app:app /frontend/dist /app/frontend/dist

ENV PATH="/app/.venv/bin:$PATH"

USER app
WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "library.main:app", "--host", "0.0.0.0", "--port", "8000"]
