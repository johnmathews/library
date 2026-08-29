# Money facts and payment identity

**Status:** active. **Last updated:** 2026-08-29 (fix round 3: R3's mutual-nearest test is now **directional** — a payment follows the thing it pays, so every candidate receipt dated on or after its invoice outranks every one dated before it, and distance decides only within those groups. §4.1 is rewritten around that and now names the four CTEs (`sym` keyed `(due, made)`, `best_due`, `best_made`, `mutual`); the round-2 claims that "the cross-cycle edge is never drawn" and that "nothing that previously merged stops merging" were both **untrue** and are gone — the unsigned ranking they described tied on a 1st/16th cadence (twelve cycles collapsed to nine payments) and picked the wrong pair across a short February, and two shapes do stop merging under the new rule. `sym` now also excludes VETO'd pairs, so a document whose reference conflicts can no longer hold a neighbour's nearest slot. §5 gains a fourth known limit — a systematically reversed cadence pairs off by one cycle, measured as four payments from three, and the trade against the alternative ranking is stated; §4.2 now names the test that pins the 60-day bound on both sides; §7's identical-timestamp tie-break is now labelled defensive, because no API call path can produce a tie. Earlier the same day — fix round 2: §4's rule table and §4.1 now document R3's **mutual-nearest** requirement, which closes a real defect — a recurring same-amount charge documented as invoice-then-receipt chained across billing cycles, collapsing three cycles into one payment of six documents; §4.2's dateless-document bullet now names the `sym` CTE that carries R3's 60-day test, since the `pairs` CTE's `gap` column no longer feeds any rule; §7 now describes override resolution as **latest-wins in both directions** (a tie falls to `SPLIT`, and re-recording a correction refreshes its timestamp) — previously "record the opposite kind" held only for split-then-merge, because a `SPLIT` recorded after a `MERGE` did nothing at all; new §5.1 discloses that the design spec's promised `amount_kind` validation and review queue were never built, so an undecided kind is safe but invisible and uncorrectable by hand; §8 notes the payment panel is not mounted on a soft-deleted document. Earlier the same day — fix round 1: §4's rule table now lists the evaluation order the `payment_edges` `CASE` expression actually uses — VETO, R2, R1, R3, not R1-before-R2 — and states that every rule also requires the same `amount_total`/currency baseline from the `pairs` CTE, not just R1/R3; §4.1 now says explicitly that R3 needs *both* complementarity and the 60-day bound, neither alone being sufficient; §5 now lists a third known-limit shape — a complementary pair with no shared reference more than 60 days apart — and no longer describes what a spending total would do with an unmerged pair, since no spending-total query exists in this codebase yet. Earlier the same day: initial version — `amount_kind`, `reference`, the `payment_edges`/`payments` SQL views, `payment_overrides`, the `/api/payments/*` REST surface, and `library backfill-amounts`. Design: [superpowers/specs/2026-08-28-charts-redesign-design.md](superpowers/specs/2026-08-28-charts-redesign-design.md) §8.1–8.3, plan: [superpowers/plans/2026-08-28-charts-money-facts.md](superpowers/plans/2026-08-28-charts-money-facts.md)).
**Last verified:** 2026-08-29 — method: re-read the `payment_edges` view in `migrations/versions/0033_money_facts.py` line-by-line after the directional rewrite (the `(due, made)`-keyed `sym`, its `CASE`-based `rank` with the 1000 offset, its VETO exclusion, `best_due`/`best_made`, and the rank-equality join in `mutual`) and diffed §4's R3 row, §4.1, §4.2 and §5 against it clause by clause; re-read `add_override` in `src/library/money/payments.py` and both routes in `src/library/api/payments.py` to confirm §7's tie-break really is unreachable (each route writes one override row then commits, and `created_at` defaults to the transaction timestamp) before calling it defensive. Every claim in §4.1 and §5's fourth bullet is covered by an executed assertion in `tests/test_payment_identity.py`: ten new cases (tied 1st/16th cadence over twelve months, Jan–Apr across a short February, the VETO'd neighbour, a reference-less prepayment, backward-only-when-no-forward, one invoice against two receipts, an unpaid invoice beside a later cycle, an equidistant receipt, the day-60/day-61 boundary, and the reversed-cadence limit), seven of which were observed FAILING against the previous view before the fix and all of which pass after it. Full backend suite run green in this pass (1969 passed, 7 skipped). Confirmed no real sender, amount or reference appears in the text added. Earlier the same day — method: re-read the rewritten `payment_edges` view in `migrations/versions/0033_money_facts.py` line-by-line (the new `sym`/`best`/`mutual` CTEs, R3's `m.a IS NOT NULL` arm, and the `created_at`-comparing `NOT EXISTS` on the `MERGE` union arm) and `add_override`'s `on_conflict_do_update` in `src/library/money/payments.py`, and diffed §4/§4.2/§7 against both; re-read `src/library/api/payments.py` for §8's status codes. Every rule and override claim here is covered by an executed assertion in `tests/test_payment_identity.py` and `tests/test_api_payments.py`, both run green in this pass, including three new cases: the monthly-subscription chain (observed failing before the fix as one group of six), merge-then-split (observed failing before the fix as an unchanged merged pair), and merge/split/merge. §5.1's claims were checked by grep rather than assumed: `amount_kind` appears nowhere in `src/library/schemas.py` or `src/library/api/documents.py` (no PATCH path), nowhere in `src/library/extraction/validation.py` (no validation rule, so no review finding), and the quoted spec sentence was read at `docs/superpowers/specs/2026-08-28-charts-redesign-design.md` §8.3. Confirmed no real sender, amount or reference appears in the text added. Earlier the same day — method: re-read the `payment_edges` view's `CASE` expression in `migrations/versions/0033_money_facts.py` line-by-line to confirm the evaluation order (VETO, then `ra=rb` → R2, then `same_day` → R1, then the `gap<=60 AND` complementary-kind test → R3) and that the `pairs` CTE's join (`a.amount_total = b.amount_total AND a.currency IS NOT DISTINCT FROM b.currency AND a.sender_id = b.sender_id`) is the shared precondition every rule sits on top of; cross-checked the same order against `src/library/money/payments.py`'s module docstring, which states it identically. Confirmed `SUMMABLE_AMOUNT_KINDS` (`src/library/models.py`) is unreferenced anywhere under `src/` (`grep -rn SUMMABLE_AMOUNT_KINDS src/` matches only its own definition), i.e. no spending-total query exists yet, before rewriting §5 to stop asserting what such a total would do. Confirmed no `Vendor`-style invented sender name appears anywhere in this document (only the generic word "sender"). Earlier the same day — method: read `src/library/models.py` (`AmountKind`, `SUMMABLE_AMOUNT_KINDS`, the `Document.amount_kind`/`reference` columns, `PaymentOverride`) and `migrations/versions/0033_money_facts.py` (the `payment_edges`/`payments` views) in full; read `src/library/money/payments.py` and `src/library/money/backfill.py` in full, including `AMOUNT_SYSTEM_PROMPT` and the exact `classified`/`empty`/`skipped` accounting in `run_amount_backfill`; read `src/library/api/payments.py` for the four routes' status codes; read `src/library/extraction/schema.py` for `normalize_amount_kind` and `MAX_REFERENCE_CHARS`. Every rule claim below is covered by an executed assertion in `tests/test_payment_identity.py` (VETO, R1–R3, the dateless/currency-less pairing cases, the un-backfilled-`amount_kind` non-merge, the soft-delete cases, both override directions) and `tests/test_money_backfill.py`/`tests/test_api_payments.py`; not re-run as part of writing this document (a full suite run is recorded in the journal entry for this work).
**Covers:** src/library/money/, src/library/api/payments.py, migrations/versions/0033_money_facts.py

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

Seven values, declared in `AmountKind` (`src/library/models.py`):

| value | meaning | summed? |
| --- | --- | --- |
| `payment_due` | an invoice or bill the household owes | yes |
| `payment_made` | a receipt or confirmation that money was paid | yes |
| `assessment` | a tax or levy demand | yes |
| `coverage_limit` | an insurance sum insured or maximum payout — not money paid | no |
| `balance` | an account or statement position | no |
| `estimate` | a quote or indicative price, not yet owed | no |
| `none` | the amount is incidental, or zero because nothing is due | no |

Only `payment_due`, `payment_made` and `assessment` are ever summed into a
spending total — `SUMMABLE_AMOUNT_KINDS` in `src/library/models.py` is the
single source of truth for which three.

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
(`migrations/versions/0033_money_facts.py`), not in Python, so any future
consumer — a chart query, a report — can join payment identity without
reimplementing it. `src/library/money/payments.py` is the read API over the
view plus the one write path (an override row).

Every automatic rule is checked only between documents that already share
the same sender, the same `amount_total` and the same currency — that
precondition is enforced once, in the view's `pairs` CTE join, rather than
repeated inside each rule's own condition. What differs between the three
rules is the *additional* evidence each one demands on top of that shared
baseline. The view's `CASE` expression evaluates them in a fixed order —
**VETO, then R2, then R1, then R3** — because more than one could otherwise
fire on the same pair, and this order is what decides which one wins
(`src/library/money/payments.py`'s module docstring documents the same
order):

| rule | additional condition (on top of same sender, amount, currency) | date reach |
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

Four shapes are not handled correctly by the rules here. The first three are
simply not merged; the fourth is merged into the wrong groups:

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

None of the first three is merged, and none is vetoed — the documents simply
remain separate payments as far as this layer is concerned, with no
`payment_id` connecting them.

The fourth was a deliberate trade, not an oversight. The obvious alternative
— ranking by unsigned distance and using direction only as a tie-break — was
tried and measured: it fixes the reversed cadence and re-breaks the short
February, which is the more common shape by far, because the archive's normal
order is invoice-then-payment. Forward-preference is the correct side of that
trade, and this is the price of it. This repository has no spending-total query
yet (`SUMMABLE_AMOUNT_KINDS` in `src/library/models.py` declares which
`amount_kind` values *would* be summed, but nothing in `src/` sums them), so
what a future total would do with these shapes is not yet settled behaviour
— it is a gap to design for, not a claim to make here. Naively, the invoice
(`payment_due`, summable) and the settling receipt(s) (`payment_made`, also
summable) would both enter such a total independently, which would more
likely **double-count** the underlying spend than omit it. There is no
proposed-merge review surface for any of these shapes yet; today the only
correction path is the manual `merge` override (§7).

### 5.1 An undecided `amount_kind` is safe, but invisible

The design spec (`superpowers/specs/2026-08-28-charts-redesign-design.md`
§8.3) says `amount_kind` "gets its own extraction validation and enters the
review queue when the model is unsure". **Neither exists yet.** This is a gap,
not settled behaviour, and it is worth stating plainly because the safe half
of it is easy to mistake for the whole story:

- **Safe.** An unsure answer becomes NULL, and NULL is never summed (§2). The
  failure mode is under-reporting, never a wrong number.
- **Invisible.** Nothing surfaces *how many* documents still lack a decided
  kind. `backfill-amounts` reports an `empty` count for the documents one run
  touched (§6), but there is no review queue, no needs-review flag, and no
  count anywhere in the API or the UI for the standing backlog.
- **Uncorrectable by hand.** There is no way for a person to set or fix a
  document's `amount_kind` — not in `PATCH /api/documents/{id}`, not on the
  detail page. A wrong kind can only be changed by another backfill run, and
  a backfill deliberately skips any document that already has one.

So a document the model was unsure about is quietly absent from every future
total with nothing pointing at it. Closing this needs a review surface and an
edit path; both are a later layer, and neither is claimed here.

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
