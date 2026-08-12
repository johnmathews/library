/**
 * Real-geometry regression spec for the document detail view.
 *
 * The floating **Action dock** is the piece with the fix history: a `sticky
 * z-40 h-0` wrapper whose row is absolutely positioned, mounted by `v-if` only
 * once an IntersectionObserver says the hero has left the viewport, and anchored
 * by a per-user `dock_position` preference. "It is on screen" and "it looks
 * right on my machine" are different statements there, and jsdom can check
 * neither.
 *
 * Two things this spec had to learn the hard way, both now encoded rather than
 * rediscovered:
 *
 *   1. The dock is not in the DOM until the hero scrolls away, so it must be
 *      scrolled to, not merely waited for.
 *   2. `window.scrollTo` does nothing in this app — `#app-content` is the
 *      internal scroll container (see `scrollAppContentToBottom`).
 *
 * It also seeds its own tall note: the shared e2e fixture document renders
 * exactly one viewport tall, so nothing scrolls and the dock never mounts.
 *
 * Companion to `ask-layout.spec.ts`; same rule — layout is asserted against real
 * rects, and the unit specs assert behaviour and data flow.
 */
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'
import {
  expectNoHorizontalOverflow,
  rectOf,
  scrollAppContentToBottom,
  viewportOf,
} from './fixtures/layout'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

const DOCK = '[data-testid="action-dock"]'

requireStack()

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

/**
 * Seed a note long enough that the detail view actually scrolls, and return its
 * id. A note (not an upload) because its body renders immediately — no OCR or
 * extraction has to complete first.
 */
async function seedTallNote(page: Page, marker: string): Promise<number> {
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  const body = Array.from(
    { length: 120 },
    (_, i) => `Paragraph ${i} of ${marker}, long enough to make the page scroll.`,
  ).join('\n\n')
  const response = await page.request.post('/api/notes', {
    headers: { 'X-CSRF-Token': csrf?.value ?? '' },
    data: { title: `layout fixture — ${marker}`, body_markdown: body },
  })
  expect(response.status(), 'seeding the tall note failed').toBe(201)
  const created = (await response.json()) as { id: number }
  return created.id
}

/** Open a document and scroll until the dock mounts. */
async function openScrolled(page: Page, documentId: number): Promise<void> {
  await page.goto(`/documents/${documentId}`)
  await expect(page.locator('#document-hero')).toBeVisible()
  await scrollAppContentToBottom(page)
  await expect(page.getByTestId('action-dock')).toBeVisible()
}

test('the action dock sits fully inside the viewport', async ({ page }) => {
  await signIn(page)
  const documentId = await seedTallNote(page, 'dock-viewport')
  await openScrolled(page, documentId)

  const dock = await rectOf(page, DOCK)
  const viewport = await viewportOf(page)

  expect(dock.width, 'the dock must have been measured').toBeGreaterThan(0)
  expect(dock.left, `dock left edge ${dock.left} is off-screen`).toBeGreaterThanOrEqual(0)
  expect(
    dock.right,
    `dock right edge ${dock.right} exceeds the ${viewport.width}px viewport`,
  ).toBeLessThanOrEqual(viewport.width + 1)
  expect(dock.top, `dock top ${dock.top} is above the viewport`).toBeGreaterThanOrEqual(0)
  expect(
    dock.bottom,
    `dock bottom ${dock.bottom} exceeds the ${viewport.height}px viewport`,
  ).toBeLessThanOrEqual(viewport.height + 1)
})

test('the action dock honours the stored dock-position preference', async ({ page }, testInfo) => {
  test.skip(
    testInfo.project.name !== 'chromium',
    'the dock-position sweep is checked once, on the desktop project',
  )
  await signIn(page)
  const documentId = await seedTallNote(page, 'dock-position')

  // The preference is server-synced, so drive it through the same settings API
  // the Appearance tab uses rather than reaching into localStorage.
  for (const [position, side] of [
    ['top-left', 'left'],
    ['top-right', 'right'],
  ] as const) {
    const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
    // `background_tone` has no default on the schema, so a partial body is a
    // 422 — and every other appearance field falls back to its default on a PUT,
    // which is why the whole payload is sent rather than just the one key.
    const response = await page.request.put('/api/settings/appearance', {
      headers: { 'X-CSRF-Token': csrf?.value ?? '' },
      data: { background_tone: 'neutral', dock_position: position },
    })
    expect(response.ok(), `setting dock_position=${position} failed`).toBeTruthy()

    await openScrolled(page, documentId)
    const dock = await rectOf(page, DOCK)
    const row = await rectOf(page, '[data-testid="action-dock-row"]')

    // Anchored, not centred: the dock hugs the row's own edge on that side.
    const distance = side === 'left' ? dock.left - row.left : row.right - dock.right
    expect(
      distance,
      `dock_position=${position}: the dock should hug the ${side} edge, got ${distance}px`,
    ).toBeLessThan(row.width / 3)
  }

  // Leave the preference where the other specs expect to find it.
  const csrf = (await page.context().cookies()).find((c) => c.name === 'library_csrftoken')
  await page.request.put('/api/settings/appearance', {
    headers: { 'X-CSRF-Token': csrf?.value ?? '' },
    data: { background_tone: 'neutral', dock_position: 'top-right' },
  })
})

test('the detail view does not overflow horizontally at any width', async ({ page }, testInfo) => {
  test.skip(
    testInfo.project.name !== 'mobile-webkit',
    'the width sweep is checked once, on the mobile project',
  )
  await signIn(page)
  const documentId = await seedTallNote(page, 'detail-overflow')
  await page.goto(`/documents/${documentId}`)
  await expect(page.locator('#document-hero')).toBeVisible()

  for (const width of [320, 375, 768, 1920]) {
    await page.setViewportSize({ width, height: 720 })
    await expect(page.locator('#document-hero')).toBeVisible()
    await expectNoHorizontalOverflow(page, `/documents/:id @${width}`)
  }
})
