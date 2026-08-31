# Deleting the series stack

**Status:** design (2026-08-31). Plan 5 of the charts redesign — step E of
[the charts redesign design](2026-08-28-charts-redesign-design.md) §11's build
order. Supersedes nothing; completes the migration §11 begins.

> **Note on examples.** This repository is public. No real sender, amount,
> address, registration or person appears below.

Plans A–D shipped a second charting stack. This plan deletes the first one.

The legacy stack answers "how does this recurring bill compare to its usual
value"; the new one answers "how much am I spending on X per period". Both are
live in the same application today: six backend modules, fifteen routes, seven
tables, six migrations, eight frontend components and composables, and
4,865 lines of backend tests. The legacy stack lost its board in plan 4b but is
still mounted, still reachable at `/charts/legacy` and `/charts/:seriesId`, and
still doing work in three background jobs.

## 1. What this plan is

A **removal**. Nothing is rebuilt on the new engine. One defect is fixed in
passing (§6), because the file it lives in is being opened anyway.

The redesign spec's §11 fixed the removal list. This document exists because
that list is incomplete: it names six modules, five components and seven tables,
and does not name the six live consumers that make deleting them a broken build.
Those are §4 and §5.

## 2. Decisions taken before design

Each of these kills or keeps a working capability. They are recorded here so
that nothing dies as collateral.

### 2.1 Smart Groups dies; its doc is archived

[`docs/smart-groups.md`](../../smart-groups.md) is documented `active`, shipped
2026-07-24, and built entirely on this stack: `semantic_membership.py`, the
`authored_series_exclusions` table, and a creation UI that exists *only* inside
`ChartsView.vue` at `/charts/legacy`.

It dies, because the new engine serves its **purpose** better than its
mechanism did. A Smart Group existed because emergent series are keyed on
`(sender_id, kind_id, currency)`, so a concept spanning senders — a set of EV
charging networks, an accountant sending two kinds of invoice — could never
become one chart. A facet rule spans senders **by construction**:
`category IN (...) AND cost_type IN (...)` is deterministic, reads a curated
closed vocabulary, and needs no embeddings.

What is lost with it is worth naming precisely, because it is not nothing: a
Smart Group learns membership from document meaning, so it can catch a sender
the vocabulary has not anticipated. What is gained is that it can no longer be
**wrong silently** — `semantic_group_min_similarity` (0.55) and
`semantic_group_neg_margin` (0.02) are recorded in that doc as "first-guess
tunables, not calibrated against a labeled set", and the forward auto-add path
adds members with no review step.

`docs/smart-groups.md` is archived rather than deleted: it records the
name→seed-query poisoning incident and the reasoning behind
nearest-positive-neighbour scoring, both of which outlive the feature.

### 2.2 `compare_to_series` and `DocumentSeriesTrend` die; the rebuild is a later plan

Both answer "is this bill higher than usual" — a distribution question the
spending engine cannot answer, since it is a summation engine. Neither has a
replacement.

They die anyway, because **the answer they give is known-wrong**. Both call
`series.summarize_series`, and the redesign spec's own §2.1 establishes what
that function does: it narrows silently to the most populous `(sender, kind)`
group and then to the dominant currency bucket, so the "usual" band is computed
over whichever subset survived two undisclosed filters. §2.4 adds that an
unmerged invoice/receipt pair enters that band twice. Keeping the capability
means keeping a wrong answer that reports a `coverage` block precise enough to
look right.

`DocumentSeriesTrend` has a second, structural reason: its tile sets
`detailHref` to `/charts/{seriesId}`, the route this plan deletes. Keeping the
panel means rebuilding its deep-link target.

The rebuild is deferred to the plan in §11.1, not to memory. It belongs there
rather than here because the honest version reads `spend_facts` — which is
payment-deduplicated and label-bearing — and that is the same change §11.1
already needs for a different reason.

### 2.3 Two PRs: the code first, the drop after a soak

Every migration in this project so far has been additive, and the redesign spec
says why: "Nothing is dropped until the replacement has run against the live
archive." This plan is the drop. Production holds real rows in at least
`series_insights` and the authored-series tables.

- **PR 1** removes every module, route, job, setting, component, composable,
  client function, test and doc. The seven tables stay, orphaned and unread.
- **PR 2** is one migration, `0038_drop_series_stack.py`, and nothing else
  (the current head is `0037`).

The split buys one specific thing: between the two deploys, `git revert` plus a
redeploy of the previous image **works**, because the rows are still there.
Shipped together, the only rollback is forward — the previous image would query
tables that no longer exist. PBS backs the database up nightly, so the drop is
recoverable either way; the soak window makes recovery cheap rather than
possible.

