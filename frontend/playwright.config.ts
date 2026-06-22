import { defineConfig, devices } from '@playwright/test'

/**
 * E2E tests against the REAL stack (docker compose backend + built
 * frontend). They self-skip when E2E_BASE_URL is unset, so `npx playwright
 * test` is a no-op without the stack — see docs/frontend.md §1.5 for the
 * full local recipe.
 *
 * Five projects (the W16 cross-device matrix plus desktop Firefox and desktop
 * WebKit), all running the same specs: desktop Chromium, a 375px mobile WebKit
 * pass (iPhone 14 descriptor, width pinned to the 375px acceptance viewport),
 * a portrait iPad WebKit pass (iPad (gen 11) descriptor — the registry has no
 * gen 10), desktop Firefox, and desktop WebKit (Safari). The two desktop engine
 * additions exercise the self-drawn PDF preview (pdf.js canvas rendering) in
 * all three browser engines — the exact gap that let the original native-iframe
 * browser-specific bugs ship.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 15_000 },
  // The projects share one backend: run them serially so the later projects
  // deterministically hit the duplicate-upload path.
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
    {
      name: 'tablet-webkit',
      use: { ...devices['iPad (gen 11)'] }, // portrait
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
  ],
})
