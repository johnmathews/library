# Retrieval reach

**Date:** 2026-08-27

## What shipped

Plan B of the semantic-quality review
(`docs/superpowers/specs/2026-08-26-ask-semantic-quality-design.md` §8), four
findings:

- **#5 — `semantic_search` scoping.** The tool now accepts the same metadata
  filters as `query_documents`/`compare_to_series` (`kind`, `sender_contains`,
  `recipient_contains`, `projects`, `matters`, `tags`, `date_from`/`date_to`),
  minus `review_status` — offering that filter would promise a
  `filtered_review_status` coverage reason the tool has no way to report. A
  content question scoped to a year or a sender no longer has to search the
  whole archive and rely on ranking alone.
- **#7 — tunable retrieval depth.** `semantic_search` accepts `top_k`, clamped
  into `[1, LIBRARY_ASK_SEARCH_MAX_TOP_K]` (new setting, default ceiling 50)
  rather than rejected out of range, so the model can ask for more than the
  shipped default of 10 on a "find every document about X" question.
- **#14 (surfaced, not fixed) — search reach.** Every `semantic_search` result
  now carries a `coverage` block: `matched` (documents passing the filters),
  `returned` (hits actually returned), `unembedded` (of `matched`, how many
  carry no chunks at all and are therefore invisible to vector search no
  matter what the query says). This distinguishes "the archive doesn't say
  this" from "the documents exist but were never indexed" — a distinction
  `matched` alone could not make.
- **#6 — chunks carry their document's identity.** `document_chunks` gained a
  `context_header` column (migration `0031`): a `sender · date · kind · title`
  line, composed by `jobs.compose_context_header` and prepended to the chunk
  text before embedding. A chunk that is otherwise just a figures block (e.g.
  "Bedrag EUR 0,00") now carries a trace of who sent it, when, and what kind
  of document it is, so a question naming any of those can match it on
  meaning. Editing `sender`, `kind`, `title` or `document_date`
  (`documents_service.header_fields_changed`, keyed on the storage-level
  names `apply_document_update` actually returns — see the defect below)
  defers a re-embed via `library.jobs.embed_document`, at both write sites
  (`PATCH /api/documents/{id}` and Ask's own `update_document_metadata`
  tool). Chunks written before migration `0031` carry no header until
  `library backfill-embeddings --include-existing` runs.
- **#15 — a recall eval.** `library.ask.recall_eval` (pure, stdlib-only
  `score_recall`/`RecallVerdict`) scores recall@k against
  `library.ask.recall_scenarios` — 53 synthetic documents (every sender name
  suffixed `(recall-eval fixture)`), 6 cases, one shared haystack so a
  shrinking per-case corpus can't make recall@10 trivial. `library eval-recall`
  (+ `--only`, `--ask`, `--write-baseline`) seeds and embeds the corpus inside
  a rolled-back transaction, runs layer 1 (raw retrieval, embedder only) or
  layer 2 (`--ask`, the real Ask loop scored on citations, additionally needs
  Claude credentials), and reports each case plus the mean against
  `recall-baseline.json`. It runs nightly in `e2e-nightly.yml` after the
  embedder warms, deliberately not merge-gating (no embedder in the PR gate;
  no arm64 TEI image, so it cannot run on this development machine either).

`docs/ask.md` §1.10 item 6 ("`semantic_search` takes no metadata filters") is
retired and the list renumbered; items 10 and 11 record the two limitations
this branch introduced (stale pre-`0031` headers; `matched` counting documents
not passages). §1.2 documents the new `semantic_search` surface and adds a
*Measuring recall* subsection beside *Measuring disclosure*.

## The measured numbers: there are none

**This is the load-bearing fact of this entry, not a footnote.** This
development machine is arm64. TEI (the bge-m3 embedding sidecar) publishes no
arm64 image, so no embedder is reachable here, and `library eval-recall` —
either layer — has never been run against real bge-m3 vectors. Not on this
branch, and not before it. `recall-baseline.json` does not exist anywhere in
this repository (`git ls-files | grep recall-baseline` is empty).

Concretely, that means:

- There is no baseline mean recall@10, and no per-case table, because no run
  has ever produced one.
- There is no before/after number for `sender-named-bare-chunk` — the case
  finding #6 exists to move (its target's body states neither its sender nor
  its year; both live only in the metadata the old chunk text carried no
  trace of). Whether the context-header change helps that case, or any case,
  is **unverified**.
