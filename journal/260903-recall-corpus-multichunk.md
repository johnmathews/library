# The recall corpus could not have measured what it was about to be asked

**Date:** 2026-09-03
**Branch:** `worktree-eng-recall-corpus-chunking`

Issues #105 and #106 both said the corpus had to grow multi-chunk fixtures before
any ranking change could be judged. That was right. What neither anticipated is
that building those fixtures the way the issues describe would have produced a case
a random retriever scores **0.92** on.

## The measurement that was already there

#106 proposes an experiment: re-run the breadth case against a stack with no real
archive documents. `e2e-nightly.yml` has been doing exactly that every night for a
week — full stack, embedder included, fresh database. Six consecutive runs report
`breadth-many-mentions` at **0.58**, not the 0.33 in the committed baseline, each
printing `baseline was measured against 259 archive documents, this run against 0`.

Nobody had read it. The number appears nowhere in `docs/` or `journal/`.

That splits the shortfall in two: about 0.25 is real-document competition, and the
residual is not — at 0.58 every competitor is also a single-chunk fixture, so
neither "thin fixtures" nor "long documents crowd" explains it.

## Both issues had the mechanism wrong

#106's leading remedy is "consider only a document's best-scoring chunk when
ranking". `search.py` has done that since it was written — `DISTINCT ON
(document_id) ORDER BY distance`. Verified by execution: varying a document 1 → 5 →
50 chunks with its best chunk fixed leaves rank *and* RRF score byte-identical.

I then proposed my own mechanism — that many-chunk documents crowd others out of the
ANN prefetch window before the collapse — and measured it against production with
real query embeddings. **Refuted.** The 300-chunk window returns 87–162 distinct
documents for a pool needing 60. Nowhere near saturated.

What survives is an order-statistics effect: the collapse takes a *minimum* over a
document's chunks, and the minimum of 45 draws beats the minimum of one. Measured,
the top of the ranking is ~2.3× enriched in multi-chunk documents. That is real and
it is **not yet a defect** — a long document may top-rank because it genuinely
discusses the query, and one of the six displacing documents has a single chunk.

Separating those needs ground truth, which is the corpus. Which is why the corpus
came first.

## The trap in the corpus work

The blind-floor guard computed `min(k, |pool|) / |pool|` — documents drawn
uniformly. But ranking is by nearest chunk, so a document with *c* chunks gets *c*
draws. The two models agree exactly while every fixture is one chunk, which is why
the formula was right for as long as the corpus existed, and they diverge the moment
lengths vary — **in the passing direction**:

| expected doc size | true floor | old formula |
|---|---|---|
| 1 chunk | 0.250 | 0.250 |
| 5 chunks | 0.703 | 0.250 |
| 8 chunks | 0.835 | 0.250 |

`MAX_BLIND_RECALL` is 0.35. So the first fixture built the way #106 describes —
"the answer lives in a specific passage of a **long** document" — would have sailed
through a guard reporting 0.25 while a coin-flip retriever scored 0.70.

The replacement models the race properly (chunks as Exp(1) clocks, so
Plackett-Luce weighted by chunk count; Poisson-binomial DP under a substituted
integral). The thing that made it safe to swap in was the **regression anchor**:
on today's all-single-chunk corpus it reproduces all five existing floors to
machine precision, deltas ~3e-16. A model that moved today's numbers would have
been wrong, not stricter.

The rule that falls out, and it is the inverse of the obvious reading:
**crowders long, expected documents no longer than their crowders.**

## Mutation testing earned its place twice

Every new guard was broken on purpose. Two results were worth more than the
confirmations:

**The first mutation passed, and the guard was right.** I padded a 130-character
body by 200 characters expecting the declared count to go stale — but 330 characters
is still one chunk, so the declaration stayed true and green was the correct answer.
A mutation that does not create the violation proves nothing. *Check the mutation
before blaming the guard.*

**The second mutation passed, and the guard was wrong.** Moving the buried answer to
offset 1750 left the full sentence in one chunk — so the whole-sentence check passed
— while the 200-character overlap carried its opening words back into the previous
chunk. Both chunks held part of the answer, which is the duplicated signal the guard
exists to stop. It now checks the sentence *and each of its ends*, verified red at
three straddle offsets where the original caught one.

So the companion rule: *check the mutation first, but don't stop there.*

## What the corpus is now

201 documents / 201 chunks → **252 documents / 450 chunks**, seven cases.

The chunk total matters on its own: `semantic_search` prefetches
`top_k * 5 * VECTOR_CANDIDATE_FANOUT` chunks — 300 at k=12 — before collapsing. At
201 chunks that LIMIT never bound, so on a clean stack the vector leg was an **exact
global argmax**, not the approximate retriever that ships. The deployed archive
(1300 chunks) binds and CI did not, and that structural difference has been showing
up in the numbers as though it were a haystack effect.

Blind floors: breadth 0.211 → **0.074**; the new passage case 0.2516.

Measured, on both stacks:

| case | before | after | floor |
|---|---|---|---|
| breadth-many-mentions | 0.33 host / 0.58 CI | **0.17** | 0.074 |
| passage-buried-clause | — | **0.33** | 0.252 |
| the other five | 1.00 | 1.00 | 0.23-0.25 |
| **mean** | 0.889 host / 0.931 CI | **0.786 both** | |

The criterion is met, and met in both environments rather than only on the host.

## The result nothing predicted

Both stacks now return the same twelve documents **in the same rank order** —
checked marker by marker. The 266 real archive documents contribute nothing to
the breadth case's top 12, because the corpus's own crowders outrank all of them.

That dissolves the confound the investigation started from: the eval measures the
corpus, not the host it runs on, and 0.889-versus-0.931 cannot recur.

The cost, stated rather than left to be found: `breadth-many-mentions` no longer
measures real-archive interference, which is what #105 was *about*. It now
measures whether retrieval can separate solar paperwork from other installation
paperwork — harder, legitimate, different.

## The passage case had to be fixed twice, and the first version measured nothing

Built as planned, it scored **1.00** on both stacks. The crowders discussed
scope, charges and response times and never mentioned notice, so the question's
own vocabulary appeared in exactly the documents that answered it. Finding them
was lexical. The corpus already had the technique — "same sender, same kind,
adjacent dates, overlapping vocabulary" — and this case had not used it. All 24
crowders now discuss notice with different periods about different things:
1.00 → 0.50. Then three expected documents rather than two, because recall over
two targets can only be 0, 0.5 or 1.0: → 0.33.

Worth being explicit, since "adjust until the number looks right" is the failure
this branch is about: the first change fixed a defect (a case measuring nothing),
the second improved granularity. Neither targeted a number, and I stopped at two.

## Still open, deliberately

- Whether the chunk-count enrichment is bias or genuine relevance. That is what the
  rebuilt corpus exists to answer, and answering it is the next piece of work.
- Whether #6's contextual header *costs* cross-sender breadth recall. The breadth
  case misses **0 of 4** third-party documents while finding 7 of 8 from the
  installer, and the header injects the sender into every chunk. Unsettleable today:
  `_seed_corpus` embeds through the real `run_embed`, so there is no header-free
  path to difference against.
- `hnsw.ef_search` defaults to 40 and is set nowhere, so a 250-row prefetch is
  silently truncated on the index path. Latent — the planner takes a sequential scan
  at 1300 chunks — and it arms as the archive grows.
