# Extraction quality: validate, review, measure

**Date:** 2026-06-21. **Branch:** `feat/extraction-quality`.
**Workflow:** spec → 14 tasks (T1–T14).

## 1.1 What and why

Structured metadata from Claude extraction is only useful if it is trustworthy.
Before this work the system stored a self-reported `confidence` (`high`/`medium`/
`low`) but had no way to detect hallucinated amounts, implausible dates, or
documents where extraction simply produced nothing. There was also no aggregate
accuracy measurement — no way to know whether a prompt change improved things.

This sprint delivered two complementary capabilities:

1. **Per-document trust signal** — deterministic validation rules fire on every
   extraction, set `review_status`, and surface a "needs review" queue in the UI.
2. **Aggregate eval harness** — `library eval-extractions` combines a corrections
   flywheel with an LLM-as-judge to produce per-field accuracy numbers, written to
   `eval_runs` and comparable across prompt/model versions.

## 1.2 Key decisions

1. **Per-doc signal and aggregate harness are equal priorities.** Both were required
   to justify each other: the queue shows individual problems; the harness shows
   whether fixing prompts actually helps.
2. **Ground truth = corrections flywheel + LLM-as-judge.** No large hand-labelled
   gold set required upfront. User corrections are the highest-quality signal;
   the judge covers documents the user hasn't touched.
3. **Six deterministic rules shipped; date-grounding deliberately excluded.**
   Locale date-format matching (e.g. `21-06-26` vs `21 juni 2026`) is fiddly;
   the simpler rules (`amount_grounding`, `date_plausibility`, etc.) prove the
   pattern first.
4. **Trust signal = `review_status` enum + per-field findings; no numeric score.**
   The enum is filterable, indexable, and unambiguous. Per-field findings in
   `extra["validation"]` tell the user exactly what to check. A scalar score
   is harder to interpret and easier to game.
5. **LLM judge is batch-only.** Running the judge in the live pipeline would add
   latency and cost on every document. An `extraction_judge_inline` config flag is
   reserved (default `false`) so this can be wired up later without redesign.
6. **Review UX = list filter + "needs review" preset + inline detail badges.**
   A dedicated triage screen is deferred until list-based triage shows friction.
7. **"Improve" is the measurement loop, not new optimisation code.** Because
   corrections are stored in the mining-ready `extra["corrections"]` shape, few-shot
   injection and stale-prompt re-extraction can be added later with no data migration.

## 1.3 What shipped (T1–T13, plus this journal)

1. **T1** — `src/library/extraction/validation.py`: six deterministic rules,
   `Finding` dataclass, `validate()`, `derive_review_status()`.
2. **T2** — migration 0006: `documents.review_status` enum column
   (`verified`/`needs_review`/`unreviewed`, default `unreviewed`, indexed);
   `eval_runs` table (`id`, `created_at`, `prompt_version`, `model`,
   `version_mix` JSONB, `sample_size`, `per_field` JSONB, `overall` JSONB).
3. **T3** — `apply_extraction` calls `_apply_validation` as its final step (same
   transaction); sets `review_status` and writes `extra["validation"]`. Three new
   config settings: `extraction_validation_ocr_floor` (50.0),
   `extraction_judge_model` (`claude-sonnet-4-6`), `extraction_judge_inline`
   (`false`).
4. **T4** — `library backfill-validation [--limit N]` CLI: idempotent, mirrors
   `backfill-embeddings` shape, seeds the review queue on the existing corpus.
5. **T5** — corrections flywheel: `PATCH /api/documents/{id}` now also appends
   structured records to `extra["corrections"]` with `field`, `original_value`,
   `corrected_value`, `source_excerpt`, `prompt_version`, `model`, `corrected_at`.
6. **T6** — API: `review_status` query param on `GET /documents`; `review_status`
   field on list items; `validation` field on detail; `POST /documents/{id}/verify`
   (sets `review_status=verified`, records `review_verified` event, returns detail).
7. **T7** — `src/library/extraction/judge.py`: async `judge(document, *, client,
   settings) -> JudgeResult`; per-field `FieldVerdict` (`correct`/`wrong`/
   `unsupported`); structured outputs via `messages.parse()`.
8. **T8** — `src/library/extraction/eval.py`: pure scoring (`flywheel_accuracy`,
   `judge_agreement`, `combine`, `version_distribution`, `modal_version`).
9. **T9** — `library eval-extractions [--sample N | --all]` CLI: runs flywheel +
   judge, prints per-field table, persists `EvalRun` row with version pinning
   (`modal` pair in scalar columns, full distribution in `version_mix`).
10. **T10** — unit tests for all six rules (table-driven correct/incorrect cases
    including `€ 1.234,56` normalisation), `review_status` derivation, correction
    shape + `source_excerpt` location, judge parsing, eval scoring edge cases.
11. **T11** — frontend: `needs_review` filter + "Needs review" preset tab in
    `DocumentListView.vue`.
12. **T12** — frontend: per-field validation badges from `extra["validation"]`
    findings in `DocumentDetailView.vue`; "Mark verified" button calling the new
    endpoint.
13. **T13** — Playwright e2e: open needs-review queue → open flagged document →
    correct field → status clears.

## 1.4 Schema changes (migration 0006)

- `documents.review_status`: Postgres enum column, default `unreviewed`, indexed.
  No backfill in the migration — `library backfill-validation` handles existing rows
  so the migration stays fast and reversible.
- `eval_runs` table: records each eval harness run.
- `extra["validation"]` and `extra["corrections"]` are JSONB additions — no DDL.

## 1.5 Follow-ups

- **Date-grounding rule.** Once the simpler rules prove out, revisit matching
  extracted dates against the OCR text's date strings (locale-aware).
- **Dedicated triage screen.** If working through the needs-review queue from the
  list view feels slow (e.g. with >50 flagged documents), a focused triage screen
  that shows one document + findings at a time would be the next UX step.
- **Active improvement — few-shot mining.** `extra["corrections"]` is already in
  the mining-ready shape. Extracting (text, correction) pairs for few-shot prompt
  injection or fine-tuning is a later step that needs no data migration.
- **Random/seeded sampling for the judge.** `eval-extractions` currently uses a
  deterministic head-slice (`eligible[:N]`). Seeded random sampling would make
  coverage less sensitive to corpus ordering.
- **E2e triage flow needs Claude extraction.** The Playwright e2e stack currently
  mocks extraction; a full end-to-end triage flow (ingest → extract → review →
  verify) requires a live Anthropic API key in the e2e environment.
