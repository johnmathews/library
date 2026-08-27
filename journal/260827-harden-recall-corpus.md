# The recall corpus failed its own acceptance criterion

**Date:** 2026-08-27
**Branch:** `chore/harden-recall-corpus`

## The measurement

`library eval-recall --write-baseline` ran for the first time against real
bge-m3 vectors, on the deployed host:

```
PASS control-unique-term      recall@10=1.00
PASS contract-clause          recall@10=1.00
PASS sender-named-bare-chunk  recall@10=1.00
PASS kind-scoped              recall@10=1.00
PASS date-scoped              recall@10=1.00
FAIL breadth-many-mentions    recall@12=0.50
5 passed, 1 failed, mean recall 0.917
```

Spec §8.6 holds this corpus to **mean recall@10 below 0.90**. It came out at
0.917, so the criterion failed and the corpus had to be made harder before any
recall number from it could justify anything.

The number understates the problem. Five of six cases scored *exactly* 1.00,
and the mean is really "five saturated cases plus one measurement, averaged".

## Why those five could not fail

Each expected a **single** document, against three or four hand-authored
distractors, scored at k=10. For a single-target case to lose recall, ten
documents must outrank the target — but the entire plausible cluster was four
documents, and the other 49 were topically unrelated filler (dentist, vet, gym,
opticians). So the target had nine free slots and no competition. The case
would have scored 1.00 against almost any embedder.

The branch's own handoff predicted exactly this ("4 of the 6 cases may be unable
to lose recall at k=10") and the first real run confirmed it. The prediction was
recorded as a risk to read the baseline against; it turned out to be the
headline finding.

## The rebuild

Two changes, both structural rather than cosmetic:

1. **Every case except the control now expects several documents.** Recall then
   degrades gradually — 3 expected, 2 retrieved is 0.67 — instead of being a
   coin flip on one document. `control-unique-term` keeps a single target
   deliberately: it is the canary, it is supposed to score 1.00, and its job is
   to make a broken embedder or harness announce itself.
2. **Every cluster is now larger than its case's rank cut.** Mortgage,
   bare-figure statements, boiler and parking all grew to 13 documents; the
   corpus went from 53 to 90. A pool that fits inside k cannot lose recall.

Both properties are now enforced, not just intended:
`test_only_the_control_case_expects_a_single_document` and
`test_every_case_competes_against_more_documents_than_its_cut`. Mutation-tested
— reverting `kind-scoped` to one expected document fails the first, and
shrinking the parking cluster back to four fails the second.

The 0.917 is **not** committed as a baseline. It describes a corpus that no
longer exists; recording it as the reference for future deltas would be
measuring against a fiction, which is the thing Ruling 2 refused to do when it
declined to invent a baseline at all.

## Two things the run exposed that the plan had not

**The haystack is the archive.** Every case scores over the whole `documents`
table, so on the deployed host the corpus competed against 259 real documents —
and six of them outranked expected documents in the breadth case. That is
realistic and good. It also means the number is not portable: the nightly runs
against a near-empty CI stack, where the same corpus is much easier, so a delta
between the two environments would read as a retrieval change when it is a
change of haystack. `recall-baseline.json` now carries a `measured_against`
block (`archive_documents`, `corpus_documents` — counts only, the file is
committed to a public repo) and `eval-recall` warns when the archive differs
from the recorded one by more than ten per cent.

**#6's benefit was never measured, and cannot now be measured this way.** Spec
§8.1's order was: baseline first, then chunk context headers, then re-measure.
No embedder was reachable while the branch was built, so the baseline step never
happened and #6 shipped first. `_seed_corpus` embeds through the real
`run_embed`, so the fixtures carry context headers — meaning the 2026-08-27 run
was a *post*-#6 measurement, not the "before". `sender-named-bare-chunk` scoring
1.00 is consistent with the header working, but is not evidence of it: there is
no header-free comparison to difference against, and that case could not have
failed regardless. A real #6 delta would need a way to embed the corpus without
headers, which does not exist. Recorded as unmeasured rather than quietly
treated as confirmed.

## Worth remembering

An eval that cannot fail is not a weak measurement, it is not a measurement.
The acceptance criterion existed precisely because synthetic corpora drift
toward being easy, and it did its job on the first run — but only because
someone ran it and read the per-case numbers rather than the mean. A mean of
0.917 looks like "nearly passing"; the per-case breakdown showed five cases
contributing nothing at all.
