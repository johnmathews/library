import { defineConfig, devices, type Project } from '@playwright/test'

/** A project whose `name` is required — sharding selects projects by name. */
type NamedProject = Project & { name: string }

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
const ALL_PROJECTS: NamedProject[] = [
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
  // Desktop Firefox + WebKit exist to prove the self-rendered (pdf.js) PDF
  // preview behaves identically across engines — the bug the native <iframe>
  // got wrong three different ways. They run ONLY pdf-preview.spec.ts; the
  // rest of the suite stays on the chromium/mobile/tablet matrix above so
  // adding these engines doesn't silently put every spec on Firefox.
  {
    name: 'firefox',
    use: { ...devices['Desktop Firefox'] },
    testMatch: /pdf-preview\.spec\.ts/,
  },
  {
    name: 'webkit',
    use: { ...devices['Desktop Safari'] },
    testMatch: /pdf-preview\.spec\.ts/,
  },
]

/**
 * The projects this run should execute, from `E2E_PROJECTS` (space- or
 * comma-separated). Unset — the local default — runs all five.
 *
 * CI sets it to shard the matrix across parallel jobs, each with its own
 * stack, because at `workers: 1` the five projects took 389s of a 407s step.
 * Sharding rather than raising `workers` is deliberate: the specs assert on
 * library-wide state (`.first()` of the dashboard grid, facet counts), so two
 * running concurrently against one backend would interfere. A shard still runs
 * its projects' specs one at a time, exactly as before; it just no longer also
 * waits for the other projects.
 *
 * An unknown name throws rather than silently selecting nothing: a typo in the
 * workflow's matrix would otherwise produce a job that runs zero tests, and
 * "ran nothing" must never look like "nothing was wrong". (The floor in
 * `scripts/assert-e2e-ran.mjs` is the second net under the same hole.)
 */
function selectedProjects(): NamedProject[] {
  const raw = process.env.E2E_PROJECTS?.trim()
  if (!raw) return ALL_PROJECTS

  const wanted = raw.split(/[\s,]+/).filter(Boolean)
  const known = ALL_PROJECTS.map((project) => project.name)
  const unknown = wanted.filter((name) => !known.includes(name))
  if (unknown.length > 0) {
    throw new Error(
      `E2E_PROJECTS names unknown project(s): ${unknown.join(', ')}. ` +
        `Known projects: ${known.join(', ')}.`,
    )
  }
  return ALL_PROJECTS.filter((project) => wanted.includes(project.name))
}

export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 15_000 },
  // Retry once in CI only. These specs assert on transient, SSE-driven UI state
  // (the in-flight `header-jobs-button`, a just-rendered `mark-verified` button),
  // which occasionally loses a timing race and fails the whole `e2e` job — and
  // `promote` (the deploy gate) needs `e2e` green. A single retry lets a one-off
  // flake self-heal; Playwright still reports it as "flaky" (visible, not hidden)
  // and a genuinely broken test fails both attempts, so real regressions still
  // gate. Each spec seeds a unique `Date.now()` marker, so a retry is isolated
  // even under workers:1. Locally retries stay off so flakes surface immediately.
  retries: process.env.CI ? 1 : 0,
  // Serial within a run: the specs share one backend and assert on library-wide
  // lists, so concurrent workers would see each other's documents. Parallelism
  // comes from sharding across CI jobs instead — see `selectedProjects` above.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  // The json reporter feeds scripts/assert-e2e-ran.mjs, which fails the CI run
  // when the suite reported success without executing anything. Playwright exits
  // 0 when every test skips and there is no --fail-on-skip, so the count has to
  // be checked from outside.
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never' }], ['json', { outputFile: 'e2e-report.json' }]]
    : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:4173',
    trace: 'retain-on-failure',
  },
  projects: selectedProjects(),
})
