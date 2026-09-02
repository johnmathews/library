# Ask's money answers move onto spend_facts

**Date:** 2026-09-02
**Branch:** `feat/136-ask-money-spend-facts`
**Issue:** [#136](https://github.com/johnmathews/library/issues/136)

## 1. What went

`structured_query.sum_amount` summed `documents.amount_total` directly — the
model the chart engine replaced back in #133. It now builds its rows from the
`spend_facts` view, which fixes three defects at once: a refund **added** to a
total instead of reducing it, non-spending amounts (`coverage_limit`, `balance`,
`estimate`, and an undecided NULL kind) were summed as though they were
expenditure, and one payment documented as both an invoice and a receipt was
counted twice.

Ask's tools also gained a `facets` filter, and the archive-context block gained
the facet vocabulary so the model can actually spell one.

## 2. Measuring first overturned nothing, but it sized the bug

I queried the live archive read-only before designing anything, on the principle
that measuring beat assuming on #138. It did not overturn a stated constraint
this time, but it did establish that all four defect classes are present in real
data rather than theoretical: **18** documents hold a non-canonical `spend_facts`
row, **15** carry a non-summable `amount_kind`, **1** is a refund, and **1** has
an amount with an undecided kind.

The size of the resulting error is not reproduced here — this repository is
public and the figure is a fact about the archive's contents. It is enough to
say the unfiltered spend total the old query returned and the one the new query
returns differ by roughly an order of magnitude, dominated by documents whose
amount is a cover ceiling rather than money spent. `confirmed` — both totals were
computed side by side in one query against the deployed database.

The measurement also turned up something the fixtures had to answer for: there
are **zero** `spend_lines` rows in production. The view's line branch has never
run against real data, so nothing but a fixture can exercise it.

## 3. The finding that halved the unit

The obvious fear was that moving the sum onto a view forces `count_coverage` to
move too — it counts over `Document` with SQLAlchemy conditions, and a
view-based aggregate looked incompatible.

It is not, and this is the whole reason the unit stayed one unit. **Every new
exclusion is expressible as a plain `documents` predicate**: `not_summable_kind`
is the `documents.amount_kind` column, and `duplicate_payment` is a `NOT EXISTS`
against `spend_facts.is_canonical`. Only the row-building query moved. Coverage,
and the partition contract it enforces, were not touched.

## 4. The quote branch nearly regressed silently

`kind='quote'` — "how much have my quotes come to?" — is an existing feature. A
quote's `amount_kind` is `estimate`, which is deliberately **not** in
`SUMMABLE_AMOUNT_KINDS`, so pointing `sum_amount` at the summable set would have
turned that feature into a permanent zero. Not an error; a plausible answer of
nothing.

The resolution, agreed with John rather than assumed: **the summable set follows
the question.** `kind='quote'` totals `estimate`; every other question totals the
spend kinds. It stays a kind gate either way, so a document filed under `quote`
but carrying a cover ceiling still falls out under `not_summable_kind` — which
is its own test, because "the quote branch is now an unconditional pass" is the
easy wrong way to implement this.

Worth noting the blast radius was small anyway: production has **no**
amount-bearing documents of kind `quote`. That is a reason the regression would
have gone unnoticed, not a reason it did not matter.

## 5. The eval would have kept scoring while measuring nothing

`SeedDoc` in `disclosure_scenarios.py` seeds `amount_total` and never set
`amount_kind`. The moment `sum_amount` started reading that column, every seeded
amount in every disclosure scenario became `not_summable_kind`, every spend total
came back empty — **and the command still ran, and still scored.** The eval needs
live Claude credentials, so CI would never have noticed; the failure would have
surfaced as a mysteriously-degrading disclosure score months later.

`SeedDoc` carries an `amount_kind` now, guarded by a pure-data test that asserts
every seeded amount carries a kind `sum_amount` would actually total. That test
is the durable half — the field could be reverted, the assertion can't be
without noticing.

This was not in the plan. It is the kind of surface the repo's own
"document field surfaces" habit exists to catch, and I found it by asking what
else reads an amount rather than by grepping for the column.

## 6. Fixtures written to fail

Per this repo's history of favourable fixtures surviving execution against real
Postgres, each case was written to assert the **number**, never the awkward
document's presence. That distinction is load-bearing here: all three defects
were documents wrongly *included*, so a fixture that merely contains the refund
and checks it appears passes against the broken query and the fixed one alike.

Eight of the ten new `sum_amount` cases were observed failing first, with
concrete wrong numbers — a refund totalling `115.00` where `85.00` is right, an
invoice/receipt pair totalling `200.00` for one `100.00` payment, a cover limit
and a balance reaching the total, a NULL `amount_kind` summed. Three of the four
new engine cases failed on the absent `facets` property.

The two that passed unchanged are behaviour-preservation guards, not evidence of
anything, and are reported that way: totalling quotes already worked, and
`filter_conditions` already handled facets (the gap was only that no tool schema
offered one).

The itemised case earns its place by being **merged** with a receipt. A split
document alone cannot go red — the trigger forces its lines to sum to
`amount_total`, so the line branch and the document branch produce the same
total. Merging it makes the line-bearing document win the canonical slot, which
is red against the old query *and* exercises the line branch. It asserts against
the view directly that the contributing document really is two canonical line
rows before asserting the total, so the branch is stated rather than hoped for.

## 7. Three things found reviewing my own diff

1. The signed-amount `CASE` iterated a **frozenset**, so the branch order varied
   per process. The total is order-independent — the branches are mutually
   exclusive — but SQLAlchemy's compiled-statement cache is keyed on statement
   structure, so every process emitted a differently-ordered statement and took a
   fresh cache miss. Sorted now.
2. A doc-audit subagent, run in wrap-up, found that I had replaced one false
   exclusivity claim in `docs/charts.md` ("nothing else issues SQL against
   `spend_facts`") — and then, fixing it, I wrote a *second* false one ("no
   **router** builds SQL against the view"). `api/facets.py` is a router and
   does. It was caught only because the stamp's method was actually executed
   rather than asserted: counting `FROM|JOIN spend_facts` per file enumerates
   four readers and puts `api/facets.py` among them. The true, narrower claim —
   the chart engine's own router `api/spending.py` builds no SQL — is what makes
   `charts/query.py` the single path behind every chart, and is what both the doc
   and the module docstring now say.
3. The archive-context module promises every list is sorted so the block is
   byte-stable inside the cached prompt prefix. The facets line is ordered by the
   curator's ordinals at the database instead, which is equally deterministic but
   arrives there differently. Rather than "unify" it into a sort and throw away
   the curator's ordering, I documented why the line differs and added a test for
   the property that actually matters: two loads render byte-identical.

## 8. What is deliberately not done

1. **`compare_to_series` is not rebuilt.** #136 argues it belongs in the same
   move and that is probably right — a distribution over `spend_facts` starts
   payment-deduplicated and label-scoped rather than needing the caveats bolted
   on. But it doubles the unit, and it needs three things `sum_amount` has no
   shape for: a distribution, a basis for "usual", and a coverage block spanning
   both sides of a comparison. `docs/roadmap.md` §1.1 now carries it as the
   remaining half, and Ask has answered no comparative question since
   2026-08-31.
2. **Facet filtering is document-level, not line-level.** The `facets` argument
   narrows by a document's own labels, which is what the list API and the
   vocabulary panel mean by a facet filter. A chart splits by the labels on
   `spend_facts` rows, and a split document's lines may carry their own — so for
   an *itemised* document filtered by facet, an Ask total includes the whole
   document where a chart would count only the matching lines. Recorded in
   `docs/ask.md` §1.10 item 11 rather than fixed; with zero `spend_lines` rows in
   production it is currently unobservable.
3. **The two new exclusion reasons have no disclosure-eval scenario.** I first
   wrote here, and in `docs/ask.md`, that seeding either "needs a shape `SeedDoc`
   does not express". That was **wrong**, and the wrap-up's documentation audit
   caught it: `not_summable_kind` needs one seed with a non-summable
   `amount_kind` — a field this very change added — and `duplicate_payment` needs
   two seeds sharing sender, currency, amount and date so `payment_edges` rule R1
   merges them, all four already `SeedDoc` fields. `confirmed` against R1's
   definition in migration 0033.

   The real reason they are unwritten is smaller and worth stating plainly: a
   disclosure scenario is only meaningful once it has been seen to route to the
   intended tool against a live model, and this work was done without the
   credentials the eval needs. Shipping two scenarios I could not run would have
   put unverified content into the instrument that measures honesty. `docs/ask.md`
   now says five of eight reasons are unmeasured, up from three of six, and says
   the true reason rather than the flattering one.
4. **Date filters still bound `document_date`.** Moving onto the view did not
   change this — the view carries the document's own date — so the
   bill-issued-in-January-for-December problem in §1.10 item 6 is untouched.

## 9. Verification

Full backend suite green: **2119 passed, 7 skipped**, coverage 95%. `ruff check`,
`ruff format --check`, `mypy`, `actionlint`, the journal-index check and
`check_docs` all clean via `make lint`.
