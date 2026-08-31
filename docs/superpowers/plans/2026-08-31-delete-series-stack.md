# Delete the series stack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy series/charts stack — six backend modules, fifteen
routes, seven tables, eight frontend components and composables, and 4,865
lines of backend tests — leaving the spending stack as the only charting
surface, and fix the one `amount_total` writer the chart engine left unguarded.

**Architecture:** Two pull requests. PR 1 (Tasks 1–13) removes all code, tests
and documentation, leaving the seven tables in the database orphaned and
unread. PR 2 (Task 14) drops them. The split exists so that between the two
deploys a `git revert` plus a redeploy of the previous image still works.
Task order is reverse-topological — a consumer is removed before the thing it
consumes — so the tree never carries a dangling import between commits.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2 async / Alembic /
PostgreSQL 17 · Vue 3 + TypeScript / Vite / Vitest / Playwright · uv, ruff,
mypy.

**Spec:** [2026-08-31-delete-series-stack-design.md](../specs/2026-08-31-delete-series-stack-design.md)

## Global Constraints

- **This repository is PUBLIC.** No real sender, amount, address, registration
  or person in fixtures, docs, commit messages or PR bodies. Illustrate the
  shape; invent the values.
- **Verify every removal with `grep -rn` over all four trees** — `src/`,
  `tests/`, `frontend/src/`, `frontend/e2e/`. Not `src/` alone. This is the
  direct analogue of plan 4a's recorded failure, where eleven defects across
  eight tasks all originated in plan text that had never been executed.
- **`make lint` does not run eslint or vue-tsc.** From `frontend/`, run
  `npm run test:unit`, `npm run lint` and `npm run type-check` yourself.
- **A `cd frontend &&` in one Bash call moves every later call.** Use absolute
  paths, or re-`cd` to the repo root each time.
- **CI runs ruff check and ruff format over the whole repository**, including
  `migrations/`. Format any new migration before pushing.
- **E2E runs on three viewport projects**: chromium 1280, mobile-webkit 375,
  tablet-webkit 656. Assertions must hold on all three.
- **Deleting a component whose spec file remains breaks the build**; deleting a
  route whose e2e spec remains breaks CI. Sweep tests with the code.
- **Every mutation check is a hypothesis, not a result.** If a specified
  mutation does not turn the test red, say so loudly and find one that does —
  do not record it as passed. Three specified mutations in plan 4c turned out
  not to discriminate.
- Repo root: `/Users/john/projects/syncthing/agent-lxc/library-5`, branch
  `delete-series-stack`.

**Commands.** `uv run pytest` (full backend suite, ~6 min, 2222 tests today) ·
`make lint` · from `frontend/`: `npm run test:unit` (1233 tests today),
`npm run lint`, `npm run type-check`, `npm run test:e2e`.

---

# PR 1 — the code removal

## Task 1: Unmount the series-chart card from the document detail page

`DocumentSeriesTrend` is mounted by `DocumentDetailView` and registered as a
layout pane. Both consumers go first, so the component is orphaned before
Task 3 deletes it. The pane id may be **persisted in a user's stored card
order**, so this task proves the stored preference degrades safely rather than
assuming it.

**Files:**
- Modify: `frontend/src/views/DocumentDetailView.vue` — the import (`:50`), the
  `seriesChartPresent` ref and its comment (`~:625-633`), the reset in the
  route watcher (`~:842-846`), and the `v-else-if="cardId === 'series-chart'"`
  branch (`:1377-1381`)
- Modify: `frontend/src/composables/useDocumentLayout.ts` — `'series-chart'` in
  `DEFAULT_CARD_COLUMNS.right` (`:107`) and in `LEGACY_RIGHT` (`:197`)
- Modify: `frontend/src/views/__tests__/DocumentDetailView.spec.ts` — drop any
  case asserting the series card
- Test: `frontend/src/composables/__tests__/useDocumentLayout.spec.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `DocumentSeriesTrend.vue`, `useChartsTimeframe`, `useChartsGrouping`
  and `SeriesChartTile.vue` become unreferenced by application code (Task 3
  deletes them). `DEFAULT_CARD_COLUMNS.right` becomes
  `['preview', 'markdown']`; `LEGACY_RIGHT` becomes `new Set(['preview', 'markdown'])`.

- [ ] **Step 1: Write the failing test for the persisted pane**

A user who arranged their detail page before this change has `'series-chart'`
saved in their card order. `reconcileCardColumns` *appears* to drop unknown
ids; this proves it, and proves nothing else is lost with it.

Add to `frontend/src/composables/__tests__/useDocumentLayout.spec.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { reconcileCardColumns, DEFAULT_CARD_COLUMNS } from '@/composables/useDocumentLayout'

describe('a stored order naming a removed card', () => {
  it('drops series-chart and keeps every card that still exists', () => {
    // What a user's localStorage holds if they arranged their page before
    // the series chart was removed.
    const stored = {
      left: ['notes', 'comments', 'actions'],
      right: ['preview', 'series-chart', 'markdown'],
    }

    // Two args: the known-id set is built from `defaults`, not from a module
    // constant, so this is exactly what discriminates - before the change
    // 'series-chart' is in DEFAULT_CARD_COLUMNS and survives; after, it is not.
    const result = reconcileCardColumns(stored, DEFAULT_CARD_COLUMNS)

    expect(result.right).not.toContain('series-chart')
    // Order among the survivors is preserved, not reset to the default.
    expect(result.right.filter((id) => id === 'preview' || id === 'markdown')).toEqual([
      'preview',
      'markdown',
    ])
    // Nothing the user had is silently lost, and no known card goes missing.
    expect(result.left).toContain('notes')
    const all = [...result.left, ...result.right]
    for (const id of [...DEFAULT_CARD_COLUMNS.left, ...DEFAULT_CARD_COLUMNS.right]) {
      expect(all).toContain(id)
    }
    // No duplicates across the two columns.
    expect(new Set(all).size).toBe(all.length)
  })
})
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5/frontend && npx vitest run src/composables/__tests__/useDocumentLayout.spec.ts -t 'stored order naming a removed card'
```

Expected: **FAIL** — `'series-chart'` is still a known id, so `result.right`
contains it.

If it PASSES at this point, `reconcileCardColumns` is already dropping it for
another reason. Stop and read the function before continuing: the test must
fail for the stated reason, or it is not testing what it claims.

- [ ] **Step 3: Remove the pane id from the composable**

In `frontend/src/composables/useDocumentLayout.ts`:

```typescript
export const DEFAULT_CARD_COLUMNS: CardColumns = {
  left: ['notes', ...METADATA_CARD_IDS, 'comments', 'actions', 'history'],
  right: ['preview', 'markdown'],
}
```

and

```typescript
const LEGACY_RIGHT = new Set(['preview', 'markdown'])
```

- [ ] **Step 4: Run it and watch it pass**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5/frontend && npx vitest run src/composables/__tests__/useDocumentLayout.spec.ts
```

Expected: PASS, and every pre-existing case in that file still passes.

- [ ] **Step 5: Mutation-check the new test**

Temporarily make `reconcileCardColumns` pass unknown ids through instead of
dropping them (find the filter against the known-id set and neutralise it).
Re-run Step 4.

Expected: **FAIL**. If it still passes, the assertion is not load-bearing —
say so explicitly in your task report and strengthen it before moving on.
Restore the file and confirm `git diff` on it is empty.

- [ ] **Step 6: Remove the card from DocumentDetailView**

Delete four things in `frontend/src/views/DocumentDetailView.vue`:

1. `import DocumentSeriesTrend from '@/components/DocumentSeriesTrend.vue'` (`:50`)
2. The `seriesChartPresent` ref and its explanatory comment block (`~:625-633`)
3. The `seriesChartPresent.value = true` reset in the route watcher, with its
   comment (`~:842-846`)
4. The template branch (`:1377-1381`):

```vue
        <DocumentSeriesTrend
          v-else-if="cardId === 'series-chart'"
          :document-id="doc.id"
          @presence="seriesChartPresent = $event"
        />
```

Then grep for `seriesChartPresent` in that file — it is also referenced by the
`cardPresent` helper (see the comment at `:625`). Remove that arm too, and read
`cardPresent` afterwards to confirm it still returns correctly for every
remaining card.

