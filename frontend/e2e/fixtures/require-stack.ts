/**
 * The stack gate: skip locally, **throw** in CI.
 *
 * Every spec used to open with
 *
 *   test.skip(!BASE_URL, 'E2E_BASE_URL is not set — ...')
 *
 * which is right on a laptop and a hole in CI. `npm run test:e2e` is a bare
 * `playwright test` with no `--fail-on-skip`, so dropping `E2E_BASE_URL` from
 * the workflow makes all 19 specs skip and the job report **green having
 * launched nothing**. A skip is reported as success; nothing distinguishes
 * "the stack was fine and everything passed" from "the stack was never there".
 *
 * So the behaviour is split by environment, because the two environments want
 * genuinely different things:
 *
 * - **Locally** (`CI` unset): `test.skip`, preserving the documented ergonomics
 *   of running `npm run test:e2e` without a stack and getting a clean skip.
 *   That is the whole reason the original gate existed and it stays.
 * - **In CI** (`CI` set): `throw`. A missing base URL there is a broken
 *   workflow, and the only useful response is to fail loudly at collection.
 *
 * Belt and braces, because a throw at module scope can still be miscounted if
 * the reporter changes: `frontend/scripts/assert-e2e-ran.mjs` independently
 * asserts the executed count against a floor. See `docs/frontend.md`.
 */
import { test } from '@playwright/test'

const MISSING_MESSAGE =
  'E2E_BASE_URL is not set — start the compose stack and vite preview first (docs/frontend.md §1.5)'

/**
 * Gate a spec on the e2e stack being reachable.
 *
 * Call at module scope, before any `test()`. Returns the base URL so a spec can
 * use it directly, though most rely on Playwright's configured `baseURL`.
 */
export function requireStack(): string {
  const baseUrl = process.env.E2E_BASE_URL

  if (!baseUrl) {
    if (process.env.CI) {
      // Not test.skip: in CI this is a broken workflow, and a skipped suite
      // reports as a passing one.
      throw new Error(
        `${MISSING_MESSAGE}\n\n` +
          'This is CI, where the stack is supposed to be running, so this is a ' +
          'failure rather than a skip. If the e2e job legitimately has no stack, ' +
          'fix the job — do not weaken this gate.',
      )
    }
    test.skip(true, MISSING_MESSAGE)
  }

  return baseUrl ?? ''
}