`downgrade()` recreates the seven tables empty, matching the convention in
`0036_charts.py`. It restores schema, never rows, and the migration says so.

### 2.4 `/api/spending` keeps its prefix

`api/spending.py`'s docstring currently promises the opposite — that it "takes
that prefix when that one is deleted", i.e. moves to `/api/charts`. That
promise is withdrawn, and the docstring is corrected as part of this plan.

The only argument for `/api/charts` is a legacy name that the redesign spec's
§1 spent a page arguing is the wrong frame: "`/charts` cannot answer the
questions it exists to answer." Reclaiming it re-imports the vocabulary the
redesign replaced. Against that, renaming twelve live routes touches
`api/spending.py`, `frontend/src/api/spending.ts`, three e2e specs,
`docs/charts.md` §11 and `docs/api.md` — a second mechanical sweep landing in
the same diff as a large deletion, where each can hide the other's mistakes.

The cost is accepted knowingly: the frontend route `/charts` no longer matches
its API prefix, unlike `/documents`, `/notes` and `/ask`. The frontend route is
not renamed, because 4b shipped it to production days ago.

### 2.5 The LLM-backend surface layer stays, with one row

`BACKEND_SURFACES` has two entries, `ask` and `series_insight`; after this plan
it has one, and Settings → LLM backend renders a card with a single row.

The layer stays. It is entirely generic — a dict, a copy map, two routes and a
`v-for` — so it works unchanged with one row and gains rows the moment another
surface wants switching. Collapsing it into the Ask settings would delete an
abstraction that has to be rebuilt; the chart engine's rule-drafting LLM is the
obvious next surface, and is explicitly out of scope here.

## 3. The inventory

Verified against `main` at `f8ffbaa` by reading the tree, not the spec.

**Backend modules** (~3,250 lines):

| Module | Lines | What it is |
| --- | --- | --- |
| `src/library/series.py` | 1437 | series detection + comparative stats |
| `src/library/series_insight.py` | 400 | cached LLM prose per series |
| `src/library/series_match.py` | 138 | authored-series auto-continue |
| `src/library/semantic_membership.py` | 272 | Smart Groups embedding membership |
| `src/library/api/series.py` | 152 | 2 routes: pin/exclude membership overrides |
| `src/library/api/charts.py` | 855 | 13 routes: emergent + authored series |

All three routers are mounted in `app.py` (`charts` L263, `series` L264).

**ORM** (`src/library/models.py`): `SeriesInsight`,
`SeriesMembershipOverride`, `SeriesMetaOverride`, `AuthoredSeries`,
`AuthoredSeriesMember`, `AuthoredSeriesSuggestion`, `AuthoredSeriesExclusion`,
plus the `SeriesMode` and `SuggestionState` enums. Their relationships are
self-contained among themselves — there is no `Document`, `Sender` or `Kind`
backref to unpick.

**Tables**, created by migrations `0009`, `0015`, `0018`, `0019`, `0021`,
`0029`: `series_insights`, `series_membership_overrides`,
`series_meta_overrides`, `authored_series`, `authored_series_members`,
`authored_series_suggestions`, `authored_series_exclusions`.

**Settings** (`config.py:189-217`): `series_insight_llm_backend`,
`series_min_documents`, `series_typical_pct`, `series_flat_pct`,
`series_autocontinue_enabled`, `series_autocontinue_min_dominance`,
`series_suggestion_limit`, `semantic_group_enabled`,
`semantic_group_min_similarity`, `semantic_group_neg_margin`.

**Frontend**: `views/SeriesChartView.vue`, `views/ChartsView.vue`,
`components/SeriesChartTile.vue`, `components/charts/ChartControls.vue`,
`components/DocumentSeriesTrend.vue`, `composables/useChartsGrouping.ts`,
`composables/useChartsTimeframe.ts`, `utils/chartExport.ts` (its only consumer
is `SeriesChartView`), and 17 client functions in
`frontend/src/api/documents.ts:648-882` with their types.

**Routes**: `/charts/legacy` (`charts-legacy`) and `/charts/:seriesId`
(`series-chart`).

