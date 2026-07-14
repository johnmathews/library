/**
 * Held-emails acceptance e2e: sign in → open /held-emails → verify the view
 * shell (heading, status filter, empty state) renders.
 *
 * NAVIGATION + EMPTY STATE ONLY: the e2e compose stack has no IMAP server, so
 * no email can ever be held here — the queue is deterministically empty and
 * the dashboard "N emails held" affordance never renders (it hides at zero,
 * which this spec also asserts). The full held→ingest/dismiss lifecycle is
 * covered by the vitest suites (stores/heldEmails.spec.ts,
 * views/HeldEmailsView.spec.ts) against mocked APIs.
 *
 * No fixtures are created (and none with document_date — shared-backend sort
 * convention). Runs in all matrix projects including mobile-webkit and
 * tablet-webkit: the view is reached by direct URL, so no sidebar/hamburger
 * interaction is needed below the lg breakpoint. Skips itself when
 * E2E_BASE_URL is unset.
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

test('held-emails view renders its empty state; dashboard affordance hides at zero', async ({
  page,
}) => {
  await signIn(page)

  // Nothing can be held in this stack, so the dashboard button must be hidden.
  await expect(page.getByTestId('held-emails-button')).toHaveCount(0)

  // The route is reachable directly (it sits behind the auth guard).
  await page.goto('/held-emails')
  await expect(page.getByRole('heading', { name: 'Held emails', exact: true })).toBeVisible()
  await expect(page.getByTestId('held-emails-status-filter')).toBeVisible()
  await expect(page.getByTestId('held-emails-empty')).toBeVisible()
  await expect(page.getByTestId('held-emails-empty')).toContainText(
    'No held emails — everything filed itself.',
  )
})

test('unauthenticated /held-emails redirects to login with the target preserved', async ({
  page,
}) => {
  await page.goto('/held-emails')
  await expect(page).toHaveURL(/\/login\?redirect=%2Fheld-emails/)
})
