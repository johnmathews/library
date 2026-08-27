# Retrieval reach — final fix wave

**Date:** 2026-08-27

## What this was

The single fix wave before merging `plan-b-retrieval-reach`
(`journal/260827-retrieval-reach.md`), applying nine findings from a
whole-branch code review against
`docs/superpowers/specs/2026-08-26-ask-semantic-quality-design.md` §8. Two
Important, seven Minor. One commit.

**Deliberately not touched:** `library eval-recall` was not run and
`recall-baseline.json` was not created. This machine is arm64 with no bge-m3
sidecar reachable — the same constraint the branch itself already documents
in `docs/ask.md`. Nothing in this pass changes that state.

## The fixes

**1 (Important) — the nightly recall gate aborted the journey it exists
beside.** `.github/workflows/e2e-nightly.yml` ran `library eval-recall`
*before* the Smart Groups journey, with no `|| true`. `_report_recall` exits
non-zero on any failing case, and the corpus is deliberately built so some
cases fail at baseline (`sender-named-bare-chunk`'s own docstring says
"expected to fail at baseline"; `breadth-many-mentions` is capped at 0.83 by
construction). So the first scheduled run would have exited at that step and
never reached the journey — reinstating exactly the "never runs anywhere"
defect this workflow's own header comment says it exists to fix. Moved the
step to after the journey and its "did it actually run?" assertion, added
`continue-on-error: true`, and corrected the now-false prose in both the
workflow's header comment and `docs/ask.md`'s *Where it runs* paragraph
(neither `_report_recall`'s exit code nor the missing baseline supported the
old "a recall regression reds the nightly" claim). The command's own exit
code is untouched — a human running `library eval-recall` by hand still gets
non-zero on failure, so it can still gate a release manually.

**2 (Important) — `_seed_corpus` had no post-condition that embedding
happened.** `run_embed` is fail-open: a disabled or unreachable embedder is
recorded as an `IngestionEvent` and swallowed, never raised. Verified by
execution: with `LIBRARY_EMBEDDING_ENABLED=false`, `_seed_corpus` seeded 53
documents, created zero chunks, and raised nothing — so `eval-recall` would
have silently scored the FTS leg of RRF alone and reported it as retrieval
recall. `_seed_corpus` now counts chunks per seeded document after
`run_embed` and raises `RuntimeError` naming the likely cause if any document
produced none. New test in `tests/test_recall_seed.py` forces the embedder
off and asserts the raise.

**3 (Important) — the system prompt didn't know about `semantic_search`'s
coverage block.** Task 4 gave `semantic_search` a `coverage` block
(`matched`/`returned`/`unembedded`), but `ASK_SYSTEM_PROMPT_TEMPLATE`'s
coverage rule still named only `query_documents` and `compare_to_series` —
the strongest surface actively implied `semantic_search` carried no coverage,
leaving the `unembedded` disclosure obligation resting only on the (weaker)
tool description. The prompt now names all three tools and states the
`unembedded` obligation as a MUST, in the same voice as the existing
`excluded`/`needs_review` rules. Extended
`tests/test_api_ask.py::test_ask_system_prompt_requires_disclosing_partial_coverage`'s
neighbourhood with a new pinning test.

**4 (Important) — the Ask-side re-embed hook had no test.** Proven by
mutation: deleting the two-line hook in `ask/engine.py` (`if
header_fields_changed(edited): await embed_document.defer_async(...)`) left
71 tests green. Added a test that drives the write tool through a *confirmed*
header-field edit (cross-turn preview-then-confirm, same shape as the
existing `test_engine_confirm_after_prior_turn_preview_writes`) and asserts
exactly one `embed_document` job lands in the `job_connector` in-memory
connector, plus a non-header companion asserting none. Re-verified the
mutation directly for this pass (see *Verification* below) rather than
trusting the earlier reasoning.

**5 (Minor) — misleading test name.** `test_a_patch_that_changes_nothing_defers_nothing`
sent `json={}` — it only tested the empty-payload case, not "nothing
changes" in general (a same-value `PATCH` does defer). Renamed to
`test_an_empty_patch_defers_nothing`; added
`test_a_same_value_patch_still_defers_a_reembed` documenting the accepted
behaviour without changing `apply_document_update`'s semantics.

