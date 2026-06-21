# Extraction Quality: measure, warn, improve

**Status:** approved design, pre-implementation (2026-06-21).
**Sub-project:** 1 of 5 in the extraction/Ask improvement track. The remaining four
(document markdown layer, PDF page-number citations, conversational Ask, document
series + comparative queries) get their own specs later.

## Problem

Aggregation and natural-language Ask are only as reliable as the structured
metadata Claude extracts from each document. Today the system stores a
*self-reported* `confidence` (`high`/`medium`/`low`) and rich provenance in
`extra["extraction"]`, but there is:

- **no validation of extracted values against the source text** — a hallucinated
  amount or mis-parsed date is indistinguishable from a correct one;
- **no aggregate accuracy measurement** — no way to tell whether a prompt or model
  change actually improved extraction;
- **no review surface** — nothing tells the user which documents deserve a second
  look.

## Goal

Two complementary capabilities:

1. **Per-document trust signal** (operational / "warn") — for each document, know
   whether to trust its metadata and *what specifically* looks wrong, and surface a
   "needs review" queue.
2. **Aggregate accuracy harness** (evaluative / "measure") — produce real per-field
   accuracy numbers, comparable across prompt/model versions, so improvement work is
   measured rather than blind.

"Improve" is delivered as the *loop these two unlock*, not as new optimization code
(see [Improve](#improve)).

## Decisions (from brainstorming)

- Per-document signal **and** aggregate harness are equal priorities.
- Ground truth = **corrections flywheel (B) + LLM-as-judge (C)**; no large
  hand-labeled gold set required upfront.
- Ship all proposed deterministic validation rules **except date-grounding**.
- Trust signal = **derived `review_status` enum (A) + per-field findings (C)**; no
  numeric score.
- LLM judge runs **on-demand / batched (C)**, not in the live pipeline; an optional
  config flag may enable per-extraction judging later.
- Review UX = **list filter + "needs review" preset + inline detail-view badges**;
  dedicated triage screen deferred.
- Harness = **`library eval-extractions` CLI**, flywheel accuracy + sampled judge,
  results persisted per `prompt_version` + `model`.
- Improve = **the measurement loop now (A)**, corrections stored mining-ready so
  active improvement (B) can be added later without migration (C).

## Architecture

Five units, each independently testable.

### 1. Validation rules — `src/library/extraction/validation.py`

Pure, deterministic, zero API cost.

```python
def validate(document: Document) -> list[Finding]: ...
```

`Finding` is a small dataclass / typed dict: `(rule, field, severity, message)`.
Rules, table-driven so adding one is a single entry:

| rule | field(s) | fires when |
|---|---|---|
| `amount_grounding` | `amount_total` | amount set but its digits do not appear in `ocr_text` |
| `date_plausibility` | `document_date`, `due_date`, `expiry_date` | `document_date` in the future or before 1990; or `due_date`/`expiry_date` < `document_date` |
| `amount_currency_coupling` | `amount_total`, `currency` | exactly one of amount/currency is set |
| `ocr_confidence_gate` | (document) | `ocr_confidence` is below `extraction_validation_ocr_floor` (config, default e.g. 50.0) |
| `empty_extraction` | (document) | `kind == other` **and** no sender **and** no `document_date` **and** no `amount_total` |
| `self_reported_low` | (document) | `extra["extraction"]["confidence"] == "low"` |

Notes:
- `amount_grounding` compares on normalized digit sequences (strip currency symbols,
  thousands separators) to tolerate `€ 1.234,56` vs `1234.56`.
- **Date-grounding is explicitly out of scope** this phase (locale date-format
  matching is fiddly; revisit once the simpler rules prove out).
- The module imports only from models + stdlib so it stays trivially unit-testable.

### 2. Trust signal storage

- **New column** `documents.review_status` — Postgres enum
  `verified` / `needs_review` / `unreviewed`, indexed (cheap filtering). Added in
  **migration 0006**, default `unreviewed`.
- **Findings** persisted to `extra["validation"]` as
  `{"prompt_version": ..., "findings": [ {rule, field, severity, message}, ... ],
  "validated_at": ...}` alongside the existing `extra["extraction"]` blob.
- **Status derivation:** any finding (any severity) **or** self-reported low
  confidence ⇒ `needs_review`; otherwise `unreviewed`. User action (clearing the
  queue / "mark verified", or correcting all flagged fields) ⇒ `verified`.

### 3. Corrections flywheel — extends the existing edit path

Today a user edit records the field name in `extra["user_edited_fields"]`. Extend the
document-edit path so it **also** appends a structured record to `extra["corrections"]`:

```json
{"field": "amount_total", "original_value": "120.00", "corrected_value": "12.00",
 "source_excerpt": "…Totaal € 12,00…", "prompt_version": "v3",
 "model": "claude-haiku-4-5", "corrected_at": "2026-06-21T10:00:00Z"}
```

This is **both** a ground-truth label source for the harness **and** the mining-ready
shape for later few-shot improvement. `source_excerpt` is a best-effort window from
`ocr_text` around the corrected value (empty string if not locatable — never blocks
the edit).

### 4. LLM-as-judge — `src/library/extraction/judge.py`

```python
async def judge(document, *, client, settings) -> JudgeVerdict: ...
```

Grades one document's extraction against its source text using a stronger model
(default `claude-sonnet-4-6`, configurable). Returns per-field verdicts
(`correct` / `wrong` / `unsupported`) plus a short note per field, via structured
outputs (Pydantic schema, same pattern as `extraction/schema.py`). **Batch-only** —
never called from `apply_extraction`. A future `extraction_judge_inline` config flag
(default off) could enable per-extraction judging without redesign.

### 5. Aggregate harness — `library eval-extractions` CLI + `eval_runs` table

- **CLI command** (Typer, in `cli.py`):
  - `--sample N` (judge a random sample) or `--all`;
  - computes **flywheel accuracy** over every document that has `extra["corrections"]`
    (original-extraction value vs. corrected value, per field);
  - runs the **judge** over the sampled set for coverage on untouched docs;
  - prints a per-field table (accuracy from flywheel, agreement from judge, counts)
    to the terminal;
  - persists a run summary row.
- **`eval_runs` table** (migration 0006): `id`, `created_at`, `prompt_version`,
  `model`, `version_mix` (JSONB), `sample_size`, `per_field` (JSONB: field →
  {flywheel_accuracy, judge_agreement, n}), `overall` (JSONB). Version pinning: each
  judged document carries its own `prompt_version` + `model` (from
  `extra["extraction"]`); the run records the **modal** (most common) pair in the
  scalar `prompt_version`/`model` columns for easy filtering, and the full
  distribution in `version_mix` so a sample spanning versions is never silently
  misattributed. Clean apples-to-apples comparison comes from running over a corpus
  re-extracted at a single version; `version_mix` makes a mixed corpus visible rather
  than misleading.

## Pipeline integration

- `validate()` runs as the **final step inside `apply_extraction`**
  (`src/library/extraction/apply.py`), in the same transaction that writes the
  metadata. It sets `review_status` and writes `extra["validation"]`. No new pipeline
  stage; negligible latency; runs on every extraction and re-extraction.
- **Backfill:** `library backfill-validation` CLI command computes `review_status` +
  findings for the existing production corpus (idempotent, throttleable, mirrors the
  existing `backfill-embeddings` command shape).

## Per-document UX (warn)

- **`DocumentListView.vue`**: `review_status` becomes a filterable field; add a
  prominent **"Needs review (N)"** preset/tab; within it, sort by number of findings
  descending.
- **`DocumentDetailView.vue`**: render **inline per-field warning badges** from
  `extra["validation"].findings` next to the affected fields. Correcting a flagged
  field (existing inline-edit + `user_edited_fields` path) clears that field's
  finding; a **"mark verified"** action sets `review_status = verified`.
- **API**: add `review_status` as a list-filter query param and include it (plus
  `extra["validation"]`) in the document response schema.
- Dedicated single-doc triage screen is **deferred** until list-based triage friction
  is felt.

## Improve

No active-improvement code this phase. The deliverable is the **measurement loop**:
read the harness output + review queue → edit the extraction prompt or swap the model
→ re-run `eval-extractions` on the same sample → compare `eval_runs`. Because
corrections are stored in the mining-ready shape (unit 3), few-shot injection or
stale-prompt re-extraction can be added later with no data migration.

## Data / schema changes (migration 0006)

1. `documents.review_status` enum column (`verified`/`needs_review`/`unreviewed`,
   default `unreviewed`, indexed).
2. `eval_runs` table (see unit 5).
3. No backfill in the migration itself — the `backfill-validation` CLI handles
   existing rows so the migration stays fast and reversible.

(`extra["validation"]` and `extra["corrections"]` are JSONB additions — no DDL.)

## Configuration (new settings)

- `extraction_validation_ocr_floor: float` — OCR-confidence threshold for the
  `ocr_confidence_gate` rule (default e.g. 50.0).
- `extraction_judge_model: str` — judge model (default `claude-sonnet-4-6`).
- `extraction_judge_inline: bool` — reserved, default `false` (per-extraction judging
  hook; not wired to the pipeline this phase).

## Testing

- **Unit**: each validation rule (table-driven correct/incorrect cases including the
  `€ 1.234,56` normalization), `review_status` derivation, correction-record shape +
  `source_excerpt` location, judge response parsing, eval scoring math (flywheel
  accuracy, judge agreement, edge cases: no corrections, all-correct, zero sample).
- **Integration**: `apply_extraction` sets `review_status` + `extra["validation"]`;
  `backfill-validation` over a seeded corpus; `eval-extractions` end-to-end against a
  fixture corpus with the judge mocked; `eval_runs` row persisted with correct
  version pinning.
- **Frontend**: list filter + "needs review" preset, detail-view badges render from
  findings, clear-on-correct, "mark verified" (Vitest + @vue/test-utils); one
  Playwright e2e covering open-queue → open-flagged-doc → correct field → status
  clears.

## Non-goals (this phase)

Date-grounding rule; numeric quality score; dedicated triage screen; always-on
per-document judging; active improvement (few-shot mining, auto re-extraction of
stale docs); any Ask-side or aggregation-side changes; a metrics dashboard (CLI table
+ `eval_runs` history is sufficient).
