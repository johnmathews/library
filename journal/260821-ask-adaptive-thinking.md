# Ask: turn reasoning on

**Date:** 2026-08-21

Third Ask change today, after
[document view](260821-ask-document-view.md) /
[document default](260821-ask-document-default-and-rail-actions.md) and
[prompt caching](260821-ask-prompt-cache-and-token-accounting.md).

## The finding

`confirmed` — grepping the whole of `src/library/` for `thinking` and
`output_config` returned **nothing**. Not in Ask, not in extraction, not in the
judge, not in titling. Every LLM call in this project ran with no extended
reasoning.

That is not a neutral default. On this model family, *omitting* the `thinking`
parameter means the model runs without reasoning — you have to ask for it. Ask
is the call that most wants it: retrieve, cross-check, compare against a
statistical distribution, cite. So the single largest accuracy lever available
was one parameter that had never been set.

## Three settings, one decision

They are coupled, and shipping any one alone would be a mistake:

| setting | from | to | why |
| --- | --- | --- | --- |
| `thinking` | absent (= off) | `{"type": "adaptive"}` | the lever |
| `ask_max_answer_tokens` | 1024 | 8192 | **thinking tokens count against `max_tokens`** — at 1024 reasoning could consume the budget and truncate or displace the answer entirely |
| `ask_max_tool_turns` | 4 | 8 | reasoning encourages more, better-targeted tool calls |

The second is the trap. Enabling thinking while leaving a 1024-token cap would
have made answers *worse*, not better, and the failure would have looked like a
model regression rather than a config mistake.

## Evidence for the two caps

`confirmed`, queried from the deployed `ask_turns` (51 turns):

- **5 turns produced ≥1000 output tokens** with thinking *off* — already
  crowding the 1024 cap, and the answers doing it are the table-heavy ones.
- **4 turns used all four tool calls**; **0** fell back to the no-answer
  message. So 4 was not yet failing, but had no headroom for a question needing
  search → read → compare → verify.

## A gap the tests found

Writing a test for thinking-block replay surfaced a real defect. Thinking blocks
must be returned byte-identical on the next call of a turn or the API rejects
them, and the obligation lives in the `signature`. `_serialize_block` handled
`text` and `tool_use` explicitly and ended in `return {"type": block_type}` — a
catch-all that **silently drops every field** of any other block type. Real SDK
blocks escape this via `model_dump`, so it would not have bitten in production;
but a lossy fallback whose failure mode is a 400 on a *later* call, far from the
line responsible, is worth closing. It now round-trips thinking blocks
explicitly.

Also fixed: the autouse `_stub_thread_title` fixture had drifted out of step
with `generate_thread_title`'s signature. Because titling is deliberately
non-fatal, the resulting `TypeError` was swallowed on **every** test in the file
— so every test was quietly exercising the error path instead of the stub, and
nothing failed to say so. A stale stub under a non-fatal call site is invisible
by construction.

## Verification

- 4 new tests. 3 confirmed to fail with the settings reverted; the 4th
  (thinking replay) was confirmed separately — it failed with
  `KeyError: 'signature'` before `_serialize_block` learned to round-trip
  thinking blocks.
- Full backend suite **1642 passed, 7 skipped** (was 1638).
- `ruff check`, `ruff format --check`, `mypy`, `check_docs`, journal index clean.

## What is deliberately not done

1. **No before/after accuracy measurement.** `strongly supported` at best: the
   settings are argued from the model's documented behaviour and from usage
   statistics, not from an observed change in answer quality on this archive.
   There is no eval set for Ask. Building one is the honest next step and is
   larger than this change.
2. **Expect cost to go UP.** Thinking tokens bill as output and a longer loop
   means more calls. Prompt caching offsets part of it. This is an accuracy
   change, not an efficiency one, and saying otherwise would be the kind of
   overclaim the caching entry warns about.
3. **`effort` left at its default (`high`).** `xhigh` is documented as the sweet
   spot for agentic/tool-loop work on this tier and is worth trying, but that is
   a second variable and changing two at once makes neither measurable.
4. **Retrieval untouched.** `retrieve_top_k` (10) and `retrieve_chunks_per_doc`
   (3) remain the biggest unexamined lever — a passport question still retrieved
   a car-insurance policy. Reasoning may paper over some of that, which is
   exactly why it should be measured separately.
5. **Extraction, judge and titling still run without thinking.** Same finding
   applies to them; each needs its own cost/benefit call rather than a
   repo-wide flip.
