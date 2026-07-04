/**
 * Admin-gating + admin-views acceptance e2e: proves the admin role actually
 * gates the UI.
 *
 *  - A normal user (`e2e`) sees no Admin sidebar link, and navigating straight
 *    to /admin bounces them back to the documents dashboard.
 *  - An admin user (`e2e-admin`) sees the link, reaches /admin, and the four
 *    tabs (Users / Metadata / Architecture / Coverage / System) render real data.
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

  // Users tab is the default panel and is visible on arrival.
  const usersPanel = page.getByTestId('admin-tab-users')
  await expect(usersPanel).toBeVisible()

  // Each of the other tabs is reachable and reveals its own panel.
  await page.getByTestId('admin-tab-metadata-btn').click()
  await expect(page.getByTestId('admin-tab-metadata')).toBeVisible()

  await page.getByTestId('admin-tab-architecture-btn').click()
  await expect(page.getByTestId('admin-tab-architecture')).toBeVisible()

  await page.getByTestId('admin-tab-coverage-btn').click()
  await expect(page.getByTestId('admin-tab-coverage')).toBeVisible()

  await page.getByTestId('admin-tab-system-btn').click()
  await expect(page.getByTestId('admin-tab-system')).toBeVisible()

  await page.getByTestId('admin-tab-users-btn').click()
  await expect(usersPanel).toBeVisible()
  // The admin themself appears in the user list. Scope the lookup to the Users
  // panel: the raw username also renders in the header user-menu dropdown
  // (`AppHeader`), which is hidden until clicked, so a page-wide
  // getByText(...).first() would match that hidden copy and fail.
  await expect(usersPanel.getByText(ADMIN_USERNAME).first()).toBeVisible()
})

/**
 * Admin-write round-trip through the real API (W6): create a sender, rename it,
 * and prove the rename lands in the Metadata tab. This is the only e2e that
 * mutates admin taxonomy state end-to-end. Everything is keyed to a per-run
 * UNIQUE marker so the shared backend and parallel browser projects never
 * collide, and no document_date is written (senders carry no date), so the
 * dashboard-sort invariant the other specs rely on is untouched.
 */
test('an admin creates and renames a sender through the API and the UI reflects it', async ({
  page,
}, testInfo) => {
  await signIn(page, ADMIN_USERNAME, ADMIN_PASSWORD)

  // Admin write endpoints are CSRF-protected like the document routes: read the
  // double-submit cookie set at sign-in and echo it in the header (mirrors
  // review-queue.spec.ts's seed pattern).
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  expect(csrf, 'library_csrftoken cookie must exist after sign-in').toBeDefined()
  const headers = { 'X-CSRF-Token': csrf!.value }

  // Unique per project + run so concurrent projects and reruns never collide on
  // the case-insensitive dedupe.
  const marker = `${testInfo.project.name}-${Date.now()}`
  const originalName = `W6 Sender ${marker}`
  const renamedName = `W6 Renamed ${marker}`

  // 1. Seed a brand-new sender — a fresh name is a 201 create (a dedupe hit
  //    would be 200), returning the {id, name} contract.
  const create = await page.request.post('/api/admin/senders', {
    headers,
    data: { name: originalName },
  })
  expect(create.status(), await create.text()).toBe(201)
  const created = (await create.json()) as { id: number; name: string }
  expect(created.name).toBe(originalName)
  const id = created.id

  // 2. Rename it through the real API. A plain rename (no collision) is a 200
  //    echoing the same id with the new name.
  const rename = await page.request.patch(`/api/admin/senders/${id}`, {
    headers,
    data: { name: renamedName },
  })
  expect(rename.status(), await rename.text()).toBe(200)
  expect(await rename.json()).toMatchObject({ id, name: renamedName })

  // 3. The Metadata tab reflects the rename. Senders load lazily when the tab is
  //    first entered, so navigate fresh and open it; the row is keyed by id
  //    (`sender-row-{id}`) and shows the new name.
  await page.goto('/admin')
  await page.getByTestId('admin-tab-metadata-btn').click()
  await expect(page.getByTestId('admin-tab-metadata')).toBeVisible()
  const row = page.getByTestId(`sender-row-${id}`)
  await expect(row).toBeVisible()
  await expect(row).toContainText(renamedName)
  await expect(row).not.toContainText(originalName)
})
