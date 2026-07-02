/**
 * W10 + W11 acceptance e2e: sign in → upload a PDF fixture → wait for the
 * pipeline to index it → find its tile on the dashboard grid → full-text
 * search by a Dutch stem through the header search modal; then the detail
 * page — open from a tile, edit the title via the summary-list Change
 * flow, delete via the confirmation page. Runs in all three matrix
 * projects (desktop Chromium, 375px WebKit, iPad-portrait WebKit) — see
 * playwright.config.ts.
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

/**
 * Open the Upload page through the Mosaic sidebar. Below the lg breakpoint the
 * sidebar is translated offscreen behind the header hamburger
 * (aria-controls="sidebar", rendered lg:hidden); above it the sidebar is in
 * flow. Gate on the hamburger's visibility — an offscreen-translated sidebar
 * link still reports as "visible" to Playwright, so reveal the sidebar via the
 * hamburger whenever it is present, then click the Upload link.
 */
async function openUploadPage(page: Page): Promise<void> {
  const hamburger = page.locator('button[aria-controls="sidebar"]')
  if (await hamburger.isVisible()) {
    await hamburger.click()
  }
  await page.getByTestId('sidebar-upload-link').click()
  await expect(page.getByRole('heading', { name: 'Upload documents' })).toBeVisible()
}

/**
 * Search through the Mosaic header's search button and its modal: open the
 * dialog, fill the query, submit, and wait for the dialog to close. The
 * header search trigger is always visible (it is not collapsed behind a
 * menu at any viewport), so no responsive toggling is needed.
 */
async function searchFor(page: Page, query: string): Promise<void> {
  await page.getByTestId('header-search-button').click()
  const modal = page.getByTestId('search-modal')
  await expect(modal).toBeVisible()
  await modal.locator('#search').fill(query)
  await modal.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(modal).toBeHidden()
}

test('sign in, upload a PDF, see it indexed, listed and searchable', async ({ page }) => {
  await signIn(page)
  await openUploadPage(page)

  // AppFileUpload renders a visible dashed drop-zone label wrapping a
  // visually-hidden (sr-only) real file input — target the input directly.
  await page.locator('input[type="file"]').setInputFiles(FIXTURE)
  await page.getByRole('button', { name: 'Upload', exact: true }).click()

  // First project uploads fresh content (progress → processing → indexed);
  // the later projects hit the duplicate path for the same bytes — both are
  // correct outcomes and both leave the document in the library.
  const indexed = page.locator('[data-testid="upload-list"]').getByText('Indexed', { exact: true })
  const duplicate = page.getByText('already in your library')
  await expect(indexed.or(duplicate).first()).toBeVisible({ timeout: 150_000 })

  // The document appears as a tile on the dashboard grid (poll by
  // reloading; thumbnails and search vector are written by background jobs).
  await page.goto('/')
  await expect(async () => {
    await page.reload()
    await expect(page.locator('.app-doc-card').first()).toBeVisible({ timeout: 2_000 })
  }).toPass({ timeout: 60_000 })

  // Dutch stemming: the fixture contains "rekeningen", search the stem
  // through the header search modal.
  await searchFor(page, 'rekening')
  await expect(page).toHaveURL(/q=rekening/)
  await expect(page.locator('.app-doc-card').first()).toBeVisible()
  // ts_headline snippet with the match highlighted as a real <b> element.
  await expect(page.locator('.app-doc-card__snippet b').first()).toContainText(/rekening/i)

  // A nonsense query (also via the modal) shows the no-results state,
  // not the empty library.
  await searchFor(page, 'kwijxzylqq')
  await expect(page.getByTestId('empty-results')).toBeVisible()
})

