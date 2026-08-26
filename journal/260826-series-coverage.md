# Series comparisons report what they narrow away

**Date:** 2026-08-26

## What changed

`summarize_series` (the engine behind Ask's `compare_to_series` tool) narrows
aggressively before it computes a "usual" band: it keeps only documents with
an extracted amount, then only the most-populous `(sender_id, kind_id)` group,
then only the dominant currency bucket. It used to do all three silently. It
now returns a `SeriesCoverage` block — `matched` / `included` / `excluded` /
`needs_review` — alongside the statistics, on the same shape `query_documents`
already used for its own aggregates (`260826-ask-answer-trustworthiness.md`).
`excluded` maps a reason to a count: `no_amount`, `other_series_group`,
`other_currency`, and `manually_excluded`. The Ask system prompt and the
`compare_to_series` tool description were updated to disclose it, and
`docs/ask.md` §1.2/§1.7 now describe it.

## Why

A "higher than usual" verdict computed over 3 of 11 matching documents is not
a fact about all 11, and the caller had no way to tell the difference. The
same silent-narrowing shape that motivated `query_documents`'s coverage work
applies here, just with different reasons: a series is one provider's one kind
of document in one currency by definition, and everything that doesn't fit is
dropped before the statistics ever run.

## Decisions

- **`coverage` is optional on `SeriesSummary`, and that is deliberate.** It is
  `None` for authored series (Smart Groups / user-curated series), whose
  membership is learned rather than derived and whose narrowing rules differ
  entirely. `summarize_authored_series` is not reachable from
  `compare_to_series` — that path only ever calls `summarize_series` — so in
  practice the Ask tool's result always carries a populated block. The
  optionality exists so a future caller of the authored path reports `None`
  honestly ("not reported") rather than fabricating an empty `excluded` that
  would read as "nothing was dropped". Absent and empty are different claims,
  and conflating them was exactly the kind of silent gap this work closes.
  Extending coverage to authored series is separate work, deliberately not
  done here — their membership isn't a filter to re-run, so the four existing
  reasons don't apply as written.
- **Four reasons, not three.** The plan this branch executed against was
  written with three (`no_amount`, `other_series_group`, `other_currency`).
  Partway through, a review of the override path found it broke the
  invariant this whole feature exists to guarantee — see the bug below — and
  fixing it required a fourth reason, `manually_excluded`, for documents a
  persisted `EXCLUDE` override drops that the plain filters would have kept.
  One tool description (`compare_to_series`'s) was written before that fix and
  still listed only three; caught and corrected before merge (see below).
- **The four reasons are not one chained filter.** `query_documents`'s
  aggregates build their exclusion reasons as successive refinements of a
  single include chain (documented in `docs/ask.md` §1.2). A series' first
  three reasons come from choosing which `(sender, kind, currency)` group is
  authoritative; the fourth comes from a persisted override layered on
  afterwards, a structurally different mechanism. The docs now say so
  explicitly rather than implying series coverage is "the same kind of thing"
  as the aggregate case.

## A bug caught during implementation: overrides broke the partition invariant

The first cut computed `other_currency` from the pre-override bucket and never
reconciled it against what a `PIN`/`EXCLUDE` override did afterwards. Two ways
that broke `included + sum(excluded.values()) == matched`:

- An `EXCLUDE` could remove a document that had been counted as `included`,
  with no reason left to catch it — the count silently disappeared from both
  sides of the invariant.
- A `PIN` loads its document "regardless of its own sender/kind" (it applies
  no filter conditions at all), so it could restore a document already tallied
  under `other_series_group` or `other_currency` (double-counting it), or pull
  in a document from entirely outside the caller's filters, which was never in
  `matched` to begin with.

Fixed by capturing the currency bucket both before and after
`_apply_overrides` and reconciling the two in a new
`_coverage_after_overrides` helper: a document dropped between the two
buckets lands in the new `manually_excluded` reason; a document restored by a
`PIN` is subtracted back out of whichever reason it would otherwise have
counted under, or grows `matched` itself if it came from outside the filters
entirely. A regression test now exercises a real `SeriesMembershipOverride`
combination — an `EXCLUDE`, a same-group cross-currency `PIN`, and a `PIN`
from outside the filters — in one series, and asserts the invariant holds.

A second, smaller bug: the earliest coverage test seeded exactly one amountless
document and one non-dominant-group document, so `no_amount == other_group ==
1` and a positional swap of `_load_members`'s two return values would still
pass. Fixed by making the seeded counts mutually distinguishable (verified by
temporarily swapping the return values and watching the test fail).

## Also fixed: a stale tool description

The `compare_to_series` tool description in `ask/engine.py` still enumerated
only three reasons — it was written before the `manually_excluded` fix above
landed. Cross-checked against `SeriesCoverage`'s docstring (the source of
truth) and the frontend's type definitions, which already listed all four;
the Python description was the only incomplete copy. The em-dash phrasing
reads as exhaustive, so a model reading it could see a `manually_excluded` key
it was never told existed. The description-coverage test was widened to assert
all four reason keys individually so a future drop of any one fails loudly,
not just a change in count.

## Not done

Coverage for authored series (Smart Groups) is out of scope. Their membership
is learned via suggestions and semantic matching rather than derived by
filtering, `summarize_authored_series` is not reachable from Ask's
`compare_to_series` tool, and `coverage=None` already states plainly that it
is unreported rather than pretending nothing was dropped. Widening coverage to
that path is separate work.

As with the `query_documents` coverage work, the disclosure rule's effect on
real answer wording is unmeasured — there is still no answer-quality eval
exercising what the model actually writes when a series' `coverage.excluded`
is non-empty, only schema/string-level tests of the block's shape and the
prompt's wording.

The early `status="insufficient"` exit still predates overrides. Before
`summarize_series` picks a currency bucket, it first checks whether enough
documents even match the caller's filters at all
(`settings.series_min_documents`). If they don't, it returns
`"insufficient"` immediately — before any PIN/EXCLUDE override is resolved,
since overrides are keyed on a resolved `(sender, kind, currency)` identity
that doesn't exist yet at that point. So on that path both the coverage
numbers and `status` itself can predate an override that would have changed
them: a series a PIN would push over the threshold can still report
`"insufficient"`, and a document an EXCLUDE would drop is still counted
`included`. This is pre-existing `summarize_series` behaviour, not introduced
by this branch — only the coverage numbers now surfaced on that path are new.
Not fixed here; see [docs/ask.md](../docs/ask.md) §1.7 for the full
explanation.
