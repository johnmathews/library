# Three critical fixes: pipeline resume, the unreachable `quote` kind, and the ci-gate hole

An engineering-team evaluation of the whole repo (2026-07-26) produced 39 findings and a
27-unit plan. This session implemented the units that could be done without owner
credentials: the two Criticals that were pure code, plus the CI prerequisite for the
third. A code review during wrap-up then caught a Critical *regression* one of the fixes
introduced, which is also fixed here.

## 1. Why these three

The evaluation's headline question was why this project produces more defects than its
peers. The answer had three parts, and these units attack two of them:

1. **Nothing is required to merge.** `main` has no branch protection or ruleset
   (`gh api .../rulesets` → `[]`, force-push allowed, public repo), so every gate in
   `ci.yml` is advisory. Two `failure` runs have already landed on `main`.
2. **Concepts are duplicated across unlinked files with no type checker on the Python
   half**, so one logical change means N correct edits from memory. Six such lists have
   already drifted.

W2 (creating the ruleset) needs `administration: write` and is left for the owner. W1
is its prerequisite.

## 2. W3 — a stage interrupted mid-run was silently skipped

**Grading: confirmed.** Reproduced on a live stack before the fix and again after.

`advance_pipeline` (`src/library/jobs.py`) committed `document.status = _NEXT_STATUS[previous]`
*before* running that stage's hook, so `status` recorded the stage **entered**, never one
that finished. A worker killed inside the OCR hook left `status=ocr, ocr_text=NULL`;
`sweep_stalled_jobs` re-enqueued; the loop computed `_NEXT_STATUS[OCR] = EXTRACT` and OCR
never re-ran. The document reached `indexed` with no text, permanently invisible to
search, and the job logged `Success`.

Live reproduction, document 1 of the local sample corpus:

```
before:                        status=indexed  ocr_len=14390  page_count=9
update ... set status='ocr', ocr_text=NULL
process_document.defer_async(document_id=1)
after (old code):              status=indexed  ocr_len=NULL   page_count=9
worker: process_document[39] ended with status: Success, lasted 0.099 s
```

Control: the same document reset to `status='received'` re-ran OCR and recovered all
14,390 characters — so the pipeline was sound in general and failed *only* on
resume-from-own-status.

**Fix:** re-run the entered stage before advancing past it. Three designs were considered;
committing the status only after the hook succeeds was rejected because it inverts the
meaning of `status` and regresses the live SSE progress signal, and per-stage completion
markers were rejected as another hand-maintained table. The implementer placed the new
block *inside* the existing `try` rather than before it, so a failure on the resumed hook
marks the document `failed` exactly as the same failure would on the first pass — a
better call than the plan's.

The pre-existing regression test passed against the broken behaviour: it set
`status = OCR` and asserted only that the document reached `INDEXED`, never that it had
any text. Rewritten to null the evidence and assert it returns.

## 3. W3's regression — duplicate LLM spend on resume

**Grading: confirmed.** Caught in wrap-up code review, before merge, with a test that
was observed failing.

The W3 fix has a window it cannot see. A hook commits its own results *and* its own
completion event while `status` is still X; the advance to X+1 only lands at the top of
the next loop iteration. A kill in between leaves a **finished** stage indistinguishable
by status from an **interrupted** one, so the resume re-ran it — for `apply_extraction`
and `apply_markdown`, a second billed Anthropic call on work already durably committed.

Worth stating plainly, because it is the shape of the trade: the old code got the
"died *after* the hook" case right and the "died *during* the hook" case catastrophically
wrong. W3 inverted that. The windows are not comparable — "during" is seconds to minutes,
"after" is one commit — and the consequences are not comparable either — a permanently
unsearchable document versus a bounded double charge — so the direction was right. But it
was avoidable.

**Fix:** `force: bool = False` on `apply_extraction` and `apply_markdown`, guarded by
default, with the deliberate re-run tasks (`extract_document`, `markdown_document`)
opting out. The first design considered — an unconditional current-version guard — would
have silently broken three legitimate re-run paths: note body edits
(`api/notes.py`), `POST /api/documents/{id}/extract`, and `backfill --include-current`.
Content changes without a `PROMPT_VERSION` change, so a version-only guard is the wrong
predicate for those callers.

Side effect worth knowing: duplicate `extraction_completed` / `markdown_completed` events
carry `cost_usd` and `todays_spend_usd` sums them, so a duplicated completion inflated the
**daily budget accounting** as well as the bill. The guards remove that too.

Matter classification is guarded at the job rather than in the classifier, because
`sweep-matters --all` deliberately re-runs merge mode over already-classified documents
to pick up a newly added matter; a stamp guard inside the classifier would turn that
command into a no-op.

## 4. W4 — the `quote` kind was unreachable, so spend totals were wrong

**Grading: confirmed.**

