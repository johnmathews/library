# Robustness & consistency improvements (engineering-team run)

**Date:** 2026-07-04. **Branch:** `worktree-eng-library-robustness-and-consistency`.
Run dir: `.engineering-team/runs/manual-20260704T120000Z/`.

A full engineering-team evaluate → plan → develop → wrap-up cycle prompted by:
"document sweep + freshness, test-coverage gap analysis, and investigate whether
the webapp is built in a robust/consistent way." Nothing was on fire, so no triage
pass — a straight even-handed sweep.

## Outcome

23 of 27 planned work units shipped, all green together: **backend 918 tests / 88%
coverage** (from an 85%-on-the-gate baseline), **frontend 719 unit tests**, ruff +
eslint + vue-tsc all clean. Committed in three waves (`fc43262`, `256cac3`,
`25a6c45`) plus a wrap-up commit.

## What shipped

**Test coverage — closed the genuinely-untested branches, not just the numbers:**
- Taxonomy destructive mutations (rename-merge + reassign-and-delete for
  sender/kind/recipient) — `taxonomy.py` 52%→73%, each test asserting the seeded
  documents' FK actually moved.
- Frontend `admin.ts` request builders 25%/0%→100%/100% (the three-state
  `reassign_to` delete encoding + FX `rate_to_base` spread).
- Auth session/API-token lifecycle → `auth/service.py` 100%.
- Charts/authored-series write API 45%→89% (via direct in-event-loop calls).
- Documents download/thumbnail missing-file 404s.
- An admin-write e2e (seed+rename a sender through the real API; runs in CI).

**Frontend consistency — the "two codebases in one coat" problem:**
- Settled the design system's self-contradiction: one `.filter-label` class for
  filter bars vs. the `App*` baked-in label for stacked forms, documented as two
  scoped recipes (view-principles §5).
- Added a chrome-only `.card` class and swept ~20 duplicated card literals; added
  an `AppButton` `size` prop and swept every hand-rolled `btn bg-violet-600`
  primary + raw input to `App*` (fixed the two-violets drift).
- Adopted `PageHeader` on the 4 hand-rolled view headers; removed `max-w-*` caps
  that fight the shell, with enforcement assertions.
- One error-surface contract: consolidated the triplicated hand-rolled load-error
  banner onto `AppBanner`, form errors onto `AppErrorSummary` (view-principles §6).
- Housekeeping: raw `localStorage`→`useStorage`; `taxonomyOptions`
  module-singleton → a Pinia store (call sites unchanged).

**Backend + deploy:**
- Fail-fast on an unpriced model: moved `MODEL_PRICING_USD_PER_MTOK` to a leaf
  `extraction/pricing.py`, added a `Settings` validator that rejects any unpriced
  `*_model` knob at startup, and made `estimate_cost_usd` raise instead of
  silently returning $0 (which had been zeroing the daily-spend budget gate).
- Split the 1213-line `api/admin.py` into an `api/admin/` package
  (`_base`/`users`/`taxonomy`/`fx`) — 19 routes preserved exactly, zero test edits.
- Small robustness nits (capture `failed_in` before rollback; `_ilike_escape`;
  factored ask-history traversal preserving the propose-then-confirm invariant;
  narrowed `confidence` to `high`/`low`; silenced the test-only Starlette warning).
- Pinned every base image by `@sha256` and fixed the runtime to
  `python:3.13-slim-bookworm` (matching the builder so the compiled-extension venv
  runs against the same glibc); added `.github/dependabot.yml`.
- `deploy.sh` promote-gate: verifies `:latest` points at HEAD's image (digest
  compare) before deploying, with `--force`/`SKIP_PROMOTE_CHECK` bypass.

**Docs:** README GOV.UK→Mosaic fix (the front door was still wrong); new
`docs/README.md` index; documented three missing surfaces (MCP `list_recipients`,
`GET /api/documents/{id}/markdown`, admin reference-CRUD rows); roadmap/CHANGELOG
refresh; H1-convention cleanups; marked the completed superpowers plans historical.

## Key findings worth remembering

1. **The "low API-layer coverage" was largely a measurement artifact.** FastAPI's
   `TestClient` runs the app in an anyio blocking-portal thread that coverage.py
   doesn't trace, so endpoint bodies read as "missing" even though the integration
   tests exercise them (`api/admin.py`, `api/documents.py`). Confirmed empirically:
   running the full `test_admin_api.py` with Docker left `taxonomy.py` at 52%.
   Adding `concurrency = ["thread"]` did **not** fix it (coverage already traces
   plain threads; the portal thread is a subtler case) — so I documented the
   limitation in the coverage config and covered the critical branches with
   direct in-event-loop service tests instead. A proper measurement fix is a
   separate follow-up.
2. **A planning subagent claimed the taxonomy/auth/charts gaps were "already
   covered" — it was reasoning from test names, not measurement, and was wrong.**
   Re-measuring settled it. Lesson: verify coverage claims by running coverage,
   not by reading test titles.
3. **One evaluation false-positive (jobs.py MissingGreenlet) was cleared during
   synthesis** — two dedicated tests exercise that exact post-rollback path against
   a real async Postgres and pass. The reviewer had marked it SUSPECTED; verifying
   downgraded it. (W19 still moved the read before the rollback for defensive
   robustness.)
4. **Two evaluation false-positives on the frontend** — DocumentDeleteView and
   DocumentDetailView `<h1>`s are in-card/hero headings, correctly NOT PageHeader
   candidates. The frontend planning agent caught these against the files.

## Deferred (follow-up plan)

Four units left as follow-ups, all flagged L / spike-first / unverifiable-here:
- **W14** AppPopover primitive — needs the API/a11y design spike before refitting
  the 4 bespoke overlays (FilterPill, DashboardFieldsMenu, JobsView menu, header
  dropdowns).
- **W15/W16** decompose AdminView (2473 L) and DocumentDetailView (2245 L) —
  genuine multi-session refactors; the plan calls for a seam spike first, and both
  are backed by very large specs (36k/58k) that make a careless extraction risky.
- **W23** commit the prod compose override — the live `/srv/apps` compose can't be
  verified from here; a wrong reconstruction is worse than none. Reconcile against
  the host on a future deploy.

Full detail: `.engineering-team/runs/manual-20260704T120000Z/improvement-plan.md`.

## Spotted, not fixed (pre-existing)

- `scripts/deploy.sh` migrate-exit-code line (`code="$(remote "docker inspect …")"`)
  is a pre-existing `set -e` + `pipefail` hazard: a failed `remote` aborts before
  the friendly "library-migrate exited …" message. Out of scope for this run's
  promote-gate work; noting for a future deploy-script pass.

## Wrap-up (/done)

Doc-freshness audit found + fixed two completeness gaps (the new `.card`/
`.filter-label` classes and `AppButton size` were missing from the frontend
vocabulary docs; the deploy runbook didn't mention the promote-gate/`--force`).
Code review of the whole diff found no correctness bugs across 8 traced areas;
its one actionable note — `_PRICED_MODEL_FIELDS` is hand-maintained — was closed
with a guard test that fails if any `*_model` Settings field escapes validation.
Security scan clean (no secrets in the diff).
