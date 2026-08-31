/**
 * Real-geometry spec for the `PageHeader` controls slot.
 *
 * `/jobs` and `/matters` each used to open a second full-width band below the
 * header just to hold their filter bar. `PageHeader`'s `#controls` slot
 * merges that bar into the header row — controls left, page commands right —
 * when the page container is wide enough, and stacks them when it is not.
 *
 * This spec drives `/jobs` for every merge/stack geometry assertion below
 * (previously `/charts/legacy` carried three of these tests, before the
 * series-stack removal deleted `ChartsView`/`ChartControls` along with that
 * route — see `router/__tests__/spending-routes.spec.ts`). The new spending
 * workspace at `/charts/:chartId(\d+)` is not a substitute: its `PageHeader`
 * usage (`SpendingWorkspaceView.vue`) passes only `#controls`
 * (`workspace-toolbar`/`workspace-toolbar-chip`), never `#actions`, so it has
 * no second group to merge with and cannot express this claim at all —
 * confirmed by driving it directly: `page-header-actions` never renders on
 * that route, at any viewport or sidebar state. `/jobs` is the fallback this
 * file already had a foothold in (the "jobs filter bar" test below predates
 * this change), and it has both groups (`jobs-filter-bar` controls,
 * `jobs-show-system`/`jobs-columns-button` actions), so it carries the whole
 * file now.
 *
 * "Wide enough" is a **container** measurement, not a viewport one, and that is
 * the claim this file exists to pin down. The content column is the viewport
 * minus a sidebar the user collapses independently, so the same viewport width
 * produces different amounts of room:
 *
 *     viewport  sidebar     #app-page  merged?
 *     1280      expanded    1024        no
 *     1280      collapsed   1200        yes
 *
 * Re-measured on 2026-08-31 against the real stack, driving `/jobs` (the
 * table's numbers happen to match the pre-2026-08-31 `/charts/legacy`
 * measurement exactly, because the `@5xl` merge threshold lives on
 * `PageHeader`'s own container — see `PageHeader.vue` — and does not depend
 * on which view's content fills the controls/actions slots; this was
 * confirmed empirically with a throwaway viewport sweep, not assumed from the
 * old table). A `lg:` breakpoint cannot express that row — it would either
 * stack a 1200px column that had ample room, or merge a 1024px one that did
 * not. `test('...container, not the viewport')` below is that table as an
 * assertion: if someone swaps the container query for a viewport one, exactly
 * that test goes red.
 *
 * Geometry, not class lists — see the note at the top of `charts-layout.spec.ts`.
 *
 * Companion to `charts-layout.spec.ts`, `ask-layout.spec.ts` and
 * `detail-layout.spec.ts`.
 */
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'
import { expectNoHorizontalOverflow, rectOf } from './fixtures/layout'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

const CONTROLS = '[data-testid="page-header-controls"]'
const ACTIONS = '[data-testid="page-header-actions"]'

requireStack()

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  // Wait for the auth guard to land before filling: the form remounts across
  // that redirect and takes the typed values with it, so a fill that races it
  // submits an empty form and never reaches the API.
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

/** Set the sidebar state the way the app persists it, then reload to apply it. */
async function setSidebar(page: Page, expanded: boolean): Promise<void> {
  await page.evaluate((value) => {
    localStorage.setItem('library:sidebar-expanded', String(value))
  }, expanded)
  await page.reload()
}

/** Opens `/jobs` — see the file header for why it now carries every
 * merge/stack geometry test in this file. */
async function openJobs(page: Page): Promise<void> {
  await page.goto('/jobs')
  await expect(page.getByTestId('jobs-filter-bar')).toBeVisible()
}

/** True when the controls and the actions render on one visual row. */
async function isMerged(page: Page): Promise<boolean> {
  const controls = await rectOf(page, CONTROLS)
  const actions = await rectOf(page, ACTIONS)
  // `items-end`: on one row they share a bottom edge. Stacked, the controls sit
  // entirely above the actions. 4px absorbs sub-pixel rounding between a select
  // and a button.
  return Math.abs(controls.bottom - actions.bottom) < 4
}

