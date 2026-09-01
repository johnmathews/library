# The first recall baseline, and the first evidence for #6

**Date:** 2026-08-27
**Branch:** `chore/record-recall-baseline`

## Third time

```
PASS control-unique-term      recall@10=1.00
PASS contract-clause          recall@10=1.00
PASS sender-named-bare-chunk  recall@10=1.00
PASS kind-scoped              recall@10=1.00
PASS date-scoped              recall@10=1.00
FAIL breadth-many-mentions    recall@12=0.33
5 passed, 1 failed, mean recall 0.889
```

0.889 against a ceiling of 0.90. The corpus passes its own acceptance criterion
for the first time, so `recall-baseline.json` is committed — the 201-document
corpus against 259 archive documents, both recorded in its `measured_against`
block.

| corpus | mean | blind floor | verdict |
|--------|------|-------------|---------|
| 53  | 0.917  | ~0.77 | fails |
| 90  | 0.9028 | ~0.77 | fails |
| 201 | 0.889  | 0.21-0.25 | passes |

## The evidence for #6

Two cases are built so that the fact which answers them exists *only* in
metadata. `sender-named-bare-chunk`: forty identically titled annual statements
whose bodies are figures blocks naming neither sender nor year. `date-scoped`:
forty identically titled penalty notices whose bodies never state their year.

In both, the sender and the year reach the embedding by exactly one route — the
`sender · date · kind · title` header that #6 prepends before embedding. FTS
does not see it either; it indexes the document text.

Both scored **1.00**. A retriever ranking at random has about a **1.2%** chance
of landing all three expected documents in ten slots drawn from forty. The two
together, at roughly 1 in 7,000, are not a coincidence worth entertaining.

This is **not** the before/after delta spec §8.1 asked for. That remains
impossible: `_seed_corpus` embeds through the real `run_embed`, so the fixtures
always carry headers, and there is no header-free path to difference against. It
is an inference from how the cases are constructed rather than a subtraction of
two measurements. But it is well powered, and after three rounds of the corpus
being unable to say anything at all, it is the first positive result #6 has
produced.

Worth noting how late this arrived. #6 shipped and deployed before any of it was
measurable, and the argument for it was a design argument the whole way. The
eval was built to make that unnecessary and only managed it on the third attempt.

## The one failing case looks real

`breadth-many-mentions` scored 0.33 — four of twelve expected documents inside
twelve slots. Half the returned slots went to **real archive documents** only
loosely related to the question: other installation and electrical paperwork,
and in two cases documents with nothing to do with the topic. They displaced
eight genuinely relevant ones.

Two readings, and the honest answer is that both are live:

- A breadth question pulling weak matches ahead of strong ones is exactly the
  behaviour finding #7 exists to address, and this is the first time it has
  shown up as a number rather than an anecdote.
- Synthetic fixtures are shorter and thinner than real documents, and that alone
  may cost them rank regardless of relevance. The case runs against whatever
  archive it is pointed at, so this number is partly a property of this archive.

The same six real documents have outranked fixtures in all three runs, which is
at least consistent across measurements.

## Worth remembering

The corpus took three attempts because the first two diagnosed it wrongly. It
was never "too easy" in the sense of its documents being too distinguishable —
it was too small relative to the rank cut for difficulty to be expressible at
all. Asking "what would a random retriever score?" settles that in one line and
would have condemned the original design on the day it was written.

The eval also only became useful once it could fail. Two of its three runs
produced numbers that looked like measurements and were not.
