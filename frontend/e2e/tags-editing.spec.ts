/**
 * Journey: tags are EDITABLE in edit mode via a chip multiselect. A user can add
 * a tag (typing a new slug creates it), remove a tag, and — in read mode — sees
 * tags as badges linking to the tag-filtered dashboard.
 *
 * Mirrors topics-readonly.spec.ts / library.spec.ts: env-driven self-skip,
 * shared sign-in helper, and the API-seeding trick (POST /api/documents with the
 * CSRF cookie). The tag slug is made unique per browser project so the shared
 * e2e backend (specs run serially across projects) can't collide.
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

test('add, then remove a tag on a document in edit mode', async ({ page }, testInfo) => {
  await signIn(page)

  // Seed a unique throwaway document via the API.
  const marker = `tags-${testInfo.project.name}-${Date.now()}`
  const tag = marker.toLowerCase()
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const response = await page.request.post('/api/documents', {
    headers: { 'X-CSRF-Token': csrf!.value },
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Testdocument ${marker} for the tags journey.`),
      },
    },
  })
  expect(response.status()).toBe(201)
  const { id } = (await response.json()) as { id: number }

  await page.goto(`/documents/${id}`)
  await expect(page.getByText('Status', { exact: true })).toBeVisible()

  // Enter edit mode: the tags editor is a chip multiselect (not a text input).
  await page.getByTestId('edit-toggle').click()
  await expect(page.locator('#edit-tags')).toBeVisible()

  // Add a brand-new tag: type its slug and commit with Enter. Autosave PATCHes;
  // wait for the per-field "Saved" flash so the reload can't race the PATCH.
  await page.locator('#edit-tags').fill(tag)
  await page.locator('#edit-tags').press('Enter')
  await expect(page.getByTestId('edit-tags-chip').filter({ hasText: tag })).toBeVisible()
  await expect(page.getByTestId('saved-tags')).toBeVisible()

  // Persisted: reload and confirm the tag survives as a read-mode badge that
  // links to the tag-filtered dashboard.
  await page.reload()
  const badge = page.getByTestId('tag-badge').filter({ hasText: tag })
  await expect(badge).toBeVisible()
  await expect(badge).toHaveAttribute('href', new RegExp(`tag=${tag}`))

  // Remove the tag: back in edit mode, click the chip's remove control. Autosave
  // PATCHes the reduced list; after reload the badge is gone.
  await page.getByTestId('edit-toggle').click()
  await page.getByTestId('edit-tags-remove').first().click()
  await expect(page.getByTestId('edit-tags-chip')).toHaveCount(0)
  await expect(page.getByTestId('saved-tags')).toBeVisible()
  await page.reload()
  await expect(page.getByTestId('tag-badge')).toHaveCount(0)

  // Clean up the throwaway document.
  const del = await page.request.delete(`/api/documents/${id}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
  })
  expect([200, 204]).toContain(del.status())
})
