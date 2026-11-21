/**
 * Accessibility gate for the design system (docs/brand.md): axe's WCAG 2 A/AA
 * rules on the main screens, in light and in dark.
 *
 * The design system's own gate (scripts/check_design_system.py) measures
 * contrast between token PAIRS. This measures what a screen actually paints,
 * which is where a role used on the wrong ground shows up.
 *
 * Two lessons from the design-system skill are kept:
 * - Controls animate colour; measuring right after a theme flip reads a
 *   half-way value, so each audit waits for transitions to settle.
 * - Dark mode is flipped on <html> (the `.dark` class tokens.css was generated
 *   for). The first test fails if flipping it changes nothing, so a "dark"
 *   audit can never silently re-run light.
 *
 * Runs on the chromium project only: axe's verdicts do not depend on the
 * engine, and the WebKit projects already cover layout.
 */
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'
const SETTLE_MS = 600

const PAGES = ['/', '/ask', '/charts', '/upload', '/vocabulary', '/jobs', '/settings']

requireStack()

test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium', 'axe runs once, on chromium')
})

async function signIn(page: Page): Promise<void> {
  await page.goto('/login')
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

async function setTheme(page: Page, theme: 'light' | 'dark'): Promise<void> {
  await page.evaluate((on) => document.documentElement.classList.toggle('dark', on), theme === 'dark')
  await page.waitForTimeout(SETTLE_MS)
}

async function violations(page: Page): Promise<string[]> {
  const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
  return violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
}

test('switching to dark changes the tokens (the .dark class matches tokens.css)', async ({ page }) => {
  await signIn(page)
  const canvas = () =>
    page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim())
  await setTheme(page, 'light')
  const light = await canvas()
  await setTheme(page, 'dark')
  expect(await canvas(), '--canvas is identical in both themes').not.toBe(light)
})

test('the sign-in page has no WCAG A/AA violations in light or dark', async ({ page }) => {
  await page.goto('/login')
  for (const theme of ['light', 'dark'] as const) {
    await setTheme(page, theme)
    expect(await violations(page), theme).toEqual([])
  }
})

for (const path of PAGES) {
  test(`${path} has no WCAG A/AA violations in light or dark`, async ({ page }) => {
    await signIn(page)
    await page.goto(path)
    // Not 'networkidle': the app holds a live /api/events stream open, so the
    // network never goes idle. Wait for the page's own heading instead.
    await expect(page.locator('h1').first()).toBeVisible()
    await page.waitForTimeout(SETTLE_MS)
    for (const theme of ['light', 'dark'] as const) {
      await setTheme(page, theme)
      expect(await violations(page), theme).toEqual([])
    }
  })
}

// axe does not judge focus visibility, and the @tailwindcss/forms plugin once
// hid the ring on every select and textarea (its rule outranked ours at equal
// specificity). Tab onto a real select and measure the ring a keyboard user sees.
test('keyboard focus on a select shows the focus ring', async ({ page }) => {
  await signIn(page)
  await page.goto('/jobs')
  await expect(page.locator('h1').first()).toBeVisible()
  let tag = ''
  for (let i = 0; i < 40 && tag !== 'SELECT'; i++) {
    await page.keyboard.press('Tab')
    tag = await page.evaluate(() => document.activeElement?.tagName ?? '')
  }
  expect(tag, 'no select reachable by Tab on /jobs').toBe('SELECT')
  await page.waitForTimeout(SETTLE_MS)
  const ring = await page.evaluate(() => {
    const s = getComputedStyle(document.activeElement as Element)
    return { style: s.outlineStyle, width: s.outlineWidth, colour: s.outlineColor }
  })
  expect(ring.style).toBe('solid')
  expect(ring.width).toBe('2px')
  expect(ring.colour).not.toMatch(/rgba\(0, 0, 0, 0\)|transparent/)
})
