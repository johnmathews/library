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
  attachment polling), in-app note authoring (markdown, edited in place with
  version history), REST, MCP, and a paperless-ngx importer.
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
- **Live jobs & notifications:** a Jobs view of current and historical
  background work, a navbar running-jobs indicator, and toasts when a
  document finishes — pushed live over Server-Sent Events (no polling).
  Each user can also opt into **Pushover** push notifications (their own
  credentials, owner-targeted, per-event toggles for success, errors,
  needs-review and duplicates). See
  [`docs/jobs-and-notifications.md`](docs/jobs-and-notifications.md).
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

### Naming: there is no separate frontend container

One container — called `api` in this repo's compose file and
**`library-webserver`** in the production stack — serves *three* things
on one port: the compiled Vue web app, the REST API (`/api`), and the
MCP server (`/mcp`). "Webserver" names its **role** (the HTTP-facing
half of the app, alongside `worker` and `db`), not a feature list: the
SPA is just static files FastAPI serves same-origin, which is what keeps
cookie auth simple and CORS nonexistent. So when looking for "the
frontend", "the API", or "the MCP server" — they are all the same
container.

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
deployment, API, MCP, ingestion, frontend, admin role & views, jobs &
notifications, paperless migration, and the OCR benchmark. The development
journal (decisions, progress, context) lives in [`journal/`](journal/).
