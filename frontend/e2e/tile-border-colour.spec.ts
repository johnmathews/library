/**
 * Per-kind tile colour — computed-style regression guard (W3, restyled W7).
 *
 * A kind's colour is a 4px band across the tile's top edge (mockup
 * 261121-documents, option A); a kind with no colour has no band at all.
 *
 * History: the border accent first shipped invisibly because the accent rule
 * lived in Tailwind's `components` cascade layer while the tile's border
 * utility lived in the higher-ranked `utilities` layer, which wins regardless
 * of specificity. The unit test only asserted the CSS *class* was attached
 * (jsdom has no layered-cascade resolution), so it passed while nothing
 * painted.
 *
 * This spec closes that gap by reading the REAL browser's computed top edge on
 * real tiles: it sets a distinctive per-kind override and asserts the accented
 * tile paints that colour at 4px, and that a neutral tile paints no band. Same
 * contract as the other specs: requires the real stack + `e2e` user;
 * self-skips when E2E_BASE_URL is unset.
 */
import { expect, test, type Page } from '@playwright/test'

import { pinDashboardLayout } from './fixtures/layout'
import { requireStack } from './fixtures/require-stack'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

// #ff2d55 — a colour no neutral edge or default kind colour ever uses, so a
// match proves the per-kind override (not a utility or a default) painted.
const OVERRIDE_HEX = '#ff2d55'
const OVERRIDE_RGB = 'rgb(255, 45, 85)'

requireStack()

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

async function csrfHeader(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  expect(csrf, 'library_csrftoken cookie must exist after sign-in').toBeDefined()
  return { 'X-CSRF-Token': csrf!.value }
}

test('per-kind accent: an accented tile paints a 4px top band; a neutral tile has none', async ({
  page,
}, testInfo) => {
  // Force light mode so the asserted value is the raw override hex. The app's
  // dark styling is class-driven and off by default here, but this keeps the
  // assertion robust.
  await page.emulateMedia({ colorScheme: 'light' })
  // The band is the tile's; rows draw theirs down the left (dashboard-layouts.spec.ts).
  await pinDashboardLayout(page, 'tiles')
  await signIn(page)
  const headers = await csrfHeader(page)

  // Seed two documents sharing one unique tag, so the dashboard can be filtered
  // down to exactly these tiles: one `invoice` (gets the override) and one
  // `other` (no default colour, no override — the neutral tile).
  const marker = `w3-accent-${testInfo.project.name}-${Date.now()}`
  async function seed(kindSlug: string): Promise<number> {
    const create = await page.request.post('/api/documents', {
      headers,
      multipart: {
        file: {
          name: `${marker}-${kindSlug}.txt`,
          mimeType: 'text/plain',
          buffer: Buffer.from(`Tile colour fixture (${kindSlug}) — ${marker}.`),
        },
      },
    })
    expect(create.status()).toBe(201)
    const { id } = (await create.json()) as { id: number }
    const patch = await page.request.patch(`/api/documents/${id}`, {
      headers,
      data: { kind_slug: kindSlug, tags: [marker] },
    })
    expect(patch.ok(), await patch.text()).toBeTruthy()
    return id
  }
  const accentedId = await seed('invoice')
  const neutralId = await seed('other')

  // Set a distinctive per-kind override for `invoice`.
  const colours = await page.request.put('/api/settings/kind-colors', {
    headers,
    data: { kind_colors: { invoice: OVERRIDE_HEX } },
  })
  expect(colours.ok(), await colours.text()).toBeTruthy()

  // Load the dashboard filtered to just our documents.
  await page.goto(`/?tag=${marker}`)
  const accented = page.locator(`#doc-card-${accentedId}`)
  const neutral = page.locator(`#doc-card-${neutralId}`)
  await expect(accented).toBeVisible()
  await expect(neutral).toBeVisible()
  await expect(accented).toHaveClass(/app-doc-card--accented/)
  await expect(neutral).not.toHaveClass(/app-doc-card--accented/)

  const topEdge = (el: Element): { color: string; width: string } => {
    const s = getComputedStyle(el)
    return { color: s.borderTopColor, width: s.borderTopWidth }
  }

  // The real, resolved top edge must be the override colour at 4px: a band, not
  // an outline (the other three edges stay unpainted).
  const band = await accented.evaluate(topEdge)
  expect(band.color).toBe(OVERRIDE_RGB)
  expect(band.width).toBe('4px')
  const sides = await accented.evaluate((el) => {
    const s = getComputedStyle(el)
    return [s.borderRightWidth, s.borderBottomWidth, s.borderLeftWidth]
  })
  expect(sides).toEqual(['0px', '0px', '0px'])

  // A kind with no colour has no band at all.
  const none = await neutral.evaluate(topEdge)
  expect(none.width).toBe('0px')
})
