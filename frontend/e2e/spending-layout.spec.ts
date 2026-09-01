/**
 * Real-geometry regression spec for `/charts/:chartId` — the spending
 * workspace's toolbar/chip switch and its drill-panel presentation.
 *
 * `SpendingWorkspaceView` measures its OWN content column with a
 * `ResizeObserver` (`SHEET_THRESHOLD_PX`, 48rem = 768px) rather than reading a
 * viewport breakpoint, because the drill panel is a native `<dialog>` — top
 * layer, out of flow, unreachable by any `@container` query rooted in the
 * page. The same 768px number drives the toolbar-vs-chip switch via a NAMED
 * container query (`@3xl/workspace:`) on `#spending-workspace-view`'s own
 * `@container/workspace`. Both are geometry claims, so both are measured here
 * against real rects — never read off a class list (see the note atop the
 * now-deleted `charts-layout.spec.ts`, this file's predecessor).
 *
 * Measured directly against the real stack, 2026-08-30, at the three matrix
 * viewports:
 *
 *     viewport   sidebar     workspace column   toolbar/chip   drill panel
 *     1280       expanded    960px               row            side (panel)
 *     1280       collapsed   1136px              row            side (panel)
 *     656        —           608px               chip           sheet
 *     375        —           343px               chip           sheet
 *
 * All four columns are on the same side of the 768px threshold in both
 * dimensions at once — chromium never sees the chip or the sheet, the two
 * narrow projects never see the row or the side panel — which is exactly why
 * the panel/sheet tests below are per-project rather than universal, each
 * skipped on the projects that cannot reach the OTHER presentation.
 *
 * The drill panel needs something to drill into. A chart's rule can be
 * empty ("All spending"), but the panel itself only opens from a bar, an
 * `Other` segment, or a footer bucket — none of which exist over a
 * genuinely empty archive. Each test that opens the panel therefore seeds one
 * throwaway document via the API: an `amount_total` with no `amount_kind` and
 * no `document_date` lands in the footer's `unclassified` bucket (never
 * `undated`/`outside` — those need a date to have an opinion about), which
 * renders as a real, clickable `spending-footer-bucket-unclassified` button
 * without waiting on OCR/embedding (`charts/footer.py`'s `unclassified` arm
 * fires before the date check). No `document_date` is set, per
 * library-e2e-shared-backend-sort: a dated fixture would pollute the
 * dashboard's default sort for specs that click `.first()` later in the same
 * shared-backend run.
 *
 * Companion to `spending-board.spec.ts`, `header-toolbar.spec.ts` and the
 * other `*-layout.spec.ts` files.
 */
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'
import {
  expectDockedToBottom,
  expectNoHorizontalOverflow,
  rectOf,
  viewportOf,
} from './fixtures/layout'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

const PANEL = '[data-testid="drill-panel"]'

requireStack()

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

/** Set the sidebar state the way the app persists it, then reload to apply
 * it — same helper `header-toolbar.spec.ts` uses for the same reason. */
async function setSidebar(page: Page, expanded: boolean): Promise<void> {
  await page.evaluate((value) => {
    localStorage.setItem('library:sidebar-expanded', String(value))
  }, expanded)
  await page.reload()
}

function csrfHeader(page: Page): Promise<{ 'X-CSRF-Token': string }> {
  return page
    .context()
    .cookies()
    .then((cookies) => {
      const csrf = cookies.find((c) => c.name === 'library_csrftoken')
      if (!csrf) throw new Error('no CSRF cookie — signIn() must run first')
      return { 'X-CSRF-Token': csrf.value }
    })
}

/** A bare "All spending" chart with no documents — enough to open the
 * workspace and read its toolbar/chip, nothing to drill into. */
async function createBareChart(page: Page, name: string): Promise<number> {
  const headers = await csrfHeader(page)
  const res = await page.request.post('/api/spending', {
    headers,
    data: { name, rule: { all: [] }, default_split: null, display_currency: 'EUR' },
  })
  expect(res.status(), 'creating the probe chart').toBe(201)
  const chart = (await res.json()) as { id: number }
  return chart.id
}

/** A throwaway document with an amount and no `amount_kind` (and no
 * `document_date`), plus an "All spending" chart over it — enough for the
 * footer's `unclassified` bucket to render a clickable drill trigger. See the
 * file header for why this specific shape and not a document_date. */
async function seedUnclassifiedChart(
  page: Page,
  marker: string,
): Promise<{ documentId: number; chartId: number }> {
  const headers = await csrfHeader(page)

  const upload = await page.request.post('/api/documents', {
    headers,
    multipart: {
      file: { name: `${marker}.txt`, mimeType: 'text/plain', buffer: Buffer.from(`layout probe ${marker}`) },
    },
  })
  expect(upload.status(), 'seeding the probe document').toBe(201)
  const { id: documentId } = (await upload.json()) as { id: number }

  const patch = await page.request.patch(`/api/documents/${documentId}`, {
    headers,
    data: { title: marker, amount_total: '42.50', currency: 'EUR' },
  })
  expect(patch.status(), 'giving the probe document an amount').toBe(200)

  const chartId = await createBareChart(page, marker)
  return { documentId, chartId }
}

