# Library image: FastAPI backend + the built Vue frontend.
# The same image serves both the `api` and `worker` services; docker-compose
# overrides the command for the worker.

# --- Frontend stage: build the Vue SPA ---
FROM node:22-slim@sha256:813a7480f28fdadac1f7f5c824bcdad435b5bc1322a5968bbbdef8d058f9dff4 AS frontend

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# `npm run build` type-checks (vue-tsc) and builds (vite) into dist/.
RUN npm run build

# --- Build stage: install dependencies and the project with uv ---
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca AS builder

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
# Pinned to -bookworm (matching the builder stage) so the compiled C-extension
# venv copied from the builder runs against the same Debian/glibc. An unqualified
# python:3.13-slim floats to newer Debian and risks an ABI mismatch.
FROM python:3.13-slim-bookworm@sha256:fcbd8dfc2605ba7c2eca646846c5e892b2931e41f6227985154a596f26ab8ed7

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

# Build metadata for the admin views (see docs/admin.md). GIT_SHA is supplied by
# the CI docker build (`--build-arg GIT_SHA=...`); LIBRARY_GIT_SHA is the env the
# app reads (library.config.Settings.git_sha).
ARG GIT_SHA=""
ENV LIBRARY_GIT_SHA=$GIT_SHA

# Bake the unified CI coverage summary that scripts/coverage_summary.py writes
# into the build context just before `docker build`. It lands at
# /app/coverage-summary.json — the default relative path the backend reads
# (Settings.coverage_summary_path); absent → the admin view reports "unavailable".
# The bracket-glob makes the COPY optional for ad-hoc/local builds that lack the
# file: pairing it with a file that always exists (pyproject.toml) means a
# zero-match on coverage-summar[y].json does not fail the build. CI always writes
# a real summary (or a null-pct placeholder) so the published image ships one.
COPY --chown=app:app pyproject.toml coverage-summar[y].json /app/

# The markdown docs the admin Architecture view renders read-only at runtime
# (Settings.docs_dir default `docs` → /app/docs). Top-level *.md only — the
# heavy docs/ subtrees are kept out of the build context by .dockerignore.
COPY --chown=app:app docs/*.md /app/docs/

ENV PATH="/app/.venv/bin:$PATH"

USER app
WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "library.main:app", "--host", "0.0.0.0", "--port", "8000"]
