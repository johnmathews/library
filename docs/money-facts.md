# Money facts and payment identity

**Status:** active. **Last updated:** 2026-11-05 (§5.2: the date window no longer chooses which twin carries a payment, so month totals add up; new reason `payment_dated_elsewhere`; a facet filter on an itemised document recorded as a follow-up). Earlier entries are in this file's history.
**Last verified:** 2026-11-05 — method: partial, scoped to §5's spending-total paragraph and §5.2. Read `structured_query._payment_choices` and `sum_amount` against migration 0035's ranking, and `charts/query.py`, `charts/footer.py` and `api/facets.py`, which still filter `sf.is_canonical`. The month-window and within-filter tests in `tests/test_mcp_money.py` pass, seen red first or killed by mutants. The rest carries its earlier verifications forward.
**Covers:** src/library/money/, src/library/api/payments.py, migrations/versions/0033_money_facts.py, migrations/versions/0034_refund_amount_kind.py, src/library/llm/envelope.py, src/library/structured_query.py

> **Note on examples.** This repository is public. Every sender name, amount and
> reference number below is invented.

## 1. The problem: `amount_total` alone says nothing about what a number means

A document's `amount_total` might be an invoice the household owes, a receipt
confirming it was paid, an insurance policy's coverage ceiling, an account
balance, a quote, or a zero that means nothing was due. Summed together
without distinction, these numbers lie: a coverage ceiling of 5,000.00 is not
5,000.00 of spending, and a quote is not a payment. `amount_kind` exists to
say, for every amount-bearing document, what the number *is* — not how much
it is.

A related problem sits one level up: the same real payment often arrives as
two separate documents — an emailed invoice and a downloaded receipt, a
booking confirmation and its payment confirmation — and both carry the same
amount. Summed naively, one payment counts twice. **Payment identity**
(§4) is the mechanism that collapses those pairs back into one.

## 2. `amount_kind`

Eight values, declared in `AmountKind` (`src/library/models.py`):

| value | meaning | sign |
| --- | --- | --- |
| `payment_due` | an invoice or bill the household owes | **+1** |
| `payment_made` | a receipt or confirmation that money was paid | **+1** |
| `assessment` | a tax or levy demand | **+1** |
| `refund` | money returned, or an amount owed cancelled — a credit note, a refund receipt, a reversal | **−1** |
| `coverage_limit` | an insurance sum insured or maximum payout — not money paid | — |
| `balance` | an account or statement position | — |
| `estimate` | a quote or indicative price, not yet owed | — |
| `none` | the amount is incidental, or zero because nothing is due | — |

**`amount_total` is always a magnitude.** Whether a document *adds to* or
*subtracts from* a spending total is a property of what its number means, so the
sign is carried by `amount_kind` and nowhere else. A refund recorded as a
`payment_made` with a negative `amount_total` was rejected for two reasons that
are both about silence: nothing guards that column's sign (there is no `CHECK`
on it, and `normalize_amount_string` admits a leading `-`), so a sign error
would be invisible; and every payment rule joins on `amount_total` *equality*,
which a negated amount would quietly stop satisfying, so an invoice and its own
receipt would stop merging with no error anywhere.

The declaration is therefore a signed map, and the older frozenset derives from
it rather than restating it:

```python
AMOUNT_SIGN: Mapping[AmountKind, int] = MappingProxyType({
    AmountKind.PAYMENT_DUE: 1,
    AmountKind.PAYMENT_MADE: 1,
    AmountKind.ASSESSMENT: 1,
    AmountKind.REFUND: -1,
})
SUMMABLE_AMOUNT_KINDS: frozenset[AmountKind] = frozenset(AMOUNT_SIGN)
```

"Summable" and "signed" are the same predicate: a kind that contributes has a
sign, a kind that does not is absent from the map, and the two lists cannot
drift apart because there is only one. A spending total is
`sum(AMOUNT_SIGN[kind] * amount)` over the rows a rule resolved — see
[charts.md](charts.md).

