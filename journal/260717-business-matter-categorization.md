# Business-matter categorization

Added a new evergreen "business matter" dimension: user-curated subject
categories (car insurance, health insurance, subscriptions, business
services…) that a document can belong to any number of, surfaced as a
homepage button row, and auto-filed at ingest by an LLM. Built end-to-end via
the engineering-team workflow (discussion → plan → 8 work units → wrap-up).

## Why, and the decision that shaped it

The homepage type pills are driven by `kind` — a single-value, closed
vocabulary answering "what shape of paper is this?" The user wanted the
orthogonal axis: "what life-matter does this concern?", multi-membership, as
one-click buttons. Investigation showed the app already had four classification
dimensions; the gap was not the data model (projects are structurally
identical — M2M, OR-filter, click-to-list) but **assignment**: projects are
manual-only and semantically time-bound (archived when done), which fights an
always-on category.

Two decisions were taken with the user before planning:

1. **Assignment = LLM auto-file at ingest** (not manual, not rule-only), plus a
   backfill sweep. Documents arrive pre-filed and stay hand-correctable.
2. **Vocabulary = user-defined, editable anytime** (a DB table with a `hint`
   per matter), not a hardcoded set and not reused free-form tags.

Derived architectural decision: matter classification is a **separate,
lightweight LLM pass** (`library.matter_classifier`, modelled on
`email_label.py`), *not* a field on the main extraction call — so re-classifying
after a vocabulary change re-runs only the cheap pass, never full extraction.

## What shipped (W1–W8)

- **W1** `Matter` model + `document_matters` M2M + `Document.matters`; migration
  0028 (mirrors 0011_projects, adds a `hint` column).
- **W2** `library.matters` service + `library/api/matters.py` CRUD with per-matter
  document counts (reads open, mutations admin-gated); `MatterRef` schema.
- **W3** the standalone LLM classifier: one structured-output call that files a
  document into 0..n *existing* matters from the current vocabulary (name +
  hint). Merge-only, fail-open, budget-gated, respects
  `extra["user_edited_fields"]`. `matter_classifier_model` (default
  claude-haiku-4-5) added to `_PRICED_MODEL_FIELDS`.
- **W4** `classify_document_matters` Procrastinate task, deferred best-effort at
  the EXTRACT stage (mirrors the thumbnail defer — a queue error can't strand
  ingest); `sweep-matters` CLI (default=unclassified, `--all`, `--dry-run`).
- **W5** matters threaded through the whole document API: `?matter=` OR-filter,
  list/detail `matters` field, PATCH full-replacement (flags user-edited, emits
  `matter_changed`), detail-refresh lists in documents.py + notes.py, and the
  MCP server (search filter, `list_matters` tool, doc shape).
- **W6** homepage matter **button row** (multi-select, unlike the single-select
  kind pills) + filter dropdown pill + URL state + taxonomy cache.
- **W7** `/matters` admin management page (create/rename/edit-hint/archive/delete)
  + a matters multiselect & badges in the document editor (editing pins the
  matters, stopping auto-classification for that doc).
- **W8** docs across api.md, admin.md, architecture.md, ingestion.md, roadmap.md,
  README.md.

## Bugs found and fixed during wrap-up

1. **Budget gate was a no-op (`confirmed`).** The classifier's daily-budget gate
   reads today's `matter_classification_completed` spend via `todays_spend_usd`,
   but the pass only stamped `extra["matter_classification"]` and never emitted
   that IngestionEvent — so the budget never accrued and the gate never tripped.
   The original W3 test had *mocked* `todays_spend_usd`, hiding it. Surfaced by
   the doc-writing subagent cross-checking the module against its own docstring.
   Fix: emit the spend event (mirroring `extraction_completed`); added a test
   that exercises the real accrual instead of mocking it.
2. **PATCH matter dedup (`confirmed`, hardened).** `{"matters": ["Car insurance",
   "car-insurance"]}` deduped on the raw string, resolving to the same row twice
   → a possible `document_matters` PK violation / 500. Now dedups on the resolved
   matter id. (This carried over from the identical `projects` PATCH idiom, which
   was left as-is to avoid scope creep.)

## Verification (observed)

- Backend: `uv run pytest` → **1306 passed**; `ruff check .` + `ruff format
  --check .` clean.
- Frontend: `vitest run` → **1007 passed**; `vue-tsc` + `eslint` clean.
- Secret scan over the diff: none.
- Adversarial code review of the full diff: no high/medium correctness or
  security findings.

## What is deliberately not done

1. **Analytics/charts over matters** — out of scope for this cut; noted as a
   roadmap follow-on.
2. **Embedding-based (non-LLM) classifier** — the vocabulary hints could be
   matched by embedding similarity to cut per-doc LLM cost; deferred as a
   possible future optimization. LLM classification was chosen for accuracy.
3. **Seed matter list** — the machinery ships empty; the user will author the
   initial 8–12 matters (with hints) via the `/matters` page, then run
   `sweep-matters` to backfill the existing corpus.
4. **User-pinned docs stay perpetual sweep candidates** (low severity, known).
   A document whose matters were hand-edited never gets the
   `matter_classification` provenance stamp (the classifier early-returns), so
   the default `sweep-matters` re-enqueues it each run — each job fetches,
   early-returns, and commits a no-op. Behavior is correct, only mildly
   wasteful; `sweep-matters` is a manual admin command, so left as-is.
