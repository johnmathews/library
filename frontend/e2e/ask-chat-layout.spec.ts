/**
 * Real-browser spec for the Ask chat layout (2026-10): one edge-to-edge column
 * under the app header, a centred reading column shared by the answers and
 * the composer, conversations switched from the title's ▾ menu, and an
 * optional document view at lg+.
 *
 * Almost everything worth asserting here is geometry, which jsdom cannot see —
 * `AskView.spec.ts` covers the behaviour; these assertions are about rects.
 *
 * Project gating: the suite runs on `chromium` (1280x720), `mobile-webkit`
 * (375) and `tablet-webkit` (656). Only chromium is above `lg`, so desktop-only
 * tests are gated to it, following `responsive.spec.ts`.
 *
 * `POST /api/ask` is stubbed via `page.route`, as `ask-layout.spec.ts` does:
 * the e2e stack has no Anthropic key, and stubbing seeds no backend rows, which
 * matters because the projects share one backend serially.
 */
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'
import { rectOf, viewportOf } from './fixtures/layout'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

const TRANSCRIPT = '[data-testid="ask-transcript"]'
const VIEW_MODE = '[data-testid="ask-view-mode"]'
const STORAGE_KEY = 'library:ask-view-mode-v2'

/** A seven-column table — the shape that used to pan the whole transcript. */
const TABLE_ANSWER = [
  '| Disk | Serial | Standby % | Active % | Spinup Cycles (24h) | 7-Day Avg | Status |',
  '| --- | --- | --- | --- | --- | --- | --- |',
  '| sdf | K3S04BKQ | 1.0% | 99.0% | 2 | 3.1 | Normal |',
  '| sdg | ZR5GK5G9 | 1.0% | 99.0% | 1 | 3.1 | Normal |',
].join('\n')

requireStack()

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

async function stubAsk(page: Page, answer: string): Promise<void> {
  await page.route('**/api/ask', async (route, request) => {
    if (request.method() !== 'POST') {
      await route.continue()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        answer,
        citations: [],
        used_tools: ['search_documents'],
        cost_usd: 0.0,
        thread_id: 1,
        turn_id: 1,
      }),
    })
  })
}

async function askOnce(page: Page, question: string): Promise<void> {
  await page.goto('/ask/new')
  await expect(page.getByTestId('ask-question')).toBeVisible()
  await page.getByTestId('ask-question').fill(question)
  await page.getByTestId('ask-submit').click()
  await expect(page.getByTestId('ask-answer').first()).toBeVisible()
}

