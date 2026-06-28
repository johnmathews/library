/**
 * Journey (a): upload a markdown (.md) file → the long-form reader card on the
 * document detail page renders the rendered markdown content.
 *
 * Mirrors library.spec.ts: env-driven, self-skips when E2E_BASE_URL is unset,
 * reuses the same sign-in + responsive-sidebar (hamburger reveal) helpers, and
 * waits for the same Indexed/duplicate upload outcomes. The .md bytes carry a
 * per-project/per-run marker so each matrix project uploads fresh content (no
 * cross-project duplicate path) and the reader assertion is unambiguous.
 */
import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE_URL = process.env.E2E_BASE_URL
const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'
const FIXTURE = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  'fixtures',
  'note-fixture.md',
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

/** Open the Upload page through the responsive sidebar (reveal it behind the
 * header hamburger below the lg breakpoint). Copied from library.spec.ts. */
async function openUploadPage(page: Page): Promise<void> {
  const hamburger = page.locator('button[aria-controls="sidebar"]')
  if (await hamburger.isVisible()) {
    await hamburger.click()
  }
  await page.getByTestId('sidebar-upload-link').click()
  await expect(page.getByRole('heading', { name: 'Upload documents' })).toBeVisible()
}

test('upload a markdown file, see it indexed, reader card renders it', async ({
  page,
}, testInfo) => {
  await signIn(page)
  await openUploadPage(page)

  // Unique bytes per project/run: the fixture's heading + distinctive word plus
  // a marker that is guaranteed to be the only occurrence in the library, so
  // the reader assertion below can't match another document and no project hits
  // the cross-project duplicate-upload path.
  const marker = `mdreader-${testInfo.project.name}-${Date.now()}`
  const baseMarkdown = readFileSync(FIXTURE, 'utf8')
  const markdown = `${baseMarkdown}\n\n## Marker\n\nUnique marker: ${marker}\n`

  await page.locator('input[type="file"]').setInputFiles({
    name: `${marker}.md`,
    mimeType: 'text/markdown',
    buffer: Buffer.from(markdown),
  })
  await page.getByRole('button', { name: 'Upload', exact: true }).click()

  // Fresh content per project → progress → processing → indexed. Tolerate the
  // duplicate path too, exactly like library.spec.ts, in case of a retry.
  const indexed = page.locator('[data-testid="upload-list"]').getByText('Indexed', { exact: true })
  const duplicate = page.getByText('already in your library')
  await expect(indexed.or(duplicate).first()).toBeVisible({ timeout: 150_000 })

  // The newest document is the first tile. Poll by reloading: thumbnails and
  // the rendered-text rows are written by background jobs.
  await page.goto('/')
  await expect(async () => {
    await page.reload()
    await expect(page.locator('.app-doc-card').first()).toBeVisible({ timeout: 2_000 })
  }).toPass({ timeout: 60_000 })

  await page.locator('.app-doc-card__title a').first().click()
  await expect(page).toHaveURL(/\/documents\/\d+$/)

  // The long-form reader card (#document-markdown-card) renders the markdown:
  // the rendered <h1> heading and the unique marker are both visible in the
  // sanitised markdown content. Poll the reader (its rows are filled by a
  // background extraction job that may lag the indexed status).
  const reader = page.locator('#document-markdown-card')
  await expect(reader.getByRole('heading', { name: /Zwaluwnest report/ })).toBeVisible({
    timeout: 60_000,
  })
  await expect(reader.getByTestId('markdown-content').filter({ hasText: marker })).toBeVisible()
})
