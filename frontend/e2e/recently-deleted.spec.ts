/**
 * Recently Deleted journey: soft-delete a document, confirm it leaves the
 * library and appears under /deleted, restore it from the UI, and confirm it
 * returns.
 *
 * Mirrors projects.spec.ts: env-driven self-skip, shared sign-in helper, and
 * API-seeding (POST /api/documents with the CSRF cookie) for a unique throwaway
 * document per run.
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

test('soft-delete a document, find it under Recently Deleted, and restore it', async ({
  page,
}, testInfo) => {
  await signIn(page)

  const marker = `del-${testInfo.project.name}-${Date.now()}`
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const seed = await page.request.post('/api/documents', {
    headers: { 'X-CSRF-Token': csrf!.value },
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Testdocument ${marker} for the recently-deleted journey.`),
      },
    },
  })
  expect(seed.status()).toBe(201)
  const { id } = (await seed.json()) as { id: number }

  // Soft-delete via the API (the DELETE endpoint the tile/detail UI calls).
  const del = await page.request.delete(`/api/documents/${id}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
  })
  expect([200, 204]).toContain(del.status())

  // The document now surfaces only under Recently Deleted, with a countdown and
  // a Restore action; the sidebar entry links there.
  await page.goto('/deleted')
  await expect(page.getByTestId('deleted-title')).toBeVisible()
  const restore = page.getByTestId(`restore-${id}`)
  await expect(restore).toBeVisible()

  // Restoring removes the card and shows the confirmation banner.
  await restore.click()
  await expect(page.getByTestId('flash-banner')).toBeVisible()
  await expect(page.getByTestId(`restore-${id}`)).toHaveCount(0)

  // The document is live again: its detail page loads (no 404).
  await page.goto(`/documents/${id}`)
  await expect(page.getByText('Status', { exact: true })).toBeVisible()

  // Clean up the throwaway document.
  const cleanup = await page.request.delete(`/api/documents/${id}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
  })
  expect([200, 204]).toContain(cleanup.status())
})
