# Ask: verifying the caching and reasoning changes against the live API

**Date:** 2026-08-21

Closes the two verification gaps left open by
[prompt caching](260821-ask-prompt-cache-and-token-accounting.md) (#83) and
[adaptive thinking](260821-ask-adaptive-thinking.md) (#84). Both shipped with an
explicitly-stated hole: **CI has no Anthropic key**, so every automated test of
these features runs against a fake client. Neither the cache hit rate nor the
acceptance of the `thinking` parameter had ever been observed.

Both were checked by driving `run_ask` directly on the deployed host
(sha `ec4aa18`). `run_ask` does not persist a turn — the API layer does — so
this added no rows to `ask_turns`.

## 1. Does the real API accept adaptive thinking?

`confirmed`. A minimal call with `thinking={'type': 'adaptive'}` on
`claude-opus-4-8` returned `stop_reason: end_turn`. Live settings confirmed as
`max_tokens: 8192`, `tool_turns: 8`.

Worth recording: that trivial call returned **no** thinking block
(`block types: ['text']`). Adaptive thinking decides *when* to think, and
"reply with OK" does not warrant it. A smoke test that stopped there would have
proved the parameter is accepted and nothing about the path that actually
carries risk.

## 2. Does the thinking-block replay path work?

`confirmed`, and this was the real risk. A genuine comparative question
("compare my most recent energy bill against earlier ones from the same
supplier") produced:

```
tools used     : ['compare_to_series', 'query_documents']
thinking blocks: 3
  signature present: True | length: 540
  signature present: True | length: 424
  signature present: True | length: 692
```

The loop completed normally, which is itself the proof: a thinking block
returned without its signature intact is rejected on the *next* call of the
turn. Three blocks survived `_serialize_block` into `turn_messages` and were
replayed successfully.

The answer also showed the discrimination reasoning is meant to buy — it
identified the supplier, said two bills are not enough for a trend, gave both
amounts, and explicitly *excluded* same-`kind` documents from other senders
(EV charging, vehicle tax) as not being energy from that supplier.

`suspected`: that the answer is *better* than it would have been without
thinking. It is one anecdote and there is no baseline.

## 3. Is the prompt cache actually hitting?

`confirmed`. Wrapping `messages.create` across one real five-call loop:

| call | uncached | cache write | cache read |
| ---: | ---: | ---: | ---: |
| 1 | 2 | 0 | 3,127 |
| 2 | 2 | 0 | 3,333 |
| 3 | 2 | 290 | 3,333 |
| 4 | 2 | 264 | 3,623 |
| 5 | 2 | 1,715 | 3,887 |
| **total** | **10** | **2,269** | **17,303** |

Weighted (1.25x writes, 0.1x reads): 4,576 input-token equivalents against
19,582 uncached — a **76.6% reduction**, above the 40–50% projected in #83.

Two caveats, both material:

1. **Call 1 opens against a warm cache** (3,127 reads before the loop has
   written anything), because the identical question had been asked minutes
   earlier by the previous check. A cold start would begin with a write. The
   headline number is an upper bound, not a typical figure.
2. **One sample, one question.** Nothing here supports a per-turn average.

What it does establish without qualification is the mechanism: cache reads grow
across calls 2–5 as the tool-result tail accumulates. That growth *is* the
re-send #83 exists to stop paying for.

## What is deliberately not done

1. **No cold-start cache measurement.** Would need a question not asked within
   the 5-minute TTL. Worth doing if the figure is ever quoted as typical.
2. **No accuracy baseline.** Points 2 and 3 measure mechanism and cost. Whether
   answers got *better* remains unmeasured and needs an eval set.
3. **No aggregate before/after on `ask_turns`.** The 51-turn statistics predate
   both changes; a fair comparison needs turns recorded after this deploy, which
   means waiting for real usage.
4. **The smoke scripts were not kept.** They ran from `/tmp` in the container and
   were removed afterwards. Reproducing them is a few lines against `run_ask`;
   committing throwaway operational scripts into the repo is worse than
   rewriting them.