async function deleteChart(page: Page, chartId: number): Promise<void> {
  const headers = await csrfHeader(page)
  await page.request.delete(`/api/spending/${chartId}`, { headers })
}

async function cleanup(page: Page, documentId: number | null, chartId: number): Promise<void> {
  await deleteChart(page, chartId)
  if (documentId !== null) {
    const headers = await csrfHeader(page)
    await page.request.delete(`/api/documents/${documentId}`, { headers })
  }
}

test('the workspace toolbar is one row above the threshold and a chip below', async ({
  page,
}, testInfo) => {
  await signIn(page)
  const chartId = await createBareChart(page, `layout-toolbar-${testInfo.project.name}-${Date.now()}`)

  try {
    await page.goto(`/charts/${chartId}`)
    await expect(page.getByTestId('workspace-headline-figure')).toBeVisible()

    if (testInfo.project.name === 'chromium') {
      // 960px (expanded) and 1136px (collapsed) at 1280px — both above the
      // 768px threshold, so the row shows and the chip never does, in EITHER
      // sidebar state (the table in the file header).
      for (const expanded of [true, false]) {
        await setSidebar(page, expanded)
        await expect(page.getByTestId('workspace-headline-figure')).toBeVisible()
        await expect(
          page.getByTestId('workspace-toolbar'),
          `sidebar expanded=${expanded}: the full row should show`,
        ).toBeVisible()
        await expect(page.getByTestId('workspace-grain'), `sidebar expanded=${expanded}`).toBeVisible()
        await expect(
          page.getByTestId('workspace-toolbar-chip'),
          `sidebar expanded=${expanded}: the chip should NOT show`,
        ).toBeHidden()
      }
    } else {
      // 608px (tablet) / 343px (mobile) — both below the threshold: the chip
      // shows and the full row does not.
      await expect(page.getByTestId('workspace-toolbar-chip')).toBeVisible()
      await expect(page.getByTestId('workspace-toolbar-chip-button')).toBeVisible()
      await expect(page.getByTestId('workspace-toolbar'), 'the full row should NOT show').toBeHidden()
    }

    await expectNoHorizontalOverflow(page, `workspace toolbar @${testInfo.project.name}`)

    // The rule editor's clause row is the widest thing this view renders: a
    // facet select, an is/is-not select, a values pill and a Remove button.
    // At 343px (mobile) it MUST have stacked — `@lg/workspace` is 512px — and
    // the values list must stay inside its popover rather than in flow. This
    // assertion is the only check on that breakpoint number; nothing in jsdom
    // can measure it, per §1.7.3.
    await page.getByTestId('workspace-edit-rule').click()
    await expect(page.getByTestId('chart-rule-editor')).toBeVisible()
    await page.getByTestId('rule-add-clause').click()
    await expect(page.getByTestId('rule-editor-row')).toHaveCount(1)
    await expectNoHorizontalOverflow(page, `rule editor row @${testInfo.project.name}`)

    // With the values popover open too — its panel is `max-w-[calc(100vw-1rem)]`,
    // and that cap is what a long vocabulary would otherwise defeat.
    await page.getByTestId('filter-pill-button').first().click()
    await expectNoHorizontalOverflow(page, `rule editor values open @${testInfo.project.name}`)
  } finally {
    await cleanup(page, null, chartId)
  }
})

/**
 * The test above never actually distinguishes a container query from a
 * viewport one: at 1280px the sidebar-expanded column (960px) and the
 * sidebar-collapsed column (1136px) are BOTH above 768px, exactly as a
 * `lg:` (1024px viewport) rule would also report "wide enough" at a 1280px
 * viewport regardless of sidebar state — so swapping `@3xl/workspace:` for
 * `lg:` does not flip either assertion above (confirmed: mutating and
 * re-running the two tests around this one left both green — see the task
 * report). This test is what actually catches that swap.
 *
 * Measured 2026-08-30 (`probe_widths.js` sweep, sidebar expanded): the
 * column crosses the 768px threshold between viewport 1088px (768px column,
 * row) and 1080px (760px column, chip) — a band that sits entirely ABOVE the
 * `lg:` breakpoint (1024px), so a viewport there still satisfies `lg:` while
 * the real column has already dropped below the container threshold. 1024px
 * itself lands in that band (704px column, chip) and is a round, stable
 * number to pin the assertion to — `lg:` reports "merged" there while the
 * real container reports "not wide enough", and no viewport rule can
 * reproduce that split (the same argument `header-toolbar.spec.ts`'s
 * "gated on the container, not the viewport" test makes for `@5xl:`/#app-page).
 */
