# The amount classifier could not read its own answers

The money-facts branch merged and deployed cleanly: migration `0032 → 0033`
applied, `/healthz` green, alembic head `0033`. Then the first live run of the
command the whole branch exists to enable:

```
$ library backfill-amounts --limit 5
amount classifier returned unparseable JSON
amount classifier returned unparseable JSON
amount classifier returned unparseable JSON
amount classifier returned unparseable JSON
amount classifier returned unparseable JSON
classified 0, empty 5, skipped 0
```

Zero of five. The model was answering correctly and wrapping the answer in a
```` ```json ```` fence; `classify_amount` called `messages.create()` and
`json.loads`-ed the raw text.

## This is the second time

The facet labeller failed identically three weeks of work ago (GH #108), for
the same reason, and was fixed by moving to `client.messages.parse()` with a
Pydantic schema — which the extraction path had been using all along. The
amount classifier was written afterwards, in the same repository, and reached
for `messages.create()` anyway.

No test could have caught it either time, and for the same reason both times:
every test fed the parser a hand-written JSON string. `classify_amount` itself
was stubbed out wholesale in `tests/test_money_backfill.py`, so the API call
shape had no coverage at all. A test suite that only ever exercises a function
below the network boundary cannot see a defect that lives at it.

## What changed

- `classify_amount`'s API path uses `messages.parse()` with a new
  `AmountClassification` schema; a `None` `parsed_output` now raises
  `AmountParseError` instead of silently becoming an undecided document.
- The fence/prose stripper moved out of `facets/labeller.py` into
  `src/library/llm/envelope.py` and both callers share it. Two independent
  occurrences is the point at which a third caller writing its own bare
  `json.loads` stops being hypothetical.
- Four new tests, all watched failing against the shipped code first. The
  load-bearing one asserts the *call shape* — its stub raises if `create` is
  reached — because a stub that answered both methods would let this back in.

## What went right

The three-way counters. The run reported `classified 0, empty 5, skipped 0`
rather than "classified 5", because an earlier incident on this project taught
that counting any response as success hides exactly this. The defect was
visible in one command, before any chart was built on top of it.

That is also the case for having run the backfill against the real archive at
all rather than trusting a green branch: CI was green, every gate passed, the
deploy verified clean, and the feature did nothing whatsoever.
