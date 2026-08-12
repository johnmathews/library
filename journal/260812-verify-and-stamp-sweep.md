# Verifying sixteen documents, then closing the ratchet

**Date:** 2026-08-12. **Units:** W27 (batch J of the library-defect-generators run).

The docs gate has been running as a ratchet at a baseline of 15 since it landed:
7 documents with no stamp at all, 8 stamped but never verified. This unit does
the sweep the baseline was holding a place for — read all 16 gated documents
against the code, fix what had drifted, stamp each with a `method:` string that
is true — and then takes the baseline to 0 and puts `docs-stamps` inside
`ci-gate`.

The unit cannot be shortcut by construction: writing `method: X` requires
actually doing X. What follows is mostly what X turned up.

## 1. What the sweep found

Every one of the 16 documents had at least one substantive error. That number is
the finding — this is a repo whose documentation is unusually detailed and
unusually well-maintained, and it still drifted everywhere, because nothing was
comparing prose to code.

The three that mattered most:

**A withdrawn password, still published in two places.** `deployment.md` and
`ingestion.md` both documented `LIBRARY_PDF_UNLOCK_PASSWORDS` as defaulting to
`["2064"]`. That default was deliberately removed from `config.py` — it was a
real personal document password shipped in a public repository — and the code
comment explaining the removal is still there. The docs kept publishing it
anyway. Beyond re-leaking the secret, the claim was operationally wrong in the
worst direction: it told a reader encrypted PDFs unlock out of the box, when on
a stock install every one of them is rejected at ingest.

**An authorization claim that was too broad.** `architecture.md` §1.5.1 states,
deliberately and at length, that there is no per-user resource ownership — and
tells reviewers that an IDOR finding describes the intended model rather than a
vulnerability. That is right about the corpus and wrong in general: Ask threads
and saved views *are* scoped to their owner and genuinely enforce it
(`thread.user_id != user.id`, `_get_owned_view_or_404`). A section written to
pre-empt false findings was itself capable of producing one, in the opposite
direction — a reviewer who took it literally would have waved through a real
regression in either of those two surfaces. It now names the exceptions.

**A retry policy documented as absent.** `ingestion.md` said there is no
per-task retry policy and explained why one would not help. There is one:
`TRANSIENT_RETRY`, 5 attempts, with an allowlist of genuinely-retryable
exceptions — and the allowlist is load-bearing, since it is what stops a
constraint violation being retried five times.

The rest were smaller but the same species: `mcp.md` was missing the
`list_matters` tool and the `matter` search filter; `api.md` was missing
`DELETE /api/admin/users/{id}` and the whole Smart Groups create contract;
`jobs-and-notifications.md` described a two-table Jobs view that became one
ordered table in `8e1f6fb`; `frontend.md` had no `MattersListView`, no
Notifications tab and no PWA section; `frontend-view-principles.md` told new
views to copy `DocumentDetailView` as a header template, which is the one view
that hand-rolls its `<h1>`s in violation of the rule two paragraphs above.

## 2. The gate caught the sweep's own stamps

The first clean run was not clean. All 16 stamps carried the words "W27
verification sweep", and `check_work_unit_citations` — the rule W14 added —
rejected all 16: `W27` is not a unit of `docs/archive/260610-greenfield-build-plan.md`,
which declares W1–W17.

That is the rule working exactly as designed. Work-unit ids from an engineering
run live in `.engineering-team/` and mean nothing to a reader of `docs/`; a
`Wn` token in a gated document is a promise that the reader can resolve it. The
sweep that was closing the gate got caught by the gate, which is the best
evidence available that it is not vacuous. The stamps now say "documentation
verification sweep".

## 3. `Covers:` is deliberately only on three documents

`Covers:` is the change-driven half of the gate: name the code a document
describes, and the gate reds when that code moves. It is the rule the module
docstring calls "the more valuable signal", and until now no document used it.

Three documents get it: `mcp.md` (`mcp_server.py`), `migration.md`
(`importer/`, `cli.py`) and `runbooks/deploy.md` (`deploy.sh`, `ci_gate.sh`).
These are low-churn surfaces that rot silently — `mcp.md` had drifted *because*
`mcp_server.py` gained a tool and nothing forced a re-read.

The wide ones — `api.md` covering `src/library/api/**`, `frontend.md` covering
`frontend/src/**` — are left off, and that is a policy call rather than a
verification one, so it belongs to the owner rather than to this unit. Adding
them would mean **every backend PR is blocked until `api.md` is re-verified and
re-stamped.** That may well be the right trade, but it is a standing tax on all
future work, and it should be chosen rather than arrive as a side effect of a
docs sweep. Left as an open question in the PR.

## 4. The test could not simply be flipped

`test_repo_docs_report_the_expected_violations` asserted the gate reds the tree.
The obvious change is to assert zero violations instead. That would have been a
trap.

The test calls `git_last_commit_date`, and the `backend` job checks out at
`fetch-depth: 1`, under which every file reports HEAD's date. Today that is
harmless — the stamps are dated today, so `last_commit == verified` and
`stale-doc-edit` does not fire. On the next unrelated merge it fires for all 16
documents at once, and `backend` reds while `docs-stamps` (`fetch-depth: 0`)
stays green: a failure that looks like the docs gate is broken, in the job that
is not the docs gate.

So the test now asserts the part that is a property of the *text* — every gated
document parses a stamp, is `active`, has a real non-future date and a non-empty
`method` — and asserts nothing that needs history. The comparative rules keep
their pure unit tests, and `docs-stamps` enforces them for real. This is the
third time `fetch-depth` has shaped a decision in this lane; the docstring now
says why, in the test.

## 5. Both new guards were shown to fail

- Strip the `— method:` from one stamp → the gate exits 1 with `missing-method`,
  and `test_every_gated_doc_carries_a_verified_stamp` fails.
- `scripts/ci_gate.sh … docs-stamps=failure` → exits 1. Run the same arguments
  *without* `docs-stamps` in the list → exits 0, passing a run whose docs job
  failed. That is the silent-ignore failure mode the handover warned about,
  reproduced deliberately: a job must appear in both `needs:` and the argument
  list, and only the second one is load-bearing for the verdict. There is now a
  comment above the call saying so.

## 6. The deploy actually happened

`runbooks/deploy.md` was the one document whose stamp depended on doing
something rather than reading something, and the wording was conditional on it:
the live-host phrasing only if the runbook was really executed.

It was. `promote` was confirmed succeeded job-by-job on `main` at `2c31c4b`
(not by `gh run watch`'s exit code, which returns 0 while a run is still
going), then `make deploy` ran end to end: promote gate, `library-migrate`,
webserver/worker recreate, `/healthz`, and `--status`. The box was 2 weeks
stale at `7d1937b` and is now at `2c31c4b`, confirmed by comparing `/healthz`'s
`git_sha` against `origin/main`. Running the runbook is also what surfaced its
three inaccuracies: the SSH probe runs *before* the promote gate, `--status`
also reports the embedder, and `docs-stamps` sat outside `ci-gate` — the last of
which this same commit makes false by fixing the gate.
