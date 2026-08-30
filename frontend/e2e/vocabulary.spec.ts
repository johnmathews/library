/**
 * End-to-end facet-vocabulary-panel journey (docs/facets.md, the vocabulary
 * console at `/vocabulary`): one test that walks every write route the panel
 * exposes — create a facet, create two values, rename a value, add an alias
 * (and prove a duplicate is refused rather than silently duplicated), set and
 * clear a split colour (reloading each time to prove the write persisted, not
 * just local state), label a seeded document, preview-then-apply a merge, and
 * hit — then clear — a blocked delete before the value is gone for good.
 *
 * Mirrors facets.spec.ts / tags-editing.spec.ts: the `requireStack()` env
 * self-skip, the shared `signIn` helper, and the API-seeding trick (POST
 * /api/documents with the CSRF cookie) for a throwaway document, deleted at
 * the end. Facet creation, value creation, rename, alias, colour and delete
 * all go through the UI — that is the surface this task exists to exercise —
 * while the document seed/label/unlabel steps the brief calls out explicitly
 * as API calls (PUT /api/documents/{id}/labels) go straight through
 * `page.request`, exactly as facets.spec.ts labels its own throwaway document.
 *
 * The facet key and both value keys are derived from `Date.now()` AND the
 * project name (not just the timestamp) per playwright.config.ts's "scope by
 * a key unique to the run" rule: the three projects run serially against one
 * shared backend, so a bare timestamp collision between projects — unlikely
 * but not impossible — would otherwise let one project's leftover value
 * collide with another's. Every label/alias/key here is an invented,
 * run-unique "Widget" placeholder — never a real company, address or vehicle
 * (this repo is public and the live archive holds real ones).
 *
 * Nothing here asserts on layout: the panel reflows below the container
 * breakpoint (`@container`/`@md:` in FacetsPanel.vue) and this spec runs on
 * all three of chromium@1280, mobile-webkit@375 and tablet-webkit@656 — only
 * presence, text, values and counts are checked. Selectors use
 * `page.getByTestId(...)` (exact match) throughout rather than any
 * `[data-testid^="..."]` prefix locator — Task 8 found that a prefix selector
 * on `sender-row-` also matched nested `sender-{id}-*` testids and inflated a
 * count; exact `getByTestId` sidesteps that class of bug entirely.
 */
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

requireStack()

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

