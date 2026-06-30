/**
 * Journey (d): topics are READ-ONLY. There is no topics editor anymore — the
 * core contract this asserts. Topics are auto-extracted (which needs an LLM API
 * key that may be absent in CI), so this test does not depend on any topics
 * being present: it seeds a plain document and asserts there is no topics edit
 * control in edit mode, and — when topics happen to be present — that they
 * render as read-only badges with no input.
 *
 * Mirrors library.spec.ts: env-driven self-skip, shared sign-in helper, and the
 * API-seeding trick (POST /api/documents with the CSRF cookie).
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

test('topics render read-only — no topics editor in edit mode', async ({ page }, testInfo) => {
  await signIn(page)

  // Seed a unique throwaway document via the API.
  const marker = `topics-${testInfo.project.name}-${Date.now()}`
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const response = await page.request.post('/api/documents', {
    headers: { 'X-CSRF-Token': csrf!.value },
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Testdocument ${marker} for the topics journey.`),
      },
    },
  })
  expect(response.status()).toBe(201)
  const { id } = (await response.json()) as { id: number }

  await page.goto(`/documents/${id}`)
  await expect(page.getByText('Status', { exact: true })).toBeVisible()

  // Read mode: topics, when present, are read-only badges — never inputs. The
  // section is absent for a topic-less document, so guard on its presence.
  const topicsRow = page.getByTestId('row-topics')
  if (await topicsRow.count()) {
    await expect(topicsRow.getByTestId('topic-badge').first()).toBeVisible()
    await expect(topicsRow.locator('input, textarea, select')).toHaveCount(0)
  }

  // Edit mode: the read-only contract. There is no topics editor at all — no
  // `#edit-topics` control. The topics section renders read-only in both modes
  // when topics are present; this seeded document has none, so its row stays
  // absent here regardless of mode.
  await page.getByTestId('edit-toggle').click()
  await expect(page.locator('#edit-topics')).toHaveCount(0)
  await expect(page.getByTestId('row-topics')).toHaveCount(0)

  // Clean up the throwaway document.
  const del = await page.request.delete(`/api/documents/${id}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
  })
  expect([200, 204]).toContain(del.status())
})
