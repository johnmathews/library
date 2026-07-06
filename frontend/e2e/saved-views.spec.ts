/**
 * Saved views + custom dashboards journey: from the homepage, save the current
 * filter/sort state as a named, pinned view; confirm it becomes a sidebar
 * dashboard whose link reproduces that state; confirm it appears on the
 * management page. Cleans up the view and the seed document afterwards.
 *
 * Mirrors projects.spec.ts (env self-skip, sign-in helper, API seeding). The
 * sidebar collapses below the lg breakpoint (mobile/tablet projects), so the
 * dashboard link is checked by attachment + href and navigated via goto rather
 * than by clicking a possibly-offscreen element (docs/frontend responsive note).
 */
import { expect, test, type Page } from '@playwright/test'

const BASE_URL = process.env.E2E_BASE_URL
const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

test.skip(
  !BASE_URL,
  'E2E_BASE_URL is not set — start the compose stack and vite preview first (docs/frontend.md §1.5)',
)

interface SavedView {
  id: number
  name: string
  pinned: boolean
  filter_state: Record<string, string | string[]>
}

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

test('save a pinned view, use it as a sidebar dashboard, and manage it', async ({
  page,
}, testInfo) => {
  await signIn(page)
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')

  // Seed a throwaway document so the library is non-empty — the toolbar controls
  // (including the Save-view menu) render only when there are results.
  const marker = `view-${testInfo.project.name}-${Date.now()}`
  const seed = await page.request.post('/api/documents', {
    headers: { 'X-CSRF-Token': csrf!.value },
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Testdocument ${marker} for the saved-views journey.`),
      },
    },
  })
  expect(seed.status()).toBe(201)
  const { id: docId } = (await seed.json()) as { id: number }

  // A deterministic, non-empty filter state: reorder by added_date ascending
  // (a non-default sort/dir, so it is captured verbatim in the saved query).
  await page.goto('/?sort=added_date&dir=asc')
  await expect(page.getByTestId('result-count')).toBeVisible()

  // Save the current state as a pinned view.
  const viewName = `e2e-${marker}`
  await page.getByTestId('save-view-menu').click()
  await page.getByTestId('save-view-name').fill(viewName)
  await page.getByTestId('save-view-pinned').check()
  await page.getByTestId('save-view-submit').click()

  // The view now exists server-side, pinned, with the captured query.
  await expect(async () => {
    const list = (await (await page.request.get('/api/saved-views')).json()) as SavedView[]
    const view = list.find((v) => v.name === viewName)
    expect(view, 'saved view was created').toBeTruthy()
    expect(view!.pinned).toBe(true)
    expect(view!.filter_state.sort).toBe('added_date')
  }).toPass()

  const list = (await (await page.request.get('/api/saved-views')).json()) as SavedView[]
  const view = list.find((v) => v.name === viewName)!

  // It renders as a sidebar custom dashboard whose link reproduces the query.
  // (Assert by attachment + href — the sidebar collapses on mobile/tablet.)
  await page.reload()
  const dashboardLink = page.getByTestId(`sidebar-dashboard-${view.id}`)
  await expect(dashboardLink).toBeAttached()
  const href = await dashboardLink.getAttribute('href')
  expect(href).toContain('sort=added_date')

  // Following that link reproduces the saved filter state on the homepage.
  await page.goto(href!)
  await expect(page).toHaveURL(/[?&]sort=added_date(?:&|$)/)
  await expect(page.getByTestId('result-count')).toBeVisible()

  // It also appears on the management page.
  await page.goto('/saved-views')
  await expect(page.getByText(viewName, { exact: false }).first()).toBeVisible()

  // Clean up the saved view and the throwaway document.
  const delView = await page.request.delete(`/api/saved-views/${view.id}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
  })
  expect([200, 204]).toContain(delView.status())
  const delDoc = await page.request.delete(`/api/documents/${docId}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
  })
  expect([200, 204]).toContain(delDoc.status())
})
