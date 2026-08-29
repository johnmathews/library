# Money facts and payment identity

**Status:** active. **Last updated:** 2026-08-29 (initial version: `amount_kind`, `reference`, the `payment_edges`/`payments` SQL views, `payment_overrides`, the `/api/payments/*` REST surface, and `library backfill-amounts`. Design: [superpowers/specs/2026-08-28-charts-redesign-design.md](superpowers/specs/2026-08-28-charts-redesign-design.md) §8.1–8.3, plan: [superpowers/plans/2026-08-28-charts-money-facts.md](superpowers/plans/2026-08-28-charts-money-facts.md)).
**Last verified:** 2026-08-29 — method: read `src/library/models.py` (`AmountKind`, `SUMMABLE_AMOUNT_KINDS`, the `Document.amount_kind`/`reference` columns, `PaymentOverride`) and `migrations/versions/0033_money_facts.py` (the `payment_edges`/`payments` views) in full; read `src/library/money/payments.py` and `src/library/money/backfill.py` in full, including `AMOUNT_SYSTEM_PROMPT` and the exact `classified`/`empty`/`skipped` accounting in `run_amount_backfill`; read `src/library/api/payments.py` for the four routes' status codes; read `src/library/extraction/schema.py` for `normalize_amount_kind` and `MAX_REFERENCE_CHARS`. Every rule claim below is covered by an executed assertion in `tests/test_payment_identity.py` (VETO, R1–R3, the dateless/currency-less pairing cases, the un-backfilled-`amount_kind` non-merge, the soft-delete cases, both override directions) and `tests/test_money_backfill.py`/`tests/test_api_payments.py`; not re-run as part of writing this document (a full suite run is recorded in the journal entry for this work).
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

| rule | condition | date reach |
| --- | --- | --- |
| VETO | both documents carry a `reference`, and the two differ | beats every rule below, even a same-day amount match |
| R1 | same sender, date, amount, currency | same day only |
| R2 | same sender, same non-null `reference` | **any** gap |
| R3 | same sender, amount, currency, and **complementary** `amount_kind` (`payment_due` paired with `payment_made`) | ≤ 60 days |

R4 does not exist as an automatic rule. Two documents that share a sender and
amount but neither match on `reference` nor on complementary kind within 60
days are not merged by anything in this layer — see §7 for the two shapes
that actually produce this case, and how they are handled instead.

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

### 4.2 Null-safety, and why it is load-bearing

Two details in the view exist specifically so that missing data fails toward
*not merging silently wrong*, never toward a crash or a false positive:

- **`a.currency IS NOT DISTINCT FROM b.currency`**, not `a.currency =
  b.currency`. Plain equality is NULL — neither true nor false — when both
  documents have no recorded currency, which would silently exclude every
  currency-less pair from every rule. `IS NOT DISTINCT FROM` treats "both
  NULL" as a match, so two documents with no currency on record can still
  pair.
- **`gap <= 60`** (R3's date-gap test) evaluates to NULL, not true, when
  either document has no `document_date` — so R3 can never fire for a
  dateless document. **R2 is the only rule that can pair one**, because it
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

Two shapes are not handled by any rule here, and are not silently mismatched
either way — they simply do not merge:

- **A partial payment.** One invoice for 300.00 settled by two receipts of
  150.00 each does not match on amount, so R1/R3 never fire between the
  invoice and either receipt.
- **Cross-currency settlement.** An invoice billed in one currency and paid
  in another (e.g. an invoice for 80.00 EUR settled by a receipt for 88.00
  USD) does not match on amount or currency, so nothing merges it either.

Both cases surface as separate, un-collapsed documents rather than a silent
merge or a silent double-count — the safer failure mode, but a real gap: a
spending total still counts the invoice once and the settling receipt(s) not
at all (their `amount_kind` would be `payment_made`, summable, and separate).
There is no proposed-merge review surface for these cases yet.

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
sorts them before insert — and the insert is idempotent: repeating the same
correction is a no-op, not a conflict. There is no delete: to reverse a
correction, record the opposite kind. A `SPLIT` on a pair the rules would
otherwise merge wins over the rule (the `payment_edges` view excludes any
rule-derived edge that has a matching `SPLIT` row); a `MERGE` adds an edge
the rules never would have found on their own.

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
calls `split`. There is no dedicated review view over
`/api/payments/duplicates` yet; today it is consumed by this per-document
panel only.
