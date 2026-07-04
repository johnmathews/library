# Documentation

Reference documentation for Library — a self-hosted document archive (FastAPI
backend + Vue 3 SPA, Postgres/pgvector, OCR ingestion, an MCP server, and an
LLM-backed "Ask" feature).

## 1. Start here

New to the codebase? Read in this order:

1. [`architecture.md`](architecture.md) — the shape of the system: modules, data model, how the pieces fit.
2. [`deployment.md`](deployment.md) — how to run it (local and on the live host); see also [`runbooks/deploy.md`](runbooks/deploy.md).
3. [`api.md`](api.md) — the REST surface once the system is running.

## 2. Reference docs

| Doc | What it covers |
| --- | --- |
| [`architecture.md`](architecture.md) | System architecture: module layout, data model, subsystems, how ingestion/search/ask fit together. |
| [`deployment.md`](deployment.md) | Building and deploying the container (local dev + the live `paperless` LXC), env/config, migrations. |
| [`api.md`](api.md) | The full REST API: every endpoint, method, request/response shape, and query filters. |
| [`ingestion.md`](ingestion.md) | How a file becomes a Document: upload → storage → OCR → extraction → markdown → embedding. |
| [`ask.md`](ask.md) | The "Ask" semantic Q&A feature: hybrid retrieval, the agentic tool loop, citations, metadata writes. |
| [`mcp.md`](mcp.md) | The MCP server at `/mcp`: the tools LLM clients can call to search, read, and ingest documents. |
| [`frontend.md`](frontend.md) | The Vue 3 SPA: views, components, stores, the Mosaic design language, PWA behaviour. |
| [`frontend-view-principles.md`](frontend-view-principles.md) | How to build a new view that is consistent the first time: layout, shared classes, form/filter recipes. |
| [`admin.md`](admin.md) | The admin role and admin views: users, taxonomy (senders/kinds/recipients), currencies, FX rates. |
| [`jobs-and-notifications.md`](jobs-and-notifications.md) | Background jobs, the Jobs view, live SSE toasts, and Pushover notifications. |
| [`migration.md`](migration.md) | Migrating an existing archive from paperless-ngx. |
| [`roadmap.md`](roadmap.md) | Deferred work and forward-looking notes. |

## 3. Sub-directories

- [`runbooks/`](runbooks/) — operational runbooks (e.g. the [deploy runbook](runbooks/deploy.md)).
- [`benchmarks/`](benchmarks/) — performance benchmarks (e.g. the OCR engine comparison).
- [`archive/`](archive/) — superseded docs, kept for their decisions and rationale.
- [`superpowers/`](superpowers/) — historical implementation plans and design specs (completed work, kept as a decision record).

The development journal (dated decisions, progress, and context) lives in [`../journal/`](../journal/).
