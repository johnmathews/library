/**
 * Smart Groups (W-smart-groups) acceptance e2e: sign in → seed two documents
 * with near-identical content (so their embeddings are close enough for the
 * backfill sweep to match one against the other) → create an authored series
 * with the "Smart Group" toggle on, seeded from the first document → the
 * staged-review modal appears with the second document as a match → accept
 * it → the tile's member count reflects both documents.
 *
 * Mirrors charts.spec.ts (create/open/delete an authored chart) and the
 * API-seeding trick used by projects.spec.ts / tags-editing.spec.ts. Both
 * fixture documents are plain-text uploads with no date-like content, so
 * neither carries a `document_date` — the shared e2e backend runs specs
 * serially across browser projects and a dated fixture would pollute the
 * dashboard's default sort for later specs (see library-e2e-shared-backend-sort).
 */
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

requireStack()

// This journey is gated behind an explicit opt-in and does NOT run in the CI
// e2e gate. It depends on the async OCR → chunk → embed → FTS pipeline having
// fully indexed two freshly-seeded documents (so they are searchable and
// carry embeddings) AND on the semantic backfill sweep scoring them as a
// match — none of which is deterministic within the e2e harness: a just-POSTed
// document is not immediately searchable, and the CI stack does not guarantee a
// warm embedder, so the run times out. The create-toggle payload and the
// auto-added badge are covered deterministically by Vitest unit tests
// (ChartsView.spec.ts / SeriesChartTile.spec.ts) and the membership engine by
// the backend suite; this spec is the manual full-journey check. Run it against
// a warm local stack with E2E_SMART_GROUPS=1. See docs/smart-groups.md.
test.skip(
  !process.env.E2E_SMART_GROUPS,
  'Smart Groups full-journey e2e requires a warm embedding pipeline; set E2E_SMART_GROUPS=1 to run locally',
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
 * Open /charts through the sidebar. Below the lg breakpoint the sidebar is
 * translated offscreen behind the header hamburger; reveal it first when the
 * hamburger is present (an offscreen link still reports "visible").
 */
async function openChartsPage(page: Page): Promise<void> {
  const hamburger = page.locator('button[aria-controls="sidebar"]')
  if (await hamburger.isVisible()) {
    await hamburger.click()
  }
  await page.getByTestId('sidebar-charts-link').click()
  await expect(page.getByRole('heading', { name: 'Charts', exact: true })).toBeVisible()
}

/** Seed a throwaway plain-text document via the API (session + CSRF cookie,
 * same trick as projects.spec.ts / tags-editing.spec.ts). No date-like
 * content, so the document stays dateless. */
async function seedDocument(page: Page, marker: string, body: string): Promise<number> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const response = await page.request.post('/api/documents', {
    headers: { 'X-CSRF-Token': csrf!.value },
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(body),
      },
    },
  })
  expect(response.status()).toBe(201)
  const { id } = (await response.json()) as { id: number }
  return id
}

test('create a Smart Group, review the staged backfill match, and accept it', async ({
  page,
}, testInfo) => {
  await signIn(page)

  // Two documents with near-identical prose (same marker family, same
  // sentence shape) so the semantic backfill sweep scores them as close.
  const marker = `smartgroup-${testInfo.project.name}-${Date.now()}`
  const seedText =
    `Invoice reference ${marker}-a. Recurring subscription charge for the ` +
    `Smart Groups e2e journey. Amount due covers this billing period.`
  const matchText =
    `Invoice reference ${marker}-b. Recurring subscription charge for the ` +
    `Smart Groups e2e journey. Amount due covers this billing period.`
  const seedId = await seedDocument(page, `${marker}-a`, seedText)
  const matchId = await seedDocument(page, `${marker}-b`, matchText)

  await openChartsPage(page)

  // Create a Smart Group seeded from the first document.
  const name = `E2E smart group ${Date.now()}`
  await page.getByTestId('charts-create-button').click()
  await page.getByTestId('charts-create-name').fill(name)
  await page.getByTestId('charts-create-smart').check()
  await page.getByTestId('charts-create-search').fill(marker)
  await page
    .getByTestId('charts-create-result')
    .filter({ hasText: `${marker}-a` })
    .click()
  await page.getByTestId('charts-create-submit').click()

  // The staged-review modal appears with the second document as a match.
  const modal = page.getByTestId('charts-backfill-modal')
  await expect(modal).toBeVisible()
  const row = modal.getByTestId('charts-backfill-row').filter({ hasText: `${marker}-b` })
  await expect(row).toBeVisible()

  // Accept it; the modal auto-closes once every row is resolved.
  await row.getByTestId('charts-backfill-add').click()
  await expect(modal).toBeHidden()

  // The tile now shows both documents as members. Accepting a staged
  // suggestion sets origin=ACCEPTED_SUGGESTION, not `auto` — the auto-added
  // badge/count only cover the silent background auto-add path, which this
  // test doesn't exercise, so assert the member count instead.
  const tile = page.getByTestId('series-trend').filter({ hasText: name })
  await expect(tile).toBeVisible()
  await expect(tile.getByTestId('series-meta-count')).toContainText('2 documents')

  // Clean up: delete the series, then both throwaway documents.
  await tile.getByTestId('series-delete').click()
  await tile.getByTestId('series-delete-confirm-button').click()
  await expect(page.getByTestId('series-trend').filter({ hasText: name })).toHaveCount(0)

  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  for (const id of [seedId, matchId]) {
    const del = await page.request.delete(`/api/documents/${id}`, {
      headers: { 'X-CSRF-Token': csrf!.value },
    })
    expect([200, 204]).toContain(del.status())
  }
})