test('the vocabulary panel journey: create, rename, alias, colour, label, merge, delete', async ({
  page,
}, testInfo) => {
  await signIn(page)

  // Run-unique, invented-only identifiers. `run` seeds the facet key and both
  // value keys so a collision with another project's leftovers (or a prior
  // failed run) is astronomically unlikely on this shared, serially-run
  // backend.
  const run = `${testInfo.project.name}-${Date.now().toString(36)}`
  const facetKey = `e2e-vocab-${run}`
  const alphaKey = `alpha-${run}`
  const betaKey = `beta-${run}`
  const facetLabel = `E2E Widgets ${run}`
  const alphaInitialLabel = `Alpha Widget ${run}`
  const alphaRenamedLabel = `Alpha Prime ${run}`
  const betaLabel = `Beta Widget ${run}`
  const aliasText = `Widget Alias ${run}`

  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const headers = { 'X-CSRF-Token': csrf!.value }

  // Seed a throwaway document to label later (step 8) and clean up (step 11).
  const marker = `vocab-${run}`
  const upload = await page.request.post('/api/documents', {
    headers,
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Test document ${marker} for the vocabulary journey.`),
      },
    },
  })
  expect(upload.status()).toBe(201)
  const { id: documentId } = (await upload.json()) as { id: number }

  // --- Step 1: sign in, go to /vocabulary -----------------------------
  await page.goto('/vocabulary')
  await expect(page.getByTestId('vocab-tab-facets')).toBeVisible()

  // --- Step 2: create a facet with a run-unique key --------------------
  await page.getByTestId('create-facet-key').fill(facetKey)
  await page.getByTestId('create-facet-label').fill(facetLabel)
  await page.getByTestId('create-facet-save').click()
  await expect(page.getByTestId('create-facet-note')).toBeVisible()
  // The new facet card renders once the post-create reload resolves — the
  // "add a value" button is unconditional (unlike the value list, which only
  // renders once values exist), so it is the reliable "the facet is here"
  // signal.
  await expect(page.getByTestId(`create-value-${facetKey}-btn`)).toBeVisible()

  // --- Step 3: create two values, alpha-<run> and beta-<run> -----------
  async function createValue(key: string, label: string): Promise<void> {
    await page.getByTestId(`create-value-${facetKey}-btn`).click()
    await page.getByTestId(`create-value-${facetKey}-label`).fill(label)
    // The key field auto-fills from the label via slugify but stays editable;
    // overwrite it explicitly so the value's key is exactly `<kind>-<run>`
    // rather than whatever slugify derives from the human label.
    await page.getByTestId(`create-value-${facetKey}-key`).fill(key)
    await page.getByTestId(`create-value-${facetKey}-save`).click()
    await expect(page.getByTestId(`value-${facetKey}-${key}`)).toBeVisible()
  }

  await createValue(alphaKey, alphaInitialLabel)
  await createValue(betaKey, betaLabel)

  const alphaRow = page.getByTestId(`value-${facetKey}-${alphaKey}`)
  const betaRow = page.getByTestId(`value-${facetKey}-${betaKey}`)

  // --- Step 4: rename the first value's label ---------------------------
  await page.getByTestId(`value-${facetKey}-${alphaKey}-rename-btn`).click()
  await page.getByTestId(`value-${facetKey}-${alphaKey}-rename-input`).fill(alphaRenamedLabel)
  await page.getByTestId(`value-${facetKey}-${alphaKey}-rename-save`).click()
  await expect(alphaRow).toContainText(alphaRenamedLabel)

  // --- Step 5: add an alias, then the same alias again -------------------
  const aliasesLine = page.getByTestId(`value-${facetKey}-${alphaKey}-aliases`)
  await page.getByTestId(`value-${facetKey}-${alphaKey}-alias-btn`).click()
  await page.getByTestId(`value-${facetKey}-${alphaKey}-alias-input`).fill(aliasText)
  await page.getByTestId(`value-${facetKey}-${alphaKey}-alias-save`).click()
  await expect(aliasesLine).toHaveText(`aka ${aliasText}`)

  // Re-open the editor and add the identical alias again: the panel must
  // refuse it (case-insensitively, client-side — see FacetsPanel.vue) rather
  // than let the row grow a duplicate. Asserted against the aliases line's
  // own testid specifically (not the whole row) — the row also contains the
  // error paragraph below, whose text repeats `aliasText` verbatim ("Already
  // covered by the alias '<aliasText>'"), so a substring count over the whole
  // row would double-count and fail even when no duplicate was added.
  await page.getByTestId(`value-${facetKey}-${alphaKey}-alias-btn`).click()
  await page.getByTestId(`value-${facetKey}-${alphaKey}-alias-input`).fill(aliasText)
  await page.getByTestId(`value-${facetKey}-${alphaKey}-alias-save`).click()
  await expect(page.getByTestId(`value-${facetKey}-${alphaKey}-error`)).toContainText(
    'Already covered by the alias',
  )
  await expect(aliasesLine).toHaveText(`aka ${aliasText}`)
  await page.getByTestId(`value-${facetKey}-${alphaKey}-alias-cancel`).click()

  // --- Step 6: set a colour, reload, assert it persisted -----------------
  const colourTestid = `value-${facetKey}-${alphaKey}-colour`
  const chosenSwatch = page.getByTestId(`${colourTestid}-swatch-1`) // 'Orange' — arbitrary, deterministic
  await chosenSwatch.click()
  await expect(chosenSwatch).toHaveAttribute('aria-pressed', 'true')

  await page.reload()
  await expect(page.getByTestId(`${colourTestid}-swatch-1`)).toHaveAttribute('aria-pressed', 'true')

  // --- Step 7: clear the colour, reload, assert the default is selected --
  await page.getByTestId(`${colourTestid}-default`).click()
  await expect(page.getByTestId(`${colourTestid}-default`)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId(`${colourTestid}-swatch-1`)).toHaveAttribute('aria-pressed', 'false')

  await page.reload()
  await expect(page.getByTestId(`${colourTestid}-default`)).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByTestId(`${colourTestid}-swatch-1`)).toHaveAttribute('aria-pressed', 'false')

  // --- Step 8: seed-label the document via the API, reload, assert count -
  const label = await page.request.put(`/api/documents/${documentId}/labels`, {
    headers,
    data: { labels: { [facetKey]: alphaKey } },
  })
  expect(label.ok(), await label.text()).toBeTruthy()

  await page.reload()
  await expect(page.getByTestId(`value-${facetKey}-${alphaKey}-counts`)).toContainText('1 labelled')

  // --- Step 9: merge alpha into beta, preview then apply ------------------
  await page.getByTestId(`value-${facetKey}-${alphaKey}-merge-btn`).click()
  await expect(page).toHaveURL(new RegExp(`/vocabulary/${facetKey}/${alphaKey}/merge$`))

  await page.getByTestId('merge-target').selectOption(betaKey)
  const diff = page.getByTestId('merge-diff')
  await expect(diff).toBeVisible()
  await expect(diff).toContainText('1 documents relabelled')
  // Both aliases the merge would carry over: the source value's own key
  // (always gained, per ValueMergeView.vue) and the alias added in step 5.
  await expect(diff).toContainText(`gains alias "${alphaKey}"`)
  await expect(diff).toContainText(`gains alias "${aliasText}"`)

  await page.getByTestId('merge-apply').click()
  await expect(page).toHaveURL(/\/vocabulary$/)

  await expect(page.getByTestId(`value-${facetKey}-${alphaKey}`)).toHaveCount(0)
  await expect(betaRow).toContainText('1 labelled')

  // --- Step 10: delete is refused, then succeeds after unlabelling -------
  await page.getByTestId(`value-${facetKey}-${betaKey}-delete-btn`).click()
  await page.getByTestId(`value-${facetKey}-${betaKey}-delete-confirm`).click()
  await expect(page.getByTestId(`value-${facetKey}-${betaKey}-error`)).toContainText(
    `${facetKey}=${betaKey} is on 1 documents`,
  )
  // The row's delete-confirm affordance is still open after a 409 (see
  // FacetsPanel.vue's confirmDelete — it does not reset deleteKey on error).
  await expect(page.getByTestId(`value-${facetKey}-${betaKey}-delete-confirm`)).toBeVisible()

  const unlabel = await page.request.put(`/api/documents/${documentId}/labels`, {
    headers,
    data: { labels: { [facetKey]: null } },
  })
  expect(unlabel.ok(), await unlabel.text()).toBeTruthy()

  await page.getByTestId(`value-${facetKey}-${betaKey}-delete-confirm`).click()
  await expect(page.getByTestId(`value-${facetKey}-${betaKey}`)).toHaveCount(0)
  await expect(page.getByTestId(`facet-${facetKey}-empty`)).toBeVisible()

  // --- Step 11: clean up the seeded document ------------------------------
  const del = await page.request.delete(`/api/documents/${documentId}`, { headers })
  expect([200, 204]).toContain(del.status())
})