test('dashboard reflects metadata preferences', async ({ page }) => {
  await signIn(page)

  try {
    // ── Phase 1: turn off the Correspondent field and save ──────────────────
    await page.goto('/settings')
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()

    // The Dashboard tab uses DashboardFieldsEditor: the sender row's checkbox is
    // `[data-testid="dashboard-field-sender"]`. (A loose getByLabel('Correspondent')
    // would also match the row's Up/Down/drag aria-labels, so target the checkbox.)
    const correspondentCheckbox = page.getByTestId('dashboard-field-sender')
    await correspondentCheckbox.uncheck()
    await page.getByRole('button', { name: 'Save changes' }).click()
    await expect(page.getByText('Your settings have been saved.')).toBeVisible()

    // ── Phase 2: verify the preference persisted (reload the settings page) ─
    // Reloading and asserting the checkbox is still unchecked proves the PUT
    // reached the server and was stored — a more meaningful check than tile
    // absence alone, because .app-doc-card__sender is only rendered when
    // `item.sender` is set (v-if="shows('sender') && item.sender"), so it
    // would be absent even with the field enabled if no documents have senders.
    await page.goto('/settings')
    await expect(page.getByTestId('dashboard-field-sender')).not.toBeChecked()

    // ── Phase 3: the dashboard renders no sender lines while field is off ────
    await page.goto('/')
    // If documents are present (uploaded by the preceding test), no tile should
    // show a sender line. If the library is empty the count is trivially 0, which
    // is still correct. The meaningful assertion is Phase 2 above.
    await expect(page.locator('.app-doc-card__sender')).toHaveCount(0)
  } finally {
    // ── Restore state — re-enable Correspondent ──────────────────────────────
    // Settings are per-user and the e2e user is shared across all spec files.
    // Running this in finally ensures the restore happens even if an earlier
    // assertion throws, preventing state corruption in subsequent specs.
    await page.goto('/settings')
    const correspondent = page.getByTestId('dashboard-field-sender')
    if (!(await correspondent.isChecked())) {
      await correspondent.check()
      await page.getByRole('button', { name: 'Save changes' }).click()
      await expect(page.getByText('Your settings have been saved.')).toBeVisible()
    }
  }
})

test('detail: open from list, edit title, delete via confirmation page', async ({
  page,
}, testInfo) => {
  await signIn(page)

  // Create a throwaway document with UNIQUE content via the API (the
  // browser context's session cookie + CSRF cookie authenticate the call).
  // Unique bytes per project/run keep the W10 duplicate-upload path of the
  // other project intact — deleting the shared PDF fixture here would turn
  // its re-upload into a 409 deleted-duplicate error.
  const marker = `w11-${testInfo.project.name}-${Date.now()}`
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

  // Newest document (no document_date, latest created_at) → first tile.
  await page.goto('/')
  await page.locator('.app-doc-card__title a').first().click()
  await expect(page).toHaveURL(new RegExp(`/documents/${id}$`))
  await expect(page.getByText('Status', { exact: true })).toBeVisible()

  // Edit the title via the page-wide Edit toggle: enable edit mode, change the
  // field, and commit with Enter — each field autosaves on its own (no Save
  // button). The hero heading reflecting the new title proves the PATCH landed.
  const newTitle = `Titel ${marker}`
  await page.getByTestId('edit-toggle').click()
  await page.locator('#edit-title').fill(newTitle)
  await page.locator('#edit-title').press('Enter')
  await expect(page.getByTestId('saved-title')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: newTitle })).toBeVisible()

  // The edit persisted: still there after a full reload.
  await page.reload()
  await expect(page.getByRole('heading', { level: 1, name: newTitle })).toBeVisible()

  // Delete goes through a confirmation PAGE (no JS modal).
  await page.getByTestId('delete-link').click()
  await expect(page).toHaveURL(new RegExp(`/documents/${id}/delete$`))
  await expect(page.getByRole('heading', { name: /Are you sure/ })).toBeVisible()
  await page.getByTestId('confirm-delete').click()

  // Redirected to the list with a success banner; the document is gone.
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByTestId('flash-banner')).toContainText('has been deleted')
  await expect(page.locator('.app-doc-card__title a', { hasText: newTitle })).toHaveCount(0)
  await page.goto(`/documents/${id}`)
  await expect(page.getByRole('heading', { name: 'Document not found' })).toBeVisible()
})
