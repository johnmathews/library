# Ask — semantic question answering

**Status:** active. **Last updated:** 2026-08-27 (final whole-branch review fix wave on this same branch, nine fixes: (1) `.github/workflows/e2e-nightly.yml`'s `Measure retrieval recall` step moved to run AFTER the Smart Groups journey and its "did it actually run?" assertion, with `continue-on-error: true` added — previously it ran BEFORE the journey with no `|| true`, so it aborted the whole job on the very baseline failures the corpus is deliberately built to contain (`sender-named-bare-chunk`'s own docstring says "expected to fail at baseline"), reinstating the never-runs-anywhere defect this workflow exists to fix. The step now reports recall and does not gate; this section's *Where it runs* paragraph and the workflow's own header comment both wrongly claimed "a recall regression reds the nightly run" and are corrected below. (2) `_seed_corpus` (`library.cli`) now raises if any seeded document produced zero chunks after `run_embed` — verified by execution that with the embedder disabled, seeding previously "succeeded" having created zero chunks (`run_embed` is fail-open by design), so `eval-recall` would have silently scored the FTS leg of RRF alone and reported it as retrieval recall; covered by a new test in `tests/test_recall_seed.py` that forces the embedder off. (3) `ASK_SYSTEM_PROMPT_TEMPLATE`'s coverage rule now names `semantic_search` alongside `query_documents`/`compare_to_series` and states the `unembedded` disclosure obligation as a MUST — previously only the tool description carried that obligation, while the system prompt (the stronger surface) actively implied `semantic_search` carried no coverage at all. (4) A new test drives the Ask write tool's confirmed `update_document_metadata` call through a header-field edit and asserts the re-embed hook (`ask/engine.py`, beside `header_fields_changed`) defers exactly one `embed_document` job, plus a non-header companion asserting none — proven by mutation: deleting the two-line hook made the new test fail (it did not before this pass, across 71 passing tests). (5)-(9) minor: a misleadingly-named empty-payload test renamed (`test_a_patch_that_changes_nothing_defers_nothing` → `test_an_empty_patch_defers_nothing`) plus a same-value-patch behaviour test added, both in `tests/test_chunk_context_header.py`; `library eval-recall --only <case> --write-baseline` is now refused (it would silently overwrite the baseline with just that one case), matching the existing `--ask` guard; `_top_k_arg`'s missing-argument path is now clamped through `ask_search_max_top_k` exactly like an explicit value (an operator-configured default above the ceiling previously bypassed it entirely), and the `top_k` tool-description text no longer asserts a specific default it cannot guarantee; `semantic_search` now strips `review_status` after the shared `_filters_from_args` call, so a model emitting it (the schema does not declare it — see the `_REVIEW_STATUS_PROPERTY` comment) cannot silently narrow a search this tool's coverage block has no way to explain; and a new test in `tests/test_embed_comments.py` asserts comment chunks receive the same document header as content chunks (spec §8.5), which nothing previously checked. Nothing about the corpus, the acceptance criterion, or the missing-baseline state changed in this pass — see `journal/260827-retrieval-reach-fix-wave.md`. Earlier (2026-08-27): (retrieval reach (Plan B, findings #5/#6/#7/#15): §1.10 item 6 — "`semantic_search` takes no metadata filters" — is retired and the list renumbered, since the tool now accepts the same `_FILTER_PROPERTIES` as `query_documents`/`compare_to_series` (not `review_status`, which only a tool that can report a `filtered_review_status` drop is offered) and a clamped `top_k` (`LIBRARY_ASK_SEARCH_MAX_TOP_K`, default ceiling 50; non-positive values clamp to `1` rather than silently slicing from the end of the ranking). §1.2 step 2 documents that surface and the result's new `coverage` block (`matched`/`returned`/`unembedded`). Two new limitations recorded: §1.10 item 10 (chunk context headers, embedding a `sender · date · kind · title` line per chunk since migration `0031`, go stale until a re-embed — deferred automatically when one of those four fields is edited, but pre-`0031` chunks need `--include-existing`) and item 11 (`matched` counts documents, not passages — the honest reading of finding #14, not a fix for it). New *Measuring recall* subsection (§1.2, beside *Measuring disclosure*) documents `library eval-recall`'s two layers, where it runs (nightly, not a merge gate — no embedder in the PR gate and no arm64 TEI image), and the corpus's own acceptance criterion (baseline mean recall@10 below 0.90, spec §8.6). **No baseline has been measured**: this development machine is arm64 with no embedder reachable, so `library eval-recall` has never been run against real bge-m3 vectors, `recall-baseline.json` does not exist in this repository, and the chunk-context-header change's effect on recall (the `sender-named-bare-chunk` case it was built to move) is consequently unverified — a design intent, not a measured result. See `journal/260827-retrieval-reach.md`.). Earlier (2026-08-27): (docs(ask): corrected the stamp's stale claim that the disclosure rule's effect on real answer wording is unmeasured — `library eval-disclosure` (new §1.2 subsection, *Measuring disclosure*) now measures it on demand and was run once against an isolated scratch database, with all six scenarios, including the control, passing; that is evidence, not continuous verification, since CI holds no model credentials to gate on it. The new subsection also documents what the eval measures, the exact invocation, why it is a CLI command rather than a test, that it seeds and rolls back rather than touching real data, and why the control scenario exists. No other prose in this document was touched by this pass.). Earlier (2026-08-27): (final whole-branch review fix wave, three doc-only corrections: (1) §1.2's `compare_to_series` reasons list said the four reasons are "not a chained refinement of one aggregate like the three above" — false for the first three, which chain exactly as `sum_amount`'s reasons do (`no_amount` → `other_series_group` → `other_currency`, each "survived every earlier gate, fails this one"); only `manually_excluded` is the structural exception. Rewritten, because the old wording could invite a future reader to "fix" the code into independent gating — the same double-counting bug a sibling branch already shipped and had to fix. (2) The adjacent `other_currency` parenthetical said dropped documents are "still listed in `other_currencies`" — wrong: `other_currencies` skips a `NULL` currency by construction while the `other_currency` exclusion count does not, so an amount-bearing, currency-`NULL` document lands in `excluded.other_currency` but is never named in `other_currencies`; corrected. (3) §1.10 gained a new item 10 for the early-`status="insufficient"`-predates-overrides gap: §1.7 already explained it in full, but §1.10 — the limitations register a reader actually scans — had nothing pointing there, wrongly implying `compare_to_series` carries no coverage limitation. `src/library/series.py`'s `_insufficient` also had its `currency`/`other_currencies` threaded through on the post-bucketing call site (previously hardcoded `null`/`[]` even once a currency bucket was chosen); a code change, covered by a new test, not itself a doc correction. Earlier (2026-08-26): (a code review on this same branch caught an undisclosed gap this task's first pass missed: `summarize_series`'s early `status="insufficient"` exit — taken before a currency bucket is chosen, when too few documents even match the caller's filters — returns before any PIN/EXCLUDE override is resolved, so on that path `coverage`'s numbers, and `status` itself, can predate an override that would have changed them. Pre-existing `summarize_series` behaviour, not introduced by this branch; only the coverage numbers now surfaced there are new. §1.2's optional-`coverage` paragraph now flags this instead of implying unconditional trust, and §1.7's *Coverage* subsection explains it in full. The `SeriesCoverage` docstring in `src/library/series.py` was narrowed to scope its "invariant holds for every combination of PIN and EXCLUDE" claim to the paths where overrides actually run — a docstring-only change, no logic touched). Earlier (2026-08-26): §1.2: the *Coverage and trust on structured results* subsection now also covers `compare_to_series`, which carries the same `coverage` block on the same terms as `query_documents`; documented its four exclusion reasons — `no_amount`, `other_series_group`, `other_currency`, `manually_excluded` — and that the last of these comes from a persisted PIN/EXCLUDE override rather than a chained filter, with the partition invariant holding across every override combination; §1.7: new *Coverage* subsection describing the series' deliberate narrowing to one `(sender, kind, currency)` triple, now reported rather than silent, and that `review_status` still isn't offered as a filter there even though `needs_review` is reported; §1.10: item 10 removed — it asserted `compare_to_series` reports no coverage, which this branch made false since the tool only ever reaches an emergent series summary, which always carries a populated block). Earlier (2026-08-26): §1.2: new *Coverage and trust on structured results* subsection — every `query_documents` aggregate now returns a `coverage` block (`matched`/`included`/`excluded`/`needs_review`) beside its rows, and the system prompt requires the model to disclose a non-empty `excluded` or a non-zero `needs_review`; §1.10: three new limitations — `semantic_search`'s missing metadata filters, `sum_amount`'s document-not-period coverage, and no-answer citation suppression keyed on the `_NO_ANSWER` sentinel. Earlier (2026-08-25): §1.2 *Archive context*: the system prompt now names the user, their recipient names, their free-text **About you** notes (Settings → Ask) and the archive's kind/tag/project/matter/sender vocabulary; `query_documents` and `compare_to_series` filter by `recipient_contains`, `projects`, `matters`, `tags`. Earlier (2026-08-22): the composer is one flat full-width bar — the nested pill is gone. Earlier (2026-08-21): adaptive thinking on the tool loop, with the answer-token and tool-turn caps raised to match). Earlier (2026-08-21): prompt caching inside the tool loop and token accounting that counts cached tokens; document layout is the DEFAULT at `lg+` with the collapsed rail's actions in the thread bar; per-table horizontal scroll containment. Earlier (2026-08-20): `LIBRARY_ASK_LLM_BACKEND` — Ask's tool loop and title call can run against a Claude subscription instead of the metered API; §1.4. Earlier (2026-07-21): two-screen, route-driven Ask (Option B) and the desktop fixed-height fill; §1.6.)
**Last verified:** 2026-08-27 — method: for this fix-wave pass, read the full diff of every change before writing this entry: `.github/workflows/e2e-nightly.yml`'s reordered `Measure retrieval recall` step and its rewritten header comment; `_seed_corpus` and its new post-condition in `src/library/cli.py`; `ASK_SYSTEM_PROMPT_TEMPLATE`'s coverage rule and `_run_semantic_search`/`_top_k_arg` in `src/library/ask/engine.py`; and confirmed by running the full backend suite (`uv run pytest -q`, 1841 passed), `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy`, and `uv run python scripts/check_docs.py` after these edits — see `journal/260827-retrieval-reach-fix-wave.md`. For FIX 4, additionally deleted the two-line re-embed hook in `ask/engine.py`, re-ran the two new tests to confirm the header-field one fails (0 jobs deferred instead of 1) while the non-header companion still passes, then restored the file and diffed it byte-identical to the pre-mutation copy. Still true, unchanged by this pass: `recall-baseline.json` does not exist in this repository and `library eval-recall` has still never been run against a real embedder (this machine is arm64 with none reachable) — no baseline was written, none was invented, and the acceptance-criterion and #6-effect claims below remain exactly as unverified as before this pass. The rest carries forward its previous verification: for this docs pass (retrieval reach), read `library.ask.recall_eval` (`score_recall`/`RecallVerdict`) and `library.ask.recall_scenarios` in full (53-document `CORPUS`, the six `CASES` including `sender-named-bare-chunk`'s docstring naming it as the #6 case); `library.cli`'s `eval_recall`, `_report_recall`, `_seed_corpus` and `RECALL_BASELINE_PATH`; `ask/engine.py`'s `_FILTER_PROPERTIES`/`_REVIEW_STATUS_PROPERTY` split, the `semantic_search` tool schema, `_run_semantic_search` and `_top_k_arg` (confirmed the negative-slice comment and the `max(1, min(...))` clamp); `library.search`'s `SearchReach`/`search_reach`; `library.jobs.compose_context_header` and `run_embed`'s header composition; `library.documents_service.HEADER_FIELDS`/`header_fields_changed`; migration `0031_chunk_context_header.py`; and `.github/workflows/e2e-nightly.yml`'s `eval-recall` step and its header comment explaining why it is nightly-only. Confirmed by `grep -rn "§1.10" docs/ src/ frontend/src/` that no file outside `docs/api.md` (its own, unrelated §1.10 subsections) and `docs/ask.md` itself cites an `ask.md` §1.10 item by number, so the item-6 renumbering needed no other file updated. Confirmed `recall-baseline.json` does not exist in the repository (`git ls-files | grep recall-baseline` empty) before writing the "no baseline measured" claim. Ran `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy`, `uv run python scripts/check_docs.py`, and the full backend suite (`uv run coverage run -m pytest && uv run coverage report`) after these doc edits — see the journal entry for the results. The rest carries forward its previous verification: for this docs pass (disclosure eval), read `library.ask.disclosure_eval.score`/`mentions_count`, `library.ask.disclosure_scenarios` (all six `SCENARIOS`), and `library.cli`'s `eval_disclosure`/`_seed_scenario`/`_coverage_from_turn_messages` in full, alongside `ask/engine.py`'s `_tool_result_payloads` and `_previewed_ids_from_history`, and confirmed the new §1.2 subsection's claims against that code: the eval seeds inside one transaction and rolls it back in a `finally` regardless of outcome; the write tool's confirmation gate cannot be satisfied from a single fresh question with no prior history, so no scenario can commit; and `_coverage_from_turn_messages` now decodes coverage via the shared `_tool_result_payloads` helper (commit `e4e2a09`), which handles both the `api` backend's single-JSON `tool_result` content and the `subscription` backend's double-wrapped content, rather than a second copy of that decode. The six-scenario PASS result (all passing, including the control) quoted in the new subsection is this branch's own recorded live run against an isolated scratch database, not reproduced independently by this pass — this pass did not itself run `library eval-disclosure`, `pytest`, `ruff`, or `mypy`; it ran `scripts/check_docs.py` and the journal-index `--check` after its edits. The rest carries forward its previous verification: for this fix-wave pass, re-read §1.2's reasons-list and the adjacent chained-refinement paragraph against `series.py:483` (`no_amount` gate), `:490-494` (`other_series_group` gate), and `:952-953` (`other_currency` list vs. count, confirming the `NULL`-currency asymmetry: `other_currencies` excludes `c is None`, `other_currency`'s count does not); confirmed the first three reasons chain by tracing `_load_members`/`summarize_series` in order. Re-read §1.10's numbered list to confirm item 10 was genuinely missing (not just misnumbered) before adding the new item. Ran `uv run ruff format .`, `uv run ruff check .`, and `uv run mypy src/library/series.py` (all clean) after the `_insufficient` signature change in `src/library/series.py`, plus `scripts/check_docs.py` and the journal-index `--check` (both clean) after these doc edits; did not run the backend test suite as part of this doc verification, that remains the controller's job. The rest carries forward its previous verification: 2026-08-26 — method: for this second pass, re-read `summarize_series` end to end in `src/library/series.py` (both `status="insufficient"` exits — the early one before currency-bucket selection, at `settings.series_min_documents` over raw filter matches, and the later one after `_apply_overrides`/`_coverage_after_overrides` have run) to confirm which one skips override resolution, and checked `git show main:src/library/series.py` to confirm the early-return shape predates this branch. Ran `scripts/check_docs.py` (clean) and the journal-index `--check` (clean) again after this edit. Because this pass also edited `src/library/series.py` (a docstring only, no logic), it ran `ruff format .`, `ruff check .`, and `mypy src/library/series.py` this time — all clean — but it still did **not** run the backend test suite; that remains the controller's job. The disclosure rule's effect on real answer wording is no longer unmeasured, corrected in this pass: `library eval-disclosure` (§1.2, *Measuring disclosure*) exercises it directly and was run once, with all six scenarios — including the control — passing. That is evidence gathered on demand by a human running the command, not a continuously-verified property: CI holds no model credentials, so no CI gate gives repeat assurance between runs, and a future regression in answer wording would not be caught automatically.
**Covers:** src/library/ask/

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
   compare to last year?", "are my bills going up?") — answered by the series
   engine against the statistical distribution of recurring documents from the
   same sender and kind.

## 1.2 How it works

```
question ─▶ Claude (tool-use loop) ─┬─▶ semantic_search ──▶ hybrid retrieval ─┐
                                    │                       (FTS + vector RRF) │
                                    ├─▶ query_documents ───▶ structured query ─┤
                                    │                       (sender/kind/date) │
                                    ├─▶ compare_to_series ─▶ series stats ─────┤
                                    │                       (distribution/     │
                                    │                        trend/YoY)        │
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
   **length normalization** (Postgres bitmask `1`: divide rank by `1 + log(length)`),
   so a long, multi-topic document cannot out-rank a short on-topic invoice
   merely by repeating the matched term — score reflects match *density*. For
   long documents, Ask retrieval also pulls the `LIBRARY_RETRIEVE_CHUNKS_PER_DOC`
   nearest chunks per result (best-first) and joins them into the excerpt with a
   `[…]` separator, so multi-topic answers see more than the single best passage.
   The per-document candidate ranking and anti-crowding guarantee are unchanged
   (one chunk per document still drives fusion).

   **Scoping the search.** `semantic_search` accepts the same metadata filter
   properties as `query_documents` and `compare_to_series` — `kind`,
   `sender_contains`, `recipient_contains`, `projects`, `matters`, `tags`,
   `date_from`/`date_to` (§1.2 step 3) — so a content question naming a year or
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
   fact (§1.10 item 11).
3. **Structured query** (`query_documents`). Aggregations over the extracted
   columns: distinct senders, summed amounts (by currency, optionally grouped by
   sender/kind), and document lists. Filters are the list API's
   `DocumentFilters` vocabulary: `kind`, `sender_contains`, `recipient_contains`,
   a date range, and the user's own organisation — `projects` and `matters`
   (a document in *any* of the given slugs matches) and `tags` (a document must
   carry *all* of them). Blank strings and empty lists — which the model does
   send — are treated as absent. Every row carries the contributing document
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
4. **Series comparison** (`compare_to_series`). Statistical summary of a
   recurring-document series — see §1.7 for details. Takes the same filters as
   `query_documents`, so a series can be pinned to a matter or a recipient. Returns distribution
   (count/mean/median/stdev/min/max), a reference-vs-usual verdict, a trend
   direction, and a year-over-year comparison. All members contribute their ids
   to the citation set.
5. **Full-document read** (`get_document`). Once another tool has located a
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
slug: Ask's own write tool can create both, so nothing else bounds them), and
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
sorted, and no counts or timestamps appear in it — because it sits inside the
cached prompt prefix. It shares the static prompt's cache breakpoint rather
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

Every `query_documents` result carries a `coverage` block beside its rows, and
so does `compare_to_series` (§1.7) — the two tools share the same shape:

| Field | Meaning |
|-------|---------|
| `matched` | Documents that met the call's filters |
| `included` | Documents the rows (or, for `compare_to_series`, the statistics) actually account for |
| `excluded` | Reason → count for the difference; `{}` when the rows are the whole story |
| `needs_review` | Of `included`, how many carry a `needs_review` extraction flag |

`included + sum(excluded.values()) == matched` is an invariant, pinned by
`tests/test_structured_query.py` for `query_documents` and by
`tests/test_series_db.py` for `compare_to_series`.

`coverage` is optional on the series side: it is present whenever
`compare_to_series` returns (this tool only ever resolves an emergent
`(sender, kind, currency)` series, never a user-authored one — see §1.7), so
the block itself is never missing from this tool's results. The field exists
to let `None` mean "not reported" for the authored/Smart-Group series this
tool cannot reach, as distinct from a present block with an empty `excluded`,
which means "nothing was dropped". That said, presence isn't the same as
completeness: §1.7 describes a near-threshold case where the numbers — and
even `status` itself — predate any override, so read that caveat before
trusting the block fully on an `"insufficient"` result.

Each aggregate's exclusion reasons are built as **successive refinements of one
include chain**, not independently-gated conditions. `sum_amount`, for
example, starts from "has an amount", narrows to "and is not a quote" (unless
the caller is asking about quotes specifically), then — only when
grouping — narrows again to "and has the group-by column". Each reason
therefore means "survived every earlier gate, fails this one", so the reasons
partition the matched set by construction: a document that is both a quote and
senderless lands under `quote_not_spend` alone, never under both. An earlier
version of this gated each reason independently off "has an amount", which let
that case match two reasons at once and broke the invariant above; it was
caught before release and fixed by chaining the conditions instead.

The reasons a document is dropped, by aggregate:

- `sum_amount` — `no_amount` (extraction found no total), `quote_not_spend`
  (quotes are not expenditure; see below), and `no_sender`/`no_kind` — present
  only when grouping by that column, whose inner join drops a document
  lacking it.
- `distinct_senders` — `no_sender` (its inner join to `Sender` drops a
  document with no extracted sender).
- `list` — `over_limit` (the result limit is 50 and the drop is positional —
  which documents fall off depends on sort order, not a predicate).
- `compare_to_series` — `no_amount` (the document carries no extracted total,
  so it cannot contribute a data point), `other_series_group` (a
  loosely-filtered query matched more than one `(sender, kind)` pair; only the
  most-populous group becomes the series), `other_currency` (the series
  bucket is one currency; every other currency present is dropped from the
  statistics), and `manually_excluded` (the user persisted an EXCLUDE
  override on this document — see [api.md §1.15](api.md)).

  `other_currency` and `other_currencies` (the field, plural) are not the same
  list. `other_currencies` names the *codes* present outside the chosen
  bucket, and skips a `NULL` currency by construction; `other_currency` (the
  exclusion count) tallies every document outside the chosen bucket,
  `NULL`-currency ones included. An amount-bearing document whose `currency`
  is `NULL` — extraction found a total but no currency — is therefore counted
  in `excluded["other_currency"]` but never named in `other_currencies`.

`compare_to_series`'s first three reasons **are** a chained refinement, the
same "survived every earlier gate, fails this one" rule as the three
aggregates above: a document must have an amount (`no_amount`) before its
`(sender, kind)` group can be judged dominant-or-not (`other_series_group`),
and must be in the dominant group before its currency bucket can be judged
chosen-or-not (`other_currency`) — so an amountless document in a
non-dominant group lands under `no_amount` alone, never both. Only
`manually_excluded` breaks that chain: it comes from a persisted PIN/EXCLUDE
override layered on afterwards, a structurally different mechanism, not a
fourth gate in the same sequence. A PIN is keyed on the resolved series
identity, not on the call's filters, so it can restore a document the filters
would otherwise have dropped as `other_series_group` or `other_currency`
(subtracted back out of whichever reason it would have landed in, so it is
never double-counted), or pull in a document the filters never matched at all (which grows `matched`
itself — `matched` is "everything the filters matched, union anything
pinned in"). Either way the `included + sum(excluded.values()) == matched`
invariant holds across every combination of PIN and EXCLUDE
(`tests/test_series_db.py` pins this explicitly, including the override
case).

The system prompt requires the model to disclose a non-empty `excluded` and a
non-zero `needs_review` in its answer, so a partial total reads as one. It is
also told **not** to filter flagged documents out of a total to avoid the
caveat — `review_status` is offered as a filter for *listing* what needs
checking, not for quietly improving a number.

`needs_review` is a trust signal about the *extraction*, not the document: it
usually means `library.extraction.validation`'s `amount_grounding` rule fired,
i.e. the amount being summed does not appear anywhere in the document's text.

### Measuring disclosure: `library eval-disclosure`

The paragraphs above describe the `coverage` block and the prompt instruction
built on top of it. Whether the model's answers actually *follow* that
instruction is a different question, and it is checked by a command, not a
test:

```
LIBRARY_CLAUDE_CONFIG_DIR="$HOME/.claude" uv run library eval-disclosure
```

`library.ask.disclosure_scenarios` defines six synthetic scenarios, each
naming documents to seed and a question expected to route to
`query_documents` or `compare_to_series`. The command seeds one scenario's
documents at a time, drives the real Ask loop against it, and scores the
answer with `library.ask.disclosure_eval.score` for whether it named every
non-zero `excluded` reason and any non-zero `needs_review` the tool's
`coverage` block actually reported.

**What it measures, exactly.** The reasons list above names nine exclusion
reasons across `sum_amount`, `distinct_senders`, `list` and
`compare_to_series`. This eval exercises exactly **four** of them: `no_amount`
(`utilities-no-amount`), `quote_not_spend` (`spend-excludes-quotes`),
`over_limit` (`list-truncation`), and `other_currency`
(`series-other-currency`) — plus a `needs_review` case (`flagged-amounts`,
unrelated to `excluded`). The remaining five — `no_sender`, `no_kind`,
`other_series_group`, `manually_excluded`, and `filtered_review_status` — are
**not** measured by any scenario here; a green run says nothing about whether
the model discloses those. The sixth scenario, `complete-no-gaps`, is a
**control** where nothing was dropped — without it, a model that hedges in
every answer regardless of the facts would score a perfect pass, which is the
opposite of what the eval is for.

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
all six scenarios passed, including the control — the model disclosed every
excluded-reason count and the `needs_review` count the tool actually
reported, on both `query_documents` and `compare_to_series`, and invented no
caveat on the control question where nothing was dropped. That is evidence,
not a guarantee, and it is not continuous: the eval is measurable on demand by
a human running the command above, but CI has no model credentials to run it
as a regression gate, so a future change could regress disclosed wording
silently between runs.

### Measuring recall: `library eval-recall`

Disclosure asks whether an answer owned up to a gap; recall asks the prior
question — whether the documents that could answer it were *retrieved* at
all. `library.ask.recall_eval` (`score_recall`/`RecallVerdict`) scores
recall@k against `library.ask.recall_scenarios`'s synthetic corpus — 53
documents authored for this purpose, every sender name carrying a
`(recall-eval fixture)` suffix so it can never collide with real archive data
and reads as synthetic at a glance. **The corpus is public-repo-safe by
construction**: no sender, amount, date or sentence in it resembles anything
real. Six cases each name a question and the document ids expected back;
every case shares one seeded haystack (a shrinking per-case corpus would make
recall@10 meaningless once fewer documents exist than slots), and each ships
hand-authored near-miss distractors — same sender, same kind, adjacent dates,
overlapping vocabulary — so the corpus has headroom to fail at baseline and
room to show a retrieval change moving it.

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
runs `library eval-recall` (layer 1, no `--ask`) after the Smart Groups journey
and its "did it actually run?" assertion, with `continue-on-error: true` — the
step **reports** recall and does not gate on it. It cannot run in the PR gate:
that job starts no embedder at all, and TEI (the bge-m3 sidecar) publishes no
arm64 image, so it also cannot run on an Apple Silicon development machine —
only a host with a reachable embedder (Linux/amd64, or the deployed host) can
drive it. It also cannot gate the nightly today even if it were made to: the
corpus is deliberately built so some cases fail at baseline (below), and
`recall-baseline.json` does not exist in this repository, so there is nothing
yet for a regression to be measured against. Gating on a regression becomes
possible once a baseline has been recorded with `--write-baseline`;
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

**No baseline has been measured.** This development machine is arm64 with no
embedder reachable, so `library eval-recall` and `library eval-recall --ask`
have never been run against real bge-m3 vectors — neither on this branch nor
before it. `recall-baseline.json` does not exist in this repository. The
§8.6 acceptance criterion above is therefore **unverified**: nobody has
confirmed the corpus is hard enough to measure anything. Consequently, the
chunk-context-header change (§1.10 item 10, the fix for finding #6) has
**not been shown to improve recall** — `sender-named-bare-chunk` is the case
it was built to move (its target's body states neither its sender nor its
year; both live only in metadata), but the before/after delta on that case
has never been measured. Producing the numbers requires a host with a
reachable embedder — the deployed host, or a future nightly run:

```
uv run library eval-recall --write-baseline   # records recall-baseline.json
uv run library eval-recall --only sender-named-bare-chunk   # the #6 case alone
uv run library eval-recall --ask              # layer 2; additionally needs Claude credentials
```

Until one of these has actually been run, treat every claim above about #6's
effect on retrieval as a design intent, not a measured result.

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
| `LIBRARY_SERIES_MIN_DOCUMENTS` | `3` | Minimum members before series stats are reported; below this `status:"insufficient"` is returned. |
| `LIBRARY_SERIES_TYPICAL_PCT` | `0.10` | Half-width of the "typical" verdict band as a fraction of the median (OR'd with ±1 stdev). |
| `LIBRARY_SERIES_FLAT_PCT` | `0.05` | First→last change fraction at or below which the trend direction is reported as `flat`. |

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
That is one observation, not an accuracy measurement.

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

## 1.7 Document series + comparative queries

The `compare_to_series` tool answers questions about recurring documents — a
monthly energy bill, an annual insurance renewal — by computing live statistics
over the series they belong to.

### Series detection

A **series** is the set of documents that share the same `(sender_id, kind_id)`
pair and carry an `amount_total`. The engine identifies the series automatically
from the `kind` and `sender_contains` parameters the model supplies (plus the
other §1.2 step 3 filters — recipient, projects, matters, tags — when it
narrows further); no user tagging or configuration is needed. If a loose filter matches multiple
(sender, kind) combinations, the most-populous group is used.

Detection is **on the fly** — there is no materialized series table, and the
*statistics* are recomputed at query time from the live document set. The only
thing cached per series is the natural-language **description** (see below).

### Four statistical framings

Every series summary provides four views:

| Framing | What it answers |
|---------|----------------|
| **Distribution** | Mean, median, stdev, min, max over the series' amounts. |
| **Reference-vs-usual** | Where the reference document falls: `higher`, `typical`, or `lower`. |
| **Trend** | Whether amounts are `rising`, `falling`, or `flat` over time (`flat` when first→last change ≤ `SERIES_FLAT_PCT`; otherwise the sign of the least-squares slope decides). |
| **Year-over-year** | The member closest to 12 months before the reference date (within a cadence-dependent tolerance) and the percentage change. |

The cadence (`monthly`, `quarterly`, `yearly`, `irregular`) is derived from the
median gap between consecutive document dates, and influences the YoY match
tolerance.

### Typical-band rule

The `typical` verdict is given when the reference value is within **±1 stdev
OR within ±`SERIES_TYPICAL_PCT` (default 10%) of the median**. The OR ensures
that a very tight, consistent series (small stdev) doesn't flag normal variation
as `higher`/`lower`; the percent band handles the degenerate case where stdev is
zero or very small.

### Currency bucketing

Amounts in different currencies are kept separate and cannot be combined. The
bucket reported is the one matching the reference document's currency; if
unspecified, the dominant (most-document) currency is used. Other currencies
present in the series are listed in `other_currencies`.

### Coverage

A series is deliberately narrowed to one `(sender, kind, currency)` triple —
the dominant `(sender, kind)` group (above), then the one currency bucket
(above), then documents with no `amount_total` are dropped because they carry
no data point. That narrowing used to happen silently; it is now reported via
the `coverage` block described in [§1.2](#12-how-it-works) (`matched` /
`included` / `excluded` / `needs_review`), so an answer can say what fraction
of the matching documents its "usual" band actually covers instead of leaving
the caller to assume it was all of them. Unlike `query_documents`,
`compare_to_series` does not accept `review_status` as a filter — none of its
four exclusion reasons is a review-state gate, so offering the filter would
promise something the tool cannot honour — but `needs_review` is still
reported as a count within the block, so an answer can flag that some of what
it included is unverified even though it cannot filter on that state.

**Near-threshold results can predate overrides — including `status` itself.**
Before choosing a currency bucket, `summarize_series` first checks whether
enough documents even match the caller's filters at all
(`settings.series_min_documents`). If they don't, it returns
`status="insufficient"` immediately — before picking a currency bucket and
therefore before resolving any PIN/EXCLUDE override, since overrides are
keyed on a resolved `(sender, kind, currency)` identity that doesn't exist yet
at that point. On that path `coverage.excluded` only ever holds `no_amount`
and `other_series_group` (never `manually_excluded`), and — more
importantly — both `included` and the `"insufficient"` verdict itself predate
any override: a series the owner has PINned enough documents into to clear
the threshold can still be reported `"insufficient"` here, and a document the
owner has EXCLUDEd is still counted in `included`. This is pre-existing
`summarize_series` behaviour (present before this coverage feature — it
already returned early on too few matching documents), not something
introduced by coverage reporting; only the numbers now surfaced on that path
are new, and this is the one case where they don't reflect overrides at all.
A series that clears the threshold on filters alone — the ordinary case — is
unaffected: its `coverage` (and any later `"insufficient"` verdict from too
few documents in the *chosen currency bucket*) is computed after overrides
have run, with the full four-reason partition described above.

### Cached series descriptions

Each series also carries a one- or two-sentence **LLM-generated prose
description** (e.g. *"Energy bills have crept up ~12% over the past year, peaking
in winter"*). Because that costs an LLM call, it is **precomputed and cached**
rather than generated per request:

- **Storage.** One row per `(sender_id, kind_id, currency)` in the
  `series_insights` table (`library.models.SeriesInsight`), with the description,
  the generating model, the member count it was generated over, and token/cost
  provenance. The unique key treats a NULL currency as a single bucket
  (`NULLS NOT DISTINCT`).
- **Generation.** `library.series_insight.refresh_series_insight` summarises the
  series, builds a compact stats prompt, and calls the **extraction LLM client**
  (`settings.extraction_model`, the cheap Haiku tier) to write the prose, then
  upserts the row. It is best-effort: a disabled feature, a missing API key, or
  an insufficient series all skip quietly.
- **Membership hints.** If the owner has manually pinned/excluded documents
  for this series (see [api.md §1.15](api.md)), up to
  `MAX_OVERRIDE_EXAMPLES` examples per direction are appended to the prompt as a
  labelled, authoritative "curated membership" block, and the system prompt is
  told to weight them — so the description reflects the corrected series. The
  cap bounds prompt size and cost; tests assert prompt construction only (no
  live LLM call).
- **Trigger.** The `library.jobs.generate_series_insight` Procrastinate task is
  deferred whenever a document reaches `indexed` with both a sender and a kind,
  so the description refreshes as the series grows.

`summarize_series` attaches the cached description (and per-point document
`title`s for citation links) to its output; `serialise_summary` includes them in
the API body. The description is absent until the first successful generation.

### Detail-view trend widget + the /charts view

The document detail view includes a **`DocumentSeriesTrend`** panel that fetches
the document's series on mount and renders a **`SeriesChartTile`**: a Chart.js
bar chart of the series' dated points (current document's point highlighted),
the cached description, a one-line verdict (e.g. *"6.4% above usual · trend
rising"*), and a list of **citation links** (each point → `/documents/{id}`). The
panel hides itself silently when `status:"insufficient"` or on fetch error.
It also carries its own `ChartControls` row (time range, from/to, group-by),
persisted under `library:doc-series-*` keys, and shows a `doc-series-empty`
state when the selected window contains no points.

The **`/charts`** view (sidebar nav) renders a responsive grid of the same
`SeriesChartTile`, one per eligible series, fed by `GET /api/charts`. Tiles here
have no per-document reference, so the latest member is highlighted.

The raw data is supplied by `GET /api/documents/{id}/series` and `GET /api/charts`;
see [api.md §1.13–1.14](api.md) for the wire contracts.

## 1.8 Editing document metadata (the write tool)

Ask is an agentic tool-use loop, and beyond the four read-only retrieval tools
(`semantic_search`, `query_documents`, `compare_to_series`, `get_document`) it
carries one **write tool, `update_document_metadata`** (`ask.engine`), so a
conversation can *correct* a document's metadata, not just read it. Writes are
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
9. `compare_to_series`'s coverage and `status` itself can predate overrides on
   a near-threshold series. When too few documents even match the caller's
   filters, `summarize_series` returns `status="insufficient"` before
   choosing a currency bucket and therefore before resolving any persisted
   PIN/EXCLUDE override — so a series a PIN would push over the threshold can
   still report `"insufficient"`, and a document an EXCLUDE would drop is
   still counted `included`. See §1.7 for the full explanation.
10. **Chunk context headers reflect metadata as of the last embed.** A chunk
    embeds a `sender · date · kind · title` line alongside its text. Editing
    one of those four fields defers a re-embed, so the header self-heals — but
    chunks written before migration `0031` carry no header at all until
    `library backfill-embeddings --include-existing` is run, and until then a
    question naming a sender cannot match those documents on metadata.
    Structured filters are unaffected: they read live metadata, not the
    chunk's stored header.
11. **`semantic_search`'s `matched` counts documents, not passages.** A
    document matching the filters but carrying no chunks is counted in
    `matched` and reported in `unembedded`, but is unreachable by vector
    search. That is the honest reading of finding #14, not a fix for it:
    there is still no UI listing documents missing from the index.
