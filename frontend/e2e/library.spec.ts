/**
 * W10 acceptance e2e: sign in → upload a PDF fixture → wait for the
 * pipeline to index it → find it in the list → full-text search by a
 * Dutch stem. Runs in two projects (desktop Chromium, 375px WebKit).
 *
 * Requires the real stack (docker compose db/migrate/api/worker + the
 * built frontend behind `vite preview`'s /api proxy) and an `e2e` user;
 * skips itself entirely when E2E_BASE_URL is unset. Claude extraction is
 * not required: the assertions only rely on the OCR/text-layer pipeline
 * (status `indexed`, text search), never on extracted metadata.
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
  'library-fixture.pdf',
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

/** Open the Upload page through the service navigation (mobile: via Menu). */
async function openUploadPage(page: Page): Promise<void> {
  const menuToggle = page.getByRole('button', { name: 'Menu' })
  if (await menuToggle.isVisible()) {
    await menuToggle.click()
  }
  await page.getByRole('link', { name: 'Upload', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Upload documents' })).toBeVisible()
}

test('sign in, upload a PDF, see it indexed, listed and searchable', async ({ page }) => {
  await signIn(page)
  await openUploadPage(page)

  // The govuk-frontend FileUpload enhancement moves the id onto its visible
  // drop-zone button and hides the real input — target the input directly.
  await page.locator('input[type="file"]').setInputFiles(FIXTURE)
  await page.getByRole('button', { name: 'Upload', exact: true }).click()

  // First project uploads fresh content (progress → processing → indexed);
  // the second hits the duplicate path for the same bytes — both are
  // correct outcomes and both leave the document in the library.
  const indexed = page.locator('[data-testid="upload-list"]').getByText('Indexed', { exact: true })
  const duplicate = page.getByText('already in your library')
  await expect(indexed.or(duplicate).first()).toBeVisible({ timeout: 150_000 })

  // The document appears in the list (poll by reloading; thumbnails and
  // search vector are written by background jobs).
  await page.goto('/')
  await expect(async () => {
    await page.reload()
    await expect(page.locator('.app-doc-list__item').first()).toBeVisible({ timeout: 2_000 })
  }).toPass({ timeout: 60_000 })

  // Dutch stemming: the fixture contains "rekeningen", search the stem.
  await page.locator('#search').fill('rekening')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page).toHaveURL(/q=rekening/)
  await expect(page.locator('.app-doc-list__item').first()).toBeVisible()
  // ts_headline snippet with the match highlighted as a real <b> element.
  await expect(page.locator('.app-doc-list__snippet b').first()).toContainText(/rekening/i)

  // A nonsense query shows the no-results state, not the empty library.
  await page.locator('#search').fill('kwijxzylqq')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByTestId('empty-results')).toBeVisible()
})

test('document detail stub opens from the list', async ({ page }) => {
  await signIn(page)
  await page.locator('.app-doc-list__title a').first().click()
  await expect(page).toHaveURL(/\/documents\/\d+/)
  await expect(page.getByText('Status', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Back to documents' })).toBeVisible()
})