test('the toolbar/chip switch is gated on the container, not the viewport', async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', 'a desktop-viewport claim')
  await signIn(page)
  const chartId = await createBareChart(page, `layout-gate-${Date.now()}`)

  try {
    await page.setViewportSize({ width: 1024, height: 900 })
    await setSidebar(page, true)
    await page.goto(`/charts/${chartId}`)
    await expect(page.getByTestId('workspace-headline-figure')).toBeVisible()

    // At exactly the `lg:` breakpoint (1024px viewport), sidebar expanded,
    // the workspace's OWN column is 704px — below the 768px threshold. A
    // `lg:` rule would show the row here (viewport satisfies `min-width:
    // 1024px`); the real container query must show the chip instead.
    await expect(
      page.getByTestId('workspace-toolbar-chip'),
      '1024px viewport, sidebar expanded: column is 704px (<768px) — the chip must show',
    ).toBeVisible()
    await expect(
      page.getByTestId('workspace-toolbar'),
      '1024px viewport, sidebar expanded: the row must NOT show — a `lg:` rule would wrongly show it here',
    ).toBeHidden()

    await expectNoHorizontalOverflow(page, 'workspace toolbar @1024, sidebar expanded')
  } finally {
    await cleanup(page, null, chartId)
  }
})

test('the drill panel sits beside the chart above the threshold', async ({ page }, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium',
    'neither narrow project (656px, 375px) ever produces the >=768px workspace ' +
      'column this presentation needs — both are the "docked to bottom" case below',
  )
  await signIn(page)
  const marker = `layout-panel-${Date.now()}`
  const { documentId, chartId } = await seedUnclassifiedChart(page, marker)

  try {
    await page.goto(`/charts/${chartId}`)
    await page.getByTestId('spending-footer-bucket-unclassified').click()

    const panel = page.getByTestId('drill-panel')
    await expect(panel).toBeVisible()
    await expect(panel).toHaveAttribute('data-presentation', 'panel')

    // Measured 2026-08-30 against the real stack, at BOTH
    // sidebar states (the panel is `position: fixed` against the viewport,
    // not the sidebar-relative content column, so both give identical
    // numbers): top 0px, right === viewport.width, bottom === viewport.height,
    // width 448px (28rem). A side panel is flush to the right edge and
    // full-height; a bottom sheet (the other test below) is neither.
    const rect = await rectOf(page, PANEL)
    const viewport = await viewportOf(page)

    expect(viewport.width - rect.right, 'the panel must be flush to the right edge').toBeLessThanOrEqual(2)
    expect(rect.top, 'the panel must reach the top of the viewport').toBeLessThanOrEqual(2)
    expect(
      viewport.height - rect.bottom,
      'the panel must reach the bottom of the viewport (full height, not a short sheet)',
    ).toBeLessThanOrEqual(2)
    expect(
      rect.width,
      'the panel must not cover the whole viewport — the chart stays beside it, dimmed',
    ).toBeLessThan(viewport.width * 0.6)

    await expectNoHorizontalOverflow(page, 'workspace with the side panel open')
  } finally {
    await cleanup(page, documentId, chartId)
  }
})

test('the drill panel is docked to the bottom below the threshold', async ({ page }, testInfo) => {
  test.skip(
    testInfo.project.name === 'chromium',
    'at 1280px the workspace column is 960px (sidebar expanded) or 1136px ' +
      '(collapsed) — never below the 768px threshold, so the sheet ' +
      'presentation is unreachable here; see the side-panel test above',
  )
  await signIn(page)
  const marker = `layout-sheet-${testInfo.project.name}-${Date.now()}`
  const { documentId, chartId } = await seedUnclassifiedChart(page, marker)

  try {
    await page.goto(`/charts/${chartId}`)
    await page.getByTestId('spending-footer-bucket-unclassified').click()

    const panel = page.getByTestId('drill-panel')
    await expect(panel).toBeVisible()
    await expect(panel).toHaveAttribute('data-presentation', 'sheet')

    await expectDockedToBottom(page, PANEL, 'drill panel (sheet)', 2)

    // Docked AND a sheet, not a full-height side panel wearing the wrong
    // attribute: full viewport width, and short — the `max-height: 70vh` from
    // `.app-drill-panel[data-presentation='sheet']`, measured well under it
    // here (one row of footer-bucket content).
    const rect = await rectOf(page, PANEL)
    const viewport = await viewportOf(page)
    expect(rect.left, 'the sheet must reach the left edge').toBeLessThanOrEqual(2)
    expect(viewport.width - rect.right, 'the sheet must span the full width').toBeLessThanOrEqual(2)
    expect(
      rect.height,
      'the sheet must be shorter than the viewport — a full-height rect would be the side-panel presentation',
    ).toBeLessThan(viewport.height * 0.85)

    await expectNoHorizontalOverflow(page, 'workspace with the bottom sheet open')
  } finally {
    await cleanup(page, documentId, chartId)
  }
})