- [ ] **Step 7: Drop the series case from the detail-view spec**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && grep -n -i "series" frontend/src/views/__tests__/DocumentDetailView.spec.ts
```

Delete the cases that assert the series card renders, is hidden, or reacts to
`presence`. Leave every other case untouched.

- [ ] **Step 8: Verify nothing else references the pane id**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && grep -rn "series-chart\|seriesChartPresent" src/ tests/ frontend/src/ frontend/e2e/
```

Expected: **no output**. `frontend/e2e/detail-layout.spec.ts` drives this page
— if it names the card, update it here.

- [ ] **Step 9: Run the gates**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5/frontend && npm run test:unit && npm run type-check && npm run lint
```

Expected: all pass. `vue-tsc` is what catches a missed reference in the SFC.

- [ ] **Step 10: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add frontend/src/views/DocumentDetailView.vue frontend/src/composables/useDocumentLayout.ts frontend/src/composables/__tests__/useDocumentLayout.spec.ts frontend/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "refactor(detail): remove the series-chart card and its pane id

The card's backend (GET /api/documents/{id}/series) is removed later in
this branch. A stored card order naming 'series-chart' now reconciles to
a valid layout without it, proved by a test rather than assumed."
```

---

## Task 2: Delete the two legacy routes and the views behind them

**Files:**
- Delete: `frontend/src/views/ChartsView.vue`,
  `frontend/src/views/SeriesChartView.vue`
- Delete: `frontend/src/views/__tests__/ChartsView.spec.ts`,
  `frontend/src/views/__tests__/SeriesChartView.spec.ts`
- Delete: `frontend/e2e/legacy-charts.spec.ts`, `frontend/e2e/smart-groups.spec.ts`
- Modify: `frontend/src/router/index.ts` — the `charts-legacy` entry
  (`:117-121`) and the `series-chart` entry (`:137-141`)
- Modify: `frontend/src/router/__tests__/spending-routes.spec.ts`

**Interfaces:**
- Consumes: Task 1's removal of the detail-page mount.
- Produces: `/charts` (`SpendingBoardView`) and `/charts/:chartId(\d+)`
  (`SpendingWorkspaceView`) are the only `/charts*` routes. `chartExport.ts`
  and `ChartControls.vue` become unreferenced (Task 3 deletes them).

- [ ] **Step 1: Delete the two route entries**

In `frontend/src/router/index.ts`, delete the whole `charts-legacy` object
**including its four-line comment**, and the whole `series-chart` object
including its comment. Leave `/charts` and `/charts/:chartId(\d+)` untouched.

Then read the comment that survives on `/charts/:chartId(\d+)` (`:98-106`): it
explains coexistence with `:seriesId`, which no longer exists. Rewrite it to
state only what is still true — that the digit constraint is kept as
convention, matching `/ask/:threadId(\d+)`.

- [ ] **Step 2: Delete the views and their specs**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git rm frontend/src/views/ChartsView.vue frontend/src/views/SeriesChartView.vue \
       frontend/src/views/__tests__/ChartsView.spec.ts \
       frontend/src/views/__tests__/SeriesChartView.spec.ts \
       frontend/e2e/legacy-charts.spec.ts frontend/e2e/smart-groups.spec.ts
```

- [ ] **Step 3: Update the router spec**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && grep -n "legacy\|seriesId\|series-chart" frontend/src/router/__tests__/spending-routes.spec.ts
```

Delete the cases that resolve `/charts/legacy` and `/charts/:seriesId`. **Keep
and do not weaken** the cases pinning `/charts` and `/charts/:chartId(\d+)` —
those still matter. Add one case asserting a non-numeric child no longer
resolves to a named route:

```typescript
it('a non-numeric /charts child no longer resolves to a legacy route', () => {
  const resolved = router.resolve('/charts/12-3-EUR')
  expect(resolved.name).not.toBe('series-chart')
  expect(resolved.matched.length).toBe(0)
})
```

- [ ] **Step 4: Verify nothing references the deleted routes**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "charts-legacy\|series-chart'\|ChartsView\|SeriesChartView\|/charts/legacy" src/ tests/ frontend/src/ frontend/e2e/
```

Expected: **no output**. `docs/` will still mention them — Task 13 handles docs.

- [ ] **Step 5: Run the gates**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5/frontend && npm run test:unit && npm run type-check && npm run lint
```

- [ ] **Step 6: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add -A
git commit -m "refactor(charts): delete the legacy board and single-series views

/charts/legacy and /charts/:seriesId are gone; /charts and
/charts/:chartId(\\d+) are now the only /charts routes. smart-groups.spec.ts
goes with them - the Smart Groups creation UI lived only in ChartsView."
```

---

## Task 3: Delete the orphaned components, composables and chart export

Everything here is now unreferenced by application code. Task 1 removed the
detail-page consumer; Task 2 removed the two views.

**Files:**
- Delete: `frontend/src/components/DocumentSeriesTrend.vue`,
  `frontend/src/components/SeriesChartTile.vue`,
  `frontend/src/components/charts/ChartControls.vue`,
  `frontend/src/composables/useChartsGrouping.ts`,
  `frontend/src/composables/useChartsTimeframe.ts`,
  `frontend/src/utils/chartExport.ts`
- Delete: `frontend/src/components/__tests__/DocumentSeriesTrend.spec.ts`,
  `frontend/src/components/__tests__/SeriesChartTile.spec.ts`,
  `frontend/src/components/__tests__/SeriesChartTile.navigation.spec.ts`,
  `frontend/src/composables/__tests__/useChartsGrouping.spec.ts`,
  `frontend/src/composables/__tests__/useChartsTimeframe.spec.ts`,
  `frontend/src/utils/__tests__/chartExport.spec.ts`
- Modify: `frontend/src/components/spending/SpendingChart.vue` (a comment at
  `:24` references `SeriesChartTile.vue`), and
  `frontend/src/components/facets/FacetFilterBar.vue` (a comment at `:7`
  references `ChartControls.vue`)

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: no `frontend/src/` file imports anything from the legacy chart
  stack. `frontend/src/components/charts/` should be empty and removed.

- [ ] **Step 1: Confirm every one is genuinely orphaned before deleting**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
for sym in DocumentSeriesTrend SeriesChartTile ChartControls useChartsGrouping useChartsTimeframe chartExport; do
  echo "--- $sym ---"
  grep -rn "$sym" frontend/src frontend/e2e | grep -v "__tests__" | grep -v "^frontend/src/components/DocumentSeriesTrend.vue\|^frontend/src/components/SeriesChartTile.vue\|^frontend/src/components/charts/ChartControls.vue\|^frontend/src/composables/useChartsGrouping.ts\|^frontend/src/composables/useChartsTimeframe.ts\|^frontend/src/utils/chartExport.ts"
done
```

Expected: only the two **comment** references in `SpendingChart.vue:24` and
`FacetFilterBar.vue:7`. Any other hit is a real consumer — stop and handle it
before deleting.

- [ ] **Step 2: Delete the six source files and six specs**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git rm frontend/src/components/DocumentSeriesTrend.vue \
       frontend/src/components/SeriesChartTile.vue \
       frontend/src/components/charts/ChartControls.vue \
       frontend/src/composables/useChartsGrouping.ts \
       frontend/src/composables/useChartsTimeframe.ts \
       frontend/src/utils/chartExport.ts \
       frontend/src/components/__tests__/DocumentSeriesTrend.spec.ts \
       frontend/src/components/__tests__/SeriesChartTile.spec.ts \
       frontend/src/components/__tests__/SeriesChartTile.navigation.spec.ts \
       frontend/src/composables/__tests__/useChartsGrouping.spec.ts \
       frontend/src/composables/__tests__/useChartsTimeframe.spec.ts \
       frontend/src/utils/__tests__/chartExport.spec.ts
