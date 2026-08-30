/**
 * Permanent guard for `/charts/legacy` (the pre-spending-board `ChartsView` +
 * its Smart Groups create form) — the ONLY reachable UI for creating a Smart
 * Group, and unlinked from the sidebar (see `docs/frontend.md` §1.5,
 * `ChartsView` row).
 *
 * Why this spec exists rather than relying on `smart-groups.spec.ts` to
 * catch a break here: that spec is gated behind `E2E_SMART_GROUPS` and only
 * runs nightly (`.github/workflows/e2e-nightly.yml`), not in the CI e2e gate
 * that guards every merge. A regression on this route — the create button
 * vanishing, the form's controls losing their testids, `/charts/legacy`
 * itself 404ing — would surface up to a day late, reported by the nightly
 * job as an apparently unrelated Smart Groups failure rather than as what it
 * actually is: the legacy route breaking. This spec runs in the ordinary
 * (non-gated) e2e suite and checks only that the route renders its create
 * affordance and the form's controls are reachable — not the full smart
 * create → review → accept journey, which is `smart-groups.spec.ts`'s job
 * and requires a warm embedding pipeline this spec does not.
 *
 * The second assertion (`/charts` has ZERO `charts-create-button`) exists so
 * the first one cannot pass vacuously: `/charts` and `/charts/legacy` are
 * titled "Charts" and "Series charts" respectively (see
 * `spending-board.spec.ts`'s note on the shared-heading defect this used to
 * be), but this spec targets testids, not headings, and testids the wrong
 * route also happened to render would let a routing regression go unnoticed
 * without this negative check.
 *
 * Modelled on the sign-in helper in `spending-board.spec.ts` /
 * `smart-groups.spec.ts`.
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

test('legacy /charts/legacy renders its create form; /charts does not', async ({ page }) => {
  await signIn(page)

  await page.goto('/charts/legacy')
  const createButton = page.getByTestId('charts-create-button')
  await expect(createButton).toBeVisible()

  await createButton.click()
  await expect(page.getByTestId('charts-create-name')).toBeVisible()
  await expect(page.getByTestId('charts-create-smart')).toBeVisible()
  await expect(page.getByTestId('charts-create-search')).toBeVisible()
  await expect(page.getByTestId('charts-create-submit')).toBeVisible()

  // The negative check: without it, the assertions above could pass on the
  // wrong route and this spec would never notice.
  await page.goto('/charts')
  await expect(page.getByTestId('charts-create-button')).toHaveCount(0)
})
