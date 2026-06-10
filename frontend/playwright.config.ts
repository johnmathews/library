import { defineConfig, devices } from '@playwright/test'

/**
 * E2E tests against the REAL stack (docker compose backend + built
 * frontend). They self-skip when E2E_BASE_URL is unset, so `npx playwright
 * test` is a no-op without the stack — see docs/frontend.md §1.5 for the
 * full local recipe.
 *
 * Two projects only (W16 widens the matrix): desktop Chromium and a
 * 375px-wide mobile WebKit pass (iPhone 14 device descriptor, width pinned
 * to the 375px acceptance viewport).
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 15_000 },
  // The two projects share one backend: run them serially so the second
  // project deterministically hits the duplicate-upload path.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:4173',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-webkit',
      use: {
        ...devices['iPhone 14'],
        viewport: { width: 375, height: 667 }, // acceptance: usable at 375px
      },
    },
  ],
})
