# 1. App improvements — 12-item batch (engineering-team cycle)

Date: 2026-06-30. Branch: `worktree-eng-app-improvements-12`. Run dir:
`.engineering-team/runs/manual-20260629T210402Z/`.

A full evaluate → plan → develop → wrap-up cycle over 12 user-requested UI/UX
improvements, decomposed into 14 work units (W1–W14). All shipped; 743 backend +
501 frontend tests pass (was 701 / 471), ruff + eslint + type-check + build
clean, single migration head `0020`.

## 1.1 What shipped

### 1.1.1 Document detail view
- **W1** — Fixed the edit-mode layout: panels keep their column count (removed
  `|| editMode` forcing `sm:col-span-2`), and the duplicated field label is gone
  (added `hideLabel`/`hideLegend` to App{Input,Select,Textarea,DateInput} so
  inline editors render an `sr-only` label, leaving the existing `<dt>` as the
  single visible label in the same position as read mode).
- **W2** — System panel STATUS is plain text now, matching its siblings; dead
  `statusAccent()` removed.
- **W6** — New `quote` kind (migration 0017) + inline "Add kind…" in the Kind
  dropdown, backed by `POST /api/kinds` (slugify, sentence-case, case-insensitive
  dedupe, length-aware near-duplicate guard). See
  [260629-quote-kind-and-inline-create.md](260629-quote-kind-and-inline-create.md).

### 1.1.2 Ask & spend
- **W7** — Quotes are not expenditure: `sum_amount` excludes `kind='quote'`
  (correlated NOT EXISTS, so it holds regardless of phrasing) unless the caller
  explicitly filters `kind='quote'`; the Ask tool description + concept hints
  updated. "What have I spent" now ignores quotes.
- **W10** — `/ask` rebuilt as one cohesive Mosaic chat panel (conversation rail +
  internally-scrolling thread + docked composer) instead of floating cards;
  dropped the brittle `h-[calc(100dvh-14rem)]` for a shell-derived height. All
  `data-testid`s preserved.

### 1.1.3 Charts
- **W8** — "Documents in this series" is collapsible (collapsed by default) and
  columnar (title · date · amount per row), with an Undo after removing a doc
  (the override toggle is self-reversing).
- **W9** — Temporal x-axis: switched chart.js to a `TimeScale` with
  `{x: date, y: amount}` points (added `chartjs-adapter-date-fns` + `date-fns`),
  so gaps reflect real elapsed time.
- **W12** — Single-chart shareable route `/charts/:seriesId` + editable chart
  title/description (new `series_meta_overrides` table, migration 0018; stable
  `seriesId` = `sender-kind-currency`).
- **W14** — Authored ("manual") series: name a series, add documents, get a chart
  even without a natural ≥3-doc emergent seed (`authored_series` +
  `authored_series_members`, migration 0019; addressed as `a-{id}`). Refactored
  `series.py` to a shared `_summarize_members` so emergent and authored series use
  one code path (parity asserted by test).

### 1.1.4 Admin & settings
- **W4** — Admin tabs reordered to Users · Metadata · Architecture · Coverage ·
  System (Users default); architecture doc §1.3 rewritten from a wall of text into
  scannable bullets; obsolete §1.6 Implementation status removed; stale "GOV.UK"
  note corrected to Mosaic.
- **W5** — Architecture markdown now renders GFM tables (added `.doc-markdown`
  table CSS); the wide ASCII "Flow overview" diagram replaced with a readable
  numbered step list.
- **W11** — Coverage tab mirrors all four CI test types: backend (pytest) and
  frontend (Vitest) keep %/gate/worst-files; e2e (Playwright) and compose-smoke
  show as "CI gate" cards (no line coverage — the image builds in parallel with
  them). `coverage_summary.py` gained a `test_types` enumeration.
- **W13** — `recipients.user_id` link (migration 0020): creating a user
  auto-links a recipient named by display name; ingestion resolves a document to
  that recipient when the extracted name matches the user's username **or**
  display name; new guarded `DELETE /api/admin/users/{id}` (no last-admin, no
  self) + inline two-step Delete button.
- **W3** — `/settings` Pushover secrets show a masked placeholder when set, with
  an eye-reveal toggle (cosmetic — secrets stay write-only server-side).

## 1.2 Key decisions

1. **Quote = the spend-exclusion signal.** Rather than a separate `is_estimate`
   column, the new `quote` kind drives exclusion from spend totals. Ties items 1
   and 3 together with no schema churn; a document is a quote by being kind=quote.
2. **Pushover reveal is cosmetic.** The backend never returns saved secrets
   (write-only by design); a true "reveal the stored value" would reverse that
   posture, so the eye toggle only reveals what the user is currently typing and a
   `••••` placeholder signals "a value is saved".
3. **Authored series are shared, not owner-scoped.** Library is a single shared
   family archive — every authenticated member already sees/edits all documents
   and emergent charts — so authored series follow suit; `owner_id` is provenance,
   not access control. A commit security review flagged this as a potential IDOR;
   acknowledged and documented in `api/charts.py` as intentional.
4. **Full charts scope.** The user chose manual series creation + editable
   title/description + single-chart links over the smaller "polish only" option.

## 1.3 Gotchas discovered

1. **Session-scoped test DB + default list page.** `api_database_url` is
   session-scoped, so documents accumulate across files; `GET /api/documents`
   defaults to `limit=25`. A pre-existing test (`test_list_item_includes_amount`)
   passed in isolation but failed in the full run once the new tests seeded more
   rows — fixed by scoping its list query to the doc's unique tag (the module
   docstring already mandates this). `test_kinds_seeded` also needed +1 for the
   new `quote` kind. Both only surfaced in the **full** suite, not targeted runs.
2. **ruff check ≠ ruff format.** A W7 edit was ruff-check-clean but not
   format-clean; CI runs both. Always run `ruff format --check` before relying on
   green.
3. **Alembic chain under parallel agents.** Migration-creating units were run
   sequentially (0017→0020) to keep a single head and avoid the test-DB
   `upgrade head` referencing a not-yet-written down_revision.

## 1.4 Follow-ups / notes

- The live instance still has the `smoke`/`verify`/`cookietest` users; W13 added
  the delete capability, but removing them on the live DB is a manual post-deploy
  step (the CI `compose-smoke` job recreates `smoke` on its own ephemeral DB).
- Authored series with zero members render an empty chart; members are added via
  the create flow up front or the single-chart view (the add control lives inside
  the points-present block).