rmdir frontend/src/components/charts 2>/dev/null || ls frontend/src/components/charts
```

If `frontend/src/components/charts/` is not empty, list what remains and handle
it rather than forcing the delete.

- [ ] **Step 3: Fix the two surviving comments**

`SpendingChart.vue:24` currently explains Chart.js scale registration by
reference to `SeriesChartTile.vue`. Rewrite it to state the fact without the
dead reference — the registration is global and additive, so it must not be
undone here.

`FacetFilterBar.vue:7` cross-references `components/charts/ChartControls.vue`
as a precedent. Delete that parenthetical.

- [ ] **Step 4: Verify and run the gates**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "DocumentSeriesTrend\|SeriesChartTile\|ChartControls\|useChartsGrouping\|useChartsTimeframe\|chartExport" src/ tests/ frontend/src/ frontend/e2e/
cd frontend && npm run test:unit && npm run type-check && npm run lint
```

Expected: no grep output; all gates pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add -A
git commit -m "refactor(charts): delete the orphaned legacy chart components

DocumentSeriesTrend, SeriesChartTile, ChartControls, the two charts
composables and chartExport had no remaining consumer after the detail
card and the two legacy views were removed."
```

---

## Task 4: Delete the 17 legacy client functions

**Files:**
- Modify: `frontend/src/api/documents.ts` (`:648-882` and the types those
  functions use)

**Interfaces:**
- Consumes: Tasks 1–3 (every caller is gone).
- Produces: `documents.ts` exports no series or authored-series function. The
  spending client is `frontend/src/api/spending.ts` and is untouched.

- [ ] **Step 1: Confirm no caller survives**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
for fn in fetchDocumentSeries addSeriesMember removeSeriesMember fetchCharts seriesId fetchChart updateSeriesMeta authoredSeriesId createAuthoredSeries updateAuthoredSeries deleteAuthoredSeries addAuthoredMember removeAuthoredMember fetchAuthoredSuggestions acceptAuthoredSuggestion dismissAuthoredSuggestion fetchAuthoredOddOnesOut; do
  hits=$(grep -rn "\b$fn\b" frontend/src frontend/e2e | grep -v "^frontend/src/api/documents.ts")
  [ -n "$hits" ] && { echo "STILL USED: $fn"; echo "$hits"; }
done; echo "sweep done"
```

Expected: `sweep done` with no `STILL USED` lines.

- [ ] **Step 2: Delete the functions and their types**

Delete the seventeen functions from `frontend/src/api/documents.ts`. Then
delete the interfaces and type aliases that only they used — `DocumentSeries`,
`ChartsResponse`, `SeriesMetaUpdate`, `AuthoredSeriesCreate`,
`CreateSeriesResult`, and any suggestion / odd-one-out / membership types
declared for them.

Do not guess which types are orphaned. For each candidate:

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && grep -rn "\bDocumentSeries\b" frontend/src frontend/e2e
```

Delete only those with no surviving reference. `npm run type-check` in Step 4
is the backstop.

- [ ] **Step 3: Check the api spec file**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && ls frontend/src/api/__tests__/ && grep -rn -il "series\|charts" frontend/src/api/__tests__/
```

Remove any case covering a deleted function. `admin.spec.ts` also appears here
— leave it for Task 9.

- [ ] **Step 4: Run the gates**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5/frontend && npm run test:unit && npm run type-check && npm run lint
```

`vue-tsc` is the real check here: an orphaned type deleted while still
referenced, or a referenced type left behind, both surface as type errors.

- [ ] **Step 5: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add frontend/src/api/documents.ts frontend/src/api/__tests__/
git commit -m "refactor(api-client): delete the 17 legacy series client functions

Every caller was removed in the preceding three commits."
```

---

## Task 5: Remove `compare_to_series` from the Ask engine

**Files:**
- Modify: `src/library/ask/engine.py` — the module docstring (`:5`), the
  system-prompt tool list (`:63-65`), the coverage rule (`:102`), the
  `_REVIEW_STATUS_PROPERTY` comment (`:177-182`), the tool declaration
  (`:279-306`), `_run_compare_to_series` (`:730-746`), the dispatch arm
  (`:942-945`), the import (`:44`), and the `_filters_from_args` docstring
  (`:646`)
- Modify: `src/library/ask/disclosure_scenarios.py` — the
  `series-other-currency` scenario (`:222`) and the module docstring's
  references (`:5`, `:20`, `:49`)
- Modify: `tests/test_ask*.py`, `tests/test_disclosure*.py` as the greps find them

**Interfaces:**
- Consumes: nothing.
- Produces: Ask's tool set is `semantic_search`, `query_documents`,
  `get_document`, `update_document_metadata`. `src/library/series.py` is no
  longer imported by `src/library/ask/`. The disclosure eval has **five**
  scenarios.

- [ ] **Step 1: Find every site before changing any**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "compare_to_series\|summarize_series\|serialise_summary\|_run_compare_to_series" src/library/ask/ tests/
```

Record the list. You will re-run this exact command in Step 6.

- [ ] **Step 2: Delete the tool declaration, implementation and dispatch**

Remove from `src/library/ask/engine.py`:

1. `from library.series import serialise_summary, summarize_series` (`:44`)
2. The whole `{"name": "compare_to_series", ...}` dict in the tools list
   (`:279-306`), including its `input_schema`
3. `async def _run_compare_to_series(...)` in full (`:730-746`)
4. The dispatch arm:

```python
    if name == "compare_to_series":
        result = await _run_compare_to_series(session, settings, args, cited)
        editable_ids.update(cited)
        return result
```

- [ ] **Step 3: Update the four prose sites**

These are not cosmetic — the system prompt is what the model reads.

**Module docstring (`:2-6`)** — drop the `compare_to_series` clause so the tool
list names only the tools that exist.

**System prompt tool list (`:63-65`)** — delete the whole
`- compare_to_series: ...` bullet.

**System prompt coverage rule (`:101-102`)** — currently:

```
- Some tool results carry a "coverage" block (query_documents,
  compare_to_series, and semantic_search).
```

becomes:

```
- Some tool results carry a "coverage" block (query_documents and
  semantic_search).
```

**`_REVIEW_STATUS_PROPERTY` comment (`:174-182`)** — the second half explains
why `compare_to_series` is not offered `review_status`. Delete that half; keep
the first half, which explains why only `query_documents` accepts it. Read the
result to confirm it still reads as a complete explanation.

Also check `_filters_from_args`'s docstring (`:646`), which names
`compare_to_series` as a co-user of the shared filter parsing.

- [ ] **Step 4: Remove the disclosure scenario**

Delete the `series-other-currency` `Scenario` from
`src/library/ask/disclosure_scenarios.py` (`:217-...`, including the four-line
comment above it explaining the `SeriesCoverage` exclusion it exercises).

Then fix the module docstring: `:5` names `compare_to_series` as one of the two
tools, `:20` and `:49` reference `series.py`'s identity rules. Rewrite so the
docstring describes only `query_documents` and `semantic_search`.

- [ ] **Step 5: Update the backend tests**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "compare_to_series\|series-other-currency" tests/
```

Delete cases that call the tool or assert its declaration. If a test asserts
the **count** of tools or scenarios, update the number rather than deleting the
test — that assertion is doing useful work.

- [ ] **Step 6: Verify the sweep**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "compare_to_series\|summarize_series\|serialise_summary" src/library/ask/ tests/ frontend/src/ frontend/e2e/
```

Expected: **no output**. (`src/library/api/documents.py` still imports
`summarize_series` — that is Task 7. It is deliberately not in this grep's
scope.)

- [ ] **Step 7: Run the Ask tests**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && uv run pytest tests/ -k "ask or disclosure" -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add src/library/ask/ tests/
git commit -m "refactor(ask): remove the compare_to_series tool

The band it reported was computed over a series summarize_series narrows
twice in silence (redesign spec 2.1), inflated by the duplicate documents
of 2.4. Removed rather than kept: a wrong answer with a precise-looking
coverage block is worse than no answer. Rebuild on spend_facts is a
later plan (spec 11.1)."
```

---

## Task 6: Guard Ask's `amount_total` commit

The one addition in this plan. `docs/charts.md` §10.1 names five writers of
`amount_total`; four translate migration 0035's deferred-trigger refusal into a
named 400. Ask's is the fifth and commits unguarded, so the same refusal is a
500 with a poisoned session.

**Files:**
- Modify: `src/library/ask/engine.py:847`
- Test: `tests/test_ask_write_tool.py` (or the existing Ask write-tool test
  file — find it in Step 1)

**Interfaces:**
- Consumes: Task 5 (same file).
- Produces: `_run_update_document` returns `{"error": <refusal text>}` instead
  of raising, when the edit would orphan a document's spend lines.

