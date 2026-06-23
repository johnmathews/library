/**
 * Jobs view + live notifications acceptance e2e: sign in → upload a unique
 * document via the API while the dashboard's SSE stream is connected → see the
 * navbar running-jobs indicator light up (NOTIFY → SSE → store → navbar) and a
 * success toast when it finishes → open /jobs and find the job row linking to
 * the document.
 *
 * Requires the real stack (docker compose db/migrate/api/worker + the built
 * frontend behind `vite preview`'s /api proxy) and an `e2e` user; skips itself
 * entirely when E2E_BASE_URL is unset. Only the OCR/text-layer pipeline is
 * needed (status reaches `indexed`); Claude extraction is not required.
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

test('upload surfaces the navbar indicator, a toast, and a /jobs row', async ({
  page,
}, testInfo) => {
  await signIn(page)

  // Upload via the API from the authenticated browser context so the page's
  // live SSE stream observes the new document being processed.
  const marker = `jobs-${testInfo.project.name}-${Date.now()}`
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const response = await page.request.post('/api/documents', {
    headers: { 'X-CSRF-Token': csrf!.value },
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Testdocument ${marker} over rekeningen en facturen.`),
      },
    },
  })
  expect(response.status()).toBe(201)
  const { id } = (await response.json()) as { id: number }

  // The navbar indicator appears while the document is in flight (SSE-driven).
  await expect(page.getByTestId('header-jobs-button')).toBeVisible({ timeout: 30_000 })

  // When processing finishes, the success toast is raised from the SSE
  // 'indexed' event.
  await expect(page.getByText('Document processed')).toBeVisible({ timeout: 150_000 })

  // The Jobs page lists the run and links it to the document.
  await page.goto('/jobs')
  await expect(page.getByRole('heading', { name: 'Jobs', exact: true })).toBeVisible()
  const row = page.locator(`a[href="/documents/${id}"]`).first()
  await expect(row).toBeVisible({ timeout: 30_000 })
})
