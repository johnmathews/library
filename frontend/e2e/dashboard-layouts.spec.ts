/**
 * Dashboard layouts (rows, tiles, table, list) in a real browser.
 *
 * Unit tests cover which component renders; this covers what only a browser
 * can: a fresh browser starting on rows, the remembered choice surviving a
 * reload, a document opening from every layout, the table falling back to the
 * list where the results area is too narrow (a measured width, so jsdom
 * cannot see it), and the row's type band actually painting (a
 * components-layer rule, see tile-border-colour.spec.ts for why that needs a
 * computed-style check).
 *
 * Seeds two dateless documents under a unique tag and filters to them, so the
 * shared backend's other fixtures neither show up nor get reordered.
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

async function seedTwo(page: Page, marker: string): Promise<{ invoice: number; other: number }> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  expect(csrf, 'library_csrftoken cookie must exist after sign-in').toBeDefined()
  const headers = { 'X-CSRF-Token': csrf!.value }
  async function seed(kindSlug: string): Promise<number> {
    const create = await page.request.post('/api/documents', {
      headers,
      multipart: {
        file: {
          name: `${marker}-${kindSlug}.txt`,
          mimeType: 'text/plain',
          buffer: Buffer.from(`Layout fixture (${kindSlug}) — ${marker}.`),
        },
      },
    })
    expect(create.status()).toBe(201)
    const { id } = (await create.json()) as { id: number }
    const patch = await page.request.patch(`/api/documents/${id}`, {
      headers,
      data: { title: `Layout ${kindSlug} ${marker}`, kind_slug: kindSlug, tags: [marker] },
    })
    expect(patch.ok(), await patch.text()).toBeTruthy()
    return id
  }
  return { invoice: await seed('invoice'), other: await seed('other') }
}

const results = (page: Page) => page.getByTestId('dashboard-results')

test('each layout shows the documents, opens one, and is remembered', async ({ page }, testInfo) => {
  await signIn(page)
  const marker = `layouts-${testInfo.project.name}-${Date.now()}`
  const ids = await seedTwo(page, marker)
  await page.goto(`/?tag=${marker}`)
  await expect(page.locator(`#doc-card-${ids.invoice}`)).toBeVisible()
  // A fresh browser starts on rows (#246), on every viewport.
  await expect(results(page)).toHaveAttribute('data-layout', 'rows')
  await expect(page.getByTestId('layout-select')).toHaveValue('rows')

  for (const layout of ['tiles', 'table', 'list'] as const) {
    await page.getByTestId('layout-select').selectOption(layout)
    // The table needs room; a phone or tablet results area shows the list instead.
    const shown = await results(page).getAttribute('data-layout')
    expect(layout === 'table' ? ['table', 'list'] : [layout]).toContain(shown)
    await expect(page.getByTestId('doc-card')).toHaveCount(2)

    await page.locator(`#doc-card-${ids.other}`).getByRole('link').click()
    await expect(page).toHaveURL(new RegExp(`/documents/${ids.other}(\\?|$)`))
    await page.goBack()
    await expect(page.locator(`#doc-card-${ids.other}`)).toBeVisible()
    await expect(results(page)).toHaveAttribute('data-layout', shown!)
  }

  // The last choice (list) survives a reload.
  await page.reload()
  await expect(page.locator(`#doc-card-${ids.invoice}`)).toBeVisible()
  await expect(results(page)).toHaveAttribute('data-layout', 'list')
  await expect(page.getByTestId('layout-select')).toHaveValue('list')
})

test('the table shows where it fits and the list where it does not, keeping the choice', async ({
  page,
}, testInfo) => {
  await signIn(page)
  const marker = `layouts-fit-${testInfo.project.name}-${Date.now()}`
  const ids = await seedTwo(page, marker)
  await page.goto(`/?tag=${marker}`)
  await expect(page.locator(`#doc-card-${ids.invoice}`)).toBeVisible()
  await page.getByTestId('layout-select').selectOption('table')

  const width = await results(page).evaluate((el) => el.getBoundingClientRect().width)
  const shown = await results(page).getAttribute('data-layout')
  if (shown === 'table') {
    // Where the table shows, it fits: no sideways scroll inside its card.
    const overflow = await page
      .getByTestId('doc-table')
      .evaluate((el) => el.scrollWidth - el.clientWidth)
    expect(overflow).toBe(0)
  } else {
    expect(shown).toBe('list')
    expect(width).toBeLessThan(960)
  }
  // Either way the stored choice is still the table.
  await expect(page.getByTestId('layout-select')).toHaveValue('table')
  expect(await page.evaluate(() => localStorage.getItem('library:doc-layout-v2'))).toBe('table')
})

test('rows paint the type colour as a 4px band down the left edge', async ({ page }, testInfo) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await signIn(page)
  const marker = `layouts-band-${testInfo.project.name}-${Date.now()}`
  const ids = await seedTwo(page, marker)
  await page.goto(`/?tag=${marker}`)
  await page.getByTestId('layout-select').selectOption('rows')

  const leftEdge = (el: Element): { color: string; width: string } => {
    const s = getComputedStyle(el)
    return { color: s.borderLeftColor, width: s.borderLeftWidth }
  }
  const accented = await page.locator(`#doc-card-${ids.invoice}`).evaluate(leftEdge)
  const neutral = await page.locator(`#doc-card-${ids.other}`).evaluate(leftEdge)
  // Same 4px edge on both, so titles line up; only the coloured one paints.
  expect(accented.width).toBe('4px')
  expect(neutral.width).toBe('4px')
  expect(accented.color).not.toBe('rgba(0, 0, 0, 0)')
  expect(neutral.color).toBe('rgba(0, 0, 0, 0)')
})
