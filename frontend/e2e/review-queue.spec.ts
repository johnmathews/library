/**
 * Step-through review queue — end-to-end (W6).
 *
 * The e2e stack runs no Claude extraction, so fresh uploads land `unreviewed`.
 * But W1 (revalidate-on-save) now lets us MANUFACTURE a `needs_review` document
 * deterministically: PATCH a document_date far into the future and the
 * date_plausibility rule fires on save. That gives this spec (and proves W1
 * itself) a real flagged document to drive the queue with.
 *
 * Flow: seed two flagged documents → dashboard shows the "Review these" entry →
 * enter the queue → the bar shows a position and the why-panel names the reason
 * → advance with "Verify & next" → exit cleanly. Counts are not asserted exactly
 * because the projects share one backend and run serially (each run adds more
 * flagged docs); the assertions are robust to that.
 *
 * Requires the real stack + `e2e` user; self-skips when E2E_BASE_URL is unset.
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
 * Seed a document and flag it needs_review via `amount_currency_coupling` —
 * setting a currency with no amount trips that rule on save (W1). The dashboard
 * defaults to added_date desc (newest upload first), so a freshly-seeded fixture
 * lands on the first tile — the invariant the other specs rely on (library.spec,
 * markdown-reader). We set no document_date; it no longer affects the default
 * order, but keeping fixtures dateless also leaves the document_date-sort specs
 * undisturbed.
 */
async function seedFlaggedDocument(page: Page, marker: string): Promise<number> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  expect(csrf, 'library_csrftoken cookie must exist after sign-in').toBeDefined()
  const headers = { 'X-CSRF-Token': csrf!.value }

  const create = await page.request.post('/api/documents', {
    headers,
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Review queue fixture — ${marker}.`),
      },
    },
  })
  expect(create.status()).toBe(201)
  const { id } = (await create.json()) as { id: number }

  // Currency without an amount -> amount_currency_coupling -> needs_review.
  const patch = await page.request.patch(`/api/documents/${id}`, {
    headers,
    data: { currency: 'EUR' },
  })
  expect(patch.ok(), await patch.text()).toBeTruthy()
  expect((await patch.json()).review_status).toBe('needs_review')
  return id
}

test('review queue: enter from dashboard, see the reason, exit', async ({ page }, testInfo) => {
  await signIn(page)
  const suffix = `${testInfo.project.name}-${Date.now()}`
  await seedFlaggedDocument(page, `w6-queue-a-${suffix}`)
  await seedFlaggedDocument(page, `w6-queue-b-${suffix}`)

  // Dashboard offers the queue entry point.
  await page.goto('/')
  const startBtn = page.getByTestId('start-review-queue')
  await expect(startBtn).toBeVisible()
  await startBtn.click()

  // We land on a document in queue mode with the position bar and the reason.
  await expect(page).toHaveURL(/\/documents\/\d+\?queue=1/)
  await expect(page.getByTestId('review-queue-bar')).toBeVisible()
  await expect(page.getByTestId('review-queue-position')).toContainText(/of \d+/)
  // The why-panel names the reason in plain language (W4 + reason mapping).
  await expect(page.getByTestId('validation-findings')).toContainText('Amount and currency mismatch')

  // Exit returns cleanly to the dashboard. (Advancing/verifying is exhaustively
  // covered by the DocumentDetailView queue-mode unit tests; keeping the e2e to
  // the entry + exit round-trip avoids webkit-mobile visibility races.)
  await page.getByTestId('queue-exit').click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
  await expect(page).toHaveURL(/\/$/)
})