**Tests**: 15 backend files, 4,865 lines — `test_series.py`,
`test_series_db.py`, `test_series_insight.py`, `test_series_insight_db.py`,
`test_series_insight_backend.py`, `test_series_match.py`,
`test_series_membership_api.py`, `test_series_overrides.py`,
`test_series_suggestions_db.py`, `test_semantic_membership.py`,
`test_smart_groups_api.py`, `test_charts_api.py`,
`test_charts_suggestions_api.py`, `test_charts_write_paths.py`,
`test_documents_api_series.py`. Five frontend specs, and the e2e specs
`legacy-charts.spec.ts` and `smart-groups.spec.ts`.

## 4. Six live consumers

Deleting a module in §3 without first handling these is a broken build. The
redesign spec's removal list names none of them.

1. **The Ask engine.** `ask/engine.py:44` imports `summarize_series` and
   `serialise_summary`; `compare_to_series` is a registered LLM tool (declared
   `:280`, dispatched `:942`, implemented `:730`), with supporting prose at
   `:5`, `:63`, `:102` and `:177`.
2. **The document detail page.** `api/documents.py:70` imports the same two
   functions for `GET /api/documents/{id}/series` (`:405`), which feeds
   `DocumentSeriesTrend.vue`, mounted at `DocumentDetailView.vue:1377`.
3. **Three background jobs.** `jobs.py:70-72` imports `auto_add_document`,
   `refresh_series_insight` and `propose_authored_matches`; the tasks are
   defined at `:831`, `:843`, `:855` and queued at `:639`, `:649`, `:658`.
4. **The disclosure eval.** `ask/disclosure_scenarios.py` carries a
   `series-other-currency` scenario built on `SeriesCoverage`'s
   `other_currency` exclusion — one of six scenarios.
5. **The admin currency-normalise operation.** `currencies.py` rewrites four of
   the seven tables in raw SQL, and `api/admin/fx.py` refuses with **409** when
   a rename would collide with authored-series overrides. See §5.2.
6. **The nightly e2e workflow.** `.github/workflows/e2e-nightly.yml` has
   exactly one job, `smart-groups`. See §5.1.

## 5. Three things that are rewritten, not deleted

### 5.1 The nightly workflow keeps the recall measurement

`e2e-nightly.yml`'s single job starts the stack **with an embedder**, waits for
its model to load, runs `smart-groups.spec.ts`, asserts the spec actually ran,
and then runs `Measure retrieval recall` (`library eval-recall`,
`continue-on-error: true`).

That recall step is independent of Smart Groups and rides on the embedder the
job exists to start. `docs/ask.md` treats it as live capability and the recall
baseline is committed. **Deleting the workflow would silently kill it** — the
exact failure the workflow was created to fix, since before it existed
`E2E_SMART_GROUPS` was set nowhere and that journey had never run.

So the job is renamed and stripped to: build, start with embedder, wait for the
model, measure recall, upload the report. The Playwright install, frontend
build and preview server steps go with the spec.

### 5.2 The currency-normalise operation gets smaller

`currencies.py` exists partly to keep user-authored series overrides consistent
across a currency rename. With the tables gone, the following are deleted
rather than repaired:

- `_OVERRIDE_TABLES` and the `series_meta_overrides` /
  `series_membership_overrides` update statements
- the `series_insights` collision-merge and its `DELETE ... WHERE EXISTS`
  dedupe
- the `authored_series` and suggestion rewrites
- the conflict detection, `fx.py`'s **409** response and its schema
- `AdminMetadataPanel.vue`'s confirm modal and conflict rendering

What remains is a plain document rewrite. This is the "delete the second copy"
rule applying for free: a whole consistency mechanism disappears with the thing
it kept consistent.

### 5.3 The document-layout pane

`composables/useDocumentLayout.ts` registers `'series-chart'` in
`DEFAULT_CARD_COLUMNS.right` (`:107`) and in `LEGACY_RIGHT` (`:197`), the set
used to migrate a pre-column flat card order.

Both entries go. The risk is that users have **persisted** a card order
containing `'series-chart'`. `reconcileCardColumns` appears to drop unknown ids
already, which would make a stored preference degrade safely — but that is
proved by a test (§8), not assumed.

## 6. The one addition: Ask's unguarded commit

Migration `0035` added a pair of `DEFERRABLE INITIALLY DEFERRED` constraint
triggers that fire at COMMIT on any `amount_total` write against an allocated
document. Under asyncpg they arrive as a bare `DBAPIError`.
`docs/charts.md` §10.1 enumerates five writers of `amount_total`; four
translate the refusal through `spend_lines.commit_allocation` into a named
**400** (`api/documents.py:468`, `api/spending.py:1267` and `:1294`). Ask's
`_run_update_document` ends in an unguarded `await session.commit()`, so the
same refusal is a **500 with a poisoned session**.

