# 1. Webapp fixes: jobs columns, OCR-confidence label, panel removal, English metadata

**Date:** 2026-06-23
**Branch:** `fix/webapp-jobs-ocr-summary`

Four user-reported webapp issues, run through the engineering-team cycle
(scoped to the four issues, not a full audit). Run dir:
`.engineering-team/runs/manual-20260623T214115Z/`.

## 1.1 Jobs view — task name, timestamp, duration

The Jobs table showed `—` for document-less **system tasks** with no other
useful detail. `task_name` was already in the `/api/jobs` payload but unrendered,
and there was no started/finished timestamp.

- Backend: `jobs.py` `_BASE_QUERY` now pulls `started_at` (last `started` event)
  and `finished_at` (last terminal event) from `procrastinate_events` via
  correlated subqueries — no migration. Added both to the `JobInfo` schema.
- Frontend: `JobsView.vue` gained a humanised **Task** column (final segment of
  `library.jobs.*`), a **Started**/**Finished** timestamp, and a **Duration**
  (`formatDuration`). Active rows show Task + Started; Recent rows add
  Finished + Duration.

## 1.2 OCR confidence blank → "Not applicable" (not a bug)

`ocr_confidence` is set only by real OCR engines (Tesseract/RapidOCR). Born-digital
PDFs (e.g. doc 109, the Coolblue policy) route to `text-layer` extraction, which
has no confidence metric, so the column was a bare `—` — indistinguishable from
missing data. `DocumentDetailView.vue` now renders **"Not applicable —
born-digital text"** when null, the percentage otherwise.

## 1.3 Removed the "View extracted text" panel

Raw `ocr_text` (noisy OCR) was redundant next to the refined per-page markdown.
Removed the panel; kept the `ocr_text` column (still backs FTS). The now-dead
`?highlight=` search-match plumbing (`DocumentListView.vue` → OCR panel) was
removed too — consequence: search results no longer highlight the match on the
detail page, an acceptable trade for the simpler view.

## 1.4 English metadata + backfill

The extraction system prompt said "in the document's own language", so Dutch docs
got Dutch summaries. Changed the prompt: all free-text fields (title, summary,
reasoning_note) in **English**, translated as needed; the `language` field still
records the detected *source* language. Bumped `PROMPT_VERSION` →
`2026-06-23.1`. The existing `backfill-summaries` CLI (targets `summary IS NULL`)
now produces English summaries for legacy docs — run on the live host after deploy.

## 1.5 Verification

- Backend: `502 passed`, ruff clean.
- Frontend: `308 passed`, `vue-tsc` clean, eslint clean.
- New tests: jobs `started_at`/`finished_at` (backend) + task-label/duration
  (frontend); OCR-confidence "Not applicable" + percentage; English-prompt assertion.

## 1.6 Follow-up (ops)

Deploy (push → ghcr `:latest` → redeploy on the paperless LXC), then run
`library backfill-summaries` on the live host with the worker up; spot-check that
previously-summary-less docs gain English summaries.
