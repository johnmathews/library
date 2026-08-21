/**
 * Real-browser spec for the Ask transcript's document view mode.
 *
 * Document mode is clamped to `lg` (1024px) and collapses the conversation
 * rail, so almost everything worth asserting about it is *geometry* — how wide
 * the answer column ends up, and whether a wide table scrolls inside itself or
 * drags the whole transcript sideways. `AskView.spec.ts` covers the DOM
 * contract, but jsdom has no layout engine, so it can only assert classes;
 * these assertions are about rects.
 *
 * Project gating: the full suite runs on `chromium` (1280x720), `mobile-webkit`
 * (375) and `tablet-webkit` (656). Only chromium is above `lg`, so the
 * desktop-only tests below are gated to it, following the convention in
 * `responsive.spec.ts` and `charts-layout.spec.ts`. The clamp test is the
 * mirror image and runs on the mobile project.
 *
 * `POST /api/ask` is stubbed via `page.route`, as `ask-layout.spec.ts` does —
 * the e2e stack has no Anthropic key, and stubbing also means this spec seeds
 * no backend rows. That matters here: the projects share one backend serially,
 * so a spec that created a dated document would perturb the dashboard-sort
 * specs that run after it.
 */
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

const TRANSCRIPT = '[data-testid="ask-transcript"]'
const VIEW_MODE = '[data-testid="ask-view-mode"]'
const STORAGE_KEY = 'library:ask-view-mode'

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

async function answerWidth(page: Page): Promise<number> {
  return page.getByTestId('ask-answer').first().evaluate((el) => el.getBoundingClientRect().width)
}

test.describe('document view mode', () => {
  test('the layout toggle is offered on desktop and absent below lg', async ({
    page,
  }, testInfo) => {
    await stubAsk(page, 'A short answer.')
    await signIn(page)
    await askOnce(page, 'ask-document-view-e2e: toggle presence')

    if (testInfo.project.name === 'chromium') {
      await expect(page.locator(VIEW_MODE)).toBeVisible()
    } else {
      // Rendered with v-if, so it must not merely be display:none — a
      // CSS-hidden button is still focusable and still in the a11y tree.
      await expect(page.locator(VIEW_MODE)).toHaveCount(0)
    }
  })

  test('switching to document mode widens the answer and hides the rail', async ({
    page,
  }, testInfo) => {
    test.skip(testInfo.project.name !== 'chromium', 'document mode is lg+ only')
    await stubAsk(page, 'A short answer, measured in both layouts.')
    await signIn(page)
    await askOnce(page, 'ask-document-view-e2e: width check')

    const before = await answerWidth(page)
    await expect(page.getByTestId('conversation-sidebar')).toBeVisible()

    await page.getByTestId('ask-view-mode-document').click()
    await expect(page.getByTestId('ask-doc-block').first()).toBeVisible()

    // The rail is 288px wide and document mode gives it back to the transcript.
    // Without that the mode is a *narrower* read than the bubbles it replaces
    // at 1024px, which is the whole reason the collapse exists — so assert the
    // column actually grew, not merely that the class changed.
    await expect(page.getByTestId('conversation-sidebar')).toHaveCount(0)
    const after = await answerWidth(page)
    expect(after).toBeGreaterThan(before)

    // And back again.
    await page.getByTestId('ask-view-mode-conversation').click()
    await expect(page.getByTestId('conversation-sidebar')).toBeVisible()
    expect(await answerWidth(page)).toBeCloseTo(before, 0)
  })

  test('the choice survives a reload', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'chromium', 'document mode is lg+ only')
    await stubAsk(page, 'Persistence check.')
    await signIn(page)
    await askOnce(page, 'ask-document-view-e2e: persistence')

    await page.getByTestId('ask-view-mode-document').click()
    await expect(page.getByTestId('ask-doc-block').first()).toBeVisible()
    expect(await page.evaluate((k) => localStorage.getItem(k), STORAGE_KEY)).toContain('document')

    await page.reload()
    await expect(page.getByTestId('ask-answer').first()).toBeVisible()
    await expect(page.getByTestId('ask-doc-block').first()).toBeVisible()
  })

  test('a stored desktop preference does not apply on a phone', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile-webkit', 'this is the clamp, so it needs a phone')
    await stubAsk(page, 'Clamp check.')
    await signIn(page)
    // Seed the preference as though the user had chosen it on a desktop.
    await page.addInitScript(
      ([k, v]) => window.localStorage.setItem(k, v),
      [STORAGE_KEY, '"document"'],
    )
    await askOnce(page, 'ask-document-view-e2e: clamp')

    await expect(page.getByTestId('ask-doc-block')).toHaveCount(0)
    await expect(page.getByTestId('ask-turn').first()).toHaveAttribute(
      'data-view-mode',
      'conversation',
    )
  })

  test('a wide table scrolls inside itself instead of panning the transcript', async ({
    page,
  }) => {
    // The defect this replaced: the transcript is overflow-y-auto, which makes
    // the browser compute overflow-x to auto as well, so a table wider than the
    // column made the ENTIRE transcript scroll sideways — every question bubble
    // moving with it — while #ask-page's overflow-hidden clipped the remainder.
    // Measured at 1024px before the fix: transcript scrollWidth 482 against a
    // clientWidth of 414.
    //
    // Deliberately NOT gated to chromium: the narrow projects are where a
    // seven-column table is most likely to burst its column, so this is worth
    // running at 375 and 656 too.
    await stubAsk(page, TABLE_ANSWER)
    await signIn(page)
    await askOnce(page, 'ask-document-view-e2e: table containment')

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

    // The wrapper is the thing that scrolls, and it must be keyboard-reachable
    // or the content inside it is unreachable without a pointer.
    await expect(wrap).toHaveAttribute('tabindex', '0')
    await expect(wrap).toHaveAttribute('role', 'region')
  })
})
