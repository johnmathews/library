/**
 * Real-geometry regression spec for the Ask composer.
 *
 * The Ask composer has been fixed three times — `bf8da0c` (desktop composer
 * floating mid-panel after a short conversation), `60a2f06` (docking follow-up)
 * and `5a878a0` (controls colliding with the pill's curved corners) — and none
 * of those fixes left behind a test that could catch the next one. The unit
 * specs cannot: jsdom has no layout, so `AskView.spec.ts` asserts classes, which
 * is asserting the fix rather than the property the fix was for.
 *
 * So these assertions are about rects in a real browser, and they are written to
 * fail in the state each defect produced.
 *
 * **A turn must exist before the geometry means anything.** On an empty
 * `/ask/new` the composer sits at the bottom whether or not `bf8da0c` is
 * applied — measured: a 33px gap with the fix, 9px without it. The defect only
 * appears once the panel has content and the conversation-list sidebar makes the
 * unbounded panel taller than the viewport, which is exactly the case the commit
 * message describes. A spec that checked the empty state would have passed
 * against the bug.
 *
 * `POST /api/ask` is stubbed (the e2e stack has no Anthropic key, and this is a
 * layout test, not an engine test) using the same route-interception trick as
 * `ask-page-citation.spec.ts`.
 */
import { expect, test, type Page } from '@playwright/test'

import { requireStack } from './fixtures/require-stack'
import {
  expectDockedToBottom,
  expectNoHorizontalOverflow,
  expectNoVerticalOverlap,
  rectOf,
  viewportOf,
} from './fixtures/layout'

const USERNAME = process.env.E2E_USERNAME ?? 'e2e'
const PASSWORD = process.env.E2E_PASSWORD ?? 'e2e-password-123'

const COMPOSER = '[data-testid="ask-form"]'
const TRANSCRIPT = '[data-testid="ask-transcript"]'

requireStack()

async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login/)
  await page.locator('#username').fill(USERNAME)
  await page.locator('#password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Documents', exact: true })).toBeVisible()
}

/** Stub the answer so the layout can be measured without an API key. */
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

/** Ask one question and wait for the answered turn to render. */
async function askOnce(page: Page, question: string): Promise<void> {
  await page.goto('/ask/new')
  await expect(page.getByTestId('ask-question')).toBeVisible()
  await page.getByTestId('ask-question').fill(question)
  await page.getByTestId('ask-submit').click()
  await expect(page.getByTestId('ask-answer').first()).toBeVisible()
}

test('the composer stays docked at the bottom after a short conversation', async ({ page }) => {
  await stubAsk(page, 'A deliberately short answer.')
  await signIn(page)
  await askOnce(page, 'ask-layout-e2e: docking check')

  // 40px: the fix leaves a 33px gap (the `#app-page` py-8 bottom padding) and
  // the tolerance is that plus rounding — not a round number picked to pass.
  // The reverted `bf8da0c` pushes the composer's bottom *past* the viewport,
  // which `expectDockedToBottom` rejects as a negative gap.
  await expectDockedToBottom(page, COMPOSER, 'ask composer', 40)
})

test('the composer never overlaps the transcript', async ({ page }) => {
  await stubAsk(page, 'Another short answer, for the overlap check.')
  await signIn(page)
  await askOnce(page, 'ask-layout-e2e: overlap check')

  const composer = await rectOf(page, COMPOSER)
  const transcript = await rectOf(page, TRANSCRIPT)
  expectNoVerticalOverlap(transcript, composer, 'transcript vs composer')
})

test('typing a multi-line question grows the composer upward, not downward', async ({ page }) => {
  await stubAsk(page, 'Growth check.')
  await signIn(page)
  await askOnce(page, 'ask-layout-e2e: growth check')

  const before = await rectOf(page, COMPOSER)
  // The textarea auto-grows to a cap; several wrapped lines is enough to move
  // its height without hitting the cap.
  await page.getByTestId('ask-question').fill(
    Array.from({ length: 6 }, (_, i) => `line ${i} of a deliberately long follow-up question`).join(
      '\n',
    ),
  )
  const after = await rectOf(page, COMPOSER)

  expect(
    after.height,
    `the composer should grow when the question wraps (was ${before.height}, now ${after.height})`,
  ).toBeGreaterThan(before.height)
  // The bottom edge is the docked one; growth must consume space above it.
  // A composer that grows downward walks off the bottom of the screen, which is
  // the shape of the `60a2f06` follow-up defect.
  expect(
    Math.abs(after.bottom - before.bottom),
    `the composer's bottom edge must not move when it grows ` +
      `(was ${before.bottom}, now ${after.bottom})`,
  ).toBeLessThanOrEqual(2)
  expect(after.top, 'a grown composer must not be cut off above the viewport').toBeGreaterThanOrEqual(0)
})

test('the send and attach controls clear the composer pill corners', async ({ page }) => {
  await stubAsk(page, 'Corner check.')
  await signIn(page)
  await askOnce(page, 'ask-layout-e2e: corner check')

  // `5a878a0`: the controls sat in the pill's `rounded-3xl` corners, where the
  // curve clips them. The pill's radius is 1.5rem = 24px; require each control
  // to start inside that inset from the composer's own edges.
  const inset = 12
  const composer = await rectOf(page, COMPOSER)
  const attach = await rectOf(page, '[data-testid="ask-image-attach"]')
  const send = await rectOf(page, '[data-testid="ask-submit"]')

  expect(
    attach.left - composer.left,
    `attach button must clear the composer's left edge by ${inset}px`,
  ).toBeGreaterThanOrEqual(inset)
  expect(
    composer.right - send.right,
    `send button must clear the composer's right edge by ${inset}px`,
  ).toBeGreaterThanOrEqual(inset)
  expect(
    composer.bottom - send.bottom,
    'send button must sit above the composer bottom edge',
  ).toBeGreaterThan(0)
})

test('the Ask view does not overflow horizontally at any width', async ({ page }, testInfo) => {
  test.skip(
    testInfo.project.name !== 'mobile-webkit',
    'the width sweep is checked once, on the mobile project',
  )
  await stubAsk(page, 'Overflow check.')
  await signIn(page)
  await askOnce(page, 'ask-layout-e2e: overflow check')

  for (const width of [320, 375, 768, 1920]) {
    await page.setViewportSize({ width, height: 720 })
    await expect(page.getByTestId('ask-form')).toBeVisible()
    await expectNoHorizontalOverflow(page, `/ask @${width}`)
    const composer = await rectOf(page, COMPOSER)
    const viewport = await viewportOf(page)
    expect(
      composer.right,
      `@${width}: the composer must not extend past the viewport`,
    ).toBeLessThanOrEqual(viewport.width + 1)
  }
})