Since migration `0034` the column also carries a `CHECK` listing these eight
values. It had none before: `sa.Enum(..., native_enum=False)` under SQLAlchemy 2
defaults `create_constraint=False`, so `0033` produced a bare `varchar(16)` that
accepted `'not_a_real_kind'`. `tests/test_money_schema.py` compares three copies
of the vocabulary against each other — `models.AmountKind`,
`extraction/schema.AMOUNT_KINDS` and `0034`'s own `_AMOUNT_KINDS` — and, on the
sign, compares `AMOUNT_SIGN` against the kind literal the migration's sign guard
(§4) hardcodes. Note where that stops: the two classifier prompts
(`extraction/schema.py`'s field description and `money/backfill.py`'s
`AMOUNT_SYSTEM_PROMPT`) also enumerate the values in prose and **no test
compares them**, so a value added without touching both prompts is simply never
proposed by the model.

`amount_kind` is **nullable**, and the NULL case is deliberate: it means "not
yet decided", not "carries no money". `none` is the value for the latter —
an amount that genuinely carries no spending meaning, recorded as such.
Consumers treat NULL exactly like a non-summable kind, so it never
contaminates a total; the difference is that a NULL document still belongs in
the `backfill-amounts` queue (§6), and a `none` document does not. This is
also why an archive that has not been fully backfilled **under-reports**
rather than over-reports: every undecided document is left out, never
guessed into a total.

## 3. `reference`

The document's own invoice, order, booking or assessment number, extracted
alongside `amount_kind`. It is the strongest available evidence that two
documents describe one payment, and the only evidence that works across an
**arbitrary** gap between an invoice's date and its receipt's — every other
signal (§4) needs the two documents to be close in time, or needs a second
field (`amount_kind`) to tell them apart safely. A booking invoice numbered
`K-8842` and a receipt for the same amount six months later, also numbered
`K-8842`, are the same payment regardless of what else differs between them.

