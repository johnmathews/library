# Library backend image.
# The same image serves both the `api` and `worker` services; docker-compose
# overrides the command for the worker.

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
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# --- Runtime stage: slim image, non-root user ---
FROM python:3.13-slim

RUN groupadd --system app && useradd --system --gid app --create-home app \
    && mkdir -p /data && chown app:app /data

COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH"

USER app
WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "library.main:app", "--host", "0.0.0.0", "--port", "8000"]