`docs/charts.md` §13 records this and calls it pre-existing. That word is doing
too much work: before `0035` there was no trigger to translate, so the failure
mode did not exist. The chart engine's own work created it and guarded four of
five writers.

This plan fixes it, because it is already editing that function's file to
remove `compare_to_series`, the fix is to reuse an existing helper, and doing
it later costs a full PR cycle. `docs/charts.md` §13 loses that entry.

It is latent today — the archive holds no allocations — and becomes reachable
the first time a document is split.

## 7. PR 1: the code removal

Branch `delete-series-stack`. Ordered leaves-first so the tree never carries a
dangling import between tasks.

1. **Frontend leaves** — `SeriesChartTile.vue`, `ChartControls.vue`,
   `DocumentSeriesTrend.vue`, `useChartsGrouping.ts`, `useChartsTimeframe.ts`,
   `chartExport.ts`, and their five unit specs.
2. **Frontend wiring** — `SeriesChartView.vue`, `ChartsView.vue`, both router
   entries, the 17 client functions and their types, the
   `DocumentDetailView.vue` mount and its two supporting comments, the
   `useDocumentLayout` pane ids, `legacy-charts.spec.ts`,
   `smart-groups.spec.ts`.
3. **Ask** — the tool declaration, dispatch arm, `_run_compare_to_series`, the
   import, the four prose sites, the `series-other-currency` scenario, and the
   §6 commit guard **added**.
4. **Other consumers** — `GET /api/documents/{id}/series` and its import; the
   three jobs and their queue calls; `currencies.py` and `fx.py` per §5.2;
   the ten settings; the `BACKEND_SURFACES` row and its `_SURFACE_COPY` entry.
5. **The modules** — the six files, the two router mounts, the seven ORM
   classes and two enums.
6. **Backend tests** — the fifteen files in §3.
7. **The nightly workflow** — per §5.1.
8. **Docs, journal, stamps** — per §9.

The tables are untouched. After this PR the schema carries seven unread tables,
deliberately.

## 8. Testing and verification

The correctness claim of a deletion is **"nothing references a removed thing"**,
and that is proved by tools rather than by tests.

**Per removal**, `grep -rn` over **`src/`, `tests/`, `frontend/src/` and
`frontend/e2e/`** — all four trees, not `src/` alone. This is the direct
analogue of the lesson from plan 4a, where eleven defects across eight tasks all
originated in plan text that had never been executed.

**Gates.** `uv run pytest` (full suite); `make lint` (ruff, mypy, actionlint,
journal index, `check_docs`); and from `frontend/`, `npm run test:unit`,
`npm run lint`, `npm run type-check` and `npm run test:e2e`. The last three are
run explicitly because `make lint` is Python-only — a frontend-only change can
pass it and red CI. `mypy` and `vue-tsc` are what actually catch a dangling
import in each language.

**Three new tests, each mutation-checked.** A mutation that does not go red is
reported as such, not recorded as passed.

1. **Route absence, asserted against the OpenAPI path set** —
   `"/api/charts" not in app.openapi()["paths"]`, and likewise for the two
   `/api/series/...` paths and `/api/documents/{document_id}/series`. Asserted
   against the schema rather than by requesting a 404, because a 404 assertion
   passes for the wrong reasons — an auth redirect, a trailing slash, a
   mistyped path that was never mounted. Mutation: re-mount `charts.router`.
2. **The persisted pane** — a stored card order containing `'series-chart'`
   reconciles to a valid two-column layout without it, and without dropping any
   known card. Mutation: make `reconcileCardColumns` pass unknown ids through.
3. **The Ask commit guard** — an amount edit through Ask against an allocated
   document returns the named 400. Mutation: remove the guard, assert 500.

**E2E** assertions must hold on all three viewport projects (chromium 1280,
mobile-webkit 375, tablet-webkit 656). This plan deletes specs rather than
adding them, so the work is confirming no surviving spec depended on a deleted
route or a sidebar entry.

**PR 2** adds a migration test: the seven tables absent after `upgrade`, present
and empty after `downgrade`, run against a real Postgres.

## 9. Documentation

`docs/smart-groups.md` is `git mv`'d to `docs/archive/` with a
`**Status:** superseded by [charts.md](../charts.md) (2026-08-31).` header.
`check_docs.py` puts `archive` in `EXCLUDED_DIRS`, so it leaves the stamp gate;
it also requires every gated doc to be reachable from `docs/README.md`, so the
index row is **removed** rather than repointed. Four other inbound links are
updated: `api.md:1456`, `roadmap.md:149`, `frontend.md:363`, `frontend.md:936`.