**6 (Minor) — `--only <case> --write-baseline` silently wrote a one-case
baseline.** `_report_recall` writes `{v.case: v.recall for v in verdicts}`
over the *filtered* list. Guarded the combination the same way `--ask` +
`--write-baseline` already is, with a message naming the reason. New CLI test
mirrors the existing `--ask` guard test.

**7 (Minor) — `_top_k_arg`'s default path bypassed the ceiling.** `value is
None` returned `settings.retrieve_top_k` unclamped, so a misconfigured
`LIBRARY_RETRIEVE_TOP_K` above `ask_search_max_top_k` gave the model's
*default* call more depth than an *explicit* `top_k` at the ceiling is even
allowed to ask for. Both paths now go through the same `max(1, min(...))`
clamp. The tool description no longer asserts "Defaults to 10" — a number it
cannot guarantee once an operator changes the setting.

**8 (Minor) — `semantic_search` silently honoured an undeclared
`review_status`.** The tool's schema doesn't offer `review_status` (a filter
is only offered to a tool that can report what it removed — see the
`_REVIEW_STATUS_PROPERTY` comment), but `_filters_from_args` reads it from
`args` unconditionally and `_run_semantic_search` reused that helper as-is.
A model emitting the field anyway got a silently narrowed search this tool's
coverage block has no way to explain. `_run_semantic_search` now strips
`review_status` via `dataclasses.replace` after the shared helper builds the
filters — still one shared mapping, not a fork. New test seeds a
NEEDS_REVIEW and an UNREVIEWED document and confirms a `review_status`
argument doesn't narrow the result.

**9 (Minor) — no test for comment-chunk headers.** Spec §8.5 requires
comment chunks to carry the same document header as content chunks;
`jobs.py` does it (`context_header=context_header or None` on both branches)
but nothing asserted it. Added
`test_comment_chunks_receive_the_same_document_header` to
`tests/test_embed_comments.py`.

## A defect found and fixed mid-pass, not in the original review

The first implementation of fix 2 used `dict(result.tuples())` to build the
per-document chunk-count map. `Result.tuples()` just returns `self`, and
`Result` exposes a `.keys()` method — so Python's `dict()` builtin took that
as a signal to use the *mapping* protocol (call `result[key]` for each column
name) rather than iterating `(key, value)` pairs, and raised `TypeError:
'ChunkedIteratorResult' object is not subscriptable`. Every test that
exercised `_seed_corpus` failed. Fixed by calling `.all()` first (a plain
list of `Row` objects, with no `.keys()` of its own) and building the dict
from that. Caught by actually running the full suite before committing, not
by reasoning about the code — recorded here because it's exactly the kind of
"looks right, isn't" mistake the plan-code-must-be-executed lesson
(`~/.claude/.../plan-code-must-be-executed.md`) is about, this time in
implementation rather than in a plan.

## Verification

- `uv run ruff format --check .` — clean, 267 files.
- `uv run ruff check .` — clean, whole repo including `migrations/`.
- `uv run mypy` — clean, 104 source files.
- `uv run python scripts/check_docs.py` — clean, 18 documents.
- `uv run pytest -q` — **1841 passed**, run twice in the foreground (once
  after the initial pass, once after the `dict(result.tuples())` fix), no
  skips.
- FIX 4's mutation evidence, re-run for this pass: deleted the two-line hook
  at `ask/engine.py` (`if header_fields_changed(edited): await
  embed_document.defer_async(document_id=document_id)`), ran the two new
  tests — `test_engine_confirmed_header_field_edit_defers_a_reembed` FAILED
  (`assert 0 == 1`, zero jobs in `job_connector.jobs`),
  `test_engine_confirmed_non_header_field_edit_defers_nothing` still passed
  (it asserts zero jobs either way, so it can't detect this mutation — that seam
  is what the header-field test above covers) — then restored the file and
  confirmed it byte-identical to the pre-mutation copy (`diff` empty).

Full commands and output: `.superpowers/sdd/2026-08-27-retrieval-reach/fix-wave-report.md`.
