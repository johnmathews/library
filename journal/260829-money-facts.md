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

## Why a date window cannot work, in either direction

The first design instinct was a date-window join: same sender, same amount,
same currency, within N days. It fails in both directions at once, and no
single choice of N fixes it.

Widen the window and it starts merging things that must stay separate: the
archive has two genuinely different purchases from the same vendor, same
amount, four days apart — two real charges, not one event told twice.
Narrow the window and a legitimate invoice/receipt pair that happens to
settle late — the archive also has one issued five months after its invoice
— falls outside it and never merges. There is no width for N that gets both
cases right, because the thing that actually distinguishes them is not *how
far apart* the two documents are. It is *what each document is*.

## The insight that resolved it: complementarity, not proximity

An invoice and the receipt that settles it are never the same kind of
amount: one is `payment_due`, the other is `payment_made`. Two separate
purchases of the same value, by contrast, are always the *same* kind — two
receipts, or two invoices, never one of each. That is a property `amount_kind`
already records, and it does not depend on the two documents being close in
time at all.

So the rule that survived (R3 in [`docs/money-facts.md`](../docs/money-facts.md)
§4) merges on **same sender, amount and currency, plus complementary
`amount_kind`**, and only *then* applies a date bound — 60 days — as a
backstop against reaching across genuinely unrelated history, not as the
mechanism that tells the two cases apart. Complementarity does the actual
separating work: the four-days-apart pair is `payment_made`/`payment_made`
and never matches R3 regardless of the window; the five-months-apart pair is
`payment_due`/`payment_made` and matches regardless of the gap. A same-day
exact match (R1) and a shared `reference` number at any gap (R2) round out
the other two ways two documents turn out to be one payment, and a `VETO`
(both documents carry a `reference`, and they differ) beats every rule when
it applies, even a same-day amount match.

Verified against every one of the 20 known duplicate groups and against the
four-days-apart / five-months-apart pair described above:
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