- [ ] **Step 1: Find the Ask write-tool test file**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rln "_run_update_document\|update_document_metadata" tests/
```

Use the file that already builds an Ask write-tool call. Also read
`tests/test_spend_lines.py` for how an **allocated** document is constructed —
reuse that fixture shape rather than inventing one.

- [ ] **Step 2: Write the failing test**

```python
async def test_an_allocated_documents_amount_edit_is_refused_not_a_500(
    session, settings
) -> None:
    """Ask is the fifth amount_total writer; it must translate 0035's refusal.

    A document whose spend lines are allocated against its current amount
    cannot have that amount changed - the deferred mirror trigger refuses at
    COMMIT, as a bare DBAPIError. Unguarded, that is a 500 with a poisoned
    session; guarded, it is a refusal the owner can act on.
    """
    # A document with an amount, split into lines that sum to it. Invented
    # values - this repository is public.
    document = await _document_with_allocated_lines(
        session, amount_total=Decimal("100.00"), lines=[Decimal("60.00"), Decimal("40.00")]
    )

    result = await _run_update_document(
        session,
        settings,
        {"document_id": document.id, "amount_total": "250.00", "confirmed": True},
        editable_ids={document.id},
        previewed_ids={document.id},
    )

    assert "error" in result, f"expected a refusal, got {result!r}"
    assert "spend lines" in result["error"]
    # The session must still be usable - a poisoned session is the actual
    # damage a 500 here does.
    assert await session.get(Document, document.id) is not None
```

Write `_document_with_allocated_lines` as a local helper in that test file if
one does not already exist, modelled on `tests/test_spend_lines.py`'s fixture.

- [ ] **Step 3: Run it and watch it fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && uv run pytest tests/test_ask_write_tool.py -k allocated -q
```

Expected: **FAIL** — a raised `DBAPIError`, not a returned `{"error": ...}`.

If it fails for any other reason (fixture wrong, helper missing), fix the test
until it fails for the stated reason. A test that fails for the wrong reason
proves nothing when it later passes.

- [ ] **Step 4: Add the guard**

In `src/library/ask/engine.py`, add to the imports:

```python
from library.spend_lines import AllocationError, commit_allocation
```

and replace `await session.commit()` (`:847`) with:

```python
    # Committed through the allocation helper, not `session.commit()`. This is
    # the fifth writer of `amount_total` (docs/charts.md 10.1) and was the only
    # one that did not translate migration 0035's deferred mirror trigger: the
    # refusal arrives at COMMIT as a bare DBAPIError, which uncaught is a 500
    # with a poisoned session rather than something the owner can act on.
    try:
        await commit_allocation(
            session,
            refusal=(
                "this document's amount is allocated across spend lines that sum to "
                "the old amount; clear or replace its spend lines before changing it"
            ),
        )
    except AllocationError as exc:
        return {"error": str(exc)}
```

Note this returns a tool-result error dict rather than raising `HTTPException`
as the REST routes do — an Ask tool reports failure to the model in its result,
so the model can tell the user what stopped the edit.

- [ ] **Step 5: Run it and watch it pass**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && uv run pytest tests/test_ask_write_tool.py -q
```

Expected: PASS, and every other case in the file still passes — particularly
any asserting a **successful** edit still commits.

- [ ] **Step 6: Mutation-check**

Revert the guard to a bare `await session.commit()`, keeping the import.
Re-run Step 5.

Expected: **FAIL** with a `DBAPIError`. If it passes, the fixture is not
producing a genuinely allocated document — the test is not exercising the
trigger. Say so explicitly and fix the fixture before continuing. Restore the
guard and confirm `git diff` matches Step 4.

- [ ] **Step 7: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add src/library/ask/engine.py tests/
git commit -m "fix(ask): translate the allocation refusal instead of 500ing

Ask's document-edit tool is the fifth writer of amount_total and was the
only one committing without translating migration 0035's deferred mirror
trigger. On an allocated document an amount correction through Ask was a
500 with a poisoned session; it is now a refusal naming the cause.

Latent until the archive holds its first allocation. Closes the entry in
docs/charts.md 13."
```

---

## Task 7: Remove `GET /api/documents/{id}/series`

**Files:**
- Modify: `src/library/api/documents.py` — the import (`:70`) and the route
  (`:400-429`)
- Delete: `tests/test_documents_api_series.py` (139 lines)

**Interfaces:**
- Consumes: Task 1 (the frontend consumer is gone).
- Produces: `src/library/api/documents.py` no longer imports
  `library.series`. The route is absent from the OpenAPI schema — asserted in
  Task 11.

- [ ] **Step 1: Delete the route**

Remove `src/library/api/documents.py:400-429` — the `@router.get` decorator
with its `responses` block, `async def get_document_series`, its docstring, and
the trailing blank lines up to the `@router.patch` at `:431`. Leave the PATCH
route untouched.

- [ ] **Step 2: Delete the import**

Remove `from library.series import serialise_summary, summarize_series`
(`:70`). Then check whether `DocumentFilters` is still used in that file:

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && grep -n "DocumentFilters" src/library/api/documents.py
```

If the only use was inside the deleted route, remove that import too. If it has
other uses, leave it.

- [ ] **Step 3: Delete the test file**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && git rm tests/test_documents_api_series.py
```

- [ ] **Step 4: Verify**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "documents/{document_id}/series\|get_document_series\|/series'" src/ tests/ frontend/src/ frontend/e2e/
uv run mypy src/library/api/documents.py
```

Expected: no grep output; mypy clean.

- [ ] **Step 5: Run the document tests and commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
uv run pytest tests/ -k documents -q
git add -A
git commit -m "refactor(documents): remove GET /documents/{id}/series

Its only consumer, the detail page's series-chart card, was removed
earlier in this branch."
```

---

## Task 8: Remove the three background jobs

**Files:**
- Modify: `src/library/jobs.py` — three imports (`:70-72`), the three task
  definitions (`:831-863`), and the queue block in the INDEXED hook
  (`:628-659`)
- Modify: `tests/test_jobs_pipeline.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the INDEXED hook defers only `dispatch_document_completion`.
  `_defer_best_effort` keeps its other two callers (`:524`, `:539`) and stays.

- [ ] **Step 1: Delete the three task definitions**

Remove from `src/library/jobs.py`, each with its `@job_app.task` decorator and
docstring: `generate_series_insight` (`:831-840`),
`evaluate_series_autocontinue` (`:843-852`), `evaluate_semantic_groups`
(`:855-863`).

- [ ] **Step 2: Delete the queue block**

In the INDEXED hook, delete everything from the comment beginning `# This
document may have joined (or grown) a recurring series;` through the closing
`)` of the `evaluate_semantic_groups` defer — that is the `sender_id, kind_id =
...` unpacking, the `if sender_id is not None and kind_id is not None:` guard
and both defers inside it, and the unguarded `semantic-group eval` defer with
its three-line comment.

What must remain directly above it is the `dispatch_document_completion` call
and its comment. What must remain below is the `except Exception as exc:` arm.
Read the resulting `try` block to confirm it is still syntactically valid and
that `document` is still used.

- [ ] **Step 3: Delete the three imports**

```python
from library.semantic_membership import auto_add_document
from library.series_insight import refresh_series_insight
from library.series_match import propose_authored_matches
```

- [ ] **Step 4: Confirm `_defer_best_effort` still has callers**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && grep -n "_defer_best_effort" src/library/jobs.py
```

Expected: the definition plus **two** call sites. If zero call sites remain,
delete the helper too — but that would mean you removed more than this task
specifies, so re-read Step 2 first.

- [ ] **Step 5: Update the pipeline test**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && grep -n "series_insight\|autocontinue\|semantic_group" tests/test_jobs_pipeline.py
```

Delete cases asserting those three jobs are deferred. If a case asserts the
**set** of jobs deferred on INDEXED, update the expected set rather than
deleting the case — that assertion is what would catch an accidental removal of
`dispatch_document_completion`.

- [ ] **Step 6: Verify and run**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "generate_series_insight\|evaluate_series_autocontinue\|evaluate_semantic_groups\|refresh_series_insight\|propose_authored_matches\|auto_add_document" src/ tests/ frontend/src/ frontend/e2e/
uv run pytest tests/test_jobs_pipeline.py -q
```

