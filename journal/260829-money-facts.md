# Amount semantics and payment identity

**Date:** 2026-08-29
**Branch:** `money-facts`

## Why `amount_total` alone was not enough

The charts redesign wanted a real "how much am I spending" total, and the
first attempt was the obvious one: sum `documents.amount_total`, grouped
however the chart asks. Two separate problems made that number wrong.

The first was semantic: `amount_total` holds whatever number extraction
found on the page, with no record of what it *is*. An insurance coverage
ceiling, a tax assessment, a quote and a genuine payment all land in the same
column, and a naive sum adds all of them together. That is what `amount_kind`
(§2 of [`docs/money-facts.md`](../docs/money-facts.md)) exists to fix, and it
is not this entry's subject.

The second was structural, and it is what this entry is about: **counting
documents, not payments.** Grouping the live archive by an exact
`(sender, date, amount, currency)` match turned up 20 groups of documents,
covering 40 of the archive's 174 amount-bearing documents — 23% of every
amount-bearing document in the archive was sitting in some group with at
least one exact duplicate.

## The finding: all 20 were one event, documented twice

Every one of those 20 groups, inspected by hand, was the same real-world
event captured by two different documents through two different channels: an
emailed invoice next to a downloaded receipt for the same charge, a policy
cover sheet next to its premium specification, a booking confirmation next
to its payment confirmation. None were duplicate uploads — `sha256` is
unique per document, so these arrive as genuinely different files, not the
same file twice. In two cases the extractor had even given the same
underlying charge two different titles, which ruled out title matching as a
fix.

`kind` did not help either: only 3 of the 20 groups had differing `kind`
values on their two documents, so a document-shape label could not be used
to spot the pairing.

Summed naively, each of these 20 payments counted twice. The fix could not be
"pick one document per group and ignore the other" — a chart's drill-through
still needs both documents reachable from the number. What was needed was a
notion of **payment identity**: which documents describe one payment,
computed once, joinable from anywhere a total is built.

## Why a single date window cannot work, in either direction

The first design instinct was a single date-window join: same sender, same
amount, same currency, within N days, full stop. It fails in both directions
at once, and no single choice of N fixes it, because it never looks at what
each document actually *is* — only at how far apart the two dates are.

Widen the window and it starts merging things that must stay separate:
`tests/test_payment_identity.py` fixes a pair of genuinely different
purchases from the same sender, same amount, four days apart, that must
never merge — both are `payment_made`, two separate real charges, not one
event told twice. Narrow the window instead and a legitimate invoice/receipt
pair that settles late falls outside it and never merges — the same test
file's R2 case pairs an invoice and its receipt roughly two and a half
months apart, well past any window narrow enough to keep the four-days-apart
purchases separate. There is no width for N that gets both cases right using
proximity alone.

## The insight that resolved it: two separate mechanisms, not one wider window

The design does not try to make one window work for everything. It splits
the problem in two, using two independent kinds of evidence.

Where two documents share the sender's own invoice/order number (**R2**),
that number is unambiguous regardless of how far apart the two dates are —
an invoice and the receipt that settles it two and a half months
later still carry the same reference. `reference` needs no date bound at
all, because the identifier itself is the evidence:
`test_r2_a_reference_match_merges_across_any_gap` is literally named for
this — "the case a date window cannot reach."

Where no shared reference exists, the rule that decides is `amount_kind`
**complementarity** (**R3**), not a wider window. An invoice and the receipt
that settles it are never the same kind of amount — one is `payment_due`,
the other `payment_made` — while two separate purchases of the same value
are always the *same* kind. Complementarity is what actually tells the two
cases apart; the four-days-apart pair is `payment_made`/`payment_made` and
never matches R3 **at any window width**, because it fails the
complementarity test regardless of how close together the dates are. R3
still carries a 60-day bound on top of complementarity — as a backstop
against merging complementary-but-unrelated amounts separated by long,
coincidental intervals, not as the thing doing the separating work — so a
complementary pair that happens to be more than 60 days apart, and does not
share a reference, is not merged by anything in this layer (§5 in
[`docs/money-facts.md`](../docs/money-facts.md) — a known, stated gap, not a
silent one).

A same-day exact match (**R1**) and a `VETO` (both documents carry a
`reference`, and they differ, beating every other rule even on a same-day
amount match) round out the rule set.

Verified against every one of the 20 known duplicate groups and against the
four-days-apart / reference-match-across-any-gap pairs described above:
`tests/test_payment_identity.py` seeds each shape with invented senders and
amounts and asserts the collapse (or non-collapse) directly against the
`payments` SQL view.

## What shipped

- `amount_kind` (seven values, three summable) and `reference` on
  `documents`; `payment_overrides` for human corrections
  (migration `0033_money_facts.py`).
- The `payment_edges`/`payment_overrides` SQL views computing payment
  identity from the four rules above.
- `library backfill-amounts` to classify `amount_kind` on documents
  extracted before the field existed, reporting `classified`/`empty`/
  `skipped` as three separate counts rather than folding an unsure model
  response into a false success.
- `GET /api/documents/{id}/payment`, `POST /api/payments/merge`/`split`, and
  `GET /api/payments/duplicates` (`src/library/api/payments.py`).
- A `PaymentGroup` panel on the document-detail page showing "documented
  across N documents" with a per-row split control.

Full design in [`docs/money-facts.md`](../docs/money-facts.md); the REST
contract is in [`docs/api.md`](../docs/api.md) §1.24.

## Known gaps, left open rather than hidden

A partial payment (one invoice settled by two smaller receipts) does not
match on amount, and an invoice billed in one currency and settled in
another does not match either. Neither silently merges nor silently
double-counts — both simply stay as separate, un-collapsed documents. There
is no proposed-merge review surface for these two shapes yet; today the only
correction path is the manual `merge`/`split` override.
