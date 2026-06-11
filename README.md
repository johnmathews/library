# Library

A self-hosted personal/family document archive — a faster, more flexible
take on paperless-ngx. Library ingests scans, photos and digital documents
(PDF, JPG, PNG, HEIC, TIFF, text), digitises them with a routed OCR
pipeline (Tesseract for scans, neural OCR for photos), enriches them with
structured metadata via the Claude API (kind, sender, dates, amounts,
tags, summary), and makes everything searchable in Dutch and English.

**Status: v0.1.0 — feature-complete first release.** Everything below
works today (see [`CHANGELOG.md`](CHANGELOG.md) for the full list and
[`docs/architecture.md`](docs/architecture.md) for the design):

- **Ingestion from anywhere:** web upload (camera-friendly on mobile), a
  watched consume folder (Syncthing/scanner drops), email-in (IMAP
  attachment polling), REST, MCP, and a paperless-ngx importer.
- **Routed OCR:** born-digital PDFs keep their text layer; scans go
  through OCRmyPDF/Tesseract (`nld+eng`, searchable-PDF output); photos
  get OpenCV perspective correction + RapidOCR; a confidence gate retries
  weak results on the other engine.
- **Claude metadata extraction:** structured outputs (Haiku 4.5,
  escalating to Sonnet 4.6 on low confidence) fill kind, sender, title,
  summary, dates, amounts, language and tags — with a daily budget cap,
  full provenance, and user edits always winning.
- **Search:** Postgres FTS with both Dutch and English stemming,
  websearch syntax, filters, ranked snippets.
- **Web app:** Vue 3 SPA on GOV.UK design patterns (without the
  licence-restricted GDS assets), installable on iOS/Android, responsive
  to 320px.
- **Interfaces:** cookie/bearer-authenticated REST API (OpenAPI at
  `/docs`) and an MCP server at `/mcp`, so LLM clients can search, read
  and ingest documents.

## Stack

- **Backend:** Python 3.13, FastAPI, SQLAlchemy 2 (async), PostgreSQL 17,
  Procrastinate (Postgres-native job queue) — managed with [uv](https://docs.astral.sh/uv/)
- **Frontend:** Vue 3 + TypeScript, Vite, vue-router, Pinia — design based
  on the [GOV.UK Design System](https://design-system.service.gov.uk/)
- **Deployment:** Docker Compose (api + worker + db; the api image also
  serves the built frontend); images published to
  `ghcr.io/johnmathews/library`

## Quickstart (production)

Follow [`docs/deployment.md`](docs/deployment.md) — clone, `cp
.env.example .env`, `docker compose up -d --build`, create a user with
`docker compose exec api library user add …`, log in on `:8000`. The
guide covers the Proxmox-LXC walkthrough, reverse proxy, backups,
upgrades, and troubleshooting.

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

Project documentation lives in [`docs/`](docs/) — architecture,
deployment, API, MCP, ingestion, frontend, paperless migration, and the
OCR benchmark. The development journal (decisions, progress, context)
lives in [`journal/`](journal/).
