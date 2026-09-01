# Disclosure eval

**Date:** 2026-08-27

## What changed

A scorer for whether Ask's answers actually disclose their own gaps
(`library.ask.disclosure_eval`), six synthetic scenarios that drive it
(`library.ask.disclosure_scenarios`), and a CLI command, `library
eval-disclosure`, that seeds each scenario's documents inside an uncommitted
transaction, drives the real Ask loop, scores the answer, and rolls back.
`docs/ask.md` §1.2 now documents the eval; its stamp's standing claim that the
disclosure rule's effect on real answer wording was unmeasured is corrected.

## Why deterministic scoring, not an LLM judge

A live prototype run before this eval existed already showed the model states
a dropped-document count as a numeral in prose ("2 more utility bills matched
... but had no readable amount"). That signal is countable directly. A judge
model would add cost, latency and its own noise to a question that a regex
over digits and number words already answers, with the same false-pass risk
either way — a coincidental digit reads the same to a regex as to a lenient
judge. `disclosure_eval.score` looks for the exact expected count as a numeral
or English number word, stripping inline citations (`[#1, #2]`) first so a
citation's digit can't masquerade as a disclosed count, and excluding digit
neighbours, comma-grouped amounts, and ordinal suffixes so "12 bills" doesn't
satisfy an expectation of "2" and "EUR 2,500.00" doesn't satisfy "2" either.

## Why a CLI command, not a test

CI has no Claude credentials, so a test built on this would either be skipped
in CI (every run) or hard-fail (blocking every PR on a live model call it
cannot make). A suite that reports green while every test inside it quietly
skips is worse than no suite — `tests/golden_corpus.py` makes exactly this
argument for its own eval-shaped suite, gated the same way: `LIBRARY_GOLDEN_CORPUS`
turns a missing dependency into a hard failure in CI rather than a silent
skip. The disclosure eval doesn't have a CI-settable equivalent — there's no
way to hand a model subscription to a CI runner the way a corpus checkout can
be handed over — so the honest shape is a command a human runs by hand, not a
regression gate.

## Why a control scenario

Five of the six scenarios expect disclosure. Without a sixth that expects
none, an eval built only from those five would score a perfect pass against a
model that hedges on every single answer regardless of whether anything was
actually incomplete — which certifies the opposite of what this eval exists to
catch. `complete-no-gaps` seeds a sender with three ordinary invoices, nothing
excluded and nothing flagged, and fails if the answer contains any of a short
list of hedge phrases (`may be missing`, `might be missing`, `not be
exhaustive`, and similar). An earlier version of that hedge list included
`\bsome documents\b`, which fired on ordinary descriptive prose describing a
mixed batch and produced a false *fail* on a correct, complete control answer;
removed once found by running the scorer against realistic text rather than
by reading the pattern.

## Why the eval reads coverage from the transcript instead of recomputing it

The point is to grade what the model was actually shown and did with it, not
to re-derive the arithmetic from the seeded rows and grade that against
itself — a scorer that recomputes coverage and compares its own recomputation
to the answer can never catch the tool reporting one thing and the model
disclosing another. `_coverage_from_turn_messages` decodes the last
`coverage`-carrying `tool_result` block from the turn's own message history
instead.

## The first run found a production bug, not a model result

The eval's first live run reported 0 passed, 0 failed, 6 skipped — "no
coverage block reached the model" on every scenario, even though
`query_documents` and `compare_to_series` were both being called. The cause
was in `ask/engine.py`'s `_tool_result_payloads`, the shared helper that
decodes a `tool_result` block's content: on the `api` backend, `content` is a
single JSON-encoded string, one level of decoding. On the `subscription`
backend — the *default* backend, and the one this eval necessarily drives,
since it needs no metered API key — the SDK's tool result content is already
a list of content blocks, which then gets `json.dumps`-ed *again* on top,
making it two levels of JSON with the real payload sitting inside the inner
block's `text`. The helper only unwrapped one level.

That is not just an eval bug. `_previewed_ids_from_history` (the propose-then-
confirm write guard: a confirmed edit is refused unless the target document
was *previewed* in an earlier turn) reads through the exact same helper, and
with only one level of unwrapping it always returned an empty set on the
subscription backend — so every confirmed write was refused with "preview
required first", regardless of whether the document really had been
previewed. This has been true since the subscription backend shipped. It
failed **closed**: no confirmed edit could go through on that backend, so
nothing unauthorized was ever written — but the write tool was silently
non-functional in production the whole time. Fixed by decoding the inner
content-block list when that's the shape decoded, and the eval was then
changed to import and reuse that one fixed helper rather than keep a second,
drifted copy of the same unwrap logic in the CLI.

## The eval's first real result

Re-run after the fix, against an isolated scratch database — never the
archive:

```
PASS utilities-no-amount
PASS spend-excludes-quotes
PASS flagged-amounts
PASS list-truncation
PASS series-other-currency
PASS complete-no-gaps
6 passed, 0 failed, 0 skipped
```

The model disclosed every non-zero excluded-reason count and the
`needs_review` count, on both `query_documents` and `compare_to_series`
answers, and the control passed too — it invented no caveat when nothing was
dropped. The control passing is what makes the other five meaningful.

## Not done

The scorer is a screen, not a judge: `mentions_count` can be satisfied by a
coincidental digit for an unrelated reason, and this is stated directly in
`disclosure_eval.py`'s own module docstring rather than left to be
discovered. Every verdict carries the answer text verbatim for exactly this
reason. This is measured evidence from one run, not a continuously-verified
property — there is still no CI gate on it, and there cannot be one without
CI holding model credentials.
