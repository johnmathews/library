# Odd-one-out reason hallucination fix + chart card layout

Date: 2026-07-01. Branch: `ui-fixes-sidebar-notes-charts`. Follow-up to the
charts smart features shipped earlier today.

## 1. The bug

On a `/charts` card, the odd-one-out panel correctly flagged a member but gave a
**wrong reason**: *"This invoice is from De Hooge Waerder rather than the usual
sender of this document series."* — a sender name present in **none** of the
documents.

## 2. Root cause (systematic-debugging)

The `reason` was written by an LLM (`series_match.generate_reason`). Its prompt
(`build_reason_prompt`) described the series' "usual" identity only as **raw
numeric IDs** (`sender_id={N}`) — the model was never given the real sender name,
yet the system prompt told it to *name* the usual sender. Faced with a Dutch
water-invoice title and no grounded name, the model **confabulated a plausible
Dutch water authority** ("De Hooge Waerder"). Classic proper-noun hallucination:
we asked a probabilistic model to assert a fact (a company name) it wasn't given.

This can't be prevented by prompt-tweaking, and — critically — you can't write a
regression test proving an LLM never hallucinates. But every fact the reason
needs (the candidate's and the dominant members' real sender/kind/currency) is
already known in code.

## 3. Fix — deterministic, grounded reason (no LLM)

Removed the LLM from this path entirely and build the reason mechanically from
real values:

- `series.py`: `odd_ones_out` now returns `(member, axis, reason)`; new
  `_odd_one_out_reason` composes a sentence naming only the candidate's real
  differing value and the dominant members' real value, e.g. *"This document is
  from Vitens, unlike the rest of the series (Waternet)."* A missing value
  degrades gracefully (*"…has no sender set…"*) — never an invented name.
- `series_match.py`: deleted `generate_reason` / `build_reason_prompt` /
  `REASON_SYSTEM_PROMPT` / `MAX_REASON_TOKENS` and the Anthropic import.
- `api/charts.py`: the odd-ones-out endpoint is now a pure pass-through of the
  deterministic reason — no LLM client, no `settings` dependency.

The `authored_series_suggestions` LLM columns (`reason`/`model`/tokens/`cost_usd`)
are now unused but harmless (left reserved); no migration change.

### 3.1 Regression tests

- `test_series.py`: `test_odd_ones_out_reason_is_grounded_and_never_hallucinates`
  asserts the exact grounded sentence AND that every proper noun in the reason is
  a real member value (the anti-hallucination guarantee);
  `test_odd_ones_out_reason_handles_missing_sender` covers the null-sender path.
- `test_charts_suggestions_api.py`: the odd-ones-out API test now asserts the
  grounded sentence naming the seeded senders (was: `reason is None`).
- `test_series_match.py`: removed the `generate_reason` test + the fake Anthropic
  client (that path no longer exists).

## 4. Card layout reorder

The tile header was title → metadata → description. Reordered to **title →
description → metadata**, and split the metadata into two lines: the
count/currency line and the trend-analysis line, each on its own row (moved out
of the heading and out of the header verdict line). Singular/plural noun handled
("1 document" vs "N documents"). New tests assert the order and the separate
metadata lines.

## 5. Verification

- Backend: `773 passed`, ruff clean.
- Frontend: `536 passed`, type-check + eslint clean, build + assets OK.
