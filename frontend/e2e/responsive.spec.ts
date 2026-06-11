/**
 * W16 viewport regression spec, run by every project in the matrix
 * (chromium desktop, mobile-webkit 375px, tablet-webkit iPad portrait):
 *
 *   - no horizontal overflow on /login, / (documents) and /upload;
 *   - the service navigation is reachable: behind the Menu toggle below the
 *     GOV.UK tablet breakpoint (641px), inline above it;
 *   - the mobile project re-checks overflow at the 320px floor.
 *
 * Same contract as library.spec.ts: requires the real stack and the `e2e`
 * user; skips itself entirely when E2E_BASE_URL is unset.
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

async function expectNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
  expect(
    scrollWidth,
    `${label}: scrollWidth ${scrollWidth} must not exceed clientWidth ${clientWidth} (+1 rounding)`,
  ).toBeLessThanOrEqual(clientWidth + 1)
}

test('no horizontal overflow on login, documents and upload', async ({ page }) => {
  await page.goto('/login')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expectNoHorizontalOverflow(page, '/login')

  await signIn(page) // lands on / (documents)
  await expectNoHorizontalOverflow(page, '/')

  await page.goto('/upload')
  await expect(page.getByRole('heading', { name: 'Upload documents' })).toBeVisible()
  await expectNoHorizontalOverflow(page, '/upload')
})

test('no horizontal overflow at the 320px floor', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'mobile-webkit', '320px floor is checked once, on the mobile project')
  await page.setViewportSize({ width: 320, height: 568 })

  await page.goto('/login')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expectNoHorizontalOverflow(page, '/login @320')

  await signIn(page)
  await expectNoHorizontalOverflow(page, '/ @320')

  await page.goto('/upload')
  await expect(page.getByRole('heading', { name: 'Upload documents' })).toBeVisible()
  await expectNoHorizontalOverflow(page, '/upload @320')
})

test('service navigation is reachable on every viewport', async ({ page }, testInfo) => {
  await signIn(page)

  const menuToggle = page.getByRole('button', { name: 'Menu' })
  const uploadLink = page.getByRole('link', { name: 'Upload', exact: true })

  if (testInfo.project.name === 'mobile-webkit') {
    // Below the GOV.UK tablet breakpoint the list collapses behind the toggle.
    await expect(menuToggle).toBeVisible()
    await expect(uploadLink).toBeHidden()
    await menuToggle.click()
    await expect(uploadLink).toBeVisible()
  } else {
    // Desktop and iPad portrait (≥641px): inline navigation, no toggle.
    await expect(menuToggle).toBeHidden()
    await expect(uploadLink).toBeVisible()
  }

  await uploadLink.click()
  await expect(page.getByRole('heading', { name: 'Upload documents' })).toBeVisible()
})
