# Ask — semantic question answering

**Status:** active. **Last updated:** 2026-09-03 (**the cause of #105 is found, and it is the #6 context header.** Two measurements settle what the corpus rebuild earlier today made askable. First, chunk count is NOT the mechanism: truncating every crowder to its own first chunk — retained text byte-identical, only the extra draws removed — left `breadth-many-mentions` at exactly 0.17, the same two documents (#168, closed). Second, the header IS: embedding each expected document with and without its `sender · date · kind · title` line puts all eight installer documents CLOSER to the query and all four third-party documents FURTHER — a perfect partition by sender. §1.2's "#6's retrieval benefit is unmeasured" paragraph is replaced: it is now measured in both directions and net-unknown, a real gain on metadata-identified questions and a real cost on topic questions spanning senders. Also lands #161: `hnsw.ef_search` defaults to 40 and silently truncated the 250-row ANN prefetch on the index path; now SET LOCAL to the fanout, with a test that reds when the SET is removed.) **the recall corpus was rebuilt so it can measure chunk-count effects at all, and the acceptance criterion is restated as a property of the corpus PLUS the archive it is scored against.** §1.2's *Measuring recall* gains the fact a cold reader could not get from it: what the eval is scored AGAINST. The nightly had been reporting mean 0.931 — above the 0.90 ceiling — on a zero-archive stack every night for a week while the committed baseline read 0.889, and nothing here said the two could not be compared. The measurement table grows from three rows to six, carrying the archive size per row. The corpus is 201 -> 252 documents and 201 -> 450 chunks, with 48 long crowders and a new `passage-buried-clause` case; `breadth-many-mentions` moves 0.33 -> 0.17 and the mean 0.889 -> 0.786, now met in BOTH environments. Two cases fail at baseline by design. After the rebuild the two environments return the same twelve documents in the same rank order, so the eval measures the corpus rather than the host — at the cost, stated, that the breadth case no longer measures real-archive interference. `recall-baseline.json` gains `corpus_chunks`, because the retriever ranks chunks and the corpus more than doubled in chunks while barely moving in documents. The PR-gate claim is narrowed: `compose-smoke` DOES start an embedder, it just never waits for the model to load. §1.7.0 of frontend.md is collapsed to a pointer here, and `Covers:` now declares the seeding, chunking, search and workflow paths this section documents — #137.) **the comparative scenario was run, and it does not support the rule it was built to measure.** §1.2's *Measuring disclosure* now carries three live runs instead of the prediction it carried this morning: the scenario passes with the cross-call rule and passes with it reverted, so it cannot be evidence for the rule; and the model qualifies the comparison unprompted, so the consequence #155 predicted was not observed. The rule is kept and its justification is **downgraded in place** — it makes an implicit behaviour explicit, rather than fixing an observed defect. A caveat-requiring scorer was built and abandoned: three answers used three vocabularies and a pattern list built from two false-failed the third. The invocation block gains `--show-answer`, and the macOS Keychain caveat that makes the containerised backend unusable.) Earlier the same day (**the disclosure rule now spans a comparison** (#155, first half). §1.2's coverage section gains a second obligation. The existing rule governs one tool result, so a comparative question answered by two `sum_amount` calls can have both sides individually disclosed correctly while the comparison between them is an extraction artefact; the prompt now carries a **separate** bullet requiring the model to name any difference in the two results' `excluded` blocks before stating a trend, distinct rather than folded in because folded in it reads as being about a single result. The eval gains a sixth scenario, `comparative-uneven-coverage`. §1.10 item 11's first bullet is narrowed: the *disclosure* half of the comparative gap is closed, the missing *tool* is not. `Coverage` is unchanged and still partitions one matched set. Stated plainly in §1.2 and below: the new scenario has never been run, so nothing here claims the rule works.) Earlier the same day (**Ask's money answers now read `spend_facts`** (#136). §1.2's coverage section gains *Where a money total comes from* — sign, summable kind and payment identity — and its exclusion list gains `not_summable_kind` and `duplicate_payment`, with the include chain's ORDER stated as the contract rather than an implementation detail. The tools gain a `facets` filter and the archive-context block gains the facet vocabulary (keys **and** value keys, because a facet filter is a pair). §1.10 item 11 is rewritten: it was the statement of this gap, and is now the three things around it that remain open — no comparative tool, document-level rather than line-level facet filtering, and `document_date` bounding. **The disclosure eval's own numbers are corrected**: eight exclusion reasons, not six, and five unmeasured, not three — and its seeded documents now carry an `amount_kind`, without which every spend scenario would have totalled nothing while still scoring. `compare_to_series` is deliberately NOT rebuilt here; see [roadmap.md](roadmap.md) §1.1. **Fix round 1**, from this pass's own documentation audit — three claims written earlier in this same edit were wrong and are corrected: the archive-context paragraph said every list in the block is *sorted*, which the new facets line (ordered by the curator's ordinals at the database) is not; the money paragraph said the three fixes make an Ask total *agree* with a chart, which overstates it — line-level facet scoping and currency conversion still differ, and it now says so and points at item 11; and the disclosure-eval paragraph claimed both new reasons need a shape `SeedDoc` cannot express, which is false — both are already expressible, and the real reason they are unwritten is that a scenario needs a live model run to be worth anything.) Earlier the same day (§1.2's FTS-leg paragraph now records that the leg is accent-insensitive on both the index and query sides (migration 0039), and states the asymmetric-degradation failure that preceded it — the FTS leg contributed nothing for an accented term while the vector leg still ranked, so a hybrid answer looked plausible rather than empty (#138).) Earlier: 2026-08-31 (the legacy series stack was deleted, and this is the largest single edit in that pass. **§1.7 is removed** — the `compare_to_series` tool and the series engine behind it — and §1.8 onwards deliberately keeps its number, with a seam note at the gap. §1.1's comparative-question type now says plainly that Ask has no tool for it. The tool diagram loses its `compare_to_series` branch and §1.2's numbered tool list goes five → four. **§1.2's coverage contract is narrowed to `query_documents` and `semantic_search`**, and the distinction between the two — a partition versus a reach figure — is now stated rather than implied. `library eval-disclosure` is five scenarios, not six, exercising three exclusion reasons, not four; the single measurement on record predates the removal and has not been re-run. The three `LIBRARY_SERIES_*` env rows are gone. §1.8 gains the write tool's **third** refusal — an allocated `amount_total` now returns a named error instead of 500ing — which the guardrail list omitted. **Two stale claims in *Where it runs* are corrected**: the recall step no longer runs "after the Smart Groups journey and its did-it-actually-run assertion" (that journey is deleted; the job is renamed `retrieval-recall`), and `recall-baseline.json` **does** exist — committed by `012b013` on 2026-08-27, which predates this branch, so that claim has been false for four days. §1.10 loses item 9, renumbers, and **gains item 11**: Ask's money totals are computed from the model the chart engine replaced — no `amount_kind`, no payment identity, no facet filter — so a chart and an Ask answer can disagree and `coverage.excluded` has no bucket that would say so. **Fix round 1:** item 11's payment-identity bullet said an *unmerged* pair is double-counted, which implied Ask honours a merge. It does not honour one — the qualifier is dropped and the bullet now names the three `WHERE` clauses `sum_amount` actually has.) Earlier: 2026-08-27 (the recall corpus PASSED its acceptance criterion on the third measurement, mean recall@10 = 0.889 against the 0.90 ceiling, and `recall-baseline.json` is committed for the first time - the 201-document run, against 259 archive documents, recorded in its `measured_against` block. Two substantive results beyond the criterion. FIRST EVIDENCE FOR #6: `sender-named-bare-chunk` and `date-scoped` are built so the discriminating fact (sender, year) exists ONLY in metadata and reaches the embedding by exactly one route, the context header #6 prepends; both scored 1.00 where a random retriever had about a 1.2% chance of catching all three expected documents in ten slots. That is an inference from construction, not the before/after delta spec 8.1 wanted - still impossible, since `_seed_corpus` embeds through the real `run_embed` and no header-free path exists - but it is well powered and it is the first positive result #6 has produced. SECOND: `breadth-many-mentions` is the only failing case at 0.33, and half its returned slots went to real archive documents only loosely related to the question, displacing eight genuinely relevant ones - the behaviour finding #7 exists to address, visible in a number for the first time, though synthetic fixtures being shorter than real documents may cost them rank independently. See `journal/260827-first-recall-baseline.md`. Earlier (2026-08-27): (the recall corpus was measured a SECOND time and failed its acceptance criterion again, at mean recall@10 = 0.9028 against the 0.90 ceiling, with none of the four cases rebuilt earlier the same day having fallen. The second failure found the real defect, which is arithmetic and not authorial: a candidate cluster must be several TIMES the rank cut, not merely larger than it. At k=10 over a 13-document cluster, ten of thirteen candidates return whatever the ranking, so a retriever choosing at RANDOM already scores 0.77 and the measurable range is 0.77-1.00. Clusters grown to roughly forty documents each (corpus 90 to 201), putting blind recall at 0.21-0.25; `tests/test_recall_scenarios.py` now computes each case's blind recall and fails above `MAX_BLIND_RECALL` (0.35), mutation-checked by reverting the parking cluster to its 13-document shape. Neither 0.917 nor 0.9028 is committed as a baseline - both describe corpora that no longer exist. One positive reading retained: those four cases scored 1.00 where blind scores 0.71-0.77, and `date-scoped` can only get its year from #6's context header, which is the first positive signal #6 has produced - not proof, since ten slots from thirteen candidates gave a ~42% chance of catching all three blind. `eval-recall` now seeds 201 documents and so makes 201 embedder calls per run. See `journal/260827-recall-corpus-blind-floor.md`. Earlier (2026-08-27): (the recall corpus was measured for the first time against real bge-m3 vectors and FAILED its own acceptance criterion, so it was rebuilt. `library eval-recall --write-baseline` on the deployed host returned mean recall@10 = 0.917, above the 0.90 ceiling §1.2 holds this corpus to, with five of six cases at exactly 1.00 and only `breadth-many-mentions` moving: each of those five expected a SINGLE document against three or four distractors, so the whole candidate pool sat inside the ten-slot cut and the case could not lose recall however retrieval behaved. `recall_scenarios.py` grew from 53 documents to 90, every cluster now exceeds its case's rank cut, and every case except the control (`control-unique-term`, which is meant to sit at 1.00 and exists to catch a broken embedder or harness) expects several documents so recall degrades gradually; both properties are now enforced by `tests/test_recall_scenarios.py`. That 0.917 is recorded as the REASON for the rebuild, not as a baseline — it describes a corpus that no longer exists, and no `recall-baseline.json` was committed from it. `recall-baseline.json` now also carries a `measured_against` block (`archive_documents`/`corpus_documents`, counts only — the file is public) because every case scores over the whole `documents` table, so the same corpus is far harder on the deployed archive (259 real documents competing, six of which outranked expected ones) than on a fresh CI stack; `eval-recall` warns when a run's archive differs from the recorded one by more than ten per cent. Also recorded: #6's retrieval benefit was never measured and cannot now be measured this way — `_seed_corpus` embeds through the real `run_embed`, so the fixtures carry context headers and the 2026-08-27 run was a post-#6 measurement, not the "before" spec §8.1 intended to compare against. See `journal/260827-harden-recall-corpus.md`. Earlier (2026-08-27): (final whole-branch review fix wave on this same branch, nine fixes: (1) `.github/workflows/e2e-nightly.yml`'s `Measure retrieval recall` step moved to run AFTER the Smart Groups journey and its "did it actually run?" assertion, with `continue-on-error: true` added — previously it ran BEFORE the journey with no `|| true`, so it aborted the whole job on the very baseline failures the corpus is deliberately built to contain (`sender-named-bare-chunk`'s own docstring says "expected to fail at baseline"), reinstating the never-runs-anywhere defect this workflow exists to fix. The step now reports recall and does not gate; this section's *Where it runs* paragraph and the workflow's own header comment both wrongly claimed "a recall regression reds the nightly run" and are corrected below. (2) `_seed_corpus` (`library.cli`) now raises if any seeded document produced zero chunks after `run_embed` — verified by execution that with the embedder disabled, seeding previously "succeeded" having created zero chunks (`run_embed` is fail-open by design), so `eval-recall` would have silently scored the FTS leg of RRF alone and reported it as retrieval recall; covered by a new test in `tests/test_recall_seed.py` that forces the embedder off. (3) `ASK_SYSTEM_PROMPT_TEMPLATE`'s coverage rule now names `semantic_search` alongside `query_documents`/`compare_to_series` and states the `unembedded` disclosure obligation as a MUST — previously only the tool description carried that obligation, while the system prompt (the stronger surface) actively implied `semantic_search` carried no coverage at all. (4) A new test drives the Ask write tool's confirmed `update_document_metadata` call through a header-field edit and asserts the re-embed hook (`ask/engine.py`, beside `header_fields_changed`) defers exactly one `embed_document` job, plus a non-header companion asserting none — proven by mutation: deleting the two-line hook made the new test fail (it did not before this pass, across 71 passing tests). (5)-(9) minor: a misleadingly-named empty-payload test renamed (`test_a_patch_that_changes_nothing_defers_nothing` → `test_an_empty_patch_defers_nothing`) plus a same-value-patch behaviour test added, both in `tests/test_chunk_context_header.py`; `library eval-recall --only <case> --write-baseline` is now refused (it would silently overwrite the baseline with just that one case), matching the existing `--ask` guard; `_top_k_arg`'s missing-argument path is now clamped through `ask_search_max_top_k` exactly like an explicit value (an operator-configured default above the ceiling previously bypassed it entirely), and the `top_k` tool-description text no longer asserts a specific default it cannot guarantee; `semantic_search` now strips `review_status` after the shared `_filters_from_args` call, so a model emitting it (the schema does not declare it — see the `_REVIEW_STATUS_PROPERTY` comment) cannot silently narrow a search this tool's coverage block has no way to explain; and a new test in `tests/test_embed_comments.py` asserts comment chunks receive the same document header as content chunks (spec §8.5), which nothing previously checked. Nothing about the corpus, the acceptance criterion, or the missing-baseline state changed in this pass — see `journal/260827-retrieval-reach-fix-wave.md`. Earlier (2026-08-27): (retrieval reach (Plan B, findings #5/#6/#7/#15): §1.10 item 6 — "`semantic_search` takes no metadata filters" — is retired and the list renumbered, since the tool now accepts the same `_FILTER_PROPERTIES` as `query_documents`/`compare_to_series` (not `review_status`, which only a tool that can report a `filtered_review_status` drop is offered) and a clamped `top_k` (`LIBRARY_ASK_SEARCH_MAX_TOP_K`, default ceiling 50; non-positive values clamp to `1` rather than silently slicing from the end of the ranking). §1.2 step 2 documents that surface and the result's new `coverage` block (`matched`/`returned`/`unembedded`). Two new limitations recorded: §1.10 item 10 (chunk context headers, embedding a `sender · date · kind · title` line per chunk since migration `0031`, go stale until a re-embed — deferred automatically when one of those four fields is edited, but pre-`0031` chunks need `--include-existing`) and item 11 (`matched` counts documents, not passages — the honest reading of finding #14, not a fix for it). New *Measuring recall* subsection (§1.2, beside *Measuring disclosure*) documents `library eval-recall`'s two layers, where it runs (nightly, not a merge gate — no embedder in the PR gate and no arm64 TEI image), and the corpus's own acceptance criterion (baseline mean recall@10 below 0.90, spec §8.6). **No baseline has been measured**: this development machine is arm64 with no embedder reachable, so `library eval-recall` has never been run against real bge-m3 vectors, `recall-baseline.json` does not exist in this repository, and the chunk-context-header change's effect on recall (the `sender-named-bare-chunk` case it was built to move) is consequently unverified — a design intent, not a measured result. See `journal/260827-retrieval-reach.md`.). Earlier (2026-08-27): (docs(ask): corrected the stamp's stale claim that the disclosure rule's effect on real answer wording is unmeasured — `library eval-disclosure` (new §1.2 subsection, *Measuring disclosure*) now measures it on demand and was run once against an isolated scratch database, with all six scenarios, including the control, passing; that is evidence, not continuous verification, since CI holds no model credentials to gate on it. The new subsection also documents what the eval measures, the exact invocation, why it is a CLI command rather than a test, that it seeds and rolls back rather than touching real data, and why the control scenario exists. No other prose in this document was touched by this pass.). Earlier (2026-08-27): (final whole-branch review fix wave, three doc-only corrections: (1) §1.2's `compare_to_series` reasons list said the four reasons are "not a chained refinement of one aggregate like the three above" — false for the first three, which chain exactly as `sum_amount`'s reasons do (`no_amount` → `other_series_group` → `other_currency`, each "survived every earlier gate, fails this one"); only `manually_excluded` is the structural exception. Rewritten, because the old wording could invite a future reader to "fix" the code into independent gating — the same double-counting bug a sibling branch already shipped and had to fix. (2) The adjacent `other_currency` parenthetical said dropped documents are "still listed in `other_currencies`" — wrong: `other_currencies` skips a `NULL` currency by construction while the `other_currency` exclusion count does not, so an amount-bearing, currency-`NULL` document lands in `excluded.other_currency` but is never named in `other_currencies`; corrected. (3) §1.10 gained a new item 10 for the early-`status="insufficient"`-predates-overrides gap: §1.7 already explained it in full, but §1.10 — the limitations register a reader actually scans — had nothing pointing there, wrongly implying `compare_to_series` carries no coverage limitation. `src/library/series.py`'s `_insufficient` also had its `currency`/`other_currencies` threaded through on the post-bucketing call site (previously hardcoded `null`/`[]` even once a currency bucket was chosen); a code change, covered by a new test, not itself a doc correction. Earlier (2026-08-26): (a code review on this same branch caught an undisclosed gap this task's first pass missed: `summarize_series`'s early `status="insufficient"` exit — taken before a currency bucket is chosen, when too few documents even match the caller's filters — returns before any PIN/EXCLUDE override is resolved, so on that path `coverage`'s numbers, and `status` itself, can predate an override that would have changed them. Pre-existing `summarize_series` behaviour, not introduced by this branch; only the coverage numbers now surfaced there are new. §1.2's optional-`coverage` paragraph now flags this instead of implying unconditional trust, and §1.7's *Coverage* subsection explains it in full. The `SeriesCoverage` docstring in `src/library/series.py` was narrowed to scope its "invariant holds for every combination of PIN and EXCLUDE" claim to the paths where overrides actually run — a docstring-only change, no logic touched). Earlier (2026-08-26): §1.2: the *Coverage and trust on structured results* subsection now also covers `compare_to_series`, which carries the same `coverage` block on the same terms as `query_documents`; documented its four exclusion reasons — `no_amount`, `other_series_group`, `other_currency`, `manually_excluded` — and that the last of these comes from a persisted PIN/EXCLUDE override rather than a chained filter, with the partition invariant holding across every override combination; §1.7: new *Coverage* subsection describing the series' deliberate narrowing to one `(sender, kind, currency)` triple, now reported rather than silent, and that `review_status` still isn't offered as a filter there even though `needs_review` is reported; §1.10: item 10 removed — it asserted `compare_to_series` reports no coverage, which this branch made false since the tool only ever reaches an emergent series summary, which always carries a populated block). Earlier (2026-08-26): §1.2: new *Coverage and trust on structured results* subsection — every `query_documents` aggregate now returns a `coverage` block (`matched`/`included`/`excluded`/`needs_review`) beside its rows, and the system prompt requires the model to disclose a non-empty `excluded` or a non-zero `needs_review`; §1.10: three new limitations — `semantic_search`'s missing metadata filters, `sum_amount`'s document-not-period coverage, and no-answer citation suppression keyed on the `_NO_ANSWER` sentinel. Earlier (2026-08-25): §1.2 *Archive context*: the system prompt now names the user, their recipient names, their free-text **About you** notes (Settings → Ask) and the archive's kind/tag/project/matter/sender vocabulary; `query_documents` and `compare_to_series` filter by `recipient_contains`, `projects`, `matters`, `tags`. Earlier (2026-08-22): the composer is one flat full-width bar — the nested pill is gone. Earlier (2026-08-21): adaptive thinking on the tool loop, with the answer-token and tool-turn caps raised to match). Earlier (2026-08-21): prompt caching inside the tool loop and token accounting that counts cached tokens; document layout is the DEFAULT at `lg+` with the collapsed rail's actions in the thread bar; per-table horizontal scroll containment. Earlier (2026-08-20): `LIBRARY_ASK_LLM_BACKEND` — Ask's tool loop and title call can run against a Claude subscription instead of the metered API; §1.4. Earlier (2026-07-21): two-screen, route-driven Ask (Option B) and the desktop fixed-height fill; §1.6.)
**Last verified:** 2026-09-03 — method: **executed** on the deployed host against the real archive. (a) `library eval-recall` with every crowder truncated to one chunk: `breadth-many-mentions` 0.17 in both arms, same two documents. (b) `embed_texts` on each expected document with and without its header, cosine distance to the embedded question: 8 closer, 4 further, split exactly by sender; numbers in the table below and in #164. (c) all 466 documents ranked by nearest chunk for the same query, giving the 1-33 versus 308-456 split. The `hnsw.ef_search` change is covered by two tests, one of which was observed to fail with the SET removed. Full backend suite green; the eval numbers themselves are unchanged from this morning's baseline.
**Covers:** src/library/ask/, src/library/embedding/, src/library/search.py, src/library/cli.py, .github/workflows/e2e-nightly.yml

Ask lets you put a natural-language question to the archive and get a prose
answer with citations — e.g. *"do I have a travel allowance in my job
contract?"* or *"who was my energy provider last year?"*. It runs in-app
(`/ask` in the web UI, `POST /api/ask` in the REST API); document text never
leaves the host for indexing (local embeddings), and only the final answer
step calls Claude.

## 1.1 The three question classes

Ask handles three different shapes of question, and the answer engine picks the
right tool per question:

1. **Content questions** ("what does this document say?") — e.g. the travel
   allowance clause. Answered by **semantic retrieval**: the question is
   matched against document *contents*, including paraphrase and cross-language
   synonyms (a Dutch "reiskostenvergoeding" clause answers an English "travel
   allowance" question).
2. **Aggregation questions** ("who / how many / how much / over time") — e.g.
   the energy provider. Answered by a **structured query** over the extracted
   metadata columns (`sender`, `kind`, `document_date`, `amount_total`), not by
   reading text.
3. **Comparative questions** ("is this bill higher than usual?", "how does it
   compare to last year?", "are my bills going up?") — **Ask has no dedicated
   tool for these any more.** A `compare_to_series` tool answered them against
   the statistical distribution of recurring documents from the same sender and
   kind until 2026-08-31, when it was deleted with the legacy series stack: its
   answer was known-wrong in ways it could not report (see §1.10 item 11). Such a
   question now reaches `query_documents` and `semantic_search` like any other,
   and gets a comparison the model assembles from rows rather than a computed
   distribution. The comparison built on the model that replaced it lives in the
   chart engine ([charts.md](charts.md)); rebuilding a tool over it is on the
   [roadmap](roadmap.md).

## 1.2 How it works

```
question ─▶ Claude (tool-use loop) ─┬─▶ semantic_search ──▶ hybrid retrieval ─┐
                                    │                       (FTS + vector RRF) │
                                    ├─▶ query_documents ───▶ structured query ─┤
                                    │                       (sender/kind/date) │
                                    └─▶ get_document ──────▶ full text +      │
                                                            comments (one doc) │
            answer + citations ◀───── Claude (answers from tool results) ◀─────┘
```

1. **Embedding (indexing).** After OCR + extraction + markdown generation,
   each document's text is split into overlapping chunks and embedded with
   **bge-m3** (1024-dim, multilingual) by a local **embedder** sidecar
   (HuggingFace text-embeddings-inference). Vectors are stored in
   `document_chunks` with an HNSW index (cosine). This is a pipeline stage:
   `received → ocr → extract → markdown → embed → indexed`. When a markdown
   layer exists, chunks are drawn from the per-page markdown and each chunk
   carries its `page_number`; without one, chunks come from `ocr_text` with
   `page_number = NULL`. Embedding is best-effort — a document that fails to
   embed still reaches `indexed` and stays searchable by full-text.
2. **Hybrid retrieval** (`semantic_search`). At query time the question is
   embedded and run two ways: vector k-NN over `document_chunks` and the
   existing bilingual Postgres full-text search. The two rankings are fused with
   **Reciprocal Rank Fusion** (RRF, k=60), so exact-term matches (invoice
   numbers, names) and paraphrase matches both surface. Each result carries its
   nearest chunk as the citation excerpt and, when the chunk came from a
   page-aware document, its `page_number`. Each result also exposes the
   document's metadata to the model (title, **sender**, **recipient**,
   document date) so it can attribute and group answers. The FTS leg uses `ts_rank`
   **length normalization** (Postgres bitmask `1`), so a long, multi-topic
   document cannot out-rank a short on-topic invoice merely by repeating the
   matched term — score reflects match *density*. PostgreSQL's prose describes
   the divisor as "1 + the logarithm of the document length"; measured against a
   running database it is `log2(length + 1)`, which grows *faster* than the
   saturating numerator, so at equal match density the ordering **inverts** and
   the shorter document wins outright. That matters for #106: the vector leg's
   max-over-chunks collapse favours long documents, this leg favours short ones,
   and RRF therefore self-corrects part of that bias — so a remedy applied to the
   vector side alone risks over-correcting past neutral. The
   FTS leg is **accent-insensitive**: both the generated tsvector columns and
   the query are folded through `unaccent` (migration `0039`), so the plain-ASCII
   spelling a person naturally types finds an accented document. Before that,
   the two legs failed *asymmetrically* on an accented term — the FTS leg
   contributed nothing while the vector leg (a multilingual embedding, never
   accent-sensitive) still ranked — so a hybrid answer looked plausible rather
   than empty and the gap was easy to miss. For
   long documents, Ask retrieval also pulls the `LIBRARY_RETRIEVE_CHUNKS_PER_DOC`
   nearest chunks per result (best-first) and joins them into the excerpt with a
   `[…]` separator, so multi-topic answers see more than the single best passage.
   The per-document candidate ranking and anti-crowding guarantee are unchanged
   (one chunk per document still drives fusion).

   **Scoping the search.** `semantic_search` accepts the same metadata filter
   properties as `query_documents` — `kind`,
   `sender_contains`, `recipient_contains`, `projects`, `matters`, `tags`,
   `facets`, `date_from`/`date_to` (§1.2 step 3) — so a content question naming a year or
   a sender can be scoped instead of searching the whole archive and relying on
   ranking alone. It does **not** accept `review_status`: that filter is only
   offered to a tool that can report what it removed (a `filtered_review_status`
   coverage reason), and `semantic_search` has no such reason to report — the
   tool just wouldn't honour the promise the filter makes elsewhere. It also
   accepts `top_k`, clamped into `[1, LIBRARY_ASK_SEARCH_MAX_TOP_K]` (default
   ceiling 50; non-positive values clamp to `1`) rather than rejected, so the
   model can ask for more than the shipped default of 10 on a "find every
   document about X" question without risking an unhandled negative value
   (`ranked[:top_k]` slices from the *end* on a negative `top_k`, so an
   unclamped `-1` would silently drop most of the ranking rather than error).
   Every result also carries a `coverage` block: `matched` is how many
   documents passed the call's filters, `returned` is how many hits actually
   came back, and `unembedded` is how many of the matched documents have no
   chunks at all — invisible to vector search regardless of what the query
   says. `matched: 0` means the filters excluded everything; `matched: 40,
   returned: 0` means those 40 documents genuinely don't say this; a non-zero
   `unembedded` means the negative is partly a technical gap, not a content
   fact (§1.10 item 10).
3. **Structured query** (`query_documents`). Aggregations over the extracted
   columns: distinct senders, summed amounts (by currency, optionally grouped by
   sender/kind), and document lists. Filters are the list API's
   `DocumentFilters` vocabulary: `kind`, `sender_contains`, `recipient_contains`,
   a date range, the curated label vocabulary (`facets`, a `{facet_key:
   value_key}` object; different facets AND-compose, and a document holds one
   value per facet), and the user's own organisation — `projects` and `matters`
   (a document in *any* of the given slugs matches) and `tags` (a document must
   carry *all* of them). Blank strings and empty lists — which the model does
   send — are treated as absent, and so is a `facets` entry whose key or value
   is blank: a filter coerced to `""` would match no document and narrow a total
   to zero, which reads exactly like a real answer of nothing. Every row carries the contributing document
   ids for citation; document refs also expose `title`, `sender`, `recipient`,
   `kind`, `document_date`, and `amount_total`. Aggregation citations have no text location, so their
   `page_number` is always `None`.

   **Quotes are not expenditure.** Documents of kind `quote` (estimates not yet
   incurred) are **excluded from `sum_amount` spend totals by default**, so a
   question like "what have I spent in the last 3 months?" ignores quotes. To
   total quotes specifically, the model passes `kind="quote"` (also surfaced via
   the concept→kind hints `quote`/`estimate` → `quote`). The exclusion lives in
   `structured_query.sum_amount`, not the prompt, so it holds regardless of how
   the question is phrased.
4. **Full-document read** (`get_document`). Once another tool has located a
   document, this reads it in full: structured fields, its **comments** (see
   §1.9), and its text (joined per-page markdown, falling back to `ocr_text`),
   truncated to `LIBRARY_ASK_GET_DOCUMENT_MAX_CHARS` (default 8000 chars, with
   a `text_truncated` flag on the result). A document's comments are the user's
   own dated notes about it and are **authoritative personal context** — the
   system prompt tells the model to trust them over inference from the
   document text alone (e.g. a comment saying "this is my current house"
   settles which address is current). Comments are also independently
   discoverable via `semantic_search`, since each is embedded as its own chunk
   (§1.9) — `get_document` is for reading a *located* document in full, not
   for finding one.
6. **Answer** (`ask.engine`). Claude (`ask_model`, default
   `claude-opus-4-8`) is given the four read tools above — plus a fifth,
   **`update_document_metadata` write tool** (§1.8) — and a bounded number of turns
   (`ask_max_tool_turns`). It is instructed to answer **only** from tool results,
   to say plainly when the archive doesn't contain the answer, and to cite the
   document ids it used. The endpoint returns the answer, the citations
   (document id + title + `page_number`), the tools used, and the estimated cost.
   The web UI **collapses the citations by default** behind an `AppDetails`
   disclosure ("Citations (N)") under each answer, and renders each citation as
   `Title, p. N` when a page number is available and deep-links the PDF iframe to
   that page (`#page=N` in the URL fragment); citations from documents without a
   markdown layer show only the title.

**Archive context.** The model is told who is asking and what the archive
calls things. Each turn, `library.ask.context` reads the user's name
(`display_name`, falling back to the username), the recipient names linked to
that user (`Recipient.user_id` — the "this is me" link ingestion maintains),
and the archive's vocabulary — every kind (slug and name), up to 100 tags
(alphabetical by slug), the active projects with their descriptions and the
active matters with their classifier hints (each capped at 50, alphabetical by
slug: Ask's own write tool can create both, so nothing else bounds them), the
curated facet vocabulary (up to 200 values across all facets, in the curator's
own ordinal order — **both** the facet keys and their value keys, because a
facet filter is a `{key: value}` pair and keys alone would leave the model
guessing at values), and
the forty most frequent senders — and renders it as an "Archive context" block
appended to the system prompt. The prompt tells the
model to use those exact slugs in tool calls and to read "my"/"me"/"I" as that
user, so "who was my energy provider" can filter on the user's own recipient
names rather than answering for the whole household, and "what did the
kitchen project cost" can pass the real project slug instead of guessing one.
Archived projects and matters are omitted (nobody files under them any more).

The block also carries the user's own **"About you" notes** when they have
written any (**Settings → Ask**, `PUT /api/settings/ask-profile`,
[api.md §1.10.11](api.md)): free text for the facts no document states — who
lives with them, the current address, whose car the Volvo is, where they work,
when they moved. It is rendered as an "About the user" bullet and the prompt
frames it exactly as it frames document comments: authoritative personal
context, to be trusted over inference from the documents. Blank notes render
nothing. This is the generalisation of per-document comments to the whole
archive — a comment settles one document's question ("this is my current
house"); the notes settle the questions every turn starts from.

The block is **byte-stable between requests** by construction — every list is
deterministically ordered and no counts or timestamps appear in it — because it
sits inside the cached prompt prefix. Most lists are sorted at render time,
because the queries behind them carry no `ORDER BY`; the facets line is the
exception, ordered by the curator's own ordinals at the database instead, which
is why it reads in that order rather than alphabetically. It shares the static prompt's cache breakpoint rather
than taking one of its own: it only changes when the taxonomy does, and the
cache TTL is minutes, so a separate breakpoint would spend one of the four the
API allows for nothing. At personal scale it is a few hundred to a couple of
thousand tokens, billed at the cache-read rate after the first call of a
session. Both backends receive it (it is part of the single system string the
subscription transport takes). `run_ask` takes it as `archive_context` and
answers without it when the caller passes none, which is what the engine tests
do.

**Image attachments.** `ask_model` (`claude-opus-4-8`) is multimodal, so
a question may carry up to 5 base64 images (see [api.md §1.11](api.md)). They are
rendered as image content blocks on the question turn alongside the text, and the
system prompt tells the model to read them as evidence and combine them with tool
results. Attachments persist in `ask_turns.messages`, so they replay as history on
follow-ups. The composer offers an **Attach image** control with preview + remove.

### Coverage and trust on structured results

**Two tools report coverage: `query_documents` and `semantic_search`.** They
report different things, and the difference matters. `query_documents` reports a
**partition** — every matched document is either included or accounted for under
a named exclusion reason. `semantic_search` reports a **reach** figure instead
(`matched` / `returned` / `unembedded`, §1.2 step 2): it says how many documents
the filters admitted and how many of those the index cannot see at all, not why
each missing one is missing.

`query_documents`'s block sits beside its rows and has this shape:

| Field | Meaning |
|-------|---------|
| `matched` | Documents that met the call's filters |
| `included` | Documents the rows actually account for |
| `excluded` | Reason → count for the difference; `{}` when the rows are the whole story |
| `needs_review` | Of `included`, how many carry a `needs_review` extraction flag |

`included + sum(excluded.values()) == matched` is an invariant, pinned by
`tests/test_structured_query.py`.

A third tool used to report coverage: `compare_to_series` shared this exact
shape, and its `coverage` was declared optional so that `None` could mean "not
reported" for the authored/Smart-Group series it could not reach. Both the tool
and that distinction were deleted on 2026-08-31 with the legacy series stack.

Each aggregate's exclusion reasons are built as **successive refinements of one
include chain**, not independently-gated conditions. `sum_amount`, for
example, starts from "has an amount", narrows to "and is not a quote" (unless
the caller is asking about quotes specifically), then to "and its `amount_kind`
is one this question totals", then to "and it is the canonical document for its
payment", and finally — only when grouping — to "and has the group-by column".
Each reason therefore means "survived every earlier gate, fails this one", so
the reasons partition the matched set by construction: a document that is both a
quote and senderless lands under `quote_not_spend` alone, never under both. An
earlier version of this gated each reason independently off "has an amount",
which let that case match two reasons at once and broke the invariant above; it
was caught before release and fixed by chaining the conditions instead.

**The order of those links is the contract, not an implementation detail.**
Swapping two of them leaves every total correct and every *reason* wrong, which
no assertion about a number would catch — so the chain's order is pinned
directly, by a case in which one document fails several gates at once.

The reasons a document is dropped, by aggregate:

- `sum_amount` — `no_amount` (extraction found no total), `quote_not_spend`
  (quotes are not expenditure; see below), `not_summable_kind` (the amount is
  real but is not spending: a `coverage_limit`, a `balance`, an `estimate`, a
  `none`, or an `amount_kind` nothing has decided yet), `duplicate_payment` (a
  second document for a payment already counted — see §1.2's money paragraph
  below), and `no_sender`/`no_kind` — present only when grouping by that column,
  whose inner join drops a document lacking it.
- `distinct_senders` — `no_sender` (its inner join to `Sender` drops a
  document with no extracted sender).
- `list` — `over_limit` (the result limit is 50 and the drop is positional —
  which documents fall off depends on sort order, not a predicate).
There is also `filtered_review_status`, reported when the caller passes
`review_status` and the filter removes documents — the reason that exists so the
filter can be offered at all (§1.2 step 2 explains why `semantic_search` is not
offered it).

**Where a money total comes from.** `sum_amount` builds its rows from the
[`spend_facts`](charts.md) view — the same relation the chart engine reads — and
not from `documents.amount_total`. Three things follow, and each removes one
reason an Ask total and a chart over the same question used to disagree — they
do not make the two agree outright, and §1.10 item 11 lists what still differs
(line-level versus document-level facet scoping, and currency: `sum_amount`
groups by currency and never converts, where a chart converts each amount at its
own document date into a display currency):

- **A refund reduces a total.** `amount_total` is always a magnitude; the sign
  is a property of what the number *means* and lives in `amount_kind`, so a
  refund summed as a raw column adds instead of subtracting — a number wrong by
  twice the refund, and plausible on its face.
- **Only expenditure is summed.** `SUMMABLE_AMOUNT_KINDS` decides, so a policy's
  cover limit, an account balance, an estimate and an undecided (NULL)
  `amount_kind` stay out, reported as `not_summable_kind`.
- **One payment counts once.** `spend_facts.is_canonical` names exactly one
  document per payment, so an invoice and a receipt for the same payment total
  once rather than twice, reported as `duplicate_payment`.

Coverage is still counted over `documents`, not over the view. Every one of
these exclusions is expressible as a document-level predicate — `amount_kind` is
a column on `documents`, and canonicality is an `EXISTS` against the view — so
the partition contract above is unchanged and only the row-building query moved.

One exception is deliberate: **the summable set follows the question.** A caller
filtering `kind='quote'` is asking "how much have my quotes come to?", and a
quote's amount is an `estimate` — the kind that exists precisely so a quote
cannot contaminate a spend total, and therefore the only kind that can answer a
question about quotes. It remains a kind gate: a document filed under `quote`
but carrying a cover ceiling still falls out under `not_summable_kind`.

The system prompt requires the model to disclose a non-empty `excluded` and a
non-zero `needs_review` in its answer, so a partial total reads as one. It is
also told **not** to filter flagged documents out of a total to avoid the
caveat — `review_status` is offered as a filter for *listing* what needs
checking, not for quietly improving a number.

`needs_review` is a trust signal about the *extraction*, not the document: it
usually means `library.extraction.validation`'s `amount_grounding` rule fired,
i.e. the amount being summed does not appear anywhere in the document's text.

**And a second obligation, about comparisons.** The rule above governs one
tool result. A comparative question ("is my spending going up?") is answered
by *two* aggregate calls the model composes itself, and each can satisfy the
per-result rule completely while the comparison between them is an artefact:
2025 totals 1,200 with nothing excluded, 2026 totals 960 with five amountless
bills dropped, both disclosed correctly, and "spending fell 20%" is a fact
about extraction rather than about spending. Every rule scoped to a single
result is silent on it, because the defect lives in the composition.

So the prompt carries a second, separate bullet: before stating any
comparison, difference, percentage or trend, the model must check whether the
results being compared have different `excluded` reasons or counts, and if
they do it MUST name the difference and say which side is affected. It is
deliberately a *distinct* bullet rather than a clause added to the first —
folded in, both a reader and the model read the whole thing as being about a
single result, which is the failure it exists to correct. `Coverage` itself is
unchanged: it remains a partition over one matched set, and expressing a
coverage block that *spans* a comparison is a property of the comparative tool
that does not exist yet (§1.10 item 11).

### Measuring disclosure: `library eval-disclosure`

The paragraphs above describe the `coverage` block and the prompt instruction
built on top of it. Whether the model's answers actually *follow* that
instruction is a different question, and it is checked by a command, not a
test:

```
LIBRARY_CLAUDE_CONFIG_DIR="$HOME/.claude" uv run library eval-disclosure
```

`--only <name>` runs one scenario; `--show-answer` prints the model's answer on
a PASS as well as a FAIL. Use it whenever you are building or tuning a scenario:
a `PASS` says nothing about whether the scenario would also pass with the
behaviour it targets removed, and the answer is the only place that difference
is visible. On macOS the subscription backend cannot run in a container — Claude
Code keeps its credentials in the Keychain rather than at
`~/.claude/.credentials.json`, so the compose mount finds nothing; run the CLI on
the host, against a scratch database, as above.

`library.ask.disclosure_scenarios` defines **six** synthetic scenarios, each
naming documents to seed and a question expected to route to
`query_documents`. (It was five between 2026-08-31, when `series-other-currency`
went with `compare_to_series`, and 2026-09-02, when the comparative scenario
below was added.) The command seeds one scenario's
documents at a time, drives the real Ask loop against it, and scores the
answer with `library.ask.disclosure_eval.score` for whether it named every
non-zero `excluded` reason and any non-zero `needs_review` the tool's
`coverage` block actually reported.

**What it measures, exactly.** The reasons list above names **eight** exclusion
reasons across `sum_amount`, `distinct_senders` and `list`. This eval exercises
exactly **three** of them: `no_amount` (`utilities-no-amount`),
`quote_not_spend` (`spend-excludes-quotes`) and `over_limit`
(`list-truncation`) — plus a `needs_review` case (`flagged-amounts`, unrelated
to `excluded`). The remaining **five** — `no_sender`, `no_kind`,
`filtered_review_status`, and `not_summable_kind` and `duplicate_payment` (both
added with #136) — are **not** measured by any scenario here; a green run says
nothing about whether the model discloses those. The two new ones are the
widest gap, and — contrary to what this paragraph claimed when it was written —
**both are already expressible.** `not_summable_kind` needs one seed carrying a
non-summable `amount_kind`, which `SeedDoc` gained in the same change that
created the gap. `duplicate_payment` needs two seeds sharing sender, currency,
`amount_total` and `document_date`, so that `payment_edges` rule R1 merges them
(see [money-facts.md](money-facts.md) §4) — four fields `SeedDoc` already had.
They are unwritten, not unreachable. Adding them is deliberately left as
follow-up rather than done here: a scenario is only meaningful once it has been
observed to route to the right tool against a live model, and this change was
developed without the credentials that run does.

**A sixth scenario measures something else entirely.**
`comparative-uneven-coverage` is not about an exclusion reason the others miss
— it uses `no_amount`, which `utilities-no-amount` already covers. It is about
the *composition*: it asks one question spanning two periods, seeding an
earlier period that excludes nothing and a later one that drops three
amountless bills, with amounts chosen so the readable totals show a 20% fall
in a year whose bills actually went up. Each period is individually disclosed
correctly by a model obeying the per-result rule; the comparison is the
artefact, and it is what the cross-call bullet (§1.2) exists to catch.

Two limits worth stating, because a green run here means less than it looks.
`cli._coverage_from_turn_messages` merges every coverage block a turn produced,
per reason, taking the **maximum** — which is what lets the existing scorer see
the asymmetry without changing, and equally what stops it distinguishing
"disclosed the asymmetry between the periods" from "disclosed the exclusion at
all". A model that names the three bills and still calls the fall a trend would
pass. And because the scorer is a regex screen rather than a judge, it cannot
assess whether the *comparison* was hedged correctly, only whether the count
appears. The scenario reds on silence, which is the failure #155 describes; it
does not grade the quality of the caveat.

Every seeded amount now carries an `amount_kind`, and that is load-bearing
rather than tidy. Since #136 `sum_amount` totals only the summable kinds, a
scenario seeded with undecided amounts would have every document excluded and
every spend total come back empty — while the command still ran and still
scored, measuring disclosure of a number that no longer existed. The eval needs
live credentials, so nothing in CI would have caught that; a pure-data test in
`tests/test_disclosure_eval.py` asserts the property instead. The fifth scenario,
`complete-no-gaps`, is a **control** where nothing was dropped — without it, a
model that hedges in every answer regardless of the facts would score a perfect
pass, which is the opposite of what the eval is for.

Every scenario also drives `run_ask(..., backend="subscription")` only — the
eval never runs `backend="api"`, so a green run is likewise silent on whether
disclosure holds over the metered API transport.

It is a CLI command a human runs rather than a test in the suite because CI
holds no Claude credentials to drive a live answer with, and a test that can
only ever report `skip` under those conditions is worse than no test: it
reads green while checking nothing, the same trap `tests/golden_corpus.py`
documents for its own eval-shaped suite. Each run binds its session to an
outer, connection-level transaction that is rolled back unconditionally once
every scenario has run — any stray `commit()` reachable from `run_ask` can
only release a SAVEPOINT nested inside it — and each scenario's own seeded
documents are additionally flushed and rolled back per-scenario on top of
that. Nothing seeded by the eval is ever committed, by two independent
mechanisms rather than one.

The scorer is deliberately **a screen, not a judge**: it looks for the
expected count as a numeral or number word in the answer text, which a
coincidental digit can satisfy for the wrong reason (its own module docstring,
`library/ask/disclosure_eval.py`, states this limitation directly). Every
verdict carries the full answer text precisely so a human can read past a
false pass or fail — treat a passing score as "no obvious failure to
disclose," not as certified proof of correct disclosure.

**Measured once, against an isolated scratch database (never the archive):**
all six scenarios then defined passed, including the control — the model
disclosed every excluded-reason count and the `needs_review` count the tool
actually reported, on both `query_documents` and `compare_to_series`, and
invented no caveat on the control question where nothing was dropped. That
measurement predates the removal of `compare_to_series` and its scenario, so it
covers one scenario that no longer exists.

**`comparative-uneven-coverage` was measured on 2026-09-02, and the result is
not what the change that added it predicted.** Three live runs against a
throwaway database, with the cross-call rule in place, with it reverted, and on
the control:

| run | verdict |
| --- | --- |
| the scenario, **with** the rule | PASS |
| the scenario, **with the rule reverted** | **PASS** |
| `complete-no-gaps` control, with the rule | PASS |

Two things follow, and the second matters more.

**The scenario does not discriminate.** The scorer requires the excluded count
to appear in the answer, and the per-result rule alone already secures that — so
a green run here is not evidence that the cross-call rule does anything. This
was written into the scenario's own comment as a limitation before it was run;
what the run added is that the limitation is total rather than partial.

**The model qualifies the comparison unprompted.** With the rule reverted it
still answered *"that EUR 960 almost certainly understates your actual 2025
spending — the two years may well be closer, or 2025 could even be higher"*.
So the behaviour §1.2's cross-call rule asks for is behaviour the model already
produced without it. The prompt gap is real — there is no cross-call obligation,
which is read directly — but **the predicted consequence was not observed**.
The rule is kept because it makes an implicit behaviour explicit and cheap to
re-check, not because a measurement supports it, and the control confirms it
causes no spurious hedging.

A scorer requiring the comparison to be *qualified* was built and then
abandoned: across the three answers the model used three different vocabularies
("isn't reliable" / "understates" / "the drop is likely not real"), and a
pattern list built from two of them false-failed the third on its first live
run. A count is a literal token and screens well; "did it qualify the
comparison" is a semantic property and does not. Use `--show-answer` and read
it. That is evidence,
not a guarantee, and it is not continuous: the eval is measurable on demand by
a human running the command above, but CI has no model credentials to run it
as a regression gate, so a future change could regress disclosed wording
silently between runs.

### Measuring recall: `library eval-recall`

Disclosure asks whether an answer owned up to a gap; recall asks the prior
question — whether the documents that could answer it were *retrieved* at
all. `library.ask.recall_eval` (`score_recall`/`RecallVerdict`/`blind_recall`) scores
recall@k against `library.ask.recall_scenarios`'s synthetic corpus — 252
documents producing 450 chunks, every sender name carrying a
`(recall-eval fixture)` suffix so it can never collide with real archive data
and reads as synthetic at a glance. **The corpus is public-repo-safe by
construction**: no sender, amount, date or sentence in it resembles anything
real. Seven cases each name a question and the documents expected back;
every case shares one seeded haystack (a shrinking per-case corpus would make
recall@10 meaningless once fewer documents exist than slots), and each ships
hand-authored near-miss distractors — same sender, same kind, adjacent dates,
overlapping vocabulary.

**Documents may span several chunks, and 51 of them deliberately do** — 48
crowders at five chunks each, and the three `passage-buried-clause` answers at
three, deliberately shorter than the crowders they compete with. Until
2026-09-03 every fixture was short enough to produce exactly one chunk. That
invariant was removed for #105/#106, which cannot be measured by a corpus with
no variation in the variable under test, and two things it was silently holding
up are now enforced instead of assumed:

- **The blind floor is weighted by chunk count.** `semantic_search` ranks a
  document by its *nearest chunk*, so under a null retriever a document with `c`
  chunks gets `c` draws. The old `k / pool_size` formula modelled documents as
  exchangeable — exactly right while every document was one chunk, and wrong in
  the **passing** direction once they are not: three 5-chunk expected documents
  among 37 single-chunk crowders have a true floor of 0.70 where it reports 0.25.
  `recall_eval.blind_recall` carries the model (an exponential-race integral, so
  deterministic rather than sampled) and `RecallDoc.chunks` is declared per
  fixture and checked against the real chunker.
- **The corpus must exceed the ANN prefetch window.** `semantic_search`
  prefetches `top_k * 5 * VECTOR_CANDIDATE_FANOUT` *chunks* before collapsing to
  one row per document — 300 at the breadth case's `k=12`. At 201 chunks that
  `LIMIT` never bound, so on a clean stack the vector leg was an exact global
  argmax rather than the approximate retriever that ships. A guard now holds the
  corpus above it.

The rule that follows is the opposite of the obvious one: **crowders long,
expected documents no longer than their crowders.** Lengthening the *expected*
documents raises the floor, making a case easier while looking harder.

**Every case but the control expects several documents, and competes against a
cluster larger than its rank cut.** Both properties are load-bearing and both
are enforced by `tests/test_recall_scenarios.py`. A case expecting ONE document
at k=10 scores 1.0 unless ten documents outrank it, which no cluster here is
built to achieve — so it cannot lose recall, and cannot show a retrieval change
either way. `control-unique-term` is the deliberate exception: it expects one
document, is supposed to score 1.00, and exists so that a broken embedder or a
broken harness announces itself instead of being read as a retrieval result.

```
uv run library eval-recall                      # layer 1: retrieval only
uv run library eval-recall --ask                 # layer 2: through the Ask loop
uv run library eval-recall --only <case-name>    # one case
uv run library eval-recall --write-baseline      # records recall-baseline.json
```

**Two layers, different dependencies.** Layer 1 calls
`library.search.semantic_search` directly and needs only a reachable bge-m3
embedder (`LIBRARY_EMBEDDING_SERVICE_URL`) — no Claude credentials, so it can
run in CI. Layer 2 (`--ask`) drives the real `run_ask` loop and scores the
document ids the *answer cited*, which is the only way to tell whether the
model actually exploits the filters and `top_k` depth §1.2 documents above,
rather than ignoring a schema it was merely offered; it additionally needs
Claude credentials (`--write-baseline` refuses `--ask`, since a baseline is
a retrieval-recall figure, not an answer-citation one). Like `eval-recall`'s
sibling `eval-disclosure`, every seed is flushed then rolled back inside an
outer transaction — nothing is committed to the database it runs against.

**Where it runs, and why it isn't a merge gate.** `.github/workflows/e2e-nightly.yml`
runs `library eval-recall` (layer 1, no `--ask`) with `continue-on-error: true` —
the step **reports** recall and does not gate on it. It is **not** part of `ci-gate` and not a `promote` gate — a nightly failure is
a signal to look, not a merge blocker. It is that workflow's only
step of substance: its single job is `retrieval-recall`, renamed from
`smart-groups` on 2026-08-31 when the browser journey it used to run after — and
that journey's `assert-e2e-ran.mjs` "did it actually run?" assertion — were
deleted with the series stack. The recall step needs neither: it is a CLI command
inside the `api` container that seeds and queries the database directly, so there
is no silently-skipped failure mode for such an assertion to catch. It cannot run
in the PR gate's `e2e` job: that job starts `db migrate api worker` only, no
embedder. (`compose-smoke` does start one — `embedder` carries no `profiles:`
key — but it never waits for TEI to load bge-m3, and a started container is not
a warm embedder.) TEI publishes no arm64 image either, so it also cannot run on
an Apple Silicon development machine — only a host with a reachable embedder
(Linux/amd64, or the deployed host) can drive it. The nightly runs at 03:20 UTC
plus `workflow_dispatch`, waiting on TEI's own `/health` polled from *inside*
the compose network via the `api` container, since the `embedder` service
publishes no ports and is reachable only as `http://embedder:80`. It also cannot gate the nightly today: the corpus is
deliberately built so some cases fail at baseline (below), so a gate would fire
on the corpus's own design rather than on a regression. `recall-baseline.json`
**is** committed at the repository root — re-recorded on 2026-09-03 for the
rebuilt corpus, with `breadth-many-mentions` at 0.17, `passage-buried-clause` at
0.33 and a mean of 0.786 — so the missing piece is a passing baseline, not a
baseline. Gating on a regression
becomes possible once every case passes at baseline and stays there;
re-tightening this step is a deliberate follow-up, not done yet. Run by hand
(`library eval-recall`, no flags) it still exits non-zero on any failing case,
so it can gate a release manually.

**The acceptance criterion the corpus itself is held to.** A corpus of
obviously-distinct synthetic documents would score recall@10 at 1.0
regardless of retrieval quality and could never show a future retrieval
change helping or hurting. So the corpus is required to be hard enough to
fail some of the time: **if baseline mean recall@10 comes out at or above
0.90, the corpus is too easy and must be made harder before it is used to
justify anything.** Below that line, the corpus has room to move.

**The criterion is a property of the corpus AND the archive it is scored
against — not of the corpus alone.** Every case scores over the whole
`documents` table, so the same fixtures face a populated archive on the
deployed host and almost nothing on a CI runner. Between 2026-08-28 and
2026-09-02 those two environments disagreed by more than the criterion's own
margin: the deployed baseline read 0.889 (passes) while the nightly, on a
fresh stack, read **0.931** (fails) every single night. Neither number was
wrong and nothing in this document said the comparison could not be made.

The trap that follows, and it is live: `--write-baseline` run on a clean stack
records the clean-stack number into a file the deployed runs are then compared
against. **Re-record the baseline on the deployed host**, which is where the
committed one has always been measured.

**Six measurements, and the criterion is now met in both environments.**
The first three ran on 2026-08-27 against real bge-m3 vectors on the deployed
host; the last two are the 2026-09-03 rebuild for #105/#106, measured on the
deployed host and on a zero-archive CI stack:

| corpus | archive | mean recall@10 | blind floors | verdict |
|--------|---------|----------------|--------------|---------|
| 53 documents  | 259 | 0.917  | ~0.77 | fails |
| 90 documents  | 259 | 0.9028 | ~0.77 | fails |
| 201 documents | 259 | 0.889  | 0.21-0.25 | passes |
| 201 documents | **0** | **0.931** | 0.21-0.25 | **fails** |
| 252 documents | 266 | **0.786** | 0.07-0.25 | **passes** |
| 252 documents | **0** | **0.786** | 0.07-0.25 | **passes** |

The fourth row is the one that had been going unread: the same corpus that
passes on the deployed host fails on a clean stack, and the nightly had been
printing it for a week.

The first two failed, and the second failure is the instructive one: rebuilding
the corpus from 53 documents to 90 moved the mean by 0.014 and made *none* of
the four rebuilt cases fall. The defect was arithmetic, not authorial. **A
candidate cluster must be several TIMES the rank cut, not merely larger than
it.** At `k=10` over a 13-document cluster, ten of thirteen candidates return
whatever the ranking, so a retriever choosing at *random* already scores 0.77
and the measurable range is 0.77-1.00.

The useful quantity is therefore neither corpus size nor cluster size but **the
score a random retriever would get** — the floor of each case's range.
`tests/test_recall_scenarios.py` computes it per case and fails above
`MAX_BLIND_RECALL` (0.35). After the 2026-09-03 rebuild the floors run
**0.0744 to 0.2516**: the four single-chunk clusters of roughly forty documents
sit at 0.2273-0.2500 as they always did, `passage-buried-clause` at 0.2516 over
a pool of 27, and `breadth-many-mentions` at 0.0744 over a pool of 81 — the
lowest because its crowders are five chunks each and chunks are draws.

Neither 0.917 nor 0.9028 was recorded as a baseline; both describe corpora that
no longer exist. `recall-baseline.json` holds the **252-document** run measured
on the deployed host on 2026-09-03.

**The first real evidence that chunk context headers (#6) work.** Two cases are
built so the fact that answers them exists *only* in metadata:
`sender-named-bare-chunk` (forty identically titled statements whose bodies are
figures blocks naming neither sender nor year) and `date-scoped` (forty
identically titled notices whose bodies never state their year). The sender and
the year reach the embedding by exactly one route — the `sender · date · kind ·
title` header that #6 prepends before embedding. Both scored **1.00**, where a
retriever ranking at random would have about a **1.2%** chance of catching all
three expected documents in ten slots. `contract-clause` (0.9% blind) and
`kind-scoped` (5.8%) also scored 1.00.

This is not the before/after delta spec §8.1 wanted — that is still impossible,
because `_seed_corpus` embeds through the real `run_embed` and there is no
header-free path to compare against. It is an inference from construction
rather than a difference of two measurements. But it is well powered, and it is
the first positive result #6 has produced.

**Two cases fail at baseline, and that is the design.** After the 2026-09-03
rebuild, `breadth-many-mentions` scores 0.17 (two of twelve) and
`passage-buried-clause` scores 0.33 (one of three). Both sit above their blind
floors — 0.0744 and 0.2516 — so both can register a regression *and* an
improvement, which is what a case has to do to be worth running. The other five
sit at 1.00 and can only register a regression.

**What the breadth case measures changed with the rebuild, and it is worth
knowing which question it now answers.** Before, it measured fixtures against
*real archive* documents: at 0.33 on the deployed host, six real documents took
slots, and the competing explanations — weak ranking versus fixtures being too
thin to compete — could not be separated from inside the corpus. That question
was settled from outside it (see below). Since the rebuild, the corpus's own
crowders outrank every real document, so the case now measures whether retrieval
can separate solar-array paperwork from *other installation* paperwork. Harder,
and no longer a question about the host's archive.

**And the cause is now settled: it is not chunk count.** Truncating every
crowder to its own first chunk — same documents, same senders, retained text
byte-identical, only the extra draws removed — left recall at exactly 0.17, the
same two documents (#168). Chunk count decides *which non-answers* fill the
slots, not how many answers get through. The cause is the context header above.

**How the 0.33 was settled.** Removing the real archive entirely — which the
nightly does every night — recovered 0.33 → 0.58, so real-document competition
accounted for about a quarter of the shortfall. The residual could not be blamed
on thin fixtures, because at 0.58 every remaining competitor was also a
single-chunk fixture of identical thinness. Splitting the twelve expected
documents by sender showed what the remainder was: seven of eight documents from
the installer were retrieved and **none of the four from other senders** — a
breadth question collapsing onto the dominant sender's cluster rather than
reaching the topic across senders. See `journal/260903-recall-corpus-multichunk.md`.

**A baseline records the haystack it was measured against — both halves of
it.** Every case scores over the whole `documents` table, so the same corpus
faces a populated archive on the deployed host and almost nothing on a fresh CI
stack. `recall-baseline.json` carries a `measured_against` block
(`archive_documents`, `corpus_documents`, `corpus_chunks`; counts only, since
the file is committed to a public repository), and `eval-recall` warns when
`archive_documents` or `corpus_chunks` differs from the recorded one by more
than `max(5, 10%)` — the delta then reflects the change of haystack as much as
any change in retrieval. `corpus_documents` is recorded for a reader and is
**not** compared: it is the wrong unit, for the reason below.

`corpus_chunks` is there because documents are the wrong unit: the retriever
ranks **chunks**, and the 2026-09-03 rebuild more than doubled the corpus in
chunks (201 → 450) while its document count moved only 201 → 252. A comparison
on documents alone would have called that a retrieval delta.

**After the rebuild the two environments agree exactly** — same scores, and the
same twelve documents in the same rank order — because the corpus's own crowders
now outrank every real archive document. The environment-dependence that made
0.889 and 0.931 irreconcilable is gone; the eval measures the corpus rather than
the host it runs on.

**#6's effect on recall was never measured and cannot now be measured this
way.** The intended order (spec §8.1) was: record a baseline, then land chunk
context headers, then re-measure. No embedder was reachable while the branch
was built, so the baseline step never happened and #6 shipped first. Because
`_seed_corpus` embeds through the real `run_embed`, the fixtures are now
embedded *with* their context headers — so the 2026-08-27 run was a post-#6
measurement, not the "before" it was supposed to be compared against.
`sender-named-bare-chunk` scoring 1.00 there is consistent with the header
doing its job, but it is not evidence of it: there is no header-free comparison
to difference against, and that particular case could not have failed anyway.
Producing a real #6 delta *through the eval* would need a way to embed the
corpus without headers, which does not exist today.

**But the header's effect is no longer unmeasured, and it cuts both ways.** The
eval is not the only instrument: the two strings can be embedded directly
through the sidecar and their distances to a query compared, which needs no
seeding path at all. Done on 2026-09-03 for `breadth-many-mentions`'s twelve
expected documents:

| sender | documents | effect of the header on distance to the query |
|---|---|---|
| Solaris Install (the installer) | 8 | **all 8 closer** (-0.0009 to -0.0616) |
| Gridline / Harbour / Meridian | 4 | **all 4 further** (+0.0206 to +0.0353) |

A perfect partition by sender. The header is `sender · date · kind · title`, so
prepending `Harbour Insurance · 2023-05-30 · letter · Policy amended` to a
document whose body says "rooftop array" pulls its vector toward insurance and
away from a solar query; `Solaris Install · …` does the opposite.

So the header is a **sender-affinity amplifier**: it makes a document more
findable by questions that sound like its sender, and *less* findable by
questions that do not. That is the benefit `sender-named-bare-chunk` and
`date-scoped` are built to show — both score 1.00, and both are constructed so
the discriminating fact reaches the embedding only through the header — and it
is also the mechanism behind `breadth-many-mentions`'s cross-sender collapse.
Ranking all 466 documents for that query put the eight installer documents at
ranks 1-33 and the four third-party ones at 308, 349, 424 and 456.

Treat #6 as **measured in both directions and net-unknown**: a real gain on
questions whose answer is identified by metadata, a real cost on topic questions
whose answers span senders. Which dominates depends on the question mix, and
that has not been measured. See #164 and #105.

```
uv run library eval-recall --write-baseline   # records recall-baseline.json
uv run library eval-recall --only sender-named-bare-chunk   # the #6 case alone
uv run library eval-recall --ask              # layer 2; additionally needs Claude credentials
```

## 1.3 Configuration

All settings use the `LIBRARY_` env prefix (see `.env.example` /
`src/library/config.py`):

| Setting | Default | Purpose |
|---------|---------|---------|
| `LIBRARY_EMBEDDING_ENABLED` | `true` | Master switch for the embed stage. |
| `LIBRARY_EMBEDDING_SERVICE_URL` | `http://embedder:80` | The bge-m3 sidecar. |
| `LIBRARY_EMBEDDING_MODEL_NAME` | `bge-m3` | Recorded with each embed. |
| `LIBRARY_EMBEDDING_CHUNK_CHARS` | `1800` | Target chunk size. |
| `LIBRARY_EMBEDDING_CHUNK_OVERLAP` | `200` | Overlap carried between chunks. |
| `LIBRARY_RETRIEVE_TOP_K` | `10` | Documents returned by hybrid retrieval. |
| `LIBRARY_RETRIEVE_CHUNKS_PER_DOC` | `3` | Nearest passages per document folded into the Ask excerpt (best-first, `[…]`-joined); `1` = legacy single-chunk. Does not affect candidate ranking or citations. |
| `LIBRARY_ASK_SEARCH_MAX_TOP_K` | `50` | Ceiling on the `top_k` Ask's `semantic_search` tool may request. Values above it are clamped rather than rejected; non-positive values clamp to `1`. |
| `LIBRARY_ASK_MODEL` | `claude-opus-4-8` | Answer model. |
| `LIBRARY_ASK_TITLE_MODEL` | `claude-haiku-4-5` | Cheap model that names a new conversation from its first Q&A exchange. One bounded call per new thread; failure is non-fatal (keeps the placeholder title). Must have a `MODEL_PRICING_USD_PER_MTOK` row. |
| `LIBRARY_ASK_MAX_TOOL_TURNS` | `8` | Tool-use loop bound per turn. |
| `LIBRARY_ASK_MAX_ANSWER_TOKENS` | `8192` | Per-call output cap. Thinking tokens count against it — see §1.6 Reasoning. |
| `LIBRARY_ASK_GET_DOCUMENT_MAX_CHARS` | `8000` | Cap on the text `get_document` returns for one document; longer text is truncated with `text_truncated: true`. |
| `LIBRARY_ASK_HISTORY_TURNS` | `3` | Prior turns re-fed into the loop for follow-ups; `0` disables history (each turn answered cold, still recorded). |

Ask requires `LIBRARY_ANTHROPIC_API_KEY` (the answer step calls Claude); without
it `POST /api/ask` returns `503` and the UI shows a friendly message. Indexing
(embedding) needs only the local sidecar, not the API key.

## 1.4 Cost

The answer step's token cost is estimated and recorded per turn in the
`ask_turns` table (`query`, `answer`, `model`, token counts, `cost_usd`,
`used_tools`). The total cost of a conversation thread is the sum of its turns'
`cost_usd`. Cost is **recorded, not gated** in this release — Ask is
interactive and self-limiting. A daily-budget guard mirroring the extraction
budget (`LIBRARY_EXTRACTION_DAILY_BUDGET_USD`) can be added later. Embedding
is local and effectively free.

### Which backend answers

The transport for the tool loop *and* the thread-title call is an
admin-editable instance setting (**Settings -> LLM backend**), resolved per
request so a change needs no restart; `LIBRARY_ASK_LLM_BACKEND` supplies the
default and ships as `subscription`. `api` uses the metered Messages API,
`subscription` runs the same loop through the Claude Code CLI against a Claude
subscription.
Ask is the surface where that trade pays — `ask_model` is the priciest
configured model and calls are human-paced — but the Agent SDK adds a fixed
~43k-token Claude Code preamble to every Opus call. `cost_usd` counts it, so a
subscription-answered turn records a *higher* `cost_usd` than the same turn on
the API: under a subscription that figure is notional ("what this would have
cost"), and the real resource being spent is quota. On `subscription`, Ask needs
no Anthropic API key at all and does not 503 without one. Full mechanism,
provisioning steps and the credential runbook: [`llm-backends.md`](llm-backends.md).

## 1.5 Operations

- **Backfilling the existing corpus.** Documents indexed before the embed stage
  existed have no chunks. Queue embedding for them with the CLI (the worker must
  be running):

  ```console
  docker compose exec api library backfill-embeddings
  # --limit N to throttle; --include-existing to re-embed everything
  ```

  The job is idempotent (it replaces a document's chunks), so it is safe to
  re-run. On CPU the first run is slow for a large archive — let it work through
  the queue.
- **Backfilling conversation titles.** Threads created before the LLM-titling
  feature keep their placeholder title (the truncated first question). Re-title
  them from each thread's first Q&A exchange with a one-off script (requires
  `LIBRARY_ANTHROPIC_API_KEY`). It is idempotent — a thread already retitled or
  manually renamed no longer matches the placeholder and is skipped:

  ```console
  docker compose exec api python -m scripts.backfill_ask_titles --dry-run
  docker compose exec api python -m scripts.backfill_ask_titles   # apply
  # --limit N to stop after N retitles
  ```

- **Deployment** of the embedder sidecar and the pgvector database image:
  see [deployment.md](deployment.md) §1.1 and §1.7.

## 1.6 Conversational Ask

Ask is multi-turn. A follow-up like *"what about last year?"* resolves against
the prior turns of the same conversation rather than being answered cold.

### How it works

Each question is a **turn** within a persistent **thread**. Threads are stored
server-side in two tables: `ask_threads` (one conversation, with a title and
owner) and `ask_turns` (one Q&A turn, recording the question, answer, citations,
token cost, and the full serialized Anthropic message blocks this turn produced).

**Conversation titles.** A new thread is created with a placeholder title (the
truncated first question) so it always has *something* to show, then — after the
first answer lands — a cheap model (`ask_title_model`, default
`claude-haiku-4-5`) summarises the question/answer into a short, specific title
for the sidebar (`generate_thread_title` in `ask.engine`). Titling is
**fire-and-forget and non-fatal**: it runs after the answer is already produced,
and any failure leaves the placeholder title in place — an answer is never
blocked or failed by title generation. The title call's cost is folded into the
turn's recorded `cost_usd`. Users can override any title via
`PATCH /api/ask/threads/{id}` (see below). Existing threads created before this
feature keep their placeholder titles until the backfill script runs (see
[§1.5 Operations](#15-operations)).

When a follow-up arrives the engine loads the last `LIBRARY_ASK_HISTORY_TURNS`
turns (default 3) from the database, concatenates their serialized message
blocks into a history prefix, and prepends that prefix to the current question
before calling Claude. This means Claude can reason over earlier tool results
without re-querying — the faithful replay path.

**Reasoning.** The tool loop runs with **adaptive thinking on**
(`thinking: {"type": "adaptive"}`). This is set explicitly and must stay that
way: on this model family, *omitting* the parameter means the model runs with no
extended reasoning at all — the absence of the parameter is not a neutral
default, and Ask ran that way until 2026-08-21. Ask is a multi-hop task
(retrieve, cross-check, compare against a distribution), which is the shape that
benefits most.

Three settings move together here, and changing one without the others is a
mistake:

| setting | value | why it is coupled |
| --- | --- | --- |
| `thinking` | `adaptive` | the accuracy lever |
| `LIBRARY_ASK_MAX_ANSWER_TOKENS` | 8192 | **thinking tokens count against `max_tokens`** — at the old 1024 reasoning could consume the budget and truncate, or entirely displace, the answer |
| `LIBRARY_ASK_MAX_TOOL_TURNS` | 8 | reasoning encourages more, better-targeted tool calls; the old cap of 4 was already reached by 4 of 51 measured turns |

`display` is left at its default (omitted), so thinking blocks come back with
empty text and only a `signature`. Nothing renders the reasoning, so there is no
reason to pay to transport or store it — but the signature **must** be replayed
byte-identical on the next call of the turn or the API rejects it.
`_serialize_block` preserves it and `_text_of` filters to `type == "text"`, so
reasoning never leaks into the answer.

**Expect this to cost more per turn, not less.** Thinking tokens are billed as
output, and a longer tool loop means more calls. It buys accuracy, and the
prompt caching below offsets part of it; it is not an efficiency measure. Watch
`ask_turns.output_tokens` and `cost_usd` after this change rather than assuming
either direction.

**Verified against the live API**, 2026-08-21, since CI cannot: the e2e stack
has no Anthropic key and stubs `POST /api/ask`, so every automated test of the
thinking parameter runs against a fake client. Driving the real engine on the
deployed host with a genuine comparative question returned **three thinking
blocks, each carrying a signature** (540, 424 and 692 characters), across a
loop that used `compare_to_series` and `query_documents` and completed
normally — which also exercises the replay path, since a thinking block
returned without its signature intact is rejected on the *next* call of the
turn. The answer correctly excluded same-*kind* documents from other senders.
That is one observation, not an accuracy measurement — and it is now a
*historical* one: `compare_to_series` was deleted on 2026-08-31, so the same
question today drives a two-tool loop. Nothing about the thinking-block or
replay behaviour depends on which tools the loop calls.

**Prompt caching.** Three breakpoints, of the four Anthropic allows. The static
system prompt carries one; the tool definitions carry none but sit *before* the
system block in the cached prefix, so that breakpoint covers them too. When a
history prefix is present the engine marks its boundary with a second, so
re-sent prior turns hit the cache on follow-ups.

The third matters most, and it is *within* a single turn. The tool loop re-sends
the whole conversation on every iteration, so a tool result fetched on pass 2 is
paid for again on passes 3 and 4 — measured on this archive, one turn shipped
~247k characters across four calls while its stored transcript was only ~87k.
A top-level `cache_control: ephemeral` on each `messages.create` caches the last
cacheable block, which is exactly that accumulated tool-result tail, so re-reads
bill at ~0.1x instead of full rate. It shapes the request only: same prompts,
same answers.

**Measured in production**, 2026-08-21, one real five-call tool loop:

| call | uncached | cache write | cache read |
| ---: | ---: | ---: | ---: |
| 1 | 2 | 0 | 3,127 |
| 2 | 2 | 0 | 3,333 |
| 3 | 2 | 290 | 3,333 |
| 4 | 2 | 264 | 3,623 |
| 5 | 2 | 1,715 | 3,887 |
| **total** | **10** | **2,269** | **17,303** |

Weighted at 1.25x for writes and 0.1x for reads, that is 4,576 input-token
equivalents against 19,582 uncached — a **76.6% reduction**. Two honest caveats:
the identical question had been asked minutes earlier, so call 1 opens against
an already-warm cache and the headline number is an upper bound rather than a
typical cold start; and it is a single sample of one question. What it does
establish unambiguously is that the within-loop caching works — cache reads grow
across calls 2-5 as the tool-result tail accumulates, which is precisely the
re-send this change exists to stop paying for.

**Token accounting counts cached tokens.** Anthropic reports cache reads and
cache writes in fields *separate* from `input_tokens`, so summing `input_tokens`
alone under-reports every cached request — and the better the cache works, the
more the figure lies. `ask_turns.input_tokens` therefore records the **total**
context that went in (fresh + cache reads + cache writes), which stays
comparable across cached and uncached turns, while `cost_usd` prices those
tokens at their real weights (reads ~0.1x, writes ~1.25x). Without this,
enabling caching would have made recorded spend appear to collapse partly
because tokens stopped being *counted*, not because they stopped being *sent*.
The subscription backend already folded all three counts into one total
(`llm/subscription.py`); it prices that total at the full input rate as a
deliberately conservative notional figure, since that path is not metered.

**Sliding window trade-off.** Older turns are dropped when a thread exceeds
`LIBRARY_ASK_HISTORY_TURNS`. Dropped turns cause the history-prefix cache to
miss (the cache key changes when earlier turns fall off), while the static
system+tools prefix stays cached. Most threads are short; this is an accepted
trade-off for bounded token usage.

### Using threads via the API

```
POST   /api/ask      {"question": "..."}                     → creates a new thread
POST   /api/ask      {"question": "...", "thread_id": 42}    → continues thread 42
GET    /api/ask/threads                                      → list your conversations
GET    /api/ask/threads/42                                   → thread detail + all turns
PATCH  /api/ask/threads/42  {"title": "..."}                 → rename a conversation
DELETE /api/ask/threads/42                                   → delete a conversation
```

See [api.md](api.md) §1.11 for the full wire contract.

### Web UI

Ask is a **two-screen** chat interface whose visible screen is driven by the
**route**, so the phone's back gesture and browser history behave like a native
chat app. Three routes, all served by `AskView.vue`:

| Route | Name | Mobile screen | Desktop (`lg+`) |
| --- | --- | --- | --- |
| `/ask` | `ask` | The conversation **list** (full screen) | Two-pane, no thread selected |
| `/ask/new` | `ask-new` | A fresh **chat** (empty state) | Two-pane, composer focused |
| `/ask/:threadId` | `ask-thread` | The **chat** for that thread | Two-pane, thread active |

The `:threadId` param is constrained to digits (`:threadId(\d+)`) and the
`/ask/new` route is declared before it, so `new` is never parsed as a thread id.

**Mobile** is two full screens. The **list screen** has a compact "Ask" title
bar with a ＋ that starts a new chat, a search box, and the thread list (each row
carries a **⋯ overflow menu** with Rename and Delete — no always-on links). The
**chat screen** is a **fixed-height flex column** that fills the viewport below
the app header (`height: calc(100dvh - 4rem)`): a back arrow (→ the list) + the
thread title with its own ⋯ menu, then the **transcript as the internal scroll
area** (`flex-1; overflow-y-auto`), then the composer as a **bottom footer**
(`shrink-0`) — so the composer always docks at the bottom, short chat or long,
rather than a `sticky` that only pins on overflow (which floated it mid-page).
Because `#app-shell` and the column use `100dvh` and the viewport meta carries
`interactive-widget=resizes-content`, the on-screen keyboard shrinks the viewport
and the composer sits directly **above the keyboard**; its bottom padding
includes `env(safe-area-inset-bottom)` to clear the home indicator. The chat is
**full-bleed** — no bordered card and no page side-padding, edge-to-edge — and
each turn is **flat** (a violet question bubble over plain answer text, no nested
card). **Desktop** keeps the familiar two-pane layout (rail | thread), and now
uses the **same fixed-height fill** as mobile: the whole view is a bounded flex
column (`height: calc(100dvh - 8rem)` — viewport − the 4rem header − `#app-page`'s
4rem `py-8`), the page header is a `shrink-0` lead, and the `#ask-page` panel
takes the rest (`flex-1; min-h-0`). Both columns scroll internally (the rail's
thread list and the thread pane's transcript are each `flex-1; overflow-y-auto`),
so the composer is a **`shrink-0` bottom footer** docked at the viewport bottom —
not a `sticky` bar that floated mid-panel below the taller conversation list.

**New conversation.** The mobile ＋ and the desktop "New conversation" button both
go to `/ask/new`; the desktop button is greyed out when the view is already an
empty new conversation (no thread, no turns), since starting another does nothing.

**Rename / delete.** Both the list rows and the chat title bar expose a **⋯ menu**
with Rename and Delete. Rename swaps the title for an editable input seeded with
the current title — Enter or **Save** commits via `PATCH /api/ask/threads/{id}`,
Esc or **Cancel** aborts; a blank or unchanged title is a no-op. **Delete** is a
two-step confirm (Delete → Delete/Cancel) so a single misclick cannot destroy a
thread; deleting the active thread from the chat title bar returns to the list.

**`?q=` deep link.** A `/ask?q=<prompt>` link (the document detail view's **"Ask
about this document"** button; see [frontend.md §1.5](frontend.md)) is redirected
to `/ask/new?q=<prompt>` so the seed lands on the chat screen where the composer
lives (on mobile the list screen has no composer). It is **pre-fill only**: no
backend change and no document scoping — the named document is surfaced by the
ordinary Ask retrieval.

Each turn is visually layered: the question is a right-aligned violet bubble. **At
`lg+`** the answer (with its citations disclosure and tools/cost meta) sits on a
subtle shaded, bordered surface card; **on mobile** that card is dropped and the
answer is flat text under the bubble. The composer is a **single full-width
bar** — the `ask-form` element *is* the text-entry surface, square-cornered and
flush with the panel, marked off from the transcript only by a slightly darker
fill and a top rule. There is no pill and no boxed field nested inside it: a
rounded field floating inside a bordered footer read as a box within a box. It
holds a borderless, auto-growing textarea with the **attach (paperclip)** and
**Send/Stop** controls on their own row below it, so the text field is full
width and the controls never squeeze it. The top rule turns violet on
`focus-within` — with the textarea's own outline suppressed, that rule is the
composer's only visible focus indicator. Attach handles up to five images
(previewed as thumbnails above the text).

Sending is asynchronous and follows the Claude-app pattern: on submit the
question appears in the transcript **immediately** as an optimistic turn and the
composer clears, while the answer slot shows a **thinking indicator** until the
answer lands. The primary action becomes a live **Stop** button that cancels the
in-flight request (it is never a greyed-out, inert control); a user-initiated
stop or an API error removes the optimistic turn and restores the question to the
composer for editing/resend. In the composer, **Cmd/Ctrl+Enter** always sends and
**Shift+Enter** / **Ctrl+J** always insert a newline. Plain **Enter** sends on
**desktop (`lg+`)** but inserts a **newline on a phone** (below `lg`), where
sending is the Send button's job — matching mobile chat apps. Enter is ignored
while an IME composition is in progress (see [frontend.md §1.5](frontend.md)). The
selected conversation is marked with a full-perimeter ring.

**Transcript layout: document (default) or conversation.** At `lg+` the thread
bar carries a two-button switch (`[data-testid="ask-view-mode"]`) between
**document** layout — the default on a wide screen, because Ask's answers are
prose- and table-heavy — and the **conversation** bubble layout described above.
Document mode drops the right-aligned violet bubble: each turn becomes a
full-width tinted block under an uppercase role label (`You` / `Agent`), and the
transcript and composer are centred on one shared `max-w-5xl` measure so the
input lines up with the text.

It also **collapses the conversation rail**, and that is load-bearing rather than
cosmetic. With the global app sidebar expanded *and* the rail on screen, the
answer column at a 1024px viewport measures 332px — narrower than the 375px phone
width this app treats as its mobile floor. Giving the rail's 288px back takes it
to 620px (measured in Chromium against the built CSS). Without the collapse,
document mode would be a *narrower* read than the bubbles it replaces at the very
width it first becomes available. The rail stays when no conversation is open, so
the "select a conversation" empty state never points at a sidebar that isn't
there.

**The rail's actions move rather than disappear.** The rail is also where
"New conversation" and the thread list live, so while it is collapsed the thread
bar carries a **＋ New** button (keeping the rail button's `new-conversation`
testid, so the capability stays addressable by one selector wherever it lives)
and a **conversations** button that returns to `/ask`, where the rail is on
screen again and a thread can be picked. Both are **ghost** buttons: in this bar
a violet fill means "this layout is active", so an action wearing one too reads
as part of the toggle rather than as a verb.

**Opening a conversation starts at its beginning.** Scroll-to-bottom is right
when a turn *arrives* — you want the new thing — and wrong when you open a
thread to read it, where it lands you mid-answer with your own question scrolled
off the top. `loadThread` scrolls to the top; new answers still pull to the
bottom. The two sets are mutually exclusive —
whichever is showing, there is exactly one of each control in the DOM. This is
not a nicety: with document as the default, omitting them leaves the default
desktop experience with no way to start or switch a conversation.

The preference persists per-machine under `library:ask-view-mode`
(`useAskViewMode.ts`; see [frontend-view-principles.md](frontend-view-principles.md) §4).
It is **clamped, not overwritten**, below `lg`: a phone always renders
conversation layout, and the stored desktop choice is still there next time. The
toggle itself is rendered with `v-if` rather than a `hidden` utility, so it is
absent from the tab order on a phone instead of focusable-but-inert.

**Wide tables scroll inside themselves.** Each rendered GFM table is wrapped in a
keyboard-reachable `.ask-table-wrap` (`role="region"`, `tabindex="0"`) with
`overflow-x: auto`, and the table itself uses `width: max-content`. Before this,
a table wider than the column made the *whole transcript* pan sideways — question
bubbles and all — because the transcript is `overflow-y-auto`, which makes the
browser compute `overflow-x` to `auto` too; `#ask-page`'s `overflow-hidden` then
clipped whatever ran past the panel. The wrapper, the `max-content` width and
`min-width: 0` on `.ask-answer` are one mechanism in three parts — changing one
without the others reintroduces the defect.

The empty states distinguish three cases: a **new chat** (`ask-new`) shows a
greeting plus example-prompt buttons that fill the composer
(`[data-testid="ask-greeting"]`); when **conversations exist but none is
selected** (chiefly the desktop rail) it prompts the user to pick one
(`[data-testid="ask-select-thread"]`); and when **no conversations exist yet** it
invites a first question (`[data-testid="ask-empty"]`).

> **Section 1.7 is gone, and §1.8 onwards keeps its number.** It documented the
> `compare_to_series` tool and the series engine behind it — detection, the four
> statistical views, the currency bucket, the cached LLM description and the
> `DocumentSeriesTrend`/`SeriesChartTile` surfaces — all deleted on 2026-08-31
> with the legacy series stack. The numbering does not close up because the
> sections after it are cited by number from other documents, and nothing in the
> toolchain checks a section-number citation. What replaced the model this tool
> computed over is [charts.md](charts.md); why the tool was not rebuilt on it,
> and what Ask's money answers still get wrong as a result, is §1.10 item 11.

## 1.8 Editing document metadata (the write tool)

Ask is an agentic tool-use loop, and beyond the three read-only retrieval tools
(`semantic_search`, `query_documents`, `get_document`) it carries one **write
tool, `update_document_metadata`** (`ask.engine`), so a conversation can
*correct* a document's metadata, not just read it. Writes are
tightly guarded in code — the prompt asks for good behaviour, but the guardrails
below are enforced regardless of what the model does.

### Guardrails

- **Only documents surfaced in the conversation.** The tool refuses any
  `document_id` that a read tool did not return earlier in the thread. The
  engine accumulates the cited/returned ids into an `editable_ids` set as the
  loop runs (and rebuilds it from history on follow-ups); a write to an id
  outside that set is rejected. Ask cannot reach arbitrary documents.
- **Propose-then-confirm, enforced in code.** The tool is two-phase:
  1. A first call (`confirmed=false`, the default) returns a **preview** —
     current vs proposed value per field — and **writes nothing**.
  2. A confirmed call (`confirmed=true`) is **refused unless that document was
     previewed earlier in the same thread** (the `previewed_ids` gate,
     reconstructed from history on follow-ups). So a write can never happen
     without a preview having been shown first.

  The system prompt additionally instructs the agent to state the exact change
  in prose and get the user's **explicit agreement** before sending a confirmed
  write — but the `previewed_ids` gate is the hard, code-level guarantee.
- **An allocated `amount_total` is refused, in words.** A confirmed write does
  not call `session.commit()`; it commits through
  `spend_lines.commit_allocation`, which translates migration 0035's deferred
  mirror trigger. If the edit would leave a document's spend lines summing to the
  *old* `amount_total`, the tool returns an `error` naming the allocation and
  saying to clear or replace the lines first, and the turn continues. Until
  2026-08-31 this tool was the only one of `amount_total`'s five writers that did
  not translate that refusal: the bare `DBAPIError` escaped the whole turn as a
  500 with a poisoned session. See [charts.md](charts.md) §10.1 for the other
  four writers and how each answers.

### Editable fields

**Exactly the fields of `DocumentUpdate`** — title, summary, recipient, sender,
kind, tags, projects, matters, document/due/expiry dates, amount, currency,
language — because `_WRITABLE_FIELDS` is now *derived* as
`tuple(DocumentUpdate.model_fields)` rather than hand-listed. This is the
**same surface as `PATCH /api/documents/{id}`** ([api.md §1.5](api.md)), which is
what makes deriving correct rather than merely convenient: `DocumentUpdate` is
the specification, and it contains no status or review fields to exclude.

The hand-written list this replaced had drifted: `matters` was missing, so an Ask
write of it was dropped on the way to `DocumentUpdate` and the tool still
reported `status: updated` — a write that looked like it worked and changed
nothing. Three surfaces have to agree for a field to be usable, and each has its
own guard in `tests/test_ask_document_write.py`: the writable set (derived), the
tool's `input_schema` (**not** derivable — each property carries a hand-authored
description, so a field missing here is invisible to the model), and
`_preview_current` (a relationship without a branch there renders as
`<Matter object at 0x...>` in the preview the user is asked to approve, because
tool output is serialised with `json.dumps(..., default=str)` and so fails
silently rather than loudly).

Only the fields supplied change; `tags`, `projects` and `matters` are
full-replacement lists.
Edits are recorded with `edited_by="ask"` provenance and the standard
`user_edited` ingestion event (so an Ask edit locks the field against
re-extraction exactly like a UI edit, and is auditable as having come from Ask).

### Shared write path

Both the Ask tool and the `PATCH /api/documents/{id}` route now delegate to one
shared service, **`apply_document_update`** (`src/library/documents_service.py`),
which mutates the document (upserting sender/recipient/tags/projects), records
the `user_edited` event tagged with `edited_by`, and returns the changed field
list without committing — the caller owns the commit. The two entry points
differ only in `edited_by` (`"user"` vs `"ask"`).

## 1.9 Document comments

A **comment** (`library.models.DocumentComment`, table `document_comments`) is
user-authored, dated free text **attached to an existing document** — distinct
from a **note**, which is its own `source='note'` Document. Comments exist so
you can annotate a document ("this is my current house", "paid this by bank
transfer, not the card on file") without editing its extracted metadata, and
have Ask treat that annotation as ground truth.

- **Storage.** `id`, `document_id`, `author_id`, `body`, `created_at`,
  `updated_at`; `created_at` is the recorded date shown in the UI and in
  `get_document`'s output. Cascades on document delete.
- **API.** `GET`/`POST /api/documents/{id}/comments`,
  `PATCH`/`DELETE /api/documents/{id}/comments/{cid}` — see
  [api.md §1.19](api.md) for the wire contract. Every create/edit/delete writes
  an `IngestionEvent` (`comment_added`/`comment_edited`/`comment_deleted`) and
  defers a re-embed of the parent document.
- **Indexing.** `library.jobs.embed_document` queries a document's comments
  alongside its page/OCR text and embeds **one extra chunk per comment**,
  framed as `User comment (YYYY-MM-DD): <body>` so the text itself carries the
  date. Each such chunk carries the new nullable `document_chunks.comment_id`
  back-reference (`NULL` for chunks derived from the document's own text), so a
  comment surfaces through the ordinary `semantic_search` hybrid retrieval
  exactly like any other passage — a document can now be *found* through what
  someone said about it, not just its own text.
- **Reading in full.** `get_document` (§1.2) returns a document's comments
  verbatim (body + date) alongside its structured fields and text, and the
  system prompt instructs the model to treat them as authoritative — see §1.2.
- **UI.** A **Comments** card on the document detail page — see
  [frontend.md §1.5](frontend.md).

## 1.10 Limitations (this release)

1. **Page citations are conditional on the markdown layer.** Documents that
   have a `document_pages` row (generated by the `markdown` pipeline stage or
   `backfill-markdown`) carry a `page_number` on their citation. Documents
   ingested before the markdown layer existed, `text/plain` files, and any
   document where the markdown stage was skipped or failed will cite without a
   page number — only the document title is shown.
2. History bounding is a sliding turn window only — no rolling summarization of
   long threads.
3. RRF fusion only — no cross-encoder re-ranking.
4. Ask is in-app only; it is not exposed as an MCP tool yet.
5. CPU embedding: the one-time backfill of a large archive is slow.
6. Coverage reporting is honest about *documents*, not about *periods*.
   `sum_amount`'s date filters bound `document_date`, which is the issue date;
   a bill issued in January for December lands in the wrong year, and an
   annual settlement double-counts against the instalments it settles.
7. The no-answer citation suppression is keyed on the exact `_NO_ANSWER`
   sentinel. When the model phrases its own "not found" answer after a fruitless
   search, the prose-citation fallback still attaches the retrieved candidates.
   The system prompt instructs against it; it is not enforced in code.
8. Truncation is disclosed but not remediable. `query_documents` reports
   `over_limit` when a `list` exceeds its 50-row limit, and the tool exposes no
   `limit` parameter — so the model can say "50 of 500" but cannot fetch the
   rest. That is deliberate for this release (the goal was disclosure, not
   completeness), but it means a list answer over a large match set is a sample
   the model knows is a sample.
9. **Chunk context headers reflect metadata as of the last embed.** A chunk
    embeds a `sender · date · kind · title` line alongside its text. Editing
    one of those four fields defers a re-embed, so the header self-heals — but
    chunks written before migration `0031` carry no header at all until
    `library backfill-embeddings --include-existing` is run, and until then a
    question naming a sender cannot match those documents on metadata.
    Structured filters are unaffected: they read live metadata, not the
    chunk's stored header.
10. **`semantic_search`'s `matched` counts documents, not passages.** A
    document matching the filters but carrying no chunks is counted in
    `matched` and reported in `unembedded`, but is unreachable by vector
    search. That is the honest reading of finding #14, not a fix for it:
    there is still no UI listing documents missing from the index.
11. **Money answers read `spend_facts`; three things around them still do not.**
    `structured_query.sum_amount` was moved onto the view in #136, so a refund
    reduces a total, non-summable kinds stay out, and one payment documented
    twice counts once — see §1.2's *Where a money total comes from*. What is
    still open:
    - **Ask has no tool for comparing a series over time.** `compare_to_series`
      was deleted with the legacy series stack on 2026-08-31 and has not been
      rebuilt against `spend_facts`. A "how does this year compare with last"
      question is answered, if at all, by two `sum_amount` calls the model
      composes itself, with no shared basis and no *computed* coverage across
      the pair. The **disclosure** half of that gap is closed: the prompt now
      obliges the model to compare the two results' `excluded` blocks and name
      any difference before stating a trend (§1.2), and
      `comparative-uneven-coverage` measures whether it does. What is still
      missing is the tool — a distribution, a basis for "usual", and a
      `Coverage` shape that can express a partition over two matched sets
      rather than one.
    - **Facet filters are document-level, not line-level.** The `facets`
      argument narrows by a document's own labels, which is what the list API
      and the vocabulary panel mean by a facet filter. A chart splits by the
      labels on `spend_facts` rows, and a *split* document's lines may carry
      their own — so for an itemised document filtered by facet, an Ask total
      includes the whole document where a chart would count only the matching
      lines.
    - **Date filters still bound `document_date`**, the issue date, with the
      consequences item 6 describes. Moving the sum onto `spend_facts` did not
      change this: the view carries the document's own date.
