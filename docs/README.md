# Documentation

**Status:** active. **Last updated:** 2026-11-05 (the `mcp.md` row adds writing notes and fetching files to the tool jobs).
**Last verified:** 2026-11-05 — method: partial, scoped to the `mcp.md` row, read against the tools table in `mcp.md` §1.3; `uv run python scripts/check_docs.py` clean. No row was added or removed, so the gated set is unchanged.

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
| [`deployment.md`](deployment.md) | Building and deploying the container (local dev + the live `documents` LXC), env/config, migrations. |
| [`api.md`](api.md) | The full REST API: every endpoint, method, request/response shape, and query filters. |
| [`ingestion.md`](ingestion.md) | How a file becomes a Document: upload → storage → OCR → extraction → markdown → embedding. |
| [`ask.md`](ask.md) | The "Ask" semantic Q&A feature: hybrid retrieval, the agentic tool loop, citations, metadata writes. |
| [`mcp.md`](mcp.md) | The MCP server at `/mcp`: the tools LLM clients can call to search by keyword or meaning, read, total amounts, fetch files, ingest, write notes, edit and comment on documents, and OAuth sign-in for claude.ai and ChatGPT connectors (and Claude Code). |
| [`llm-backends.md`](llm-backends.md) | The two ways library reaches Claude: metered API vs Claude subscription, which surfaces may use which, the per-call harness cost, and the credential runbook. |
| [`observability.md`](observability.md) | What the app measures about itself: OpenTelemetry metrics for Ask (tokens by cache kind, cost, latency, tool-loop depth, errors), the Prometheus and OTLP exporters, and the content-logging guard that keeps document text out of telemetry. |
| [`frontend.md`](frontend.md) | The Vue 3 SPA: views, components, stores, the Mosaic design language, dark mode, PWA wiring, tests. |
| [`frontend-view-principles.md`](frontend-view-principles.md) | How to build a new view that is consistent the first time: layout, shared classes, form/filter recipes. |
| [`admin.md`](admin.md) | The admin role and admin views: users, taxonomy (senders/kinds/recipients), currencies, FX rates, business matters. |
| [`jobs-and-notifications.md`](jobs-and-notifications.md) | Background jobs, the Jobs view, live SSE toasts, and Pushover notifications. |
| [`facets.md`](facets.md) | The controlled facet vocabulary that replaced free-form tags: what a facet is, the shipped vocabulary, the closed-set rule and suggestion queue, vocabulary CRUD and its costs, `library label-archive`/`library recipients`, and the REST surface. |
| [`money-facts.md`](money-facts.md) | What an amount means (`amount_kind` and its sign, `reference`) and payment identity: the sign precondition, the rules and the veto that collapse one payment documented as two or more documents into a single count, the `payment_overrides` correction table, `library backfill-amounts`, the known limits, and the REST surface. |
| [`charts.md`](charts.md) | The chart engine: the `spend_facts` relation and the canonical-document rule, spend lines and label inheritance, a rule as a SQL predicate, the two orthogonal axes and the invariant total, per-document-date conversion, the footer's eight categories, drill-through, the two producers of a rule (LLM drafting and the clause editor) and their two different enforcement mechanisms, and the `/api/spending` surface including the preview route. |
| [`spending-view.md`](spending-view.md) | Using the spending charts: the board and the workspace, asking a question in plain language and its three outcomes, reading the footer's three blocks, drilling from a bar or a footer count to the documents behind it, how colour is assigned, editing a saved chart's clauses and split axis, and what the feature still does not do. The user-facing companion to `charts.md`. |
| [`migration.md`](migration.md) | Migrating an existing archive from paperless-ngx. |
| [`roadmap.md`](roadmap.md) | Deferred work and forward-looking notes. |

## 3. Sub-directories

- [`runbooks/`](runbooks/) — operational runbooks: the [deploy runbook](runbooks/deploy.md) and the [email-triage runbook](runbooks/email-triage.md) (reading the email decision trace and the held queue).
- [`benchmarks/`](benchmarks/) — performance benchmarks (e.g. the OCR engine comparison).
- [`archive/`](archive/) — superseded docs, kept for their decisions and rationale.
- [`superpowers/`](superpowers/) — historical implementation plans and design specs (completed work, kept as a decision record).

The development journal (dated decisions, progress, and context) lives in [`../journal/`](../journal/).