| Document | Change |
| --- | --- |
| `api.md` | §1.13–1.15 deleted; fifteen rows from the route table (L58-72); the `/documents/{id}/series` row |
| `ask.md` | §1.7 deleted; §1.2's coverage contract narrowed to `query_documents` and `semantic_search`; the §1.10 series item removed and the §11.1 limitation added; the tool diagram; two `LIBRARY_SERIES_*` env rows; disclosure-eval scenarios six → five |
| `architecture.md` | the six module rows in the module map |
| `frontend.md` | the `ChartsView` row, the tile and view rows, the nightly-spec paragraph |
| `charts.md` | §13's "fifth `amount_total` writer is unguarded" entry deleted — fixed by §6; `api/spending.py`'s prefix docstring corrected per §2.4 |
| `admin.md` | the currency-normalise 409 and its confirm step |
| `llm-backends.md` | the `series_insight` surface row; a note that one surface remains |
| `jobs-and-notifications.md` | the three job rows |
| `observability.md`, `deployment.md`, `roadmap.md` | remaining references; roadmap gains the §11.1 entry |

Plus `journal/260831-delete-series-stack.md`, and a re-stamp of every touched
document.

## 10. Risks

- **The nightly workflow cannot be run locally.** `actionlint` catches syntax
  and nothing catches semantics, so a stripped job that no longer starts the
  embedder correctly fails silently until the next nightly. The rewritten job
  is read against the recall step's actual needs, and a manual
  `workflow_dispatch` is run after merge.
- **`gh workflow run CI --ref <branch>` cancels the in-flight push run** — same
  concurrency group — and `gh pr checks` renders that as a failure. Open the PR
  to get `e2e` rather than dispatching.
- **Local e2e drift.** A stale API image after `main` moves produces phantom
  failures in specs this plan never touched. Check `/healthz`'s `git_sha`
  against HEAD and use the e2e compose overlay before believing one.
- **`docs-stamps` and midnight.** A squash-merge landing after 00:00 UTC reds
  `main` on a PR that was green; it has happened three times. Two PRs here
  means two chances.
- **PR 2's deploy gate.** Confirm CI's `promote` job concluded `success` — it
  is not scheduled until `build` finishes, so "all jobs complete" can be true
  while `promote` does not yet exist — and run `make deploy` from a worktree
  actually on `main`, since it reads the local worktree's HEAD.

## 11. What this plan does not do

- **It does not rename `/api/spending`.** §2.4.
- **It does not rebuild any deleted capability.** §11.1.
- **It does not add `chart_draft` as an LLM backend surface.** §2.5.
- **It does not touch the chart engine.** `docs/charts.md` §13's remaining
  known limits stay open, except the one §6 closes.

### 11.1 The next plan: Ask reads the money model

Out of scope here, recorded so that "later" has a place to live.

Ask's money answers are computed from a model the charts redesign replaced.
`structured_query.sum_amount` sums raw `Document.amount_total` and reports
three exclusions — `no_amount`, `quote_not_spend`, and `no_sender`/`no_kind`
when grouping. It knows nothing of what plans A–D built:

- **`amount_kind` and `AMOUNT_SIGN`.** `amount_total` is always a magnitude
  (`models.py:158`), and `refund` is the only negative. Ask sums all eight
  kinds unsigned, so a refund **inflates** a total instead of reducing it, and
  the non-summable kinds — `coverage_limit`, `balance`, `estimate`, `none` —
  still contaminate it. That is verbatim the defect the redesign spec's §1
  opens with.
- **Payment identity.** §2.4's unmerged invoice/receipt pair is
  double-counted; `spend_facts` collapses it via `is_canonical`.
- **Facets.** Ask's filter vocabulary is still kind, sender, recipient,
  projects, matters and tags — and §2.3 declared tags unusable. The archive
  gained a curated `category` vocabulary that Ask cannot query.

The result is two surfaces that disagree about the same question, where Ask's
is the wrong one and says nothing about it: `coverage.excluded` has no bucket
for "counted a refund as spend" or "counted this payment twice". That
contradicts the redesign's own §12 thesis — every exclusion reported at the
point it affects a number.

The plan is to point Ask's money aggregate at `spend_facts` and give it facet
filters. It is also the right home for the §2.2 rebuild of
`compare_to_series`, since a distribution over `spend_facts` is
payment-deduplicated and label-scoped — the two things that made the legacy
answer wrong.
