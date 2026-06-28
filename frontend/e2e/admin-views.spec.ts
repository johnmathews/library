/**
 * Admin-gating + admin-views acceptance e2e: proves the admin role actually
 * gates the UI.
 *
 *  - A normal user (`e2e`) sees no Admin sidebar link, and navigating straight
 *    to /admin bounces them back to the documents dashboard.
 *  - An admin user (`e2e-admin`) sees the link, reaches /admin, and the four
 *    tabs (System / Architecture / Coverage / Users) render real data.
 *
 * Requires the real stack (docker compose db/migrate/api/worker + the built
 * frontend behind `vite preview`'s /api proxy), a normal `e2e` user, and an
 * admin `e2e-admin` user (see .github/workflows/ci.yml). Skips itself entirely
 * when E2E_BASE_URL is unset.
 */
import { expect, test, type Page } from '@playwright/test'

const BASE_URL = process.env.E2E_BASE_URL
const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'
const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME ?? 'e2e-admin'
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? 'e2e-admin-password-123'

test.skip(
  !BASE_URL,
  'E2E_BASE_URL is not set — start the compose stack and vite preview first (docs/frontend.md §1.5)',
)

async function signIn(page: Page, username: string, password: string): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(username)
  await page.locator('#password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

test('a normal user has no admin link and is redirected away from /admin', async ({ page }) => {
  await signIn(page, USERNAME, PASSWORD)

  // No admin entry point in the sidebar.
  await expect(page.getByTestId('sidebar-admin-link')).toHaveCount(0)

  // Deep-linking to /admin bounces back to the documents dashboard.
  await page.goto('/admin')
  await expect(page).toHaveURL(/\/(\?|$)/)
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
})

test('an admin reaches /admin and the four tabs render', async ({ page }) => {
  await signIn(page, ADMIN_USERNAME, ADMIN_PASSWORD)

  // The admin link is present in the sidebar (the gating signal). We assert its
  // presence rather than clicking it: on mobile/tablet the sidebar is a closed
  // overlay, so nav links aren't clickable without opening the hamburger —
  // existing specs navigate via goto for the same reason.
  await expect(page.getByTestId('sidebar-admin-link')).toHaveCount(1)

  // Reach the page directly (the guard allows admins through).
  await page.goto('/admin')
  await expect(page).toHaveURL(/\/admin$/)

  // System tab is the default panel and is visible on arrival.
  await expect(page.getByTestId('admin-tab-system')).toBeVisible()

  // Each of the other tabs is reachable and reveals its own panel.
  await page.getByTestId('admin-tab-architecture-btn').click()
  await expect(page.getByTestId('admin-tab-architecture')).toBeVisible()

  await page.getByTestId('admin-tab-coverage-btn').click()
  await expect(page.getByTestId('admin-tab-coverage')).toBeVisible()

  await page.getByTestId('admin-tab-users-btn').click()
  const usersPanel = page.getByTestId('admin-tab-users')
  await expect(usersPanel).toBeVisible()
  // The admin themself appears in the user list. Scope the lookup to the Users
  // panel: the raw username also renders in the header user-menu dropdown
  // (`AppHeader`), which is hidden until clicked, so a page-wide
  // getByText(...).first() would match that hidden copy and fail.
  await expect(usersPanel.getByText(ADMIN_USERNAME).first()).toBeVisible()
})
