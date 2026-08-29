# Documentation

**Status:** active. **Last updated:** 2026-08-29 (indexed the new `charts.md` — the chart engine over `spend_facts`). Earlier the same day (indexed the new `money-facts.md` — amount semantics and payment identity). Earlier (2026-08-28): indexed the new `facets.md` — the controlled facet vocabulary. Earlier (2026-08-21): indexed the new `observability.md`. Earlier (2026-08-20): indexed the new `llm-backends.md`. Earlier (2026-08-12, documentation verification sweep): corrected the `frontend.md` one-liner, which claimed PWA coverage the document did not have.
**Last verified:** 2026-08-30 — method: this document and `architecture.md` went red on `main` not because the index drifted but because PR #121 squash-merged as commit `b32a67c`, dated 2026-08-30 UTC, one day after every stamp written on the branch — the mechanical failure mode already on record for `--since=<bare date>`, here hitting the commit-date comparison instead. Re-verified rather than merely re-dated: read every row in §2's table, confirmed each linked document still resolves on disk, and confirmed `charts.md` and `money-facts.md`'s one-line descriptions still match those documents' current section headings after the chart-engine merge; re-derived the gated set from `GATED_GLOBS`/`EXCLUDED_DIRS` in `scripts/check_docs.py` to confirm the index still covers it in both directions, and ran `uv run python scripts/check_docs.py --max-violations 0`, clean after this pass's re-stamping. The rest of the index is unchanged since the earlier 2026-08-29 verification, whose method was: confirmed `money-facts.md` resolves on disk and that its index description matches that document's section headings, and re-derived the gated set from `GATED_GLOBS`/`EXCLUDED_DIRS` in `scripts/check_docs.py` to confirm the index still covers it in both directions. The rest of the index is unchanged since the 2026-08-28 verification, whose method was: confirmed `facets.md` resolves on disk and that its index description matches that document's section headings, and re-derived the gated set from `GATED_GLOBS`/`EXCLUDED_DIRS` in `scripts/check_docs.py` to confirm the index still covers it in both directions. The rest of the index is unchanged since the 2026-08-21 verification, whose method was: confirmed the new `observability.md` row resolves on disk and that its description matches that document's headings, and re-derived the gated set from `GATED_GLOBS`/`EXCLUDED_DIRS` in `scripts/check_docs.py` to confirm the index still covers it in both directions. The rest of the index is unchanged since the 2026-08-12 sweep, whose method was: resolved every markdown link and directory link on disk, derived the gated set and diffed it against the index in both directions, and checked each description against its target's headings.

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
| [`llm-backends.md`](llm-backends.md) | The two ways library reaches Claude: metered API vs Claude subscription, which surfaces may use which, the per-call harness cost, and the credential runbook. |
| [`observability.md`](observability.md) | What the app measures about itself: OpenTelemetry metrics for Ask (tokens by cache kind, cost, latency, tool-loop depth, errors), the Prometheus and OTLP exporters, and the content-logging guard that keeps document text out of telemetry. |
| [`frontend.md`](frontend.md) | The Vue 3 SPA: views, components, stores, the Mosaic design language, dark mode, PWA wiring, tests. |
| [`frontend-view-principles.md`](frontend-view-principles.md) | How to build a new view that is consistent the first time: layout, shared classes, form/filter recipes. |
| [`admin.md`](admin.md) | The admin role and admin views: users, taxonomy (senders/kinds/recipients), currencies, FX rates, business matters. |
| [`jobs-and-notifications.md`](jobs-and-notifications.md) | Background jobs, the Jobs view, live SSE toasts, and Pushover notifications. |
| [`smart-groups.md`](smart-groups.md) | Smart Groups: semantic authored series — the membership scorer, the three flows, mixed currency, and the LLM's one narrow role. |
| [`facets.md`](facets.md) | The controlled facet vocabulary that replaced free-form tags: what a facet is, the shipped vocabulary, the closed-set rule and suggestion queue, vocabulary CRUD and its costs, `library label-archive`/`library recipients`, and the REST surface. |
| [`money-facts.md`](money-facts.md) | What an amount means (`amount_kind` and its sign, `reference`) and payment identity: the sign precondition, the rules and the veto that collapse one payment documented as two or more documents into a single count, the `payment_overrides` correction table, `library backfill-amounts`, the known limits, and the REST surface. |
| [`charts.md`](charts.md) | The chart engine: the `spend_facts` relation and the canonical-document rule, spend lines and label inheritance, a rule as a SQL predicate, the two orthogonal axes and the invariant total, per-document-date conversion, the footer's eight categories, drill-through, LLM rule drafting, and the `/api/spending` surface. |
| [`migration.md`](migration.md) | Migrating an existing archive from paperless-ngx. |
| [`roadmap.md`](roadmap.md) | Deferred work and forward-looking notes. |

## 3. Sub-directories

- [`runbooks/`](runbooks/) — operational runbooks: the [deploy runbook](runbooks/deploy.md) and the [email-triage runbook](runbooks/email-triage.md) (reading the email decision trace and the held queue).
- [`benchmarks/`](benchmarks/) — performance benchmarks (e.g. the OCR engine comparison).
- [`archive/`](archive/) — superseded docs, kept for their decisions and rationale.
- [`superpowers/`](superpowers/) — historical implementation plans and design specs (completed work, kept as a decision record).

The development journal (dated decisions, progress, and context) lives in [`../journal/`](../journal/).