Expected: no grep output (the definitions in `series_insight.py`,
`series_match.py` and `semantic_membership.py` still exist and **will** match —
that is expected until Task 11; confirm the only hits are inside those three
modules).

- [ ] **Step 7: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add src/library/jobs.py tests/test_jobs_pipeline.py
git commit -m "refactor(jobs): remove the three series background jobs

series-insight generation, authored-series autocontinue and Smart Group
auto-add. The INDEXED hook now defers only the completion push."
```

---

## Task 9: Simplify the admin currency-normalise operation

This is a net deletion, not a repair. The conflict machinery exists only
because authored-series overrides can collide on a rename; with those tables
gone, the operation is a plain document rewrite.

**Files:**
- Modify: `src/library/currencies.py` — the module docstring (`:1-25`), the
  `OverrideConflict` dataclass (`:62-70`), `NormalizeResult.conflicts` and its
  `override_conflict` status (`:72-89`), `_OVERRIDE_TABLES` (`:105-107`),
  `_override_conflicts` (`:110-142`), the conflict check inside
  `normalize_currency` (`:164-171`), and five UPDATE/DELETE statements
  (`:187-213`)
- Modify: `src/library/api/admin/fx.py` — `CurrencyConflictItem`,
  `CurrencyOverrideConflict`, the `409` entry in `responses`, the route
  docstring, and the `override_conflict` branch
- Modify: `frontend/src/api/admin.ts` — `CurrencyConflictItem`,
  `CurrencyOverrideConflict`, and three comments (`:321`, `:334`, `:349`, `:364`)
- Modify: `frontend/src/views/admin/AdminMetadataPanel.vue` — the type import
  (`:30`), `normalizeConflicts` (`:138`, `:161`, `:177`, `:189`), the conflict
  block (`:407-418`), and the copy at `:294-297`, `:361`, `:452`
- Modify: `tests/test_currency_admin.py`,
  `frontend/src/api/__tests__/admin.spec.ts`,
  `frontend/src/views/__tests__/AdminView.spec.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `NormalizeResult.status` is
  `Literal["done", "invalid_source", "invalid_target", "same_code"]` — the
  `override_conflict` member is gone. `POST /api/admin/currencies/normalize`
  can no longer return 409. `counts` holds one key, `documents`, plus
  `fx_rate_missing`.

- [ ] **Step 1: Simplify `currencies.py`**

Delete: the `OverrideConflict` dataclass; the `conflicts` field and the
`override_conflict` status from `NormalizeResult` (and its docstring bullet);
`_OVERRIDE_TABLES` with its two-line comment; `_override_conflicts` in full;
and inside `normalize_currency`, this block:

```python
    conflicts = await _override_conflicts(session, from_code, to_code)
    if conflicts:
        return NormalizeResult(
            status="override_conflict",
            from_code=from_code,
            to_code=to_code,
            conflicts=conflicts,
        )
```

Then delete these five statements, keeping the `documents` UPDATE and the
`fx_rates` check:

```python
    counts["authored_series"] = await _run(...)
    counts["authored_series_suggestions"] = await _run(...)
    counts["series_insights_merged"] = await _run(...)
    counts["series_insights"] = await _run(...)
    counts["series_membership_overrides"] = await _run(...)
    counts["series_meta_overrides"] = await _run(...)
```

(That is six statements across four `counts` keys plus the merge — delete every
one that names a series table. Only `counts["documents"]` survives.)

Finally rewrite the module docstring (`:1-25`), which is entirely about series
identity and the override policy, and `normalize_currency`'s docstring, which
promises to rewrite authored series and merge the insight cache.

- [ ] **Step 2: Simplify `fx.py`**

Delete `CurrencyConflictItem`, `CurrencyOverrideConflict`, the `409` entry from
the route's `responses` dict, and the whole `if result.status ==
"override_conflict":` branch. Change the route summary from
`"...(series-aware)"` to `"Rename/normalise a currency code across the whole
store"`, and rewrite its docstring to describe only the document rewrite and
the untouched `fx_rates`.

Then check whether `JSONResponse` is still used in that file:

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && grep -n "JSONResponse" src/library/api/admin/fx.py
```

If the 409 branch was its only use, remove the import and narrow the route's
return annotation from `CurrencyNormalizeOut | JSONResponse` to
`CurrencyNormalizeOut`.

- [ ] **Step 3: Simplify the admin frontend**

In `frontend/src/api/admin.ts`, delete both interfaces and update the four
comments that describe the rename as series-aware or mention a 409.

In `AdminMetadataPanel.vue`, delete the `CurrencyConflictItem` import, the
`normalizeConflicts` ref and its three assignments, the `409` branch in the
error handler, and the `v-if="normalizeConflicts.length"` block
(`:407-418`). Then rewrite the three copy sites that tell the user this is
series-aware — `:294-297` ("part of series identity… collide with your series
overrides"), `:361` ("across all documents and series"), and `:452`
("Cross-currency series convert via a stored USD rate"). The last one is about
FX rates generally and may only need the word "series" changed; read it and
keep what is still true.

- [ ] **Step 4: Update the tests**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -n "conflict\|409\|series" tests/test_currency_admin.py
grep -n "conflict\|409" frontend/src/api/__tests__/admin.spec.ts frontend/src/views/__tests__/AdminView.spec.ts
```

Delete cases asserting the 409 and the conflict list. **Keep** the cases
covering 422 (bad code), 400 (same code) and a successful rename — and update
the successful-rename case's expected `counts` to hold only `documents`.

- [ ] **Step 5: Verify and run the gates**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "override_conflict\|OverrideConflict\|_OVERRIDE_TABLES\|normalizeConflicts\|currency-conflict" src/ tests/ frontend/src/ frontend/e2e/
uv run pytest tests/test_currency_admin.py -q && uv run mypy
cd frontend && npm run test:unit && npm run type-check
```

Expected: no grep output; all green. `frontend/e2e/admin-views.spec.ts` drives
this panel — if it asserts the conflict block, update it here.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add -A
git commit -m "refactor(admin): drop the currency rename's series-override machinery

The conflict detection, the 409 refusal and its confirm modal existed only
because authored-series overrides carry currency in a unique tuple. With
those tables going, a currency rename is a plain document rewrite."
```

---

## Task 10: Remove the settings and the `series_insight` LLM surface

**Files:**
- Modify: `src/library/config.py:189-217` — ten fields
- Modify: `src/library/llm/backends.py:38-41` — the `BACKEND_SURFACES` row
- Modify: `src/library/api/settings.py:316+` — the `_SURFACE_COPY` entry
- Modify: `src/library/app.py:330-335` — the `/healthz` credentials tuple
- Modify: `tests/test_llm_backends.py`, `tests/test_llm_backend_config.py`,
  `frontend/src/views/__tests__/SettingsLlmBackend.spec.ts`

**Interfaces:**
- Consumes: Task 8 (nothing defers a series-insight job any more).
- Produces: `BACKEND_SURFACES == {"ask": "ask_llm_backend"}`. The generic
  surface layer, its two routes and the `v-for` in `SettingsView.vue` are
  **unchanged** — they render one row.

- [ ] **Step 1: Remove the ten settings**

Delete from `src/library/config.py`: `series_insight_llm_backend`,
`series_min_documents`, `series_typical_pct`, `series_flat_pct`,
`series_autocontinue_enabled`, `series_autocontinue_min_dominance`,
`series_suggestion_limit`, `semantic_group_enabled`,
`semantic_group_min_similarity`, `semantic_group_neg_margin` — with the comment
blocks that introduce them (`:189-195`, `:205-207`, `:211-214`).

- [ ] **Step 2: Fix the `/healthz` credentials check**

`src/library/app.py:332-335` reads:

```python
        if credentials_path(settings.claude_config_dir).exists() or "subscription" in (
            settings.ask_llm_backend,
            settings.series_insight_llm_backend,
        ):
```

Replace with:

```python
        if (
            credentials_path(settings.claude_config_dir).exists()
            or settings.ask_llm_backend == "subscription"
        ):
```

An equality test rather than a one-element tuple membership: with one surface
left, `in (x,)` reads as an accident.

