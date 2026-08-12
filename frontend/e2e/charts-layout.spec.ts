/**
 * Real-geometry regression spec for the /charts view.
 *
 * `ChartControls` is the repo's reference implementation of the filter-bar
 * pattern (`docs/frontend-view-principles.md` §5): one label recipe, `.form-*`
 * controls, `flex flex-wrap items-end gap-3`. "Bottom-aligned controls" and
 * "wraps cleanly on narrow screens" are geometry claims, so they are checked as
 * geometry rather than by asserting the class list — asserting the class list
 * would only restate the implementation.
 *
 * Companion to `ask-layout.spec.ts` and `detail-layout.spec.ts`.
 */
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'
import { expectNoHorizontalOverflow, rectOf, viewportOf } from './fixtures/layout'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

const CONTROLS = '[data-testid="chart-controls"]'

requireStack()

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

async function openCharts(page: Page): Promise<void> {
  await page.goto('/charts')
  await expect(page.getByTestId('chart-controls')).toBeVisible()
}

test('the chart controls sit on one bottom-aligned row on a wide screen', async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium',
    'single-row alignment is a desktop claim; the wrap case is checked on mobile',
  )
  await signIn(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await openCharts(page)

  const timeframe = await rectOf(page, '[data-testid="charts-timeframe"]')
  const from = await rectOf(page, '[data-testid="charts-range-from"]')
  const to = await rectOf(page, '[data-testid="charts-range-to"]')
  const grouping = await rectOf(page, '[data-testid="charts-grouping"]')
  const controls = [timeframe, from, to, grouping]

  // `items-end`: every control shares a bottom edge, which is what makes a row
  // of differently-labelled fields read as one row. 2px absorbs sub-pixel
  // rounding between an input and a select.
  const bottoms = controls.map((rect) => rect.bottom)
  const spread = Math.max(...bottoms) - Math.min(...bottoms)
  expect(
    spread,
    `controls should share a bottom edge (items-end); spread was ${spread}px: ${JSON.stringify(bottoms)}`,
  ).toBeLessThanOrEqual(2)

  // At 1440px they all fit on one line, so each starts to the right of the last.
  for (let i = 1; i < controls.length; i += 1) {
    expect(
      controls[i].left,
      `control ${i} should follow control ${i - 1} on the same row at 1440px`,
    ).toBeGreaterThan(controls[i - 1].left)
  }
})

test('the chart controls wrap instead of overflowing on a phone', async ({ page }, testInfo) => {
  test.skip(
    testInfo.project.name !== 'mobile-webkit',
    'the wrap case is checked once, on the mobile project',
  )
  await signIn(page)
  await openCharts(page)

  for (const width of [320, 375]) {
    await page.setViewportSize({ width, height: 720 })
    await expect(page.getByTestId('chart-controls')).toBeVisible()
    await expectNoHorizontalOverflow(page, `/charts @${width}`)

    const bar = await rectOf(page, CONTROLS)
    const viewport = await viewportOf(page)
    expect(
      bar.right,
      `@${width}: the controls bar must stay inside the viewport`,
    ).toBeLessThanOrEqual(viewport.width + 1)

    // Wrapping, not shrinking to illegibility: at 320px the row cannot hold all
    // four controls side by side, so it must be taller than a single control.
    const timeframe = await rectOf(page, '[data-testid="charts-timeframe"]')
    expect(
      bar.height,
      `@${width}: the controls bar should have wrapped to more than one line`,
    ).toBeGreaterThan(timeframe.height)
  }
})

test('the charts view does not overflow horizontally at any width', async ({ page }, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium',
    'the desktop width sweep is checked once, on the desktop project',
  )
  await signIn(page)
  await openCharts(page)

  for (const width of [1024, 1280, 1920]) {
    await page.setViewportSize({ width, height: 900 })
    await expect(page.getByTestId('chart-controls')).toBeVisible()
    await expectNoHorizontalOverflow(page, `/charts @${width}`)
  }
})
