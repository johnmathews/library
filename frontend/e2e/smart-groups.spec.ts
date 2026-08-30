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
// the backend suite; this spec is the full-journey check.
//
// It is NOT manual-only any more: `.github/workflows/e2e-nightly.yml` runs it
// nightly with the embedder up and warm. Until that workflow existed,
// E2E_SMART_GROUPS was set nowhere in the repo, so the gate below always fired
// and this journey had never executed anywhere. Run it locally the same way,
// against a warm stack, with E2E_SMART_GROUPS=1. See docs/smart-groups.md.
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
 * Open the legacy Smart Groups view directly. `/charts` now leads to the new
 * board (`SpendingBoardView`, titled "Charts"); this page is titled "Series
 * charts". Asserting `charts-create-button` rather than either heading pins
 * the landing page to the one view that actually renders the control —
 * `legacy-charts.spec.ts` is the permanent (non-nightly-gated) guard for
 * this same route.
 */
async function openChartsPage(page: Page): Promise<void> {
  await page.goto('/charts/legacy')
  await expect(page.getByTestId('charts-create-button')).toBeVisible()
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

/**
 * Give a seeded document the metadata a Smart Group needs, deterministically.
 *
 * `_eligible_candidate_ids` requires `amount_total IS NOT NULL`, and the only
 * thing that ever sets an amount is **extraction** — an Anthropic call. On a
 * stack with no API key (this one, and CI's) extraction is skipped, so a
 * freshly-uploaded document is permanently ineligible and the sweep can never
 * suggest it. That, not the embedder, is why this journey had never passed.
 *
 * Setting the amount through the documented PATCH endpoint keeps the test on
 * the thing it is actually about — the semantic membership engine — instead of
 * making it a test of the extractor, and removes the LLM from the loop
 * entirely. The title is set for the same reason: without extraction a document
 * has none, and the review modal lists documents by title.
 */
async function setChartableMetadata(page: Page, id: number, title: string): Promise<void> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const response = await page.request.patch(`/api/documents/${id}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
    data: { title, amount_total: '42.50', currency: 'EUR' },
  })
  expect(response.status(), `setting metadata on document ${id} failed`).toBe(200)
}

/**
 * Block until a seeded document has been through the pipeline and is `indexed`.
 *
 * The first real run of this journey (the nightly's first dispatch) died here,
 * 180s into `charts-create-search` finding nothing: the spec seeded two
 * documents and immediately searched for them. A just-POSTed document is
 * `received` — it has no `ocr_text`, so it is not in the FTS index the create
 * form searches, and it has no chunk embeddings, so the semantic sweep could not
 * have scored it either. The spec's own header always said "a just-POSTed
 * document is not immediately searchable"; it just never waited.
 *
 * `failed` raises rather than looping to the deadline, so a broken pipeline is
 * reported as a broken pipeline instead of as a timeout.
 */
async function waitForIndexed(page: Page, id: number, marker: string): Promise<void> {
  const deadlineMs = 240_000
  const startedAt = Date.now()
  let lastStatus = 'unknown'
  while (Date.now() - startedAt < deadlineMs) {
    const response = await page.request.get(`/api/documents/${id}`)
    if (response.ok()) {
      const document = (await response.json()) as { status: string }
      lastStatus = document.status
      if (lastStatus === 'indexed') return
      if (lastStatus === 'failed') {
        throw new Error(`${marker} (document ${id}) failed processing — see the worker logs`)
      }
    }
    await page.waitForTimeout(2_000)
  }
  throw new Error(
    `${marker} (document ${id}) never reached "indexed" within ${deadlineMs}ms ` +
      `(last status: ${lastStatus}). The worker or the embedder is not keeping up.`,
  )
}

test('create a Smart Group, review the staged backfill match, and accept it', async ({
  page,
}, testInfo) => {
  // Well above the 180s default: this test waits for two documents to go all the
  // way through OCR → extract → markdown → embed → indexed before it can even
  // begin, and then for a semantic sweep over the archive. The wait is bounded
  // and reported per-document by `waitForIndexed`, so a hang names the document
  // and its last status rather than expiring anonymously.
  test.setTimeout(600_000)
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

  // Both must be `indexed` before the create form can find them, and before the
  // semantic sweep has embeddings to score. This is the slow part of the test.
  await waitForIndexed(page, seedId, `${marker}-a`)
  await waitForIndexed(page, matchId, `${marker}-b`)

  // Both need a title and an amount before the sweep will look at them.
  await setChartableMetadata(page, seedId, `${marker}-a`)
  await setChartableMetadata(page, matchId, `${marker}-b`)

  await openChartsPage(page)

  // Create a Smart Group seeded from the first document.
  const name = `E2E smart group ${Date.now()}`
  await page.getByTestId('charts-create-button').click()
  await page.getByTestId('charts-create-name').fill(name)
  await page.getByTestId('charts-create-smart').check()
  await page.getByTestId('charts-create-search').fill(marker)
  // Selected by document id, not by label. The label is `docLabel()`, which
  // falls back to `Document #<id>` when the document has no title — and an
  // uploaded file has no title until *extraction* writes one, which never
  // happens on a stack with no Anthropic key (this one). Filtering on the
  // marker text could therefore only have matched in an environment that runs
  // the LLM, which is the second reason this journey had never passed: waiting
  // for `indexed` was necessary but not sufficient.
  await page
    .locator(`[data-testid="charts-create-result"][data-doc-id="${seedId}"]`)
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