- The corpus's own acceptance criterion — spec §8.6: baseline mean recall@10
  must come out **below 0.90**, or the corpus is too easy to measure anything
  — has never been checked. It is possible the corpus scores 1.0 at baseline
  and this whole eval currently measures nothing. Nobody knows yet.

What exists instead is a design-time probe, run against a throwaway 7-document
fixture while planning Task 4/5, of the *slicing bug* `top_k` had to be
clamped against — not a recall measurement, and not against this corpus:
`ranked[:top_k]` with `top_k=-1` returned 6 of the 7 seeded documents,
silently and with no error. That number is about a slicing defect, not about
recall on the real corpus, and it predates any of this branch's code running
against it.

Producing real numbers needs a host with a reachable embedder (the deployed
host, or a future nightly run) and, for layer 2, Claude credentials:

```
library eval-recall --write-baseline
library eval-recall --only sender-named-bare-chunk
library eval-recall --ask
```

## The decision at Task 7 Step 11: deferred, not made

The plan's Task 7 Step 11 called for re-embedding the corpus, re-running
`library eval-recall`, and then deciding — record the decision in this
journal either way — whether to re-embed the deployed archive with
`library backfill-embeddings --include-existing`: adopt if recall improved,
skip if it didn't (a legitimate outcome, not a failure, per the plan's own
framing).

That step could not run here for the same reason as the section above: no
reachable embedder on this machine. **The decision is therefore deferred, not
negative and not positive.** The code ships either way — new documents get
headers at ingest for free, regardless of whether a corpus-wide backfill is
ever justified — but nobody should read the deployed archive's stored vectors
as re-embedded, or as deliberately left unembedded, on the strength of a
measurement that was never taken. The next time a host with a warm embedder
is available, Task 7 Step 11 is the step to run before this decision is made
for real.

## Three defects the plan's own probes caught before implementation

The plan's "Probe results this plan depends on" table (run against the real
test Postgres while the plan was being written, before any task started) is
itself worth recording, because all three shaped the code that shipped:

- **`ranked[:top_k]` slices from the end on a negative `top_k`.** Measured
  directly: `-1` returned 6 of 7 seeded documents, `-3` returned 4, with no
  error raised anywhere — a model passing a stray negative value would have
  gotten a plausible-looking, silently-wrong result set instead of a loud
  failure. `_top_k_arg` clamps into `[1, ask_search_max_top_k]` specifically
  because of this probe, not as generic defensive tidiness.
- **`matched` alone conflates three different situations.** A document that
  matches the caller's filters but carries no chunks was, before this branch,
  indistinguishable in `matched` from a document that simply doesn't discuss
  the topic — "filter too narrow", "archive is genuinely silent", and "never
  indexed" all looked the same. The probe confirmed `func.count().filter(...)`
  could report both counts in one round trip (`matched=2, unembedded` telling
  the two cases apart), which is what `SearchReach`/`search_reach` and the
  tool's `coverage.unembedded` field now do (finding #14).
- **`apply_document_update`'s return mixes storage and body field names.**
  Probed directly: editing `sender` returns `['sender_id']`, `kind_slug`
  returns `['kind_id']`, but `title` and `document_date` return themselves
  unchanged. `documents_service.HEADER_FIELDS` and `header_fields_changed`
  are keyed on the storage-level names (`sender_id`, `kind_id`, `title`,
  `document_date`) precisely because of this probe — keying on the
  request-body names instead would have silently never fired the re-embed
  for a sender or kind edit, since those never appear under their own name in
  the return value.

## A fourth defect: the one function planning didn't execute verbatim

The plan's own convention (spec §8.7) is that every prescribed code block is
run against the real test database while the plan is written, not just
described — and the self-review table records that this held for every task
except one. Task 3's `_seed_corpus`, as prescribed in the plan text, named its
second loop's loaded document `document` — the same name the first loop uses
for the freshly-constructed `Document` it inserts. `mypy` narrows `document`
to `Document` (non-optional) from the first loop's assignment and then
rejects the second loop's `document = await session.get(...)`, which is
`Document | None`. The shipped code renames the second loop's variable to
`seeded_document`. This is the one function in Task 3 that had not, in fact,
been executed verbatim during planning — everything else in the probe table
had been.
