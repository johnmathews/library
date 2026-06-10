# Library

A self-hosted personal/family document archive — a faster, more flexible
take on paperless-ngx. Library ingests scans, photos and digital documents
(PDF, JPG, PNG, HEIC, …), digitises them with a routed OCR pipeline
(Tesseract for scans, neural OCR for photos), enriches them with structured
metadata via the Claude API (kind, sender, dates, amounts, tags, summary),
and makes everything searchable in Dutch and English.

**Status: early development.** The scaffold, CI and architecture are in
place; features are landing unit by unit — see
[`docs/architecture.md`](docs/architecture.md) for the design and current
state.

## Stack

- **Backend:** Python 3.13, FastAPI, SQLAlchemy 2 (async), PostgreSQL 17,
  Procrastinate (Postgres-native job queue) — managed with [uv](https://docs.astral.sh/uv/)
- **Frontend:** Vue 3 + TypeScript, Vite, vue-router, Pinia — design based
  on the [GOV.UK Design System](https://design-system.service.gov.uk/)
- **Interfaces:** REST API (OpenAPI) and an MCP server, so other tools can
  search, read and ingest documents
- **Deployment:** Docker Compose (api + worker + db); images published to
  `ghcr.io/johnmathews/library`

## Development

```bash
# Backend: API on :8000
uv sync
make dev

# Frontend: Vite dev server on :5173, proxies /api to :8000
cd frontend && npm install && npm run dev

# Everything via Docker
docker compose up --build
```

Run the checks CI runs:

```bash
make lint test                      # backend
cd frontend && npm run lint && npm run type-check && npm run test:unit
```

## Documentation

Project documentation lives in [`docs/`](docs/). The development journal
(decisions, progress, context) lives in [`journal/`](journal/).