`reference` is capped at 128 characters (`Document.reference`,
`String(128)`); both the extraction path (`schema.py`'s
`MAX_REFERENCE_CHARS`) and the backfill path (`backfill.py`'s `_parse`)
clamp to that width independently, so an over-long value can never reach the
database as a write failure.

## 4. Payment identity: four rules and a veto

Two documents describe one payment when one of three automatic rules fires
and the veto does not. All of this lives in the `payment_edges` SQL view
(introduced in `migrations/versions/0033_money_facts.py` and **recreated in
full, with the sign guard, by `0034_refund_amount_kind.py` — 0034 is the live
definition**), not in Python, so any future
consumer — a chart query, a report — can join payment identity without
reimplementing it. `src/library/money/payments.py` is the read API over the
view plus the one write path (an override row).

Every automatic rule is checked only between documents that already share
the same sender, the same `amount_total` and the same currency — that
precondition is enforced once, in the view's `pairs` CTE join, rather than
repeated inside each rule's own condition. What differs between the three
rules is the *additional* evidence each one demands on top of that shared
baseline.

Above all of that sits one further **precondition**: a pair is only ever
considered when both documents sit on the **same side of zero**. It is written
into the same `pairs` CTE join, above every rule rather than beside them:

```sql
AND (a.amount_kind IS DISTINCT FROM 'refund') = (b.amount_kind IS DISTINCT FROM 'refund')
```

It has to sit above R2 specifically, because R2 is otherwise the *strongest*
evidence in the table: a credit note quotes the `reference` of the invoice it
reverses, so R2 would merge them at any date gap. Verified against Postgres —
without the guard, a credit note and its invoice 90 days apart do merge.
Merging a `+X` with a `−X` erases both from every total; leaving them as two
payments nets them to zero, which is the right answer. A NULL `amount_kind`
counts as not-a-refund, so a NULL never merges with a refund — the cautious
direction, and a NULL contributes nothing to a total anyway.

The API refuses a manual `MERGE` across opposite signs with a **400**
(`_refuse_mixed_sign`, `src/library/api/payments.py`), so the invariant the
chart engine relies on — **every payment group has one well-defined sign** —
holds on the write path too. §5 records the one door that is still open.

The view's `CASE` expression then evaluates the rules in a fixed order —
**VETO, then R2, then R1, then R3** — because more than one could otherwise
fire on the same pair, and this order is what decides which one wins
(`src/library/money/payments.py`'s module docstring documents the same order,
with `SIGN` above them all):

| rule | additional condition (on top of same sender, amount, currency, and the same sign) | date reach |
| --- | --- | --- |
| VETO | both documents carry a `reference`, and the two differ | beats every rule below, even a same-day amount match |
| R2 | same non-null `reference` | **any** gap |
| R1 | same `document_date` | same day only |
| R3 | **complementary** `amount_kind` (`payment_due` paired with `payment_made`), **and** each document is the other's *nearest* complementary partner, where “nearest” prefers a receipt dated **on or after** its invoice (§4.1) | ≤ 60 days — complementarity, the bound and mutual-nearest are all required; no one of them alone is sufficient |

R4 does not exist as an automatic rule. Two documents that share a sender,
amount and currency but neither match on `reference` nor on complementary
kind within 60 days are not merged by anything in this layer — see §5 for
the shapes that actually produce this case, and §7 for the manual override
that is the only way to correct it today.

### 4.1 Why R3's complementarity is the important idea, not the 60-day window

The number "60 days" is not what makes R3 safe. What makes it safe is
requiring the two documents' `amount_kind` values to be **complementary**:
one `payment_due`, the other `payment_made`. An invoice and the receipt that
settles it are never the same kind of amount-bearing document — one records
money owed, the other money paid. Two genuinely separate purchases of the
same value, by contrast, are always the *same* kind: two receipts, or two
invoices.

A date window alone cannot tell these apart in either direction. Widen it
and two unrelated same-amount purchases a few weeks apart start merging.
Narrow it and a legitimate invoice/receipt pair that happens to arrive a
little late falls outside it and stays wrongly split. Complementarity
resolves the ambiguity a date window cannot: it is not the gap between the
two documents that distinguishes them, it is what each one *is*. The 60-day
bound only exists to keep R3 from reaching across genuinely unrelated
history — it is a backstop, not the mechanism.

**Both conditions are required; neither alone is sufficient.** R3 fires only
when complementary `amount_kind` *and* a gap of 60 days or less both hold. A
complementary pair more than 60 days apart does not merge under R3 (it merges
only if the two documents also share a `reference`, under R2 — §4 above); a
same-amount, same-currency pair within 60 days but with the *same*
`amount_kind` (two receipts, or two invoices) does not merge under R3
either. Removing the 60-day bound because complementarity "already does the
real work" would reopen exactly the false-merge risk the bound exists to
close: complementary kinds recurring at long, unrelated intervals (a policy
premium `payment_due` this year and an unrelated payment `payment_made`
lodged with the same amount years earlier) would start merging with no
bound in place.

**Complementarity and the bound together are still not enough: R3 also pairs
only *mutual nearest* partners.** A recurring charge — the same amount, from
the same sender, every month — arrives as an invoice and, days later, its
receipt. Each cycle's receipt is complementary to its own invoice, but it is
equally complementary to the *next* cycle's invoice, and that neighbour sits
well inside 60 days: a receipt on the 3rd is 29 days from the following
month's invoice on the 1st. An R3 that fired on every complementary pair in
the window would therefore link cycle to cycle, and because the `payments` view closes
those edges transitively (it is a recursive view over `payment_edges`), a
whole subscription history collapses into one payment — three cycles of one charge became a single group of six documents.

The view answers this by asking each document which complementary partner is
*closest*, and drawing the edge only where that choice is mutual. But
"closest" cannot mean the smallest **unsigned** gap, because the two
candidates are often equally close or the wrong one is closer:

- On a 1st/16th cadence the receipt is 15 days after its own invoice and, in
  a 30-day month, exactly 15 days before the next one. The two tie, both
  survive as "nearest", and the cross-cycle edge is drawn after all. Twelve
  months of that cadence came back as **nine** payments rather than twelve,
  four of them groups of four documents.
- February is 28 days long, so February's receipt on the 16th is 13 days from
  **March's** invoice against 15 from its own. The wrong pair wins outright
  and February's invoice is left unpaid.

**The domain is not symmetric: a payment follows the thing it pays.** So
"nearest" is directional. Four CTEs implement it. `sym` holds candidate
`(payment_due, payment_made)` pairs within the bound — keyed by kind, not a
symmetric self-join, so a direction can be expressed at all — and ranks each
by `made_date - due_date` when the receipt is on or after the invoice, and by
`1000 + (due_date - made_date)` when it is before. The offset is larger than
the 60-day window, so **every** forward candidate outranks **every** backward
one, and distance decides only within each group. `best_due` and `best_made`
take each document's minimum rank, and `mutual` keeps the pairs where the two
minima agree.

`sym` also drops VETO'd pairs (both references present and different). A
document that can never merge with its neighbour must not hold that
neighbour's nearest slot: an invoice one day from a receipt whose reference
contradicts it would otherwise be left unpaired, suppressing a legitimate
merge with a receipt four days out that nothing forbids.

Two pairings the unsigned rule drew are no longer drawn, and both are correct
to drop: a receipt equidistant between two invoices now takes the one it
follows rather than the one it precedes, and an invoice with a receipt on each
side takes the later one even when the earlier is a day closer. A backward match is still
used wherever no forward one exists, which is what keeps a genuine prepayment
merging. One shape is genuinely worse under this rule — see §5.

### 4.2 Null-safety, and why it is load-bearing

Two details in the view exist specifically so that missing data fails toward
*not merging silently wrong*, never toward a crash or a false positive:

- **`a.currency IS NOT DISTINCT FROM b.currency`**, not `a.currency =
  b.currency`. Plain equality is NULL — neither true nor false — when both
  documents have no recorded currency, which would silently exclude every
  currency-less pair from every rule. `IS NOT DISTINCT FROM` treats "both
  NULL" as a match, so two documents with no currency on record can still
  pair.
- **`abs(m.document_date - d.document_date) <= 60`** (R3's date-gap test,
  in the `sym` CTE that feeds it — the only date window in the view, pinned
  on both sides of the boundary by
  `test_r3_reaches_sixty_days_and_no_further` in
  `tests/test_payment_identity.py`) evaluates to NULL, not true, when either
  document has no `document_date` — so R3 can never fire for a dateless
  document, which also keeps it out of `mutual` entirely. **R2 is the only rule that can pair one**, because it
  depends on `reference` matching, not on a date comparison at all. A
  dateless invoice and its dateless receipt still merge, but only if they
  share a reference number.

A document with a NULL `amount_kind` also cannot satisfy R3's complementarity
test — `NULL = 'payment_due'` is never true — which is what keeps an
un-backfilled archive from silently over-merging same-amount documents it
has not yet classified.

### 4.3 Soft deletes

A soft-deleted document (`deleted_at` set) stops contributing an edge
entirely. `payment_edges`' rule-derived half filters `deleted_at IS NULL` on
both sides of every join, and the override-derived half (the `MERGE`
override union) filters it too. That second filter closed a real bug: without
it, a trashed document stayed reachable in the `payments` view's recursive
closure and could still win `min(member)` — reassigning a *live* document's
`payment_id` to an id that no longer existed anywhere else in the API. The
regression test for this (`tests/test_payment_identity.py`) seeds two
documents that no automatic rule connects, joins them only by a `MERGE`
override, then soft-deletes one side and asserts the survivor's `payment_id`
is its own id, not the deleted document's.

## 5. Known limits

Six shapes are not handled correctly here. The first three are simply not
merged; the fourth is merged into the wrong groups; the fifth nets a refund off
twice; the sixth is the one way a payment group can still end up holding two
signs:

- **A partial payment.** One invoice for 300.00 settled by two receipts of
  150.00 each does not match on `amount_total`, so R1/R3 never fire between
  the invoice and either receipt (the join that every rule sits on top of
  requires an exact `amount_total` match; §4).
- **Cross-currency settlement.** An invoice billed in one currency and paid
  in another (e.g. an invoice for 80.00 EUR settled by a receipt for 88.00
  USD) does not match on amount or currency, so nothing merges it either.
- **A complementary pair with no shared reference, more than 60 days
  apart.** An invoice and its receipt that settle late, and carry no
  `reference` either extractor could read, satisfy R3's complementarity test
  but not its 60-day bound, and satisfy no other rule (§4.1).
- **A systematically reversed cadence.** A recurring charge taken on the 1st
  and *invoiced* on the 5th, month after month, is the one shape R3's
  forward-preference (§4.1) gets wrong. Every receipt has the previous
  cycle's invoice 27 days behind it and its own 4 days ahead, and forward
  beats near, so the cycles pair off by one. **Measured on three cycles: four
  payments instead of three**, with the first receipt and the last invoice
  each left alone. The consequence is an overcount of one payment across a
  run of this cadence, not a collapse — every group still holds exactly one
  invoice and one receipt, and no group is wrong about how much money it
  represents. `test_a_systematically_reversed_cadence_pairs_off_by_one_cycle`
  (`tests/test_payment_identity.py`) asserts that outcome so the limit is
  pinned rather than merely described.
- **A credit note and a separate refund receipt, netted off twice.** There is
  one `refund` value, not two, so the mirror of the invoice/receipt pair — a
  credit note and a later refund receipt for the same amount — is two `refund`
  documents rather than a complementary pair. R3 cannot pair them, because
  complementarity is defined only over `payment_due` ↔ `payment_made`. R1
  pairs them when they fall on the same day and R2 when they share a
  `reference`; on different dates with no shared reference they stay separate
  and the refund is subtracted from a total twice. Splitting `refund` in two is
  what would close it, and the day the archive contains such a pair is the day
  to do it — a vocabulary addition, not a redesign. Recorded in the design spec
  §8.3 as well.
- **The mixed-sign override hole.** The sign precondition (§4) lives in the
  `pairs` CTE, and the `OVERRIDE` arm of `payment_edges` reads
  `payment_overrides` directly — it never passes through `pairs`, so the guard
  does not constrain it. `_refuse_mixed_sign` closes the *write* path, and it
  is the only path that exists today, but it checks the two documents as they
  are **at the moment of the merge**: a person can merge two documents whose
  `amount_kind` is still NULL, and a later `backfill-amounts` run can then
  classify one of them as `refund` — producing a payment group holding both a
  refund and a non-refund, which the guard never sees. Such a group has no
  well-defined sign, and only the canonical document's sign reaches a total.
  This is a **stated limit, not an oversight**: applying the guard to the
  `OVERRIDE` arm would silently undo a human's explicit correction, which is
  worse than reporting a group that needs looking at. There is no automatic
  detection for it yet; the check is a query over `payments` joined to
  `documents.amount_kind`, run after a backfill.

None of the first three is merged, and none is vetoed — the documents simply
remain separate payments as far as this layer is concerned, with no
`payment_id` connecting them.

The fourth was a deliberate trade, not an oversight. The obvious alternative
— ranking by unsigned distance and using direction only as a tie-break — was
tried and measured: it fixes the reversed cadence and re-breaks the short
February, which is the more common shape by far, because the archive's normal
order is invoice-then-payment. Forward-preference is the correct side of that
trade, and this is the price of it.

There **is** a spending total now — `library.charts.query` sums
`AMOUNT_SIGN[kind] * amount` over the canonical row of each payment
([charts.md](charts.md)), and so does `structured_query.sum_amount`, the
money total behind Ask's `query_documents` and the MCP `money_query` tool,
over one document per payment chosen within its filter (§5.2) — so what
they do with these shapes is settled behaviour rather than a
hypothetical. An unmerged invoice and its settling
receipt are two payments, both summable, and both enter the total
independently: the underlying spend is **double-counted**, not omitted. The
chart footer does not report this, and cannot: two unmerged documents are two
ordinary counted payments, indistinguishable from two genuine ones. That is why
this section exists. There is still no proposed-merge review surface for any of
these shapes; today the only correction path is the manual `merge` override
(§7), reached from the payment panel on a document's detail page (§8).

### 5.1 An undecided `amount_kind` is safe, and only partly visible

The design spec (`superpowers/specs/2026-08-28-charts-redesign-design.md`
§8.3) says `amount_kind` "gets its own extraction validation and enters the
review queue when the model is unsure". **Neither exists yet.** This is a gap,
not settled behaviour, and it is worth stating plainly because the safe half
of it is easy to mistake for the whole story:

- **Safe.** An unsure answer becomes NULL, and NULL is never summed (§2). The
  failure mode is under-reporting, never a wrong number.
- **Partly visible now.** Every chart's footer carries an `unclassified` group
  — the money and the document count with an amount and an undecided kind, in
  that chart's date and currency window ([charts.md](charts.md) §7). That is a
  real reporting surface and it is where the backlog is most likely to be
  noticed. It is not a *complete* one: it is scoped to a chart's window and to
  what its rule touched, so a document outside every chart's range is still
  counted nowhere. `backfill-amounts` reports an `empty` count for the
  documents one run touched (§6), but there is still no review queue, no
  needs-review flag, and no archive-wide count in the API or the UI.
- **Uncorrectable by hand.** There is no way for a person to set or fix a
  document's `amount_kind` — not in `PATCH /api/documents/{id}`, not on the
  detail page. A wrong kind can only be changed by another backfill run, and
  a backfill deliberately skips any document that already has one.

So a document the model was unsure about is absent from every total, and now
says so in the footer of any chart whose window contains it — but nothing lists
the backlog as a whole and nothing lets a person fix one by hand. Closing the
rest needs an archive-wide review surface and an edit path; both are a later
layer, and neither is claimed here.

### 5.2 Which document counts: archive-wide for charts, within the filter for totals

The view's `is_canonical` ranks **every** live document of a payment:
a line-bearing document first, then a `payment_made`, then the lowest id.
That is right for a query over the whole archive and wrong for a narrower
one. A water bill (`payment_due`, kind `utility-bill`) merged with its
receipt (`payment_made`, kind `receipt`) has the receipt as its canonical
row, so a total filtered to `kind='utility-bill'` that gates on
`is_canonical` matches the bill, drops it as not canonical, and reports a
payment it never counted. A tag only the bill carries does the same.

`structured_query.sum_amount` therefore does not read `is_canonical`.
`_payment_choices` ranks, with the view's ordering (and, when grouping,
having the group key first), the documents that pass **every filter
except the date window** and the earlier gates (an amount, not a quote, a
summable kind). The first of each payment carries it. **The rule:** the
filters other than dates decide which document carries a payment; the
date window then decides whether that document, and so the payment, is in
the total. Consequences:

- When both twins pass, the carrying one is the one the view picks, so a
  total over the whole archive is unchanged.
- When the view's choice is filtered out by kind, tag, project, matter,
  facet, sender, recipient or review status, the best-ranked twin that
  passed carries the payment. An itemised document still wins wherever it
  is a candidate, and its line rows are what is summed.
- A date window never picks the twin. A water bill dated 28 September and
  its receipt dated 3 October are one payment carried by the receipt: a
  September total reports the bill as `payment_dated_elsewhere` and an
  October total counts the payment, so September plus October equals the
  two months together. With `kind='utility-bill'` the bill is the only
  candidate, so it carries the payment, in September.
- An undated carrying document is in no date window: its payment counts
  only in a total without one, and its dated twins are
  `payment_dated_elsewhere`.
- `duplicate_payment` names only a document whose payment is in the rows.
  When grouping, a document lacking the key is a duplicate if a twin with
  the key carries its payment, and `no_sender`/`no_kind` otherwise. A
  document whose payment lands in a row the 50-row cap cut is `over_limit`.

Totals under different non-date filters can share payments and must not be
added: `kind='utility-bill'` counts the water payment through the bill,
`kind='receipt'` through the receipt. The tool text says so.

It also changes one case over the whole archive: a payment whose view
choice has an undecided or non-summable kind (an undecided document with
the lowest id beats a `payment_due`) used to contribute nothing; its
summable twin now counts.

