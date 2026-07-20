/**
 * Extraction-quality review e2e — Task 13.
 *
 * Covers the two flows that are achievable on the real e2e stack WITHOUT
 * Claude extraction (the e2e compose stack runs no Claude extraction, so
 * freshly-uploaded documents always land in `review_status = "unreviewed"`
 * with no validation findings).
 *
 * 1. Mark-verified flow: the "Mark verified" button appears ONLY when a
 *    document is `needs_review` (it has something to verify) — never on an
 *    `unreviewed` doc, where there is nothing flagged. So we assert the button
 *    is hidden on a fresh (unreviewed) doc, then seed a flagged one (currency
 *    without an amount trips `amount_currency_coupling` on save — no Claude
 *    extraction needed), open it → the button is visible → click it → the
 *    success banner appears and the button is gone.
 *
 * 2. Needs-review filter navigation: navigate to the dashboard with the
 *    `?review=needs_review` query (or via the "Needs review" preset pill) →
 *    the page loads without error, the URL retains the filter param, and
 *    the list renders its empty/loaded state cleanly.
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

/**
 * Upload a minimal text document via the API and return its id.  The API
 * call reuses the session cookie set by signIn().  Using the API (not the
 * upload UI) keeps this spec fast and independent of the file-upload form.
 */
async function seedDocument(page: Page, marker: string): Promise<number> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  expect(csrf, 'library_csrftoken cookie must exist after sign-in').toBeDefined()
  const response = await page.request.post('/api/documents', {
    headers: { 'X-CSRF-Token': csrf!.value },
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Extraction quality review fixture — ${marker}.`),
      },
    },
  })
  expect(response.status()).toBe(201)
  const { id } = (await response.json()) as { id: number }
  return id
}

/**
 * Seed a document and flag it `needs_review` by PATCHing a currency with no
 * amount, which trips `amount_currency_coupling` on save (revalidation-on-save;
 * no Claude extraction required). Mirrors review-queue.spec's helper.
 */
async function seedFlaggedDocument(page: Page, marker: string): Promise<number> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  expect(csrf, 'library_csrftoken cookie must exist after sign-in').toBeDefined()
  const id = await seedDocument(page, marker)
  const patch = await page.request.patch(`/api/documents/${id}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
    data: { currency: 'EUR' },
  })
  expect(patch.ok(), await patch.text()).toBeTruthy()
  expect((await patch.json()).review_status).toBe('needs_review')
  return id
}

// ---------------------------------------------------------------------------
// Test 1: Mark-verified flow
// ---------------------------------------------------------------------------

test('mark-verified: hidden on unreviewed, visible on needs_review, click marks verified', async ({
  page,
}, testInfo) => {
  await signIn(page)
  const suffix = `${testInfo.project.name}-${Date.now()}`

  // A fresh document is `unreviewed` — nothing is flagged, so the "Mark
  // verified" button must NOT be shown (regression: it used to show for any
  // non-verified doc, which confused users on clean documents).
  const cleanId = await seedDocument(page, `t13-clean-${suffix}`)
  await page.goto(`/documents/${cleanId}`)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByTestId('mark-verified')).toHaveCount(0)

  // A flagged (`needs_review`) document DOES have something to verify.
  const flaggedId = await seedFlaggedDocument(page, `t13-flagged-${suffix}`)
  await page.goto(`/documents/${flaggedId}`)

  const markVerifiedBtn = page.getByTestId('mark-verified')
  await expect(markVerifiedBtn).toBeVisible()
  await expect(markVerifiedBtn).toHaveText('Mark verified')

  // Click it and wait for the success banner.
  await markVerifiedBtn.click()
  await expect(page.getByTestId('detail-banner')).toContainText('Document marked as verified.')

  // The button must now be gone (review_status has changed to 'verified').
  await expect(markVerifiedBtn).toBeHidden()
})

// ---------------------------------------------------------------------------
// Test 2: Needs-review filter navigation
// ---------------------------------------------------------------------------

test('needs-review filter: URL retains param and list renders cleanly', async ({ page }) => {
  await signIn(page)

  // Navigate directly via query string — the app should apply the filter.
  await page.goto('/?review=needs_review')

  // The page must not crash; the Documents heading must be visible.
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()

  // The URL must still carry the filter param after navigation settles.
  await expect(page).toHaveURL(/review=needs_review/)

  // The "Needs review" preset pill must be visible.  The pill is a plain
  // <button> (no aria-pressed) that changes its CSS class when active; we
  // verify only that it is rendered and the URL is carrying the param (above).
  const pill = page.getByTestId('needs-review-filter')
  await expect(pill).toBeVisible()

  // The list must reach a stable, non-error state: either the empty-results
  // placeholder (the e2e stack produces no needs_review docs) or doc cards if
  // some happened to be flagged.  Either is a clean, non-crashed render.
  const emptyResults = page.getByTestId('empty-results')
  const firstCard = page.locator('[data-testid="doc-card"]').first()
  await expect(emptyResults.or(firstCard)).toBeVisible()
})
