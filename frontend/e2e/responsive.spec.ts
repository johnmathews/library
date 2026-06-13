/**
 * W16 viewport regression spec, run by every project in the matrix
 * (chromium desktop, mobile-webkit 375px, tablet-webkit iPad portrait):
 *
 *   - no horizontal overflow on /login, / (documents) and /upload;
 *   - the Mosaic sidebar is reachable: translated offscreen behind the
 *     header hamburger below the Tailwind `lg` breakpoint (1024px), in
 *     flow above it;
 *   - the mobile project re-checks overflow at the 320px floor;
 *   - the chromium project re-checks at 1920×1080 that the wide-desktop
 *     content container (the max-w-[96rem] wrapper in DefaultLayout) is
 *     wider than 1100px without overflow, and that the dashboard grid
 *     reaches its 4-column wide layout;
 *   - the dashboard document grid (app-doc-grid, docs/frontend.md §1.2.6)
 *     renders the expected column count per viewport: 1 at 375px,
 *     2 on iPad portrait (656px, tablet band) and 3 on the 1280px desktop.
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

test('wide desktops get the widened content container', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', 'wide-desktop layout is checked once, on the desktop project')
  await page.setViewportSize({ width: 1920, height: 1080 })

  await signIn(page) // lands on / (documents)
  // DefaultLayout wraps the routed view in a max-w-[96rem] container inside
  // <main class="grow">; measure that wrapper (main's first child element).
  const containerWidth = await page.evaluate(
    () =>
      document.querySelector('main')?.firstElementChild?.getBoundingClientRect()
        .width ?? 0,
  )
  expect(
    containerWidth,
    `at 1920px the main content container must exceed 1100px, got ${containerWidth}`,
  ).toBeGreaterThan(1100)
  await expectNoHorizontalOverflow(page, '/ @1920')

  // Above the 1400px app breakpoint the dashboard grid goes 4-up
  // (library.spec.ts ran first in this project, so documents exist).
  expect(await gridColumnCount(page)).toBe(4)
})

/** Computed column count of the dashboard grid (0 when no grid). */
async function gridColumnCount(page: Page): Promise<number> {
  const grid = page.locator('.app-doc-grid')
  if ((await grid.count()) === 0) return 0
  return grid.evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.split(' ').length,
  )
}

test('the dashboard grid has the expected column count per viewport', async ({
  page,
}, testInfo) => {
  await signIn(page) // lands on / (documents)

  // library.spec.ts runs first within each project, so the library holds
  // at least the uploaded PDF fixture and the grid is present.
  const expected = {
    chromium: 3, // 1280px default viewport: desktop, below the 1400px wide breakpoint
    'mobile-webkit': 1, // 375px: single column
    'tablet-webkit': 2, // iPad (gen 11) portrait is 656px: tablet band (641–768px)
  }[testInfo.project.name]
  expect(expected, `unknown project ${testInfo.project.name}`).toBeDefined()

  await expect(page.locator('.app-doc-card').first()).toBeVisible()
  expect(await gridColumnCount(page)).toBe(expected)
  await expectNoHorizontalOverflow(page, `/ grid (${testInfo.project.name})`)
})

/** Left edge x of #sidebar; negative when it is translated offscreen. */
async function sidebarLeftEdge(page: Page): Promise<number> {
  return page.locator('#sidebar').evaluate((el) => el.getBoundingClientRect().left)
}

test('the sidebar is reachable on every viewport', async ({ page }, testInfo) => {
  await signIn(page)

  // The header hamburger toggles the sidebar; it is rendered lg:hidden, so it
  // is present below the Tailwind lg breakpoint (1024px) and hidden above it.
  const hamburger = page.locator('button[aria-controls="sidebar"]')
  const uploadLink = page.getByTestId('sidebar-upload-link')

  if (testInfo.project.name === 'chromium') {
    // Desktop (1280px ≥ lg): sidebar sits in flow, no hamburger.
    await expect(hamburger).toBeHidden()
    expect(await sidebarLeftEdge(page), 'sidebar should be on-screen at lg+').toBeGreaterThanOrEqual(0)
  } else {
    // mobile-webkit (375px) and tablet-webkit (656px) are both below lg, so the
    // sidebar starts translated offscreen behind the hamburger.
    await expect(hamburger).toBeVisible()
    expect(await sidebarLeftEdge(page), 'sidebar should start offscreen below lg').toBeLessThan(0)
    // Toggling the hamburger slides the sidebar into view (translate-x-0).
    await hamburger.click()
    await expect(async () => {
      expect(await sidebarLeftEdge(page)).toBeGreaterThanOrEqual(0)
    }).toPass()
  }

  await uploadLink.click()
  await expect(page.getByRole('heading', { name: 'Upload documents' })).toBeVisible()
})
