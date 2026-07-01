/**
 * Journey (b): create a project by assigning a uniquely-named project to a
 * document, then filter the dashboard by that project via the DocumentFilterBar
 * Project pill and confirm the document appears (and the URL carries the slug).
 *
 * Mirrors library.spec.ts: env-driven self-skip, shared sign-in helper, and the
 * API-seeding trick (POST /api/documents with the CSRF cookie) to create a
 * unique throwaway document per project/run.
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

/** On narrow screens the filter pill row collapses behind a "Filters" toggle
 * (it is always shown from the sm breakpoint up). Reveal it when present. */
async function openFilters(page: Page): Promise<void> {
  const toggle = page.getByTestId('filter-toggle')
  if (await toggle.isVisible()) {
    await toggle.click()
  }
  await expect(page.getByTestId('filter-pills')).toBeVisible()
}

test('create a project, assign a document, filter the dashboard by it', async ({
  page,
}, testInfo) => {
  await signIn(page)

  // Seed a unique throwaway document via the API (the browser session cookie +
  // CSRF cookie authenticate the call). A lowercase, hyphenated, digit-only
  // marker is slug-safe, so the project slug equals the project name.
  const marker = `proj-${testInfo.project.name}-${Date.now()}`
  const projectName = `e2e-${marker}`
  const projectSlug = projectName
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const response = await page.request.post('/api/documents', {
    headers: { 'X-CSRF-Token': csrf!.value },
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Testdocument ${marker} for the projects journey.`),
      },
    },
  })
  expect(response.status()).toBe(201)
  const { id } = (await response.json()) as { id: number }
  const docTitle = `Titel ${marker}`

  // Open the detail page, enter edit mode, give it a unique title (so we can
  // identify its tile later) and assign the brand-new project. The projects
  // editor is a token multiselect whose text input (#edit-projects) creates a
  // new project when a typed name matches no existing option; Enter commits the
  // token and autosaves the field.
  await page.goto(`/documents/${id}`)
  await expect(page.getByText('Status', { exact: true })).toBeVisible()
  await page.getByTestId('edit-toggle').click()

  await page.locator('#edit-title').fill(docTitle)
  await page.locator('#edit-title').press('Enter')
  await expect(page.getByTestId('saved-title')).toBeVisible()

  await page.locator('#edit-projects').fill(projectName)
  await page.locator('#edit-projects').press('Enter')
  await expect(page.getByTestId('saved-projects')).toBeVisible()

  // Persisted: after a reload the project renders as a read-only badge.
  await page.reload()
  await expect(page.getByTestId('project-badge').filter({ hasText: projectName })).toBeVisible()

  // From the dashboard, apply the Project filter for the new project.
  await page.goto('/')
  await openFilters(page)
  await page.getByTestId('pill-project').getByTestId('filter-pill-button').click()
  await page.getByTestId(`project-option-${projectSlug}`).click()

  // The URL reflects the project slug and the document's tile is present.
  await expect(page).toHaveURL(new RegExp(`[?&]project=${projectSlug}(?:&|$)`))
  await expect(page.locator('.app-doc-card__title a', { hasText: docTitle })).toBeVisible()

  // A different filter state (a nonexistent project slug) does not show it.
  await page.goto(`/?project=${projectSlug}-nope`)
  await expect(page.locator('.app-doc-card__title a', { hasText: docTitle })).toHaveCount(0)

  // Clean up the throwaway document (settings/taxonomy are shared across specs).
  const del = await page.request.delete(`/api/documents/${id}`, {
    headers: { 'X-CSRF-Token': csrf!.value },
  })
  expect([200, 204]).toContain(del.status())
})
