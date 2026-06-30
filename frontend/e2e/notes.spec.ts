/**
 * Journey (c): author a markdown note → it lands on its detail page → edit it
 * in place → restore an earlier version from the version-history panel.
 *
 * Mirrors library.spec.ts: env-driven self-skip, shared sign-in helper, and a
 * responsive-sidebar reveal (the New note link lives in the same sidebar that
 * collapses behind the header hamburger below the lg breakpoint). Unique marker
 * content per project/run keeps every assertion unambiguous.
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

/** Open the New note page through the responsive sidebar (reveal it behind the
 * header hamburger below the lg breakpoint, as library.spec.ts does for Upload). */
async function openNewNotePage(page: Page): Promise<void> {
  const hamburger = page.locator('button[aria-controls="sidebar"]')
  if (await hamburger.isVisible()) {
    await hamburger.click()
  }
  await page.getByTestId('sidebar-notes-link').click()
  await expect(page.getByRole('heading', { name: 'New note', exact: true })).toBeVisible()
}

test('create a note, edit it, then restore the original version', async ({ page }, testInfo) => {
  await signIn(page)
  await openNewNotePage(page)

  const stamp = `${testInfo.project.name}-${Date.now()}`
  // The note's title is the first line of the body (markdown heading marker
  // stripped), not a separate field.
  const title = `Heading ${stamp}`
  const origBody = `# Heading ${stamp}\n\nOriginal body marker origbody-${stamp}.`
  const origMarker = `origbody-${stamp}`
  const newBody = `# Heading ${stamp}\n\nEdited body marker editbody-${stamp}.`
  const newMarker = `editbody-${stamp}`

  // Compose the note; the live preview renders the markdown as you type.
  await page.locator('#note-body').fill(origBody)
  await expect(page.getByTestId('note-preview').filter({ hasText: origMarker })).toBeVisible()

  // Save → land on the new note's detail page; the reader shows the body.
  await page.locator('#note-save').click()
  await expect(page).toHaveURL(/\/documents\/\d+$/)
  // Target the page-title element specifically: the title is the body's first
  // line, which the markdown reader ALSO renders as an <h1>, so a by-role
  // heading lookup would match two elements.
  await expect(page.locator('#document-title')).toHaveText(title)
  // On small viewports the document text is collapsed by default — expand it
  // once; the toggle state persists across the in-place edits below.
  const expandText = page.getByTestId('markdown-toggle')
  if (await expandText.isVisible()) await expandText.click()
  await expect(page.getByTestId('markdown-content').filter({ hasText: origMarker })).toBeVisible()

  // Edit in place via the note card's editor; the reader reflects the new body.
  await page.getByTestId('note-edit-button').click()
  await page.locator('#note-edit-body').fill(newBody)
  await page.getByTestId('note-edit-save').click()
  await expect(page.getByTestId('markdown-content').filter({ hasText: newMarker })).toBeVisible()
  await expect(page.getByTestId('markdown-content').filter({ hasText: origMarker })).toHaveCount(0)

  // Open the version-history panel and restore the earliest captured version
  // (the original body, snapshotted before the edit). Numbering is robust to
  // how many versions exist: grab the first version row's Restore button.
  await page.getByTestId('note-versions-toggle').click()
  const versions = page.getByTestId('note-versions')
  const firstVersion = versions.locator('[data-testid^="note-version-"]').first()
  await expect(firstVersion).toBeVisible()
  await firstVersion.locator('[data-testid^="note-restore-"]').click()

  // The reader reverts to the original body.
  await expect(page.getByTestId('markdown-content').filter({ hasText: origMarker })).toBeVisible()
  await expect(page.getByTestId('markdown-content').filter({ hasText: newMarker })).toHaveCount(0)
})
