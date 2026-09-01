/**
 * End-to-end facet vocabulary journey (docs/facets.md): create a facet and two
 * values, apply one of them to a document through the per-document editor, then
 * filter the document list by it. Two values because the filter bar only offers
 * a facet that has at least two — see the comment at the creation site.
 *
 * Mirrors tags-editing.spec.ts / library.spec.ts: env-driven self-skip via
 * requireStack(), the shared sign-in helper, and the API-seeding trick (POST
 * /api/documents with the CSRF cookie) for a throwaway document, deleted at
 * the end.
 *
 * Runs on all five playwright.config.ts projects, but firefox/webkit are
 * pinned to pdf-preview.spec.ts via `testMatch` and never execute this file —
 * in practice this only runs on chromium@1280, mobile-webkit@375 and
 * tablet-webkit@656. Nothing here asserts on layout: the filter bar wraps at
 * the narrower two, so only presence/text/values/counts are checked, never
 * visibility of a particular row shape.
 *
 * The facet key is derived from `Date.now()` (unique per run, per
 * playwright.config.ts's "scope by a key unique to the run" rule for this
 * shared, serially-run backend), so its value can never already be applied to
 * another spec's documents — the post-filter count of 1 depends on that.
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

test('a facet can be created, applied to a document, and filtered on', async ({
  page,
}, testInfo) => {
  await signIn(page)

  const marker = `facets-${testInfo.project.name}-${Date.now()}`
  const key = `e2e${Date.now().toString(36)}`
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const headers = { 'X-CSRF-Token': csrf!.value }

  // Seed a throwaway document to label.
  const upload = await page.request.post('/api/documents', {
    headers,
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Testdocument ${marker} for the facets journey.`),
      },
    },
  })
  expect(upload.status()).toBe(201)
  const { id } = (await upload.json()) as { id: number }

  // Create a facet and a value in the controlled vocabulary.
  const facet = await page.request.post('/api/facets', { headers, data: { key, label: 'E2E' } })
  expect(facet.ok()).toBeTruthy()
  // TWO values, not one. The filter bar only offers a facet once it has two or
  // more (see FacetFilterBar.vue): a one-option select cannot narrow anything,
  // because every document it can show carries the same value. Only `alpha` is
  // ever applied to a document, so the post-filter count of 1 below is
  // unaffected — `beta` exists purely to clear that threshold.
  for (const [valueKey, label] of [
    ['alpha', 'Alpha'],
    ['beta', 'Beta'],
  ]) {
    const value = await page.request.post(`/api/facets/${key}/values`, {
      headers,
      data: { key: valueKey, label },
    })
    expect(value.ok()).toBeTruthy()
  }

  // Apply the value through the per-document editor. The editor card is
  // always attached (it renders every facet, disabled, even with no values
  // yet), so wait on the select itself becoming interactable rather than on
  // the card, which would pass before the facets fetch resolves.
  await page.goto(`/documents/${id}`)
  await expect(page.getByTestId('facet-editor')).toBeAttached()
  await page.getByTestId(`facet-edit-${key}`).selectOption('alpha')

  const saveButton = page.getByTestId('facet-save')
  await saveButton.click()
  // The button's label flips back from "Saving…" to "Save labels" only once
  // the PUT round-trip resolves (success or failure) — the signal that it is
  // now safe to reload without racing the in-flight request. A save that
  // *succeeded* additionally leaves the button disabled (no more unsaved
  // changes); that combination is the assertion that it actually worked.
  await expect(saveButton).toHaveText('Save labels')
  await expect(saveButton).toBeDisabled()
  await expect(page.getByTestId('facet-error')).toHaveCount(0)

  // Persisted: reload and confirm the select still shows the saved value.
  await page.reload()
  await expect(page.getByTestId(`facet-edit-${key}`)).toHaveValue('alpha')

  // Filter the document list by the new facet value. The key is unique to
  // this run, so exactly one document — the one just labelled — can match.
  // The select is present because the facet carries two values; `beta` is
  // unapplied, so choosing `alpha` still leaves exactly one match.
  // The dashboard is '/' — there is no '/documents' route (see
  // src/router/index.ts; only '/documents/:id' exists) and no catch-all.
  await page.goto('/')
  await page.getByTestId(`facet-select-${key}`).selectOption('alpha')
  await expect(page.locator('[data-testid="doc-card"]')).toHaveCount(1)

  // Clearing the facet filter drops the constraint again.
  await page.getByTestId('facet-clear').click()
  await expect(page.getByTestId(`facet-select-${key}`)).toHaveValue('')

  // Cleanup: delete the throwaway document.
  const del = await page.request.delete(`/api/documents/${id}`, { headers })
  expect([200, 204]).toContain(del.status())
})
