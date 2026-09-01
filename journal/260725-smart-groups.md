# Smart Groups

Shipped the semantic-mode authored series ("Smart Groups") described in
[docs/superpowers/specs/2026-07-24-smart-groups-semantic-series-design.md](../docs/superpowers/specs/2026-07-24-smart-groups-semantic-series-design.md)
and documented at the time in `docs/smart-groups.md`, since **archived**:
[docs/archive/smart-groups.md](../docs/archive/smart-groups.md) (the feature
was deleted on 2026-08-31 — see
[260831-delete-series-stack.md](260831-delete-series-stack.md)). Commits
`56624c8..5623541` on `feat/smart-groups-semantic-series`.

## Why this existed

`/charts` only produced emergent series keyed on `(sender_id, kind_id,
currency)`, so a concept spanning senders — "my EV charging fees" across
Fastned/Shell Recharge/Allego, "my accountant" across bookkeeping + tax
invoices — could never become one chart. Manual authored series already let
a user hand-curate a cross-sender list, but nothing *learned* new members for
a group that has no dominant sender signature (the existing auto-continue
engine, `series_match.py`, only proposes matches against a group's dominant
sender/kind/currency triple).

## Decision: extend authored series, not a parallel system

Considered building a separate "smart group" table/entity. Rejected — an
authored series already gives naming, the chart pipeline
(`summarize_authored_series`), deep-linking, and the description-blurb
machinery for free. Instead: `AuthoredSeries.mode` (`manual`/`semantic`,
default `manual` — existing rows unaffected), `AuthoredSeriesMember.origin`
(`manual`/`accepted_suggestion`/`auto`), and a new `AuthoredSeriesExclusion`
table for negative examples. A semantic group is structurally still an
authored series; only its membership *source* differs.

## Decision: nearest-positive-neighbour scorer, not centroid or LLM

The scorer (`semantic_membership.py`) computes `sim_pos = max(cosine(cand,
p) for p in positives)` and requires `sim_pos >= τ AND sim_pos > sim_neg +
margin`. Two alternatives considered and rejected:

- **Centroid similarity.** Averaging the group's member vectors into one
  point loses diverse sub-clusters — a group that legitimately contains both
  Fastned and Shell Recharge receipts would average to a point that
  resembles neither strongly. `max` over positives means each sub-cluster
  keeps counting.
- **An LLM decides membership.** Rejected outright, and not just for cost —
  the codebase already has a precedent for what goes wrong: `series.py`'s
  odd-one-out rationale (`series_match.py:12`) records that an LLM once asked
  to *phrase* why a document breaks a series' signature hallucinated a
  sender name that appeared in none of the documents. That reasoning was
  made deterministic (built only from real sender/kind/currency values on
  the documents) specifically because of that failure. Smart Groups
  membership follows the same rule from the start: the LLM's only jobs are
  turning a group's name into a seed search query (widening initial
  positives) and writing a best-effort description blurb after the fact —
  never deciding who's in the group. Plain kNN-majority-vote was also
  considered and rejected for the same cold-start reason a centroid was: with
  zero negatives, every candidate's nearest labeled neighbour is a positive,
  so a vote would admit the whole library on day one. The `τ` gate alone has
  to hold the line until pruning supplies negatives.

## Decision: auto-add silently, but stage the one-time backfill

Two different "when does a document join" moments, treated differently on
purpose:

- **Creation-time backfill** sweeps the *entire* existing library against
  the new group's seeds. That's a big one-time retroactive change, so it's
  staged: matches land as `pending` suggestions and the user reviews them
  once in a modal before they become real members
  (`origin=accepted_suggestion`).
- **Forward auto-add** (`evaluate_semantic_groups`, queued alongside
  `evaluate_series_autocontinue` when a document reaches `INDEXED`) adds
  future matching documents with `origin=auto` and *no* review step.

The asymmetry is deliberate: reviewing every future addition would make
Smart Groups no better than manual series, defeating the point. But silent
by default still needed a visibility guardrail, since it moves real spending
totals without a human in the loop — hence the "N added automatically" badge
on the group tile and the `origin=auto` flag on members, so pruning a wrong
auto-add is a one-click fix rather than a hidden data-quality problem. A
global "review auto-adds" inbox was considered and deferred (per-tile
visibility covers v1; nothing today suggests it's needed).

## Decision: prune writes a negative, not just a delete

Removing a member or dismissing a suggestion writes an
`AuthoredSeriesExclusion` rather than just deleting the row. Two reasons:
without it, (a) the same document would resurface on the very next backfill
sweep or auto-add pass, making "remove" feel broken, and (b) a delete alone
throws away signal — a prune is evidence about where the group's boundary
sits (`sim_neg`), so treating it as a labeled negative example sharpens
future scoring instead of just reverting one document.

## Tunables are first-guess, not calibrated

`semantic_group_min_similarity` (τ = `0.55`) and `semantic_group_neg_margin`
(`0.02`) are reasonable starting points chosen by analogy to the existing
`series_autocontinue_min_dominance` (`0.6`) precedent, not fit against a
labeled dataset — there isn't one yet. Expect to revisit both once a few
real Smart Groups have been used for a while: if too much noise gets
auto-added, raise τ or the margin; if genuinely-related documents from a
different sender don't join a diverse group, lower τ. Per-group overrides
were explicitly deferred to a later iteration (§4 of the design spec) — v1
is one global pair of settings.

## Split out: the duplicate-sender companion fix

The design's §9 identifies a real, separate bug: `upsert_sender`
(`extraction/apply.py:74`) only `.strip()`s a sender name while
`create_sender` (`taxonomy.py:428`) also collapses internal whitespace
(`" ".join(name.split())`), so OCR yielding a double space creates a second
`Sender` row that displays identically — the likely cause of the duplicate
"De Hooge Waerder" / "Anthropic" tiles that motivated part of this work.
Deliberately **not** bundled into this migration/PR: it's an unrelated
one-line normalization fix plus a merge-the-existing-dupes cleanup, and
mixing it into the Smart Groups schema change would make both harder to
review and revert independently. Tracked as Task 12 — confirmed not yet
implemented as of this entry (`upsert_sender` still only `.strip()`s).

## What shipped (commits `56624c8..5623541`)

Migration + models → `semantic_membership.py` scorer (+ tests) →
FX-aware `_load_authored_members` → backfill sweep + create API → forward
auto-add job hook → prune-as-negative wiring → frontend create toggle,
staged-review modal, auto-added badge → LLM seed-query + blurb. Matches the
design's build order (§12) in sequence.
