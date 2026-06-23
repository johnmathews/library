/**
 * Cross-browser proof for the self-rendered (pdf.js) document preview.
 * Runs in every matrix project — crucially desktop Chromium, Firefox, and
 * desktop WebKit (Safari) — and asserts the preview paints canvases and
 * scrolls through pages. This is the regression guard for the bug that the
 * original native <iframe> PDF viewer got wrong differently in each engine;
 * the self-drawn canvas approach in DocumentPdfPreview.vue is identical
 * across all three, and this spec verifies it.
 *
 * Skips when E2E_BASE_URL is unset (see docs/frontend.md §1.5).
 *
 * Fixture: frontend/e2e/fixtures/pdf-preview-2page.pdf — 2 pages created by
 * pdfunite duplicating library-fixture.pdf. The 2-page fixture is required to
 * prove the lazy multi-page scroll-through behavior (canvas[data-page="2"]).
 */
import { expect, test, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE_URL = process.env.E2E_BASE_URL
const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'
const FIXTURE = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  'fixtures',
  'pdf-preview-2page.pdf',
)

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
 * Upload the 2-page fixture via the API (session + CSRF cookie, same as
 * library.spec.ts), wait for indexing, navigate to the detail page, and
 * return. The test's `page` already has a signed-in session from `signIn`.
 *
 * The backend deduplicates by CONTENT hash, and the five matrix projects run
 * serially against one stack — so a unique filename is not enough (only the
 * first project would get 201, the rest 409). Append the per-project marker as
 * a trailing PDF comment so every project uploads unique bytes while the file
 * stays a valid 2-page PDF (readers ignore bytes after %%EOF), the same
 * unique-bytes trick library.spec.ts uses.
 */
async function uploadAndOpenDetailPage(page: Page, marker: string): Promise<number> {
  // Fetch the CSRF cookie that Django sets on page load.
  await page.goto('/')
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const fixture = await import('node:fs/promises').then((fs) => fs.readFile(FIXTURE))
  const uniqueBytes = Buffer.concat([fixture, Buffer.from(`\n% ${marker}\n`)])
  const response = await page.request.post('/api/documents', {
    headers: { 'X-CSRF-Token': csrf!.value },
    multipart: {
      file: {
        name: `pdf-preview-${marker}.pdf`,
        mimeType: 'application/pdf',
        buffer: uniqueBytes,
      },
    },
  })
  expect(response.status()).toBe(201)
  const { id } = (await response.json()) as { id: number }

  // Wait for the pipeline to index the document (poll, up to 150 s).
  await expect(async () => {
    const detail = await page.request.get(`/api/documents/${id}`)
    expect(detail.status()).toBe(200)
    const doc = (await detail.json()) as { status: string }
    expect(doc.status).toBe('indexed')
  }).toPass({ timeout: 150_000, intervals: [2_000] })

  // Navigate to the detail page.
  await page.goto(`/documents/${id}`)
  await expect(page.getByText('Status', { exact: true })).toBeVisible()

  return id
}

test('pdf preview renders canvas pages and scrolls through them', async ({ page }, testInfo) => {
  await signIn(page)

  const marker = `pdf-preview-${testInfo.project.name}-${Date.now()}`
  const id = await uploadAndOpenDetailPage(page, marker)

  // The PDF preview mount must be present on the detail page.
  const preview = page.getByTestId('preview-pdf')
  await expect(preview).toBeVisible()

  // Page 1 must paint to a canvas element (not a native viewer, not a
  // black-box embed). A non-zero .width proves the canvas was drawn into.
  const firstCanvas = preview.locator('canvas[data-page="1"]')
  await expect(firstCanvas).toBeVisible()
  await expect
    .poll(async () => firstCanvas.evaluate((c: HTMLCanvasElement) => c.width), {
      message: 'canvas[data-page="1"] width should be > 0 (canvas must be painted)',
      timeout: 15_000,
    })
    .toBeGreaterThan(0)

  // The pdf-preview-pages scroll container must be visible.
  const pagesContainer = page.getByTestId('pdf-preview-pages')
  await expect(pagesContainer).toBeVisible()

  // Scroll to page 2; the component should lazy-render it into a canvas.
  // A non-zero width proves the second page was also drawn (cross-browser
  // scroll-through works correctly in pdf.js canvas mode).
  const secondCanvas = preview.locator('canvas[data-page="2"]')
  await secondCanvas.scrollIntoViewIfNeeded()
  await expect
    .poll(async () => secondCanvas.evaluate((c: HTMLCanvasElement) => c.width), {
      message: 'canvas[data-page="2"] width should be > 0 (scroll-through must paint page 2)',
      timeout: 15_000,
    })
    .toBeGreaterThan(0)

  // Cleanup: delete the throwaway document so it does not pollute the library.
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  await page.request.delete(`/api/documents/${id}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
  })
})
