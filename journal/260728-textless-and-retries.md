# A silent success and fourteen jobs with no second chance

W9 and W18, the `jobs.py` lane. Both are about failures the system already knew about and
did not record: one reached `indexed` looking like a success, the other lost work to a log
line.

## 1. W9 — a document with no text was a silent success

A document whose OCR yields nothing still reaches `indexed`. That invariant is deliberate and
stays: the original is stored, viewable, and OCR can be re-run. But with no text it is
invisible to every search query and to Ask — and nothing said so. `review_status` stayed
`unreviewed`.

**Reuse, don't invent.** `ReviewStatus`, `Finding`, `derive_review_status` and
`extra["validation"]` already exist, so this is one new rule (`no_text_extracted`) firing on
`not text.strip()`, plus the existing `read_the_image` guard so a vision-rescued photo — which
legitimately has empty `ocr_text` because the model read the page image — is not nagged about
the case the fallback exists to handle.

### The half that mattered more

The rule alone would not have fired. `validate()` runs from `_apply_validation`, which is on
`apply_extraction`'s **success** path only — and six branches return before it: `disabled`,
`missing_api_key`, `already_extracted`, `budget`, `ExtractionSkipped`, and the generic
failure. A textless document takes `ExtractionSkipped("input_unusable")`, so **the one
document that most needs flagging got no validation at all.**

So the flag is also seeded at the OCR stage (`flag_textless_document` + an `ocr_empty` event),
where the fact is first knowable and no LLM call is involved. Deliberately a floor rather than
the full rule set: at that point extraction has not run, so amount/date/sender are all None
and running `validate()` there would fire `empty_extraction` and friends prematurely. It also
skips a document that already carries a `validation` payload, so a pipeline resume re-entering
the OCR hook cannot replace a complete finding set with this single one — both directions are
tested.

**Grading: confirmed by mutation.** With the flag disabled the test fails on exactly the right
line: `assert <ReviewStatus.UNREVIEWED> is <ReviewStatus.NEEDS_REVIEW>`. Indexed, textless,
unflagged.

### Fallout, and why it was the tests that were wrong

Three tests broke: two API revalidation tests and one Ask write test, all asserting that
fixing a flagged field drops the document off `needs_review`. Each seeded an *invoice with a
date and no `ocr_text`* — which is not a state production produces. Empty `ocr_text` plus
extracted fields means the vision path ran, and the rule excludes that. The fixtures were
modelling an impossible document, so they got real text rather than the rule getting an
exemption.

Also fixed in passing: `decoration_image` has fired since the thin-OCR work but was never
given a title in `validationReason.ts`, so it rendered as the generic "Needs a quick check" in
the review queue — the exact hiding-behind-a-generic-reason the rule was written to prevent.

## 2. W18 — Procrastinate does not retry by default

All 14 tasks were declared without `retry=`. An unhandled exception marks a job `failed`
permanently, so a single Anthropic rate limit, a restarting embedder or a Postgres failover
lost the work for good. Meanwhile `jobs-and-notifications.md` said jobs "are retried by
Procrastinate per its own policy" — which was simply not true.

Nine tasks now share `TRANSIENT_RETRY`: five attempts, exponential backoff (~4s → 32s, about
a minute), with `retry_exceptions` as an **allowlist**. The direction is the design: a denylist
retries whatever nobody thought of, so a plain `ValueError` would burn five attempts and reach
`failed` a minute later with the identical message. An allowlist fails those on attempt one,
which is faster *and* more honest.

### The test caught a real flaw in my own allowlist

The plan specified SQLAlchemy `OperationalError` **and `DBAPIError`**. I wrote both, then
`test_transient_allowlist_excludes_deterministic_failures` went red:

```
AssertionError: IntegrityError is deterministic and must not be retried
```

`DBAPIError` is the *parent* of `IntegrityError`, `DataError`, `ProgrammingError` and
`NotSupportedError`. Allowlisting it would retry constraint violations and SQL bugs — precisely
what the allowlist exists to exclude. Replaced with `OperationalError` + `InterfaceError`,
which are the genuinely connection-level ones. **Correction to the plan, recorded.**

Two groups deliberately do not retry, and the table says why: `generate_thumbnail` (a
deterministic render — a failure is a bad file, not bad luck) and the four periodic tasks (the
next tick *is* the retry). **Deviation from the plan:** it listed `poll_email_inbox` among the
retrying tasks while also counting it in "the four periodic tasks", which cannot both hold. I
left it un-retried, following the plan's own principle over its list — it is periodic *and* the
only task carrying both `queueing_lock` and `lock`, so a retry would race the schedule for no
gain. The plan also omitted `evaluate_series_autocontinue` and `evaluate_semantic_groups`
entirely; both are idempotent and depend on the DB and embedder, so both retry.

`test_retry_policy_per_task` covers all 14 with a reason string per task, and a 15th task with
no entry fails the completeness test. That test is the point: Procrastinate's silent default
makes an *omitted* decision indistinguishable from a considered "no".

### Deferrals that fail before there is a job to retry

Five follow-up jobs are deferred best-effort after their document's work commits. A retry
policy cannot help if `defer_async` itself fails — there is no job yet. Each previously logged
a warning, so the observable result was a document that quietly never got its thumbnail or its
Smart Group membership with nothing on it to say why. All five now go through one
`_defer_best_effort` helper that also writes a `job_defer_failed` ingestion event, on the
document's own timeline and queryable. Recording the loss is itself guarded: noting a failure
must not become a bigger one by failing the document.

## 3. Result

1413 passed, coverage 95% against the 93% gate; frontend 1044 passed, lint and type-check
clean. Five near-identical defer guards collapsed to one helper.
