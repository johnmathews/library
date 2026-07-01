/**
 * Charts acceptance e2e: sign in → open /charts → verify the shared controls
 * (time range + grouping) render → create an authored series → click its tile
 * to open the full-screen single-chart view → verify the export/share actions
 * are present → return to the grid → delete the series via its inline confirm.
 *
 * Covers the four headline charts changes: click-to-open tiles, the delete
 * affordance, the full-width detail view, and export/share. Uses a
 * freshly-created authored series so it does not depend on seeded emergent
 * series (the single upload fixture is too sparse to form one). Runs in all
 * three matrix projects; skips itself entirely when E2E_BASE_URL is unset.
 */
import { expect, test, type Page } from '@playwright/test'

const BASE_URL = process.env.E2E_BASE_URL
const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

test.skip(
  !BASE_URL,
  'E2E_BASE_URL is not set — start the compose stack and vite preview first (docs/frontend.md §1.5)',
)

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

/**
 * Open /charts through the sidebar. Below the lg breakpoint the sidebar is
 * translated offscreen behind the header hamburger; reveal it first when the
 * hamburger is present (an offscreen link still reports "visible").
 */
async function openChartsPage(page: Page): Promise<void> {
  const hamburger = page.locator('button[aria-controls="sidebar"]')
  if (await hamburger.isVisible()) {
    await hamburger.click()
  }
  await page.getByTestId('sidebar-charts-link').click()
  await expect(page.getByRole('heading', { name: 'Charts', exact: true })).toBeVisible()
}

test('create, open full-screen, and delete an authored chart', async ({ page }) => {
  await signIn(page)
  await openChartsPage(page)

  // The shared control bar is present with the new range + grouping selects.
  await expect(page.getByTestId('chart-controls')).toBeVisible()
  await expect(page.getByTestId('charts-timeframe')).toBeVisible()
  await expect(page.getByTestId('charts-grouping')).toBeVisible()

  // Create an authored series (unique per project run so parallel matrix
  // projects sharing the stack don't collide).
  const name = `E2E chart ${Date.now()}`
  await page.getByTestId('charts-create-button').click()
  await page.getByTestId('charts-create-name').fill(name)
  await page.getByTestId('charts-create-submit').click()

  // Its tile appears with a clickable heading link to the detail page.
  const headingLink = page.getByTestId('series-heading-link').filter({ hasText: name })
  await expect(headingLink).toBeVisible()

  // Clicking the chart area opens the full-screen single-chart view.
  const tile = page.getByTestId('series-trend').filter({ hasText: name })
  await tile.getByTestId('series-chart-area').click()
  await expect(page).toHaveURL(/\/charts\/a-\d+/)
  await expect(page.getByTestId('chart-controls')).toBeVisible()
  // Export & share actions are available on the detail view.
  await expect(page.getByTestId('chart-export-pdf')).toBeVisible()
  await expect(page.getByTestId('chart-share')).toBeVisible()

  // Delete it from the detail view and land back on the grid.
  await page.getByTestId('series-delete').click()
  await page.getByTestId('series-delete-confirm-button').click()
  await expect(page).toHaveURL(/\/charts$/)
  await expect(page.getByTestId('series-heading-link').filter({ hasText: name })).toHaveCount(0)
})