- [ ] **Step 3: Remove the surface row and its copy**

In `src/library/llm/backends.py`:

```python
BACKEND_SURFACES: dict[str, str] = {
    "ask": "ask_llm_backend",
}
```

In `src/library/api/settings.py`, delete the `series_insight` entry from
`_SURFACE_COPY`. Leave `_SURFACE_COPY`'s `.get(surface, (surface, ""))`
fallback and everything else in that function alone.

- [ ] **Step 4: Update the tests**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -n "series_insight\|len(surfaces)\|surfaces\[1\]\|two surface" tests/test_llm_backends.py tests/test_llm_backend_config.py
grep -n "series_insight\|toHaveLength" frontend/src/views/__tests__/SettingsLlmBackend.spec.ts
```

Update counts from two to one and delete cases switching the
`series_insight` surface. **Keep** the case asserting an unknown surface name
is rejected (`UnknownSurfaceError`) — it now has more work to do, not less.

- [ ] **Step 5: Verify the app still boots**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "series_min_documents\|series_typical_pct\|series_flat_pct\|series_autocontinue\|series_suggestion_limit\|semantic_group_\|series_insight_llm_backend\|LIBRARY_SERIES_\|LIBRARY_SEMANTIC_GROUP" src/ tests/ frontend/src/ frontend/e2e/
uv run python -c "from library.app import create_app; create_app(); print('boots')"
uv run pytest tests/ -k "llm or settings or config" -q
```

Expected: grep hits only inside the six modules Task 11 deletes; `boots`
printed; tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add -A
git commit -m "refactor(config): remove the ten series settings and the LLM surface

BACKEND_SURFACES keeps its generic machinery and renders one row; the
chart engine's rule-drafting LLM is the obvious next surface but is out
of scope here."
```

---

## Task 11: Delete the six modules, the ORM classes and the backend tests

The root of the tree. Every consumer was removed in Tasks 1–10.

**Files:**
- Delete: `src/library/series.py`, `src/library/series_insight.py`,
  `src/library/series_match.py`, `src/library/semantic_membership.py`,
  `src/library/api/series.py`, `src/library/api/charts.py`
- Modify: `src/library/app.py` — the two imports (`:17`, `:29`) and the two
  mounts (`:263-264`)
- Modify: `src/library/models.py` — seven classes and two enums
- Delete: the fifteen backend test files listed in Step 4
- Test: a new route-absence test (Step 5)

**Interfaces:**
- Consumes: Tasks 1–10.
- Produces: no module named `series*` or `semantic_membership` exists;
  `app.openapi()["paths"]` contains no `/api/charts*` or `/api/series*` key.
  The seven tables still exist in the database — PR 2 drops them.

- [ ] **Step 1: Prove nothing imports them**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "library\.series\|library\.semantic_membership\|from library import series\|api\.charts\|api import charts\|api import series" src/ tests/ \
  | grep -v "^src/library/series\|^src/library/semantic_membership\|^src/library/api/charts.py\|^src/library/api/series.py\|^tests/test_series\|^tests/test_charts\|^tests/test_smart\|^tests/test_semantic_membership"
```

Expected: hits only in `src/library/models.py` docstrings (`:983`, `:1025`,
`:1073`, `:1110`, `:1193`), which Step 3 removes with their classes. Any other
hit is a consumer you missed — handle it before deleting.

- [ ] **Step 2: Unmount and delete the six modules**

In `src/library/app.py`, remove `charts,` (`:17`) and `series,` (`:29`) from
the import list, and both `api_router.include_router(...)` lines
(`:263-264`). Leave `spending.router` at `:261`.

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git rm src/library/series.py src/library/series_insight.py src/library/series_match.py \
       src/library/semantic_membership.py src/library/api/series.py src/library/api/charts.py
```

- [ ] **Step 3: Delete the ORM classes and enums**

From `src/library/models.py`, delete in full: `SeriesInsight` (`:979`),
`SeriesMembershipOverride` (`:1021`), `SeriesMetaOverride` (`:1069`),
`AuthoredSeries` (`:1106`), `AuthoredSeriesMember` (`:1152`),
`AuthoredSeriesSuggestion` (`:1188`), `AuthoredSeriesExclusion` (`:1248`), and
the `SeriesMode` (`:135`) and `SuggestionState` (`:123`) enums.

Their `relationship()` declarations are self-contained among these seven
classes, so there is no `Document`, `Sender` or `Kind` backref to unpick.
Confirm that:

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -n "AuthoredSeries\|SeriesInsight\|SeriesMembershipOverride\|SeriesMetaOverride\|SeriesMode\|SuggestionState" src/library/models.py
```

Expected after the deletions: **no output**.

- [ ] **Step 4: Delete the fifteen test files**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git rm tests/test_series.py tests/test_series_db.py tests/test_series_insight.py \
       tests/test_series_insight_db.py tests/test_series_insight_backend.py \
       tests/test_series_match.py tests/test_series_membership_api.py \
       tests/test_series_overrides.py tests/test_series_suggestions_db.py \
       tests/test_semantic_membership.py tests/test_smart_groups_api.py \
       tests/test_charts_api.py tests/test_charts_suggestions_api.py \
       tests/test_charts_write_paths.py
```

`tests/test_documents_api_series.py` went in Task 7. **Do not delete**
`tests/test_semantic_search.py` — that covers Ask's hybrid retrieval and is
unrelated. **Do not delete** `tests/test_chart_*.py` (`test_chart_draft`,
`test_chart_footer`, `test_chart_model`, `test_chart_query`,
`test_chart_rule`) — those cover the **new** engine.

- [ ] **Step 5: Write the route-absence test**

Asserted against the OpenAPI path set rather than by requesting a 404: a 404
assertion passes for the wrong reasons — an auth redirect, a trailing slash, a
path that was never mounted under the name you typed.

Create `tests/test_series_stack_is_gone.py`:

```python
"""The legacy series stack is unmounted.

Asserted against the OpenAPI path set, not by requesting a 404: a 404 can
mean "auth redirected", "trailing slash", or "you typed a path that never
existed", all of which pass while the router is still mounted.
"""

from library.app import create_app


def test_no_legacy_series_or_charts_route_is_mounted() -> None:
    paths = set(create_app().openapi()["paths"])

    legacy = sorted(
        p
        for p in paths
        if p.startswith("/api/charts")
        or p.startswith("/api/series")
        or p.endswith("/series")
    )
    assert legacy == [], f"legacy routes still mounted: {legacy}"


def test_the_spending_routes_are_still_mounted() -> None:
    """The guard above must not pass by the whole app failing to build."""
    paths = set(create_app().openapi()["paths"])

    assert "/api/spending" in paths
    assert any(p.startswith("/api/spending/") for p in paths)
```

The second test is not redundant: without it, the first passes trivially if
`create_app()` ever returns an app with no routes at all.

- [ ] **Step 6: Run it, and mutation-check it**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && uv run pytest tests/test_series_stack_is_gone.py -q
```

Expected: PASS.

Mutation: `git stash` the deletion of `src/library/api/charts.py` and re-add
`api_router.include_router(charts.router)` to `app.py`. Re-run.

Expected: **FAIL**, listing the thirteen charts paths. Restore afterwards and
confirm `git status` matches Step 2.

- [ ] **Step 7: Run the full backend suite**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
uv run pytest -q 2>&1 | tail -20
uv run mypy && uv run ruff check . && uv run ruff format --check .
```

Expected: green. The count drops from 2222 by roughly the number of cases in
the fifteen deleted files — record the actual number in your task report; a
much larger drop means a shared fixture went with them.

- [ ] **Step 8: Commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add -A
git commit -m "refactor(series): delete the six modules, seven ORM classes and their tests

3,250 lines of backend modules, fifteen routes, two enums and 4,865 lines
of tests. The seven tables stay in the database, orphaned, until the
follow-up PR drops them - so a revert of this deploy still has its rows."
```

---

## Task 12: Rewrite the nightly workflow to keep the recall measurement

`.github/workflows/e2e-nightly.yml` has exactly one job, `smart-groups`, and
its `Measure retrieval recall` step rides on the embedder that job exists to
start. Deleting the workflow silently kills a measurement `docs/ask.md` treats
as live — the exact class of failure this workflow was created to fix.

