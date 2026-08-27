# The number a random retriever would get

**Date:** 2026-08-27
**Branch:** `chore/harden-recall-corpus-again`

## The second failure

Earlier today the recall corpus failed its acceptance criterion at mean
recall@10 = 0.917, and was rebuilt: 53 documents to 90, every case but the
control made multi-target, every cluster grown past its rank cut. Re-measured
on the deployed host:

```
PASS control-unique-term      recall@10=1.00
PASS contract-clause          recall@10=1.00
PASS sender-named-bare-chunk  recall@10=1.00
PASS kind-scoped              recall@10=1.00
PASS date-scoped              recall@10=1.00
FAIL breadth-many-mentions    recall@12=0.42
5 passed, 1 failed, mean recall 0.9028
```

0.9028 against a 0.90 ceiling. Still failing, and **not one of the four rebuilt
cases had fallen**. The rebuild moved the mean by 0.014.

## The diagnosis

The first fix addressed the wrong half. "Cluster larger than k" is not the
condition; "cluster several TIMES k" is.

With `k=10` drawing from a 13-document cluster, ten of the thirteen candidates
come back regardless of how they are ranked. So a retriever choosing **at
random** scores 0.77 on that case. The range between a useless retriever and a
perfect one was 0.77 to 1.00 — and any case that got mildly lucky rounded to
1.00.

| cluster | blind recall at k=10 |
|---------|----------------------|
| 13      | 0.77                 |
| 20      | 0.50                 |
| 40      | 0.25                 |

The useful quantity is not the corpus size or the cluster size. It is **the
score a random retriever would get** — the floor of each case's range. That is
now computed directly in `tests/test_recall_scenarios.py` and capped at
`MAX_BLIND_RECALL = 0.35`. Clusters grew to roughly forty documents each
(corpus 90 to 201), putting every case's floor at 0.21 to 0.25.

Mutation-checked: reverting the parking cluster to its 13-document shape trips
both the new floor and the corpus-size floor.

## What the failed run still showed

Two things worth keeping, neither of which is a baseline.

**Retrieval was performing well above chance.** Those four cases scored 1.00
where a random retriever scores 0.71 to 0.77. The corpus could not show it, but
the system was not doing badly — the opposite.

**The first positive signal for #6.** `date-scoped`'s notices are identically
titled and their bodies never state a year, so "2022" reaches the embedding only
through the `sender · date · kind · title` context header. Scoring 1.00 there is
consistent with the header being used. It is not proof: with ten slots drawn
from thirteen candidates there was roughly a 42% chance of catching all three
blind. At a forty-document cluster that coincidence is far less available, so
the next run of that case is worth reading closely.

## Neither number is committed

0.917 and 0.9028 both describe corpora that no longer exist. Recording either as
`recall-baseline.json` would anchor every future delta to a fiction — the same
reasoning that refused to invent a baseline when no embedder was reachable at
all.

## Worth remembering

When an eval will not discriminate, ask what score a random answer would get
before adding more data. That single number diagnosed in one step what a whole
rebuild had failed to fix, and it would have caught the first design too: the
original corpus had a blind floor of about 0.77 the day it was written.

The corollary is that "make the corpus harder" was the wrong instruction to
myself. The corpus was not too easy in the sense of its documents being too
distinguishable; it was too *small relative to the rank cut* for any difficulty
to be expressible. Those need different fixes, and I spent a round on the wrong
one.
