# Telling the owner what extraction refused to write

**Date:** 2026-09-02
**Branch:** `et/ux-issues-20260902`
**Issue:** [#124](https://github.com/johnmathews/library/issues/124) (items 2 and 3)

## 1. Item 1 was already fixed

#124's first item says `ask/engine.py` is an unguarded fifth writer of
`amount_total`. It was fixed on 2026-08-31 in #133/#134 — `engine.py` commits
through `spend_lines.commit_allocation` and returns the refusal as a tool
result, `charts.md` §10.1 was rewritten to say so, and its §13 bullet removed.
The issue was filed two days before that. Nothing to do.

## 2. The skip was partial, and the coupling rule cannot see it

Re-extraction skipped `amount_total` on an allocated document but wrote
`currency` and `amount_kind` through the same `scalar_values` loop. So a re-read
finding a different currency left the amount pinned to the lines and the
currency describing something else.

Nothing would have reported it. `amount_currency_coupling` is
`(amount_total is None) != (currency is None)` — an XOR on **presence** — so it
provably cannot fire when both fields are set, which is exactly this state. That
is why the fix is a wider skip rather than a new validation rule: the rule has no
way to see it.

All three fields are now withheld together via `_ALLOCATION_LOCKED_FIELDS`.

### The fixture caught a distinction the plan had missed

The first version withheld any locked field whose value would change. The
existing test went red, and it was right to: its document has a NULL currency, so
extraction was *filling* a blank rather than changing one.

Filling contradicts nothing — the trigger only ever guards a change, and a
document whose currency was never extracted should still get one. The guard now
requires `current is not None`, which also leaves `amount_total`'s behaviour
byte-identical to before (it cannot be NULL on an allocated document).

Worth recording because the failing test was the only thing that raised it. A
skip that also blocked fills would have been silently over-broad, and its own
new test would have passed.

## 3. `skipped_fields` went where nobody reads it — and the stated remedy did not work

#124's third item says `skipped_fields` lands in `extra["extraction"]` rather
than the `extraction_completed` event detail, "which is what
`DocumentHistoryTimeline.vue` renders", and that "one line in `apply.py`'s event
detail fixes it".

The premise is wrong, and so the remedy is too. **The timeline renders no field
list at all** — `fields_set` appears nowhere in the frontend.
`extractionBreakdown()` reads exactly five keys: `escalated`, `input_mode`,
`model`, `confidence`, `cost_usd`. The only generic renderer is the collapsed
"Show all events" `<details>`, which dumps `JSON.stringify(event.detail)`.

So the one-line backend change would have surfaced a withheld write as raw JSON,
behind a disclosure triangle, among eight other keys. Not silent; still
invisible.

### The trap next to it

`extractionBreakdown()` returns non-null for **every** `extraction_completed`
event, so the template's `v-if` always wins and `secondary()` is never reached
for this event type — its own docstring says as much.

A branch added to `secondary()` therefore compiles, type-checks, passes a unit
test that calls `secondary()` directly, and renders nothing.

This was not reasoned about and left at that. It was **implemented** — the
template block removed, the branch added to `secondary()` — and the suite run:
the component built cleanly and two of the new vitest cases went red, because
they assert the rendered DOM rather than the function's return value. That is the
whole argument for asserting the outcome instead of the mechanism, and it cost
about a minute to make it a fact rather than a claim.

The change went into `extractionBreakdown()`, as its own amber line beside
`method` — a withheld write is a caveat, not a statistic, so it is not a chip
next to Model and Cost.

## 4. One label map, not two

The timeline needs `amount_total` → "Amount". `validationReason.ts` already had
that map, module-local. It is now exported as `fieldLabel()` and gained
`amount_kind`, rather than being copied — the labels have to agree wherever a
field is named, or the same column reads as two different attributes.

`fieldLabel()` falls back to the raw name where `resolveReviewReason` falls back
to `null`, and that asymmetry is deliberate: a finding can omit an attribute
chip, but a list of withheld fields cannot omit its members. Silence about a
write that did not happen is the defect being fixed.

## 5. What was checked, and how

| mutation | result |
| --- | --- |
| narrow `_ALLOCATION_LOCKED_FIELDS` back to `("amount_total",)` | the currency-drift test red |
| remove `skipped_fields` from the event, keep it in `extra` | two backend tests red |
| implement #124's remedy — a `secondary()` branch, template block removed | compiles and renders nothing; two vitest cases red |

Backend: 40 tests in `test_extraction_apply.py`, and 2131 in the full suite.
Frontend: `npm run type-check`, `npm run lint`, and 1445 vitest tests, all green.

**And a browser**, because the claim being made is about what a person sees and
vitest runs in jsdom. The local stack was brought up, a document given an
`extraction_completed` event carrying
`skipped_fields: [amount_total, currency, amount_kind]`, and its detail page
opened in Chromium. The curated timeline reads:

> Description & metadata added
> Read the OCR text
> **Left unchanged: Amount, Currency, Amount kind**
> `MODEL claude-haiku-4-5` `CONFIDENCE High` `COST $0.0021`

in amber, between the method line and the chips, without expanding "Show all
events" — in both light and dark themes. The only console error was a 404 for
the synthetic document's missing original file.
