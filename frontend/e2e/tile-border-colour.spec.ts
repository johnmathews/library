/**
 * Per-kind tile-border colour — computed-style regression guard (W3).
 *
 * The border-accent feature shipped invisibly because the accent rule lived in
 * Tailwind's `components` cascade layer while the tile's `border-gray-200`
 * utility lived in the higher-ranked `utilities` layer, which wins regardless of
 * specificity — so the coloured border never painted. The unit test only
 * asserted the CSS *class* was attached (jsdom has no layered-cascade
 * resolution), so it passed while the border stayed grey.
 *
 * This spec closes that gap by reading the REAL browser's computed
 * `border-top-color` on a real tile: it sets a distinctive per-kind override
 * and asserts the tile actually paints that colour (not the neutral grey it
 * would show if the utility still won). Same contract as the other specs:
 * requires the real stack + `e2e` user; self-skips when E2E_BASE_URL is unset.
 */
import { expect, test, type Page } from '@playwright/test'

const BASE_URL = process.env.E2E_BASE_URL
const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

// #ff2d55 — a colour no neutral/hover border ever uses, so a match proves the
// accent (not Tailwind's grey utility) is what painted.
const OVERRIDE_HEX = '#ff2d55'
const OVERRIDE_RGB = 'rgb(255, 45, 85)'

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

async function csrfHeader(page: Page): Promise<Record<string, string>> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  expect(csrf, 'library_csrftoken cookie must exist after sign-in').toBeDefined()
  return { 'X-CSRF-Token': csrf!.value }
}

test('per-kind accent: tile paints the override border colour (cascade layer fix)', async ({
  page,
}, testInfo) => {
  // Force light mode so the asserted value is the raw override hex (dark mode
  // adapts it via color-mix). The app's dark styling is class-driven and off by
  // default here, but this keeps the assertion robust.
  await page.emulateMedia({ colorScheme: 'light' })
  await signIn(page)
  const headers = await csrfHeader(page)

  // Seed a document and give it a known kind + a unique tag so we can filter the
  // dashboard down to exactly this tile.
  const marker = `w3-accent-${testInfo.project.name}-${Date.now()}`
  const create = await page.request.post('/api/documents', {
    headers,
    multipart: {
      file: {
        name: `${marker}.txt`,
        mimeType: 'text/plain',
        buffer: Buffer.from(`Tile border colour fixture — ${marker}.`),
      },
    },
  })
  expect(create.status()).toBe(201)
  const { id } = (await create.json()) as { id: number }

  const patch = await page.request.patch(`/api/documents/${id}`, {
    headers,
    data: { kind_slug: 'invoice', tags: [marker] },
  })
  expect(patch.ok(), await patch.text()).toBeTruthy()

  // Set a distinctive per-kind override for `invoice`.
  const colours = await page.request.put('/api/settings/kind-colors', {
    headers,
    data: { kind_colors: { invoice: OVERRIDE_HEX } },
  })
  expect(colours.ok(), await colours.text()).toBeTruthy()

  // Load the dashboard filtered to just our document.
  await page.goto(`/?tag=${marker}`)
  const tile = page.locator(`#doc-card-${id}`)
  await expect(tile).toBeVisible()
  await expect(tile).toHaveClass(/app-doc-card--accented/)

  // The real, resolved border must be the override colour — not the neutral grey
  // (rgb(229, 231, 235)) that a utility-layer win would leave — and 2px thick so
  // the colour reads on a phone (neutral tiles stay 1px).
  const border = await tile.evaluate((el) => {
    const s = getComputedStyle(el)
    return { color: s.borderTopColor, width: s.borderTopWidth }
  })
  expect(border.color).toBe(OVERRIDE_RGB)
  expect(border.width).toBe('2px')
})