**The charts still have the old behaviour.** `charts/query.py`,
`charts/footer.py` and `api/facets.py` filter `sf.is_canonical`, so a chart
rule that matches only a payment's non-canonical twin (a label on the bill
but not the receipt) leaves that payment out. Moving them onto the same
within-filter choice is a recorded follow-up.

**Follow-up: a facet filter on an itemised document.** The `facets` filter
checks the document's own labels, but `sum_amount` then sums every line
row of the carrying document. A `category=energy` filter that matches an
itemised invoice split into an energy line and a water line also sums the
water line. This predates the within-filter choice; summing only the
lines whose (inherited) labels match the filter is the fix.

## 6. `library backfill-amounts`

```
uv run library backfill-amounts [--limit N]
```

Classifies `amount_kind` (and captures `reference`) for documents extracted
before the field existed: amount-bearing, non-deleted documents with no
`amount_kind` yet (`documents_needing_amount_kind`,
`src/library/money/backfill.py`). A document that already has a kind is left
alone — it may have been corrected by hand, and re-running full extraction
would also overwrite a title, summary or sender a human may since have fixed.

Each document is classified independently, inside its own database
savepoint, and the run reports three counts rather than two:

```
classified 5, empty 0, skipped 0
```

- **`classified`** — the model returned a usable `amount_kind` and it was
  written to the row.