test('the jobs filter bar and the page actions share one header row on a wide screen', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', 'a desktop-width claim')
  await signIn(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await openJobs(page)

  expect(await isMerged(page), 'controls and actions must share the header row at 1440px').toBe(
    true,
  )

  const controls = await rectOf(page, CONTROLS)
  const actions = await rectOf(page, ACTIONS)

  // Controls left, actions right — the whole point of the merge. Without
  // `ml-auto` a lone actions group would sit next to the controls instead.
  expect(controls.left).toBeLessThan(actions.left)
  const page_ = await rectOf(page, '#app-page')
  expect(
    page_.right - actions.right,
    'actions must be flush to the content column’s right edge',
  ).toBeLessThan(40)

  await expectNoHorizontalOverflow(page, 'jobs header toolbar at 1440px')
})

test('the merge is gated on the container, not the viewport', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', 'a desktop-width claim')
  await signIn(page)
  await page.setViewportSize({ width: 1280, height: 900 })

  // Same viewport, both sidebar states. The outcomes differ, which is precisely
  // what a viewport breakpoint could not do.
  await setSidebar(page, true)
  await openJobs(page)
  expect(
    await isMerged(page),
    'at 1280px with the sidebar EXPANDED the column is too narrow — must stack',
  ).toBe(false)
  await expectNoHorizontalOverflow(page, 'jobs header, 1280px, sidebar expanded')

  await setSidebar(page, false)
  await openJobs(page)
  expect(
    await isMerged(page),
    'at 1280px with the sidebar COLLAPSED there is room — must merge',
  ).toBe(true)
  await expectNoHorizontalOverflow(page, 'jobs header, 1280px, sidebar collapsed')
})

test('stacking below the threshold puts the controls above the actions, not overlapping', async ({
  page,
}) => {
  await signIn(page)
  await setSidebar(page, true)
  await openJobs(page)

  // On a phone the header is always stacked. Reading order is DOM order is
  // visual order — controls, then actions — so focus order never disagrees with
  // what is on screen.
  const controls = await rectOf(page, CONTROLS)
  const actions = await rectOf(page, ACTIONS)
  if (Math.abs(controls.bottom - actions.bottom) >= 4) {
    expect(controls.bottom, 'stacked: the controls must sit above the actions').toBeLessThanOrEqual(
      actions.top + 1,
    )
  }
  await expectNoHorizontalOverflow(page, 'jobs header, stacked')
})

test('the jobs filter bar rides in the header toolbar with one label recipe', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', 'a desktop-width claim')
  await signIn(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/jobs')
  await expect(page.getByTestId('jobs-filter-bar')).toBeVisible()

  expect(await isMerged(page), 'jobs controls and actions must share the header row').toBe(true)

  // §5: one label recipe per bar. Both filter labels use `.filter-label`, so
  // they share a computed font-size and text-transform — the thing that made
  // the pre-2026-07-01 charts bar look wrong when it did not.
  const styles = await page.evaluate(() => {
    const of = (sel: string) => {
      const el = document.querySelector(sel)
      if (!el) return null
      const s = getComputedStyle(el)
      return { size: s.fontSize, transform: s.textTransform, weight: s.fontWeight }
    }
    return {
      task: of('label[for="jobs-task-filter"]'),
      document: of('label[for="jobs-document-filter"]'),
    }
  })
  expect(styles.task).not.toBeNull()
  expect(styles.document).toEqual(styles.task)
  expect(styles.task?.transform).toBe('uppercase')

  await expectNoHorizontalOverflow(page, 'jobs header toolbar at 1440px')
})

test('the matters archived toggle sits in the header instead of its own band', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', 'a desktop-width claim')
  await signIn(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/matters')
  await expect(page.getByTestId('matter-archived-toggle')).toBeVisible()

  const toggle = await rectOf(page, '[data-testid="matter-archived-toggle"]')
  const header = await rectOf(page, '[data-testid="page-header"]')
  expect(
    toggle.bottom,
    'the toggle must live inside the header, not in a band below it',
  ).toBeLessThanOrEqual(header.bottom + 1)

  await expectNoHorizontalOverflow(page, 'matters header toolbar at 1440px')
})
