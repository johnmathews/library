# What the archive said when we finally asked it

The first full `library backfill-amounts` over the live archive, on the image
that could actually read its own answers (previous entry):

```
classified 167, empty 3, skipped 0
```

172 amount-bearing documents decided, 241 payments from 258 documents.

## The distribution

| `amount_kind` | count |
|---|---|
| `payment_due` | 95 |
| `payment_made` | 47 |
| `assessment` | 15 |
| `estimate` | 5 |
| `balance` | 5 |
| `coverage_limit` | 5 |

`coverage_limit` earning five documents is the answer to a question the design
raised and could not settle: insurance sums-insured really do get classified
as ceilings rather than as money owed. Left alone they would have been summed
as spending, and they are large.

## No chaining, at all

Every payment group is one or two documents — 224 singletons and 17 pairs, no
group of three or more. The failure mode that cost two fix rounds (a recurring
charge chaining across billing cycles into one enormous payment) does not occur
here. Nor could it: **zero R3 edges fired.** The archive's invoices and its
receipts do not share a sender, amount and currency inside 60 days, and its one
recurring subscription is documented as receipts only — two `payment_made`
documents cannot satisfy R3's complementarity test.

So the rule that took three attempts to get right is, on this archive, dormant.
That is not an argument that the work was wasted — R3 is what makes the archive
safe to *grow* into invoice-plus-receipt shapes — but it is worth recording that
the live data exercised none of it.

## What the reference numbers changed

Before the backfill the archive had 20 payment edges, all R1 (same day, same
sender, same amount). Afterwards: 15 R2 and 2 R1.

The interesting part is the five pairs that stopped merging. Read by title
alone they look like duplicate captures of one document — near-identical
wording, same day, same amount. They are not. Once `reference` was captured
each pair turned out to carry **two different identifiers**: distinct payment
processor charge IDs in three cases, distinct invoice numbers in the other two.
They are separate same-day payments that happen to cost the same.

R1 had been merging them, and would have under-reported those spends by half.
VETO — conflicting references beat every other rule — separated them the moment
the references existed. This is the clearest vindication in the whole branch of
putting reference identity ahead of date proximity, and it was invisible until
real data arrived.

It is also a caution about reading documents by title. The first pass over
these pairs, done before `reference` existed, concluded they were duplicates.
The titles supported that reading. The data did not.

## The gap the archive found

One of the three undecided documents is an insurance **credit note** — money
refunded. No value in the vocabulary describes money moving back; a credit note
is the negative of `payment_made`. The model declined rather than forcing it,
NULL is never summed, so nothing is currently wrong — but a category containing
a refund over-reports by the refunded amount, silently. Filed as GH #117; the
sign question belongs in the design spec before the chart engine consumes it.

The other two undecided are less interesting: a contract whose amount is a
salary rather than a payment, and a tax assessment notice the model simply did
not commit to.