Migration 0017 seeded a `quote` kind. `extraction/schema.py` carried the vocabulary in
**two** hand-maintained copies eleven lines apart — a `KIND_SLUGS` tuple and a `KindSlug`
Literal — and neither was updated. The classification prompt is built from `KIND_SLUGS`,
and the structured-output Literal would have rejected `quote` anyway, so no document
could ever be auto-classified as one.

Three downstream systems were already built expecting it: `structured_query.py` maps the
user phrases "quote"/"estimate" onto it, `validation.py` counts it among
`_MONETARY_KINDS`, and `ask/engine.py` tells the model verbatim that *"quotes/estimates
are excluded automatically"* from spend totals. The exclusion is correct code that could
never fire. **Every "how much have I spent" answer in Ask silently included quotes and
estimates.**

**Fix:** the Literal is now the single source and `KIND_SLUGS = get_args(KindSlug)` —
that direction is forced, since `Literal[*KIND_SLUGS]` does not type-check. A new
assertion in `tests/test_migrations.py` pins `set(KIND_SLUGS)` to what the migrations
actually seed, against a real migrated database, so a future migration that adds a kind
without adding the slug fails CI. That guard was observed failing before the fix, naming
`quote` exactly.

The prompt gained a quote-vs-invoice rule (a total on the page does not make a quote an
invoice) and `PROMPT_VERSION` was bumped. Only one consumer selects on that version —
`library backfill` — and it is manual and budget-gated, so nothing re-extracts
automatically.

## 5. W1 — the aggregator could pass having tested nothing

**Grading: confirmed** by a test that reproduces the exact green run.

`ci-gate` omitted `changes` from its `needs:` and treated `skipped` as a pass. Every
gated job declares `needs: changes`, and GitHub reports a job whose dependency failed as
`skipped` — so a broken `changes` (checkout flake, paths-filter outage, malformed
`filters:`) skipped all five, left the gate nothing to reject, and printed *"all upstream
jobs passed or were skipped"*. Latent only because nothing requires `ci-gate` yet; the
point of doing it now is that requiring today's gate would cement the hole.

The logic moved to `scripts/ci_gate.sh` so it is unit-tested rather than only exercised
in anger. `changes` must be exactly `success`; everything else may be `success` or
`skipped` — that tolerance is deliberate and stays, because a skipped *required* check
blocks a merge forever, which once stranded a Dependabot PR.

Two cases the plan did not anticipate are also covered: empty argv, and a missing
`changes=` pair. The second matters most — it is the failure mode if someone later
removes `changes` from `needs:` again, and the script now exits 1 rather than passing
vacuously.

Path filters: `alembic.ini` (copied into the image, read by every migration test) and
`.dockerignore` matched nothing; a `conftest.py` entry was dead (there is no root
`conftest.py`); `ci:` widened to `.github/**`.

## 6. Verification

- Full backend suite **1370 passed** (baseline 1351), coverage **89%** (baseline 88%).
- `make lint` — the exact two commands CI runs — clean over 225 files.
- Every behavioural fix here had its test observed failing first, and for W3 and the
  guards, observed failing again with the fix reverted.

## 7. What is deliberately not done

1. **W2 — the branch ruleset.** Needs `administration: write`, which CI's `GITHUB_TOKEN`
   does not have and an agent should not acquire. The exact `gh api` payload is prepared
   in the run directory. Until it is applied, **every gate in this repo remains advisory**,
   including the one W1 just made sound.
2. **`strict_required_status_checks_policy`.** Left for the owner. It is the only setting
   that catches the two post-merge `main` failures observed, and it costs a full ~30-minute
   re-run per merge. Both facts belong in that decision.
3. **The other 24 plan units** — the coverage tracer (real coverage is 95%, not 88%; the
   gate has ten points of slack), the gitignored real-document test corpus, mypy, local
   dev on arm64, the docs stamp gate. Not attempted; this session was scoped to Criticals.
4. **`fastmcp` 3.4.2 → 3.4.4**, a security release (SSRF allow-list bypass, DNS-rebinding
   defence) on a server mounted into the app lifespan. Deliberately kept out of every unit
   so it can ship standalone rather than buried in unrelated work. It should go first.
5. **Duplicate `generate_thumbnail` defers and duplicate `ocr_completed` events** on a
   resume. No LLM cost; the thumbnail render overwrites its own output. Event-log noise,
   accepted.
6. **`run_embed` re-calling the embedding sidecar** on resume. Local service, not billed
   per call, and it deletes and re-inserts chunks so the result stays correct.
7. **A per-document `queueing_lock` on matter classification.** Two duplicate jobs picked
   up genuinely concurrently by two worker slots could both miss the stamp. Judged not
   worth the API surface for a race that needs the sweeper (minutes) to fire inside a
   sub-second window.
