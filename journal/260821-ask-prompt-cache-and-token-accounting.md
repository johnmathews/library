# Ask: cache the tool loop's growing prefix, and count cached tokens

**Date:** 2026-08-21

## Where this came from

A question — "why does sending a message in /ask require so many tokens?" — that
turned into a measurement rather than a guess. Everything below marked
`confirmed` was queried from the deployed database.

`confirmed`, across 51 real turns in prod:

| metric | value |
| --- | --- |
| avg input tokens | 25,968 |
| avg output tokens | 514 |
| max input tokens (one turn) | 250,286 |
| avg cost | $0.1421 |

Input outweighs output roughly 50:1. So the cost is in what gets *sent*.

## The hypothesis that was wrong

My first guess was history replay — `LIBRARY_ASK_HISTORY_TURNS=3` re-feeds prior
turns' serialized blocks, tool results included, so I expected later turns in a
thread to be the expensive ones. `confirmed` false:

| turn in thread | turns | avg input tokens |
| --- | --- | --- |
| 1 | 34 | 35,591 |
| 2 | 10 | 7,343 |
| 3 | 4 | 2,299 |

The **first** turn is the most expensive. Replay is not the driver.

## What is actually going on

`confirmed`. The tool loop (`_run_api_turn`) calls `messages.create` up to
`ask_max_tool_turns` (4) times and re-sends the entire `messages` array each
pass, summing `response.usage.input_tokens` across all of them. For the 250k
turn, the persisted transcript is 8 blocks totalling ~87k characters — but the
cumulative payload across the four calls is ~247k characters:

```
call 1: system + tools + question                   7,838 chars
call 2: + tool_result #1                           50,450
call 3: + tool_result #1 + #2                      94,445
call 4: + everything                               94,775
```

Tool result #1 is paid for three times.

Two contributing factors, both `confirmed`:

- Each `semantic_search` returns up to `retrieve_top_k` (10) × `retrieve_chunks_per_doc`
  (3) = 30 full excerpts. The two results above are 42,810 and 41,356 chars.
- The content tokenizes densely — reported tokens ≈ characters, against the ~4
  chars/token typical of English prose. The excerpts are OCR'd Dutch markdown
  tables: pipe-delimited label/value rows, `<br/>` tags, long alphanumeric
  reference codes of the shape `XXXNNN260701096895`, and JSON escaping.
  `suspected`, not confirmed: I did not run `count_tokens` on the text, and I
  checked and *rejected* the obvious explanation — pipes, dashes and backslashes
  are only ~5% of the payload, so table scaffolding is not the cause.

  (An earlier revision of this entry quoted a real policy number and a real
  field/value pair lifted verbatim from a document in the archive. This is a
  **public** repository. Illustrate the *shape* of archive content, never the
  content itself — the point being made here needed neither.)

## The fix

**1. `cache_control: {"type": "ephemeral"}` on the loop's `messages.create`.**
Top-level cache control caches the last cacheable block, which here is precisely
the accumulated tool-result tail. Re-reads bill at ~0.1x. Conditions checked
against the current API: max 4 breakpoints (we now use 3 — system, history
boundary, this), ~1024-token minimum cacheable prefix (tool results are 10k+),
5-minute default TTL (loop iterations are seconds apart).

Projected saving from the published multipliers: result #1 goes 3.0x → 1.45x,
result #2 2.0x → 1.35x. Call it 40–50% off the dominant cost. **`suspected`** —
this is arithmetic from documented pricing, not an observed cache-hit rate.

**2. Count cached tokens.** `engine.py` summed only `input_tokens`, which
*excludes* cache reads and writes. Shipping (1) alone would have made recorded
spend appear to collapse — partly from real savings and partly because tokens
stopped being counted, with no way to tell the two apart. `_cached_usage` now
returns both a **total** (fresh + reads + writes, comparable across cached and
uncached turns) and a **billable** figure weighted 0.1x/1.25x for costing.

This also fixes a pre-existing understatement: cache reads were already
invisible in `cost_usd` before any of this.

## What is deliberately not done

1. **The retrieval knobs.** `retrieve_chunks_per_doc` 3→1 and `retrieve_top_k`
   10→5 would save more than caching does, but they trade recall for cost and I
   have no evidence about answer quality. There is a hint worth chasing — the
   250k turn asked *"When does my passport expire?"* and retrieved a car
   insurance policy — but a hint is not a measurement. Once this ships, the
   recorded cache figures give a baseline to measure a change against.
2. **The model.** `ask_model` is `claude-opus-4-8`, the priciest tier. Running
   the tool-loop passes on a cheaper model and reserving Opus for the final
   answer is a known pattern, but it is a quality decision, not a cleanup.
3. **The subscription path's costing.** `llm/subscription.py` already totals all
   three counts and prices them at the full input rate. That overstates, but
   deliberately — the path is not metered and its `cost_usd` is a notional
   ceiling. Left alone rather than quietly changed.
4. **No production cache-hit measurement yet.** The whole point of change (2) is
   that `cache_read_input_tokens` will now be recorded. The honest read of this
   entry is: the mechanism is confirmed, the saving is projected, and the next
   session can check it against real data.