**Files:**
- Modify: `.github/workflows/e2e-nightly.yml`

**Interfaces:**
- Consumes: Task 2 (the spec it ran is gone).
- Produces: a job named `retrieval-recall` that starts the stack with an
  embedder and runs `library eval-recall`.

- [ ] **Step 1: Read the whole file before editing**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && cat .github/workflows/e2e-nightly.yml
```

Identify precisely which steps the recall measurement depends on: the image
build, the stack start **with the embedder profile**, the API wait, the
embedder model-load wait, and the recall step itself.

- [ ] **Step 2: Rewrite the job**

Rename the job `smart-groups` → `retrieval-recall` and update `name:` and the
file's header comment, which currently explains why the Smart Groups journey
cannot run in the PR gate. The new comment must say why **recall** cannot: it
needs a warm embedder and the CI `e2e` job starts none.

Delete these steps: create the e2e user (only if nothing else needs it — check
first), install dependencies, cache Playwright browsers, install Playwright
browsers, build the frontend, serve the build, `Run the Smart Groups journey`,
`Assert the spec actually ran`, and the Playwright report upload.

Keep: checkout, the image build, the stack start with the embedder, both waits,
`Measure retrieval recall` (**with its `continue-on-error: true`** — the corpus
is deliberately built to contain baseline failures, and gating on it would
reinstate the abort this workflow already had to fix), and `Stack logs on
failure`.

- [ ] **Step 3: Lint it**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5 && uv run actionlint .github/workflows/e2e-nightly.yml || make lint
```

Expected: clean. `actionlint` catches syntax and expression errors only — it
cannot tell you the job still does the right thing.

- [ ] **Step 4: Check nothing else references the deleted spec**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
grep -rn "smart-groups\|E2E_SMART_GROUPS" .github/ frontend/ Makefile package.json 2>/dev/null
```

Expected: no output.

- [ ] **Step 5: Commit, and note the manual check**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add .github/workflows/e2e-nightly.yml
git commit -m "ci(nightly): keep the recall measurement, drop the Smart Groups journey

The job existed to run one spec, but its embedder is also what
library eval-recall needs. Deleting the workflow with the feature would
have silently retired the retrieval-recall measurement."
```

**Record in your task report:** this job cannot be verified locally. After the
PR merges, trigger it once via `workflow_dispatch` and confirm the recall step
produced numbers — a stripped job that no longer starts the embedder correctly
fails silently until the next scheduled run.

---

## Task 13: Documentation, journal and stamps

**Files:**
- Move: `docs/smart-groups.md` → `docs/archive/smart-groups.md`
- Modify: `docs/README.md`, `docs/api.md`, `docs/ask.md`,
  `docs/architecture.md`, `docs/frontend.md`, `docs/charts.md`,
  `docs/admin.md`, `docs/llm-backends.md`, `docs/jobs-and-notifications.md`,
  `docs/observability.md`, `docs/deployment.md`, `docs/roadmap.md`
- Modify: `src/library/api/spending.py` — the prefix docstring
- Create: `journal/260831-delete-series-stack.md`

**Interfaces:**
- Consumes: Tasks 1–12.
- Produces: `scripts/check_docs.py` and the journal-index check pass.

- [ ] **Step 1: Archive the Smart Groups doc**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git mv docs/smart-groups.md docs/archive/smart-groups.md
```

Add immediately under its H1:

```markdown
**Status:** superseded by [charts.md](../charts.md) (2026-08-31). The feature
and every module behind it were removed in plan 5 of the charts redesign; a
chart rule over the facet vocabulary spans senders deterministically, which is
what a Smart Group existed to do. Kept for the reasoning it records: the
name-to-seed-query poisoning incident (§4.1) and why membership scoring used
nearest-positive-neighbour rather than a centroid (§3).
```

- [ ] **Step 2: Update the five inbound links**

`check_docs.py:64` excludes `archive` from the stamp gate, and `:573` requires
every **gated** doc to be reachable from `docs/README.md` — so the index row is
**removed**, not repointed.

- `docs/README.md:34` — delete the `smart-groups.md` row
- `docs/api.md:1456` — the sentence pointing at it
- `docs/roadmap.md:149-150` — the entry
- `docs/frontend.md:363` — the whole `ChartsView` table row
- `docs/frontend.md:936` — the paragraph about the nightly spec (rewrite for
  Task 12's job, don't delete: the workflow still exists)

- [ ] **Step 3: Rewrite the doc bodies**

| Document | Change |
| --- | --- |
| `api.md` | §1.13–1.15 deleted; the fifteen legacy rows from the route table (L58-72); the `/documents/{id}/series` row; the currency-normalise 409 |
| `ask.md` | §1.7 deleted; §1.2's coverage contract narrowed to `query_documents` and `semantic_search`; the §1.10 series item removed; the tool diagram (L40); the two `LIBRARY_SERIES_*` env rows; disclosure scenarios six → five; **add** the §11.1 limitation (Step 4) |
| `architecture.md` | the six module rows in the module map (`check_docs` enforces that section exists) |
| `frontend.md` | the tile and view rows beyond L363 |
| `charts.md` | delete §13's "fifth `amount_total` writer is unguarded" entry — Task 6 fixed it |
| `admin.md` | the currency-normalise 409 and its confirm step |
| `llm-backends.md` | the `series_insight` row; note that one surface remains |
| `jobs-and-notifications.md` | the three job rows |
| `observability.md`, `deployment.md`, `roadmap.md` | remaining references |

- [ ] **Step 4: Add the honest limitation to `ask.md` §1.10**

The spec's §11.1. Write it as a new numbered item:

```markdown
N. **Money totals are computed from the pre-facet model.** `sum_amount` sums
   raw `amount_total` and reports three exclusions (`no_amount`,
   `quote_not_spend`, `no_sender`/`no_kind`). It does not read `amount_kind`,
   so a refund **adds to** a total instead of reducing it and the
   non-summable kinds (`coverage_limit`, `balance`, `estimate`, `none`) still
   contaminate it; it does not collapse payment identity, so an unmerged
   invoice/receipt pair is counted twice; and it cannot filter by facet, so
   the `category` vocabulary is invisible to Ask. A chart and an Ask answer to
   the same money question can therefore disagree, and `coverage.excluded` has
   no bucket that would say so. See [charts.md](charts.md) and the plan in
   [the plan-5 spec](superpowers/specs/2026-08-31-delete-series-stack-design.md) §11.1.
```

Add the matching forward-looking entry to `docs/roadmap.md`.

- [ ] **Step 5: Correct the spending API docstring**

`src/library/api/spending.py:10-12` promises the router takes `/api/charts`
when the old stack is deleted. That promise is withdrawn (spec §2.4). Replace
with a note that it stays at `/api/spending` deliberately, and why: `/charts`
is the name the redesign replaced, and the frontend route keeping `/charts` is
a knowingly accepted asymmetry.

- [ ] **Step 6: Write the journal entry**

`journal/260831-delete-series-stack.md`, H1 `# Deleting the series stack`
(clean title — no number or date; that is the repo convention). Cover the five
decisions and **why**, the six live consumers the redesign spec's removal list
did not name, why the drop is a second PR, and the Ask commit-guard fix. No
real sender, amount or person.

- [ ] **Step 7: Re-stamp every touched document**

Each gated doc carries `**Status:**`, `**Last updated:**`, `**Last verified:**`
and `**Covers:**`. Update the stamps on every document you edited, saying what
you actually did — `check_docs` compares the stamp date against the mtime of
the paths in `Covers:`.

- [ ] **Step 8: Run the doc gates**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
uv run python scripts/check_docs.py && make lint
```

Expected: both clean.

- [ ] **Step 9: Full verification before the PR**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
uv run pytest -q 2>&1 | tail -5
make lint
cd frontend && npm run test:unit && npm run lint && npm run type-check && npm run test:e2e
```

E2E must be green on **all three** viewport projects. If a spec you never
touched fails, suspect a stale local stack before suspecting your change:
check `/healthz`'s `git_sha` against HEAD and use the e2e compose overlay.

- [ ] **Step 10: Commit and open PR 1**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git add -A
git commit -m "docs: retire the series stack's documentation

