# The header was the cause all along

**Date:** 2026-09-03
**Branch:** `eng-cross-sender-breadth`

The corpus rebuilt this morning was built to make one question answerable. It
answered it the same day, and the answer was not the one either issue expected.

## Chunk count is not the mechanism

`confirmed`. Truncated every crowder to **its own first chunk, verbatim** — same
252 documents, same senders, titles and dates, retained text byte-identical,
only the extra draws removed. Corpus 450 chunks → 258.

| arm | crowder chunks | `breadth-many-mentions` | blind floor |
|---|---|---|---|
| shipped | 5 | 0.17 | 0.0744 |
| truncated | 1 | 0.17 | 0.1481 |

Identical, and the same two documents. Removing four fifths of every crowder's
chunks *and* four fifths of its text changed nothing about how many answers got
through. The confound cuts conservatively — less text should make a crowder
weaker, and they were just as effective.

What chunk count does do is decide *which non-answers* fill the slots: at five
chunks the crowders shut the real archive out entirely, at one chunk two real
documents re-enter at ranks 9 and 11. It never reaches far enough to change
which answers surface.

Note the floor moved too (0.0744 → 0.1481), so relative to chance the case got
*worse*. Comparing the raw 0.17s alone would have missed that.

## The header is the mechanism

`confirmed`. Two probes, both on the deployed host against the real archive.

**Where the missing documents rank.** All 466 documents ordered by nearest chunk
for the breadth query:

```
Solaris Install   (8 docs)  ranks 1, 2, 7, 11, 13, 14, 23, 33
Gridline Networks (2 docs)  ranks 308, 349
Meridian Mortgages(1 doc )  rank  424
Harbour Insurance (1 doc )  rank  456
```

That alone kills the fix I was about to recommend. Result diversification (MMR,
a per-sender cap) re-ranks a *candidate list*; these are hundreds of places
below any pool the retriever builds. Worth recording because diversification is
the obvious answer to "one sender dominates the results" and it would have been
wasted work.

**Why they rank there.** #164 said the header's effect could not be measured
without a header-free seeding path. That was wrong — the eval is not the only
instrument. Embed the two strings *directly* through the sidecar and compare
distances to the query:

| sender | documents | effect of the header |
|---|---|---|
| Solaris Install | 8 | all 8 **closer** (−0.0009 to −0.0616) |
| Gridline / Harbour / Meridian | 4 | all 4 **further** (+0.0206 to +0.0353) |

A perfect partition. The header is `sender · date · kind · title`, so prepending
`Harbour Insurance · 2023-05-30 · letter · Policy amended` to a document whose
body says "rooftop array" pulls it toward insurance and away from a solar query.

**The header is a sender-affinity amplifier.** It makes a document more findable
by questions that sound like its sender and less findable by questions that do
not. That is simultaneously why `sender-named-bare-chunk` and `date-scoped`
score 1.00 and why breadth collapses onto one sender. Same mechanism, opposite
signs, depending on the question.

## What this says about the proposed remedies

#106 listed three. Measured against the above:

1. Per-document chunk cap in ranking — already implemented, and not the binding
   constraint either.
2. Length normalisation — **would make it worse.** It penalises long documents,
   and long documents are already losing: in the truncated arm the short
   crowders won more decisively, and the passage case's long answers scored 0.00
   against a 0.70 blind floor. What hurts is dilution *within* a chunk.
3. Larger chunks — increases dilution.

## Also landed

`hnsw.ef_search` (#161). pgvector caps an HNSW scan at that many rows whatever
the LIMIT says; the default is 40 against a 250-row prefetch, so on the index
path the fanout silently collapsed. Now `SET LOCAL` to the fanout. Two failures
found while writing it, both worth remembering: PostgreSQL's `SET` does not
accept bind parameters (`syntax error at or near $1`), and the `text` import
shadowed a loop variable in a different function.

The test asserts the **setting**, not a row count. On a small corpus the planner
takes the sequential path, so a row-count assertion would pass for the wrong
reason and keep passing if the SET were deleted. Confirmed by mutation: removing
the SET reds the "applies it" test and leaves the "does not leak" test green.

## What is deliberately not done

- **No change to the header.** The trade-off is real in both directions and the
  question mix that would decide it has not been measured. Options exist (drop
  the sender from the header; embed it separately and fuse; keep a second
  header-free vector per chunk) and each needs its own evaluation. Guessing here
  would repeat exactly the mistake this branch spent the day disproving.
- **No diversification.** Ruled out by measurement above, not by preference.
- **No fix for the five saturated cases** (#167) or the crowders' 94% sentence
  overlap. Both real, both corpus polish, neither on the path to a user-visible
  improvement.
- **The breadth case still measures solar-versus-installation rather than
  fixtures-versus-real-archive.** The crowders displaced the real archive
  entirely. Restoring the original question means making the crowders topically
  unrelated — they would still fill the prefetch window, which is their other
  job. Not done because it is a design decision, not a defect.
