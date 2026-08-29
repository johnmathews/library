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
- The `payment_edges` and `payments` SQL views computing payment identity
  from the four rules above, over the `payment_overrides` table.
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

The design spec also promised `amount_kind` its own extraction validation and
a review queue when the model is unsure. Neither was built: an unsure answer
becomes NULL, which is safe (NULL is never summed) but invisible — nothing
counts the documents still lacking a kind, and nothing lets a person set or
correct one by hand. Written up as a gap in
[`docs/money-facts.md`](../docs/money-facts.md) §5.1 rather than left to be
discovered.

## Fix wave: two ways payment identity was wrong

A whole-branch review found two defects in the SQL, both of which reproduced
on PostgreSQL before being fixed and both now pinned by a test that was
watched failing first.

**R3 chained across billing cycles.** A recurring charge documented as
invoice-then-receipt puts every cycle's receipt within 60 days of the *next*
cycle's invoice, and the two are complementary — so R3 fired on the
cross-cycle pair as readily as on the real one, and the recursive closure
merged the lot. Three monthly cycles of one charge collapsed into a single
payment of six documents. R3 now pairs only **mutual nearest** complementary
partners: the receipt on the 3rd pairs with its own cycle's invoice two days
earlier, never the next cycle's 29 days later. The bug was dormant only
because `amount_kind` is NULL everywhere until `backfill-amounts` runs.

**A `SPLIT` could not undo a `MERGE`.** The `NOT EXISTS ... kind='SPLIT'`
guard sat on the rule-derived arm of `payment_edges` only, so a `SPLIT`
recorded after a `MERGE` left the override edge standing: "Not the same
payment" answered 200 and changed nothing, on the branch's only correction
surface.

The obvious fix — copy the same unconditional guard onto the `MERGE` arm —
was wrong in the other direction, and the existing split-then-merge test
caught it immediately: it makes a `SPLIT` win forever, so nothing can undo
one. Overrides are now resolved by **recency**: the more recently recorded of
the pair's `MERGE`/`SPLIT` rows decides, a tie falls to `SPLIT` (not merging
is the safe direction), and re-recording a correction refreshes its timestamp
so the third correction on a pair lands too. Both directions, and the
merge/split/merge sequence, now have tests.

Also in the wave: the payment panel is no longer mounted on a soft-deleted
document (its endpoint 404s by design, which put a red load-failure alert on
every Recently Deleted page); one test now asserts the seven amount kinds
agree across all three places they are declared (`models.AmountKind`,
`extraction/schema.AMOUNT_KINDS`, and the migration's `_AMOUNT_KINDS`), since
drift silently NULLs a valid kind in one direction and blows up an extraction
in the other.

## Second fix wave: "nearest" had to mean *forward*, not *closest*

The mutual-nearest R3 above ranked candidates by `abs(gap)`. Its test used a
2-day intra-cycle gap against a 29-day inter-cycle one — maximally
asymmetric — so the fixture could not show what a re-review found three ways:

- **Ties still chained.** A charge invoiced on the 1st and paid on the 16th
  makes both gaps 15 days in a 30-day month. `min(gap)` ties, both candidates
  survive as "nearest", and the cross-cycle edge is admitted after all.
  Twelve months of that cadence returned **9 payments where the truth is 12**,
  including four groups of four documents.
- **A short February merged the wrong pair.** February's receipt on the 16th
  is 13 days from March's invoice against 15 from its own, so February's
  receipt paired with March's invoice and February's invoice was orphaned —
  a pair that merged *before* the first fix wave.
- **A vetoed neighbour stole the nearest slot.** The ranking applied none of
  the rule `CASE`'s precedence, so a document whose reference contradicted its
  neighbour's still consumed that neighbour's only slot and suppressed a
  legitimate merge with a document nothing forbade.

The root cause was treating a directional domain as symmetric. **A payment
follows the thing it pays.** The candidate set is now keyed `(payment_due,
payment_made)` rather than being a symmetric self-join, so a direction can be
expressed at all, and each candidate is ranked `made - due` when the receipt
is on or after its invoice and `1000 + (due - made)` when it is before. The
offset exceeds the 60-day window, so every forward candidate outranks every
backward one and distance only decides within each group. A backward match is
still used where no forward one exists, which is what keeps a genuine
prepayment merging. The candidate set also excludes vetoed pairs now.

**The trade this makes, stated rather than buried.** A *systematically*
reversed cadence — charged on the 1st, invoiced on the 5th, every month —
pairs off by one cycle, because each receipt prefers the previous month's
invoice 27 days behind it over its own 4 days ahead. Measured on three cycles:
four groups instead of three, with the first receipt and the last invoice left
alone. That is an overcount of one payment across a run, not a collapse; every
group still holds one invoice and one receipt. The alternative — rank by
magnitude, use direction only as a tie-break — was tried and measured too: it
fixes the reversed cadence and re-breaks the short February, which is the far
more common shape, because the archive's normal order is invoice-then-payment.
Forward-preference is the right side of that trade, so the limit is documented
in [`docs/money-facts.md`](../docs/money-facts.md) §5 **and asserted by a
test**, so it stays a known price rather than becoming a surprise.

Ten cases went into `tests/test_payment_identity.py`; seven were watched
failing against the old view first. The three that already passed (a
reference-less prepayment, an unpaid invoice beside a later cycle, and the
day-60/day-61 boundary) are pins on behaviour the rewrite had to preserve, and
the 60-day bound now has the explicit boundary test that §4.2 of the doc
points at.

**Two documentation claims the code contradicted** were removed in the same
pass, both written in the first fix wave: "so the cross-cycle edge is never
drawn" (it was — that is defect one above) and "Nothing that previously merged
stops merging" (two shapes do, both correctly). `docs/api.md` §1.24's "is a
no-op, not a conflict" also stopped being precise when `add_override` moved to
`on_conflict_do_update`: repeating an override is not a conflict and does not
change the resulting group, but it *does* refresh the row's timestamp, which
is exactly the mechanism that lets a third correction land. And the
identical-timestamp tie-break is now labelled **defensive**: `created_at` is
the transaction timestamp and each of the two callers writes one row and
commits, so no request sequence can reach it.

The lesson worth keeping: **a fixture that cannot fail proves nothing.** The
2-day/29-day gap made mutual-nearest look correct because it never asked the
rule the question it gets wrong. The adversarial cases — a tie, a 28-day
month, a vetoed neighbour — are where the rule actually lives.