Archives smart-groups.md as superseded, rewrites the legacy sections of
api.md, ask.md, architecture.md and frontend.md, and records in ask.md
1.10 that Ask's money totals still read the pre-facet model."
git push -u origin delete-series-stack
gh pr create --title "refactor: delete the legacy series stack (plan 5, code)" \
  --body "Plan 5 of the charts redesign, code half. Deletes six backend
modules, fifteen routes, seven ORM classes, eight frontend components and
composables and 4,865 lines of tests; archives docs/smart-groups.md as
superseded; rewrites the nightly workflow so the retrieval-recall
measurement survives; and guards Ask's amount_total commit, which was the
only one of five writers not translating migration 0035's refusal.

The seven tables are deliberately left in place - a follow-up PR drops
them, so a revert of this deploy still has its rows.

Spec: docs/superpowers/specs/2026-08-31-delete-series-stack-design.md"
```

Do **not** dispatch CI manually: `gh workflow run CI --ref <branch>` cancels
the in-flight push run (same concurrency group) and `gh pr checks` renders that
as a failure. Opening the PR is how `e2e` gets scheduled.

**Merge gate:** `backend` ~16-18 min, `e2e` ~11 min. Before deploying, confirm
the `promote` job on `main` concluded `success` — it is not scheduled until
`build` finishes, so "all jobs complete" can be true while `promote` does not
yet exist. Deploy from a worktree actually on `main`; `make deploy` reads the
local worktree's HEAD. If the squash-merge lands after 00:00 UTC, expect
`docs-stamps` to red on `main` and re-stamp.

---

# PR 2 — the drop

**Do not start Task 14 until PR 1 is merged, deployed, and production has been
confirmed healthy.** The soak window is the entire reason for the split.

## Task 14: Drop the seven tables

**Files:**
- Create: `migrations/versions/0038_drop_series_stack.py`
- Test: `tests/test_migration_0038.py`

**Interfaces:**
- Consumes: PR 1 (no code reads these tables).
- Produces: alembic head `0038`. `downgrade()` recreates the seven tables
  **empty** — it restores schema, never rows.

- [ ] **Step 1: Confirm the head and that nothing reads the tables**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
git checkout main && git pull --ff-only origin main
git checkout -b drop-series-tables
ls migrations/versions/ | tail -3
grep -rn "series_insights\|authored_series\|series_membership_overrides\|series_meta_overrides" src/ frontend/src/
```

Expected: head is `0037`; the grep returns **nothing**.

- [ ] **Step 2: Confirm a recent backup exists**

PBS backs the database up nightly. Confirm the most recent snapshot predates
nothing you care about before running this against production. This is a
manual check, not a code step — record what you saw in the task report.

- [ ] **Step 3: Write the migration**

```python
"""Drop the legacy series stack's seven tables.

Plan 5 of the charts redesign. The code that read these was removed in the
previous PR and deployed; this is the irreversible half, split out so that
between the two deploys a revert still had its rows.

``downgrade`` recreates the tables EMPTY. It restores the schema so an older
image can start, not the data - those rows are gone. Restore from a backup if
you need them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Children before parents: members, suggestions and exclusions all carry an
# FK to authored_series.
_DROP_ORDER: tuple[str, ...] = (
    "authored_series_members",
    "authored_series_suggestions",
    "authored_series_exclusions",
    "authored_series",
    "series_membership_overrides",
    "series_meta_overrides",
    "series_insights",
)


def upgrade() -> None:
    for table in _DROP_ORDER:
        op.drop_table(table)


def downgrade() -> None:
    # See the note below: this body is shape (b), mirroring the create_table
    # calls of 0009, 0015, 0018, 0019, 0021 and 0029. Write them out here.
    ...
```

**`downgrade()` is the one decision this task leaves open.** Two defensible
shapes:

(a) `raise NotImplementedError` as above — honest, and refuses to pretend.
(b) Recreate the seven tables empty, mirroring the `op.create_table` calls in
    `0009`, `0015`, `0018`, `0019`, `0021` and `0029`, so an older image can
    boot against the schema.

The spec chose **(b)**: an older image that starts is the whole point of having
kept a revert path open. Read those six migrations and mirror their
`create_table` calls exactly — column types, nullability, server defaults, the
`UNIQUE` tuples (several use NULLS NOT DISTINCT semantics) and the FKs. If
mirroring them faithfully proves larger than it looks, fall back to (a) and say
so in the task report rather than writing an approximate schema, which is worse
than none.

- [ ] **Step 4: Write the migration test**

```python
async def test_upgrade_drops_all_seven_tables(alembic_runner, sync_engine) -> None:
    alembic_runner.migrate_up_to("0038")

    inspector = sa.inspect(sync_engine)
    present = set(inspector.get_table_names())
    for table in (
        "series_insights",
        "series_membership_overrides",
        "series_meta_overrides",
        "authored_series",
        "authored_series_members",
        "authored_series_suggestions",
        "authored_series_exclusions",
    ):
        assert table not in present, f"{table} survived the drop"
    # The guard must not pass by the whole schema being absent.
    assert "documents" in present
    assert "charts" in present


async def test_downgrade_restores_the_schema_empty(alembic_runner, sync_engine) -> None:
    alembic_runner.migrate_up_to("0038")
    alembic_runner.migrate_down_to("0037")

    inspector = sa.inspect(sync_engine)
    present = set(inspector.get_table_names())
    assert "authored_series" in present
    with sync_engine.connect() as conn:
        count = conn.execute(sa.text("SELECT count(*) FROM authored_series")).scalar()
    assert count == 0, "downgrade restores schema, never rows"
```

Match the fixture names your repo's existing migration tests use — read one
first (`grep -rln "migrate_up_to" tests/`). If no migration-test harness
exists, run `alembic upgrade head` and `alembic downgrade -1` against the test
Postgres directly and assert with `sa.inspect`.

- [ ] **Step 5: Run against a real Postgres**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
uv run pytest tests/test_migration_0038.py -q
uv run ruff format migrations/versions/0038_drop_series_stack.py && uv run ruff check .
```

CI runs `ruff format --check` over `migrations/` too — format before pushing.

- [ ] **Step 6: Run the full suite and commit**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library-5
uv run pytest -q 2>&1 | tail -5
make lint
git add migrations/versions/0038_drop_series_stack.py tests/test_migration_0038.py
git commit -m "feat(db): drop the legacy series stack's seven tables

The code that read them was removed and deployed in the previous PR.
downgrade() restores the schema empty so an older image can boot; the
rows are gone and only a backup brings them back."
git push -u origin drop-series-tables
gh pr create --title "feat(db): drop the series stack's tables (plan 5, migration)"
```

- [ ] **Step 7: Deploy deliberately**

Confirm the `promote` job on `main` concluded `success`, then `make deploy`
from a worktree on `main`. After deploying, check `/healthz` and load `/charts`
and a document detail page before calling it done.

---

## Self-review notes

**Spec coverage.** §2.1 Smart Groups → Tasks 2, 11, 13. §2.2
`compare_to_series`/`DocumentSeriesTrend` → Tasks 1, 3, 5. §2.3 two PRs →
the PR 1 / PR 2 split. §2.4 `/api/spending` prefix → Task 13 Step 5. §2.5 LLM
surfaces → Task 10. §3 inventory → Tasks 1–11. §4 six consumers → Tasks 1
(detail page), 5 (Ask), 5 (disclosure eval), 7 (documents route), 8 (jobs), 9
(currencies), 12 (workflow). §5 three rewrites → Tasks 12, 9, 1. §6 commit
guard → Task 6. §8 three tests → Tasks 1, 6, 11. §9 docs → Task 13. §10 risks
→ Tasks 12, 13, 14.

**Known soft spots**, called out rather than hidden:

- Task 13's doc rewrites are the least mechanical work in the plan and the
  hardest to specify precisely — the section boundaries in `api.md` and
  `ask.md` must be read, not trusted from line numbers, which shift as earlier
  edits land.
- Task 14 Step 3 leaves a genuine decision open (faithful schema mirror vs.
  `NotImplementedError`) because the honest answer depends on reading six
  migrations. The fallback is stated so the implementer does not invent an
  approximate schema.
- Line numbers throughout are from `main` at `f8ffbaa`. They drift as tasks
  land. Treat them as locators, and confirm by content.
