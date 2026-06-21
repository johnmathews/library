/**
 * Task 13 e2e — Ask → page citation → PDF deep-link.
 *
 * Flow:
 *   1. Sign in and seed a real document (text file, for a stable id).
 *   2. Intercept GET /api/documents/{id} so the detail view believes the
 *      document is a PDF with a searchable PDF layer (has_searchable_pdf:
 *      true, mime_type: 'application/pdf'). This makes DocumentDetailView
 *      compute preview === 'pdf' and render the iframe with the correct src.
 *   3. Intercept POST /api/ask to return a deterministic citation pointing
 *      at that document with page_number = 2. This avoids the 503 the e2e
 *      stack returns (no Anthropic API key configured).
 *   4. Also intercept GET /api/documents/{id}/searchable.pdf to stop the
 *      iframe from producing a network error in the test report.
 *   5. Ask a question on /ask; assert the citation shows "p. 2" and carries
 *      data-testid="ask-citation"; click it; assert the URL carries ?page=2
 *      and the PDF iframe src contains "page=2".
 *
 * The iframe (data-testid="preview-pdf") is hidden on small screens via CSS
 * (hidden lg:block) but is still present in the DOM, so the src assertion
 * works across all three viewport projects (chromium, mobile-webkit,
 * tablet-webkit).
 *
 * Same skip contract as the other specs: self-skip when E2E_BASE_URL is
 * not set.
 */
import { expect, test, type Page } from '@playwright/test'

const BASE_URL = process.env.E2E_BASE_URL
const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

/** Deterministic question text so test runs are easy to grep in logs. */
const TEST_QUESTION = 'ask-page-citation-e2e: what is the total?'

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
 * Seed a minimal text document via the API and return its numeric id.
 * Using the API (not the upload UI) keeps this spec fast and independent
 * of the file-upload form.  The marker makes the document uniquely
 * identifiable across parallel runs.
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
        buffer: Buffer.from(`Ask citation fixture — ${marker}.`),
      },
    },
  })
  expect(response.status()).toBe(201)
  const { id } = (await response.json()) as { id: number }
  return id
}

test('ask citation deep-links to the cited PDF page', async ({ page }, testInfo) => {
  await signIn(page)

  // Seed a document to get a stable, real id.
  const marker = `t13-ask-${testInfo.project.name}-${Date.now()}`
  const docId = await seedDocument(page, marker)

  // ── Intercept GET /api/documents/{id} ──────────────────────────────────────
  // The real document is a text file, which produces preview === 'none' (no
  // iframe). Override the response to present it as a searchable PDF so
  // DocumentDetailView renders the iframe and computes the page-aware src.
  await page.route(`**/api/documents/${docId}`, async (route, request) => {
    if (request.method() !== 'GET') {
      await route.continue()
      return
    }
    const real = await route.fetch()
    const body = (await real.json()) as Record<string, unknown>
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ...body,
        mime_type: 'application/pdf',
        has_searchable_pdf: true,
        has_thumbnail: false,
      }),
    })
  })

  // ── Intercept POST /api/ask ─────────────────────────────────────────────────
  // Return a deterministic citation pointing at the seeded document on page 2.
  // Without this intercept the e2e stack returns 503 (no API key configured).
  await page.route('**/api/ask', async (route, request) => {
    if (request.method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        answer: 'The total is **€ 1 234,56** (see citation below).',
        citations: [
          {
            document_id: docId,
            title: `Ask citation fixture — ${marker}`,
            page_number: 2,
          },
        ],
        used_tools: ['search_documents'],
        cost_usd: 0.0,
      }),
    })
  })

  // ── Intercept the searchable PDF file so the iframe gets a clean 200 ───────
  // A real 404 from /api/documents/{id}/searchable.pdf would be logged in the
  // test report as a network error even though we do not assert its content.
  await page.route(`**/api/documents/${docId}/searchable.pdf**`, async (route) => {
    // Minimal 1-page PDF (from the PDF specification §7; barely valid but
    // enough for the browser to render without a fatal parse error).
    await route.fulfill({
      status: 200,
      contentType: 'application/pdf',
      body: Buffer.from(
        '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj ' +
          '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj ' +
          '3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\n' +
          'xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n' +
          '0000000058 00000 n\n0000000115 00000 n\ntrailer<</Size 4/Root 1 0 R>>\n' +
          'startxref\n190\n%%EOF',
      ),
    })
  })

  // ── Ask a question ──────────────────────────────────────────────────────────
  await page.goto('/ask')
  await page.getByTestId('ask-question').locator('textarea').fill(TEST_QUESTION)
  await page.getByTestId('ask-submit').click()

  // ── Assert the citation shows "p. 2" ───────────────────────────────────────
  const citation = page.getByTestId('ask-citation').first()
  await expect(citation).toBeVisible()
  await expect(citation).toContainText('p. 2')

  // ── Click the citation → land on the document detail page with ?page=2 ─────
  await citation.click()
  await expect(page).toHaveURL(new RegExp(`/documents/${docId}\\?page=2`))

  // ── Assert the PDF iframe src carries page=2 ────────────────────────────────
  // The iframe is data-testid="preview-pdf"; its src fragment contains
  // "#toolbar=0&navpanes=0&view=FitH&page=2". The element is CSS-hidden on
  // small screens (hidden lg:block) but is in the DOM on all viewports, so
  // this assertion holds across chromium, mobile-webkit and tablet-webkit.
  const iframe = page.getByTestId('preview-pdf')
  await expect(iframe).toHaveAttribute('src', /page=2/)
})