test.describe('Ask chat layout', () => {
  test('is one frameless column: the thread bar spans the content area edge to edge', async ({
    page,
  }) => {
    // The complaint this layout answers: a window within a window within a
    // window. The bar, the transcript and the composer sit directly on the
    // page — so the bar must run the full width of the area beside the app
    // sidebar, not the width of a card inset inside it.
    await stubAsk(page, 'A short answer.')
    await signIn(page)
    await askOnce(page, 'ask-chat-layout-e2e: frame')

    const content = await rectOf(page, '#app-content')
    const bar = await rectOf(page, '[data-testid="ask-thread-bar"]')
    expect(Math.abs(bar.left - content.left), 'bar starts at the content edge').toBeLessThanOrEqual(1)
    expect(Math.abs(bar.right - content.right), 'bar ends at the content edge').toBeLessThanOrEqual(
      // #app-content scrolls, so a vertical scrollbar may take a few pixels.
      16,
    )
    await expect(page.getByTestId('conversation-sidebar')).toHaveCount(0)
  })

  test('the answers and the composer share one reading column', async ({ page }) => {
    // Text you type should line up with the answers above it. The two used to
    // differ by the column's own padding, which put the composer box 48px wider
    // than the text at desktop widths.
    await stubAsk(page, 'An answer long enough to fill the column. '.repeat(20))
    await signIn(page)
    await askOnce(page, 'ask-chat-layout-e2e: column')

    const answer = await rectOf(page, '[data-testid="ask-answer"]')
    const composer = await rectOf(page, '[data-testid="ask-form"]')
    expect(Math.abs(answer.left - composer.left), 'left edges').toBeLessThanOrEqual(1)
    expect(Math.abs(answer.right - composer.right), 'right edges').toBeLessThanOrEqual(1)
  })

  test('the conversation switcher opens inside the viewport', async ({ page }) => {
    // The panel is anchored to the title's left edge and is 20rem wide; on a
    // 375px phone that only fits because of its max-width.
    await stubAsk(page, 'A short answer.')
    await signIn(page)
    await askOnce(page, 'ask-chat-layout-e2e: switcher')

    await page.getByTestId('ask-title').click()
    const panel = page.getByTestId('ask-switcher')
    await expect(panel).toBeVisible()
    await expect(page.getByTestId('thread-search')).toBeVisible()
    const rect = await rectOf(page, '[data-testid="ask-switcher"]')
    const viewport = await viewportOf(page)
    expect(rect.left).toBeGreaterThanOrEqual(0)
    expect(rect.right).toBeLessThanOrEqual(viewport.width)
  })

  test('the layout toggle is offered on desktop and absent below lg', async ({
    page,
  }, testInfo) => {
    await stubAsk(page, 'A short answer.')
    await signIn(page)
    await askOnce(page, 'ask-chat-layout-e2e: toggle presence')

    if (testInfo.project.name === 'chromium') {
      await expect(page.locator(VIEW_MODE)).toBeVisible()
    } else {
      // Rendered with v-if, so it must not merely be display:none — a
      // CSS-hidden button is still focusable and still in the a11y tree.
      await expect(page.locator(VIEW_MODE)).toHaveCount(0)
    }
  })

  test('conversation is the default; document view is remembered across a reload', async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'chromium', 'document view is lg+ only')
    await stubAsk(page, 'A short answer, in both layouts.')
    await signIn(page)
    await askOnce(page, 'ask-chat-layout-e2e: default')

    await expect(page.getByTestId('ask-question-bubble').first()).toBeVisible()
    await expect(page.getByTestId('ask-doc-block')).toHaveCount(0)

    await page.getByTestId('ask-view-mode-document').click()
    await expect(page.getByTestId('ask-doc-block').first()).toBeVisible()
    expect(await page.evaluate((k) => localStorage.getItem(k), STORAGE_KEY)).toBe('document')

    await askOnce(page, 'ask-chat-layout-e2e: after reload')
    await expect(page.getByTestId('ask-doc-block').first()).toBeVisible()
  })

  test('a stored document preference does not apply on a phone', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile-webkit', 'this is the clamp, so it needs a phone')
    await stubAsk(page, 'Clamp check.')
    await signIn(page)
    await page.evaluate((k) => localStorage.setItem(k, 'document'), STORAGE_KEY)
    await askOnce(page, 'ask-chat-layout-e2e: clamp')

    await expect(page.getByTestId('ask-doc-block')).toHaveCount(0)
    await expect(page.getByTestId('ask-question-bubble').first()).toBeVisible()
  })

  test('a wide table scrolls inside itself instead of panning the transcript', async ({
    page,
  }) => {
    // The transcript is overflow-y-auto, which makes the browser compute
    // overflow-x to auto as well, so a table wider than the column used to make
    // the ENTIRE transcript scroll sideways. Not gated: the narrow projects are
    // where a seven-column table is most likely to burst its column.
    await stubAsk(page, TABLE_ANSWER)
    await signIn(page)
    await askOnce(page, 'ask-chat-layout-e2e: table containment')

    const wrap = page.locator('.ask-table-wrap').first()
    await expect(wrap).toBeVisible()

    const transcript = await page.locator(TRANSCRIPT).evaluate((el) => ({
      client: el.clientWidth,
      scroll: el.scrollWidth,
    }))
    expect(
      transcript.scroll,
      'the transcript must not scroll horizontally — the table wrapper should absorb it',
    ).toBeLessThanOrEqual(transcript.client + 1)

    await expect(wrap).toHaveAttribute('tabindex', '0')
    await expect(wrap).toHaveAttribute('role', 'region')
  })
})