- **`empty`** — the classification call completed, but the model could not
  decide (an unparseable response, or a value that did not resolve to a
  usable kind). The column is left NULL on purpose — NULL means "not yet
  decided" (§2) — and the document stays in the queue for a future run.
- **`skipped`** — the document could not be classified at all: it vanished
  between selection and lookup, the call could not even be attempted (no
  client and no API key configured), or classifying it raised, most likely a
  network error. A savepoint rollback discards only that document's writes;
  the run continues with the next one.

### 6.1 How the classifier is asked

The API backend calls `client.messages.parse()` with the `AmountClassification`
schema, not a free-text call whose reply the caller decodes. The system prompt
does ask for bare JSON, and asking is not enough: the **first live run of this
command against the real archive classified nothing at all**, logging "amount
classifier returned unparseable JSON" for all five documents, because the model
wrapped its otherwise-correct JSON in a ```` ```json ```` fence. The facet
labeller had already failed the same way (GH #108); this is the same fix.

Two things follow, and both are load-bearing:

- The counters told the truth throughout. The failed run reported
  `classified 0, empty 5, skipped 0` rather than claiming success — which is
  exactly what §6's three-way accounting exists for, and how the defect was
  caught in one command rather than discovered later in a wrong chart.
- The subscription backend returns free text and *cannot* use `parse()`, so it
  still goes through `_parse`, which strips a markdown fence or surrounding
  prose (`strip_json_envelope`, `src/library/llm/envelope.py`) before decoding.
  That helper is shared with the facet labeller precisely because writing a
  third bare `json.loads` against a model reply is otherwise the obvious next
  step.

`empty` is never folded into `classified`. Counting a completed-but-unsure
response as a success would hide the very thing this command exists to
report: how much of the archive still lacks a semantic amount. A document
the model could not classify is not counted as done.

## 7. Human corrections: `payment_overrides`

Automatic rules get an ambiguous case wrong sometimes — a coincidental
same-day, same-amount pair that is not the same payment, or a genuine pair
that misses every rule (the R4 shapes in §5, or simply two documents whose
`amount_kind` was never backfilled). `payment_overrides` records the human
correction:

| column | notes |
| --- | --- |
| `kind` | `MERGE` or `SPLIT`, a check constraint |
| `doc_a`, `doc_b` | the two documents; `doc_a < doc_b` is a check constraint |
| `created_at` | when the correction was recorded |

`add_override` (`src/library/money/payments.py`) is the one place that
enforces the ordering — callers may pass the pair either way round, and it
sorts them before insert. There is no delete: to reverse a correction, record
the opposite kind. A pair may therefore hold both a `MERGE` and a `SPLIT` row
(the uniqueness is on the `(kind, doc_a, doc_b)` triple), and **the more
recent of the two wins**, in either order:

- A `SPLIT` suppresses the rule-derived edge outright — the `payment_edges`
  view excludes any rule edge with a matching `SPLIT` row — so a split pair
  separates whether the rules merged it or an earlier `MERGE` did.
- A `MERGE` adds an edge the rules never would have found on their own, and
  it is kept only while no `SPLIT` on the same pair is at least as recent.
  That is how a `MERGE` undoes a `SPLIT`.
- Identical timestamps fall to the `SPLIT`. This tie-break is **defensive**,
  not a path the API can reach: `created_at` defaults to `now()`, which is
  the transaction timestamp, and each of the two callers
  (`src/library/api/payments.py`'s `merge_payment` and `split_payment`)
  writes exactly one override row and commits, so two corrections on one pair
  are always in different transactions with different timestamps. It exists
  for whatever writes both in one transaction later. Not merging is the safe
  direction if that ever happens: two documents wrongly left apart
  under-report, two wrongly joined lose one of them from every total.

Repeating a correction that is already recorded is a no-op in effect, not a
conflict — but it does refresh that row's `created_at`, which is what makes
the *third* correction on a pair (merge, split, merge again) land rather than
silently keep the second one's outcome.

The REST surface (§8) enforces two safety checks before writing an override:
both documents must exist and be non-deleted (404 otherwise), and the two
ids must differ (422 otherwise) — a typo'd id or a same-document override
would otherwise reach the database as a raw constraint violation.

## 8. REST surface

The full wire contract — every route, status code and JSON shape — is in
[api.md](api.md) §1.24; this is the shape of it. `GET /api/documents/{id}/payment` returns the
payment group a document belongs to — every document sharing its
`payment_id`, sorted by id. `POST /api/payments/merge` and
`POST /api/payments/split` each write one override row (§7) and return the
resulting group, anchored on the first document in the request body.
`GET /api/payments/duplicates` is the review surface: every payment with
more than one document, largest group first, capped at 100 with no
pagination — the list a person works through to find and correct a bad
collapse.

On the document-detail page, `PaymentGroup.vue`
(`frontend/src/components/payments/`) renders this group whenever it holds
more than one document, with a "Not the same payment" button per row that
calls `split`. It is not mounted on a soft-deleted document: a trashed
document opens read-only, but its payment endpoint 404s by design, and asking
anyway put a load-failure alert on every Recently Deleted page. There is no dedicated review view over
`/api/payments/duplicates` yet; today it is consumed by this per-document
panel only.
