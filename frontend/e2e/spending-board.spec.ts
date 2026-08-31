/**
 * Spending board acceptance e2e: sign in → sidebar → `/charts` → the fresh
 * archive's empty state offers "All spending" first → creating it produces a
 * card on the board → renaming it via the overflow menu's "Rename" item
 * actually renames it → opening the card BY ITS NAME lands on its workspace
 * (the only route there from the board — the menu no longer has an "Edit"
 * item that navigated but changed nothing, spec review finding 5), where the
 * toolbar, the chart region and all three footer accounting blocks render →
 * back on the board, the card's overflow menu "Delete" arms a two-step
 * confirm that a dismiss leaves untouched (spec review finding 4) and an
 * accept actually deletes, returning the board to the empty state.
 *
 * **No drill-through is asserted here.** The e2e database is fresh, so "All
 * spending" covers no documents: every chart cell is empty and every footer
 * group is null, which makes a bucket-click step unreachable rather than
 * failing — the worst kind of test, one that cannot detect the breakage its
 * name claims. This spec's job is the route swap, the empty state's ordinary
 * save path, and the workspace's three regions rendering; the geometry of the
 * toolbar/chip and drill-panel presentation is `spending-layout.spec.ts`'s
 * job, and drill-through *content* is unit-tested (Task 6) against fixtures
 * that can actually express a merge, an amountless document and a 422.
 *
 * `/charts` is titled "Charts", but every navigation assertion below still
 * targets a testid unique to the new board (`spending-empty-state`,
 * `spending-card`), never a heading — asserting on testids is the sturdier
 * habit regardless of whether a title is unique.
 *
 * **This spec never asserts that "All spending" is split.** It saves as an
 * empty rule split by `category` once the archive's facet vocabulary has
 * been seeded (`library label-archive` — an operator step, never automatic
 * on migrate/startup), and unsplit on a genuinely fresh one — degrading
 * gracefully was the fix for the 422 this very spec caught on an unseeded
 * database (`SpendingEmptyState.vue`). Only the label ("All spending"), a
 * card appearing, and the workspace opening are asserted, so this spec
 * passes identically on a CI database that has never run `label-archive` and
 * on a local one that has.
 */
import { expect, test, type Page, type TestInfo } from '@playwright/test'

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

/**
 * Open /charts through the sidebar. Below the lg breakpoint the sidebar is
 * translated offscreen behind the header hamburger; reveal it first when the
 * hamburger is present (an offscreen link still reports "visible").
 *
 * Landing is asserted on `spending-empty-state`, never on a page heading.
 */
async function openChartsBoard(page: Page): Promise<void> {
  const hamburger = page.locator('button[aria-controls="sidebar"]')
  if (await hamburger.isVisible()) {
    await hamburger.click()
  }
  await page.getByTestId('sidebar-charts-link').click()
  await expect(page).toHaveURL(/\/charts$/)
  await expect(page.getByTestId('spending-empty-state')).toBeVisible()
}

/**
 * The full toolbar row lives behind a container-query threshold
 * (`spending-layout.spec.ts` measures it): visible directly on a wide column,
 * hidden behind a chip below it. The chip's own button reveals the SAME
 * controls in place, so clicking it first (when present) makes
 * `workspace-toolbar` assertable uniformly across every project without
 * guessing visibility from `isVisible()` on a container-hidden element.
 * Branching on the project name mirrors the pattern already used throughout
 * this suite (e.g. `charts-layout.spec.ts`'s per-project skips) rather than
 * risking the isVisible()-on-hidden-element trap this file was warned about.
 */
async function revealWorkspaceToolbar(page: Page, testInfo: TestInfo): Promise<void> {
  if (testInfo.project.name !== 'chromium') {
    await page.getByTestId('workspace-toolbar-chip-button').click()
  }
}

test('create "All spending" from the empty state, open it, and delete it', async ({
  page,
}, testInfo) => {
  await signIn(page)
  await openChartsBoard(page)

  // "All spending" is first and pinned (spec §4.9) — never a facet proposal,
  // which the fresh archive has none of anyway.
  const proposals = page.getByTestId('spending-empty-proposal-label')
  await expect(proposals.first()).toHaveText('All spending')

  // Click it: the ordinary save path (POST /api/spending), not a
  // migration-seeded row.
  await page.getByTestId('spending-empty-proposal').first().click()

  // A card appears on the board. Captured as `.first()`, not
  // `.filter({ hasText: 'All spending' })` — that filter is on TEXT
  // CONTENT, and the rename step below swaps the name into an `<input>`'s
  // VALUE (not text), which would make the filtered locator match nothing
  // mid-rename. There is exactly one card on this fresh board throughout
  // the whole test, so `.first()` is unambiguous.
  const card = page.getByTestId('spending-card').first()
  await expect(card).toBeVisible()
  await expect(card.getByTestId('spending-card-name')).toHaveText('All spending')

  // Rename it via the overflow menu (spec review finding 5: "Edit" used to
  // navigate but change nothing — it is now "Rename", and actually renames).
  await card.getByTestId('spending-card-menu').click()
  await card.getByTestId('spending-card-rename').click()
  await card.getByTestId('spending-card-rename-input').fill('All spending (renamed)')
  await card.getByTestId('spending-card-rename-save').click()
  await expect(card.getByTestId('spending-card-name')).toHaveText('All spending (renamed)')

  // Open the card via its name — the only route from the board into the
  // workspace now that "Edit" is gone (spec §10.3 #5: no always-visible
  // per-card controls, so this is still not a click on the whole card face).
  await card.getByTestId('spending-card-name').click()
  await expect(page).toHaveURL(/\/charts\/\d+$/)

  // The toolbar, the chart, and all three footer blocks render.
  await revealWorkspaceToolbar(page, testInfo)
  await expect(page.getByTestId('workspace-toolbar')).toBeVisible()
  await expect(page.getByTestId('workspace-grain')).toBeVisible()

  await expect(page.getByTestId('workspace-chart-region')).toBeVisible()
  await expect(page.getByTestId('workspace-headline-figure')).toBeVisible()

  await expect(page.getByTestId('spending-footer-excluded')).toBeVisible()
  await expect(page.getByTestId('spending-footer-attention')).toBeVisible()
  await expect(page.getByTestId('spending-footer-unconvertible')).toBeVisible()

  // Back to the board, then delete via the overflow menu — now a two-step
  // confirm (spec review finding 4): the menu item only ARMS a Confirm/
  // Cancel pair, so a stray click on "Delete" cannot destroy the chart.
  await page.getByRole('link', { name: 'Back to charts' }).click()
  await expect(page).toHaveURL(/\/charts$/)

  const boardCard = page.getByTestId('spending-card').first()
  await expect(boardCard).toBeVisible()
  await expect(boardCard.getByTestId('spending-card-name')).toHaveText('All spending (renamed)')
  await boardCard.getByTestId('spending-card-menu').click()
  await boardCard.getByTestId('spending-card-delete').click()

  // Dismissing the confirmation deletes nothing.
  await boardCard.getByTestId('spending-card-delete-cancel').click()
  await expect(boardCard).toBeVisible()

  // Confirming does.
  await boardCard.getByTestId('spending-card-menu').click()
  await boardCard.getByTestId('spending-card-delete').click()
  await boardCard.getByTestId('spending-card-delete-confirm').click()

  // Back to the empty state.
  await expect(page.getByTestId('spending-empty-state')).toBeVisible()
  await expect(page.getByTestId('spending-card')).toHaveCount(0)
})
