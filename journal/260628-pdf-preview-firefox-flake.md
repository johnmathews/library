# e2e: de-flake the Firefox pdf-preview navigation gate

**Date:** 2026-06-28

## 1. Symptom

The `e2e` CI job stayed green via the configured single retry, but
`pdf-preview.spec.ts:89` ("pdf preview renders canvas pages and scrolls
through them") **failed its first attempt on the `firefox` project** (20.3s),
then passed on retry #1 (6.8s). Reported as "flaky", not a hard failure — but a
real intermittent first-attempt failure worth removing.

## 2. Root cause (timing, not an app bug)

The failure was on the helper's page-loaded gate:

```
expect(getByText('Status', { exact: true })).toBeVisible()  → Timeout: 15000ms
```

- "Status" is the always-present **System** group label in
  `DocumentDetailView.vue`; it renders as soon as the `doc` ref is populated
  (before the markdown fetch), so the gate is really "has the detail page
  finished loading".
- This is a **cold full-page `page.goto`**: fresh document load, JS bundle
  eval, Vue mount, then a `getDocument` round-trip — categorically slower than
  the fast in-app assertions the **15s global `expect` timeout**
  (`playwright.config.ts`) is calibrated for.
- It passed on the retry **and on every other engine** (chromium,
  mobile-webkit, tablet-webkit, webkit). A deterministic app bug would fail on
  all engines and both attempts. This only ever bit the **first attempt on
  Firefox** — the slowest matrix engine — under post-indexing CI contention.
  So: a timing flake from a mis-calibrated timeout on a navigation boundary,
  not a rendering defect.

(Local e2e repro is impossible on this Apple-Silicon Mac — the `embedder`
compose image is amd64-only — so CI is the authoritative environment; the fix
is reasoned from the CI evidence above.)

## 3. Fix

Give that one navigation gate a budget appropriate for a cold page load:

- `page.goto(..., { waitUntil: 'domcontentloaded' })` so the gate's wait starts
  as early as possible (covering the app boot) instead of being eaten by
  Firefox's slower full `load` event.
- `toBeVisible({ timeout: 30_000 })` on the "Status" gate — 2× the 15s that
  occasionally wasn't enough, scoped to this assertion only so the global 15s
  budget still surfaces real regressions fast everywhere else.

The two canvas-paint polls (page-1 / page-2 width > 0) already carry their own
explicit 15s timeouts and were never the failure point, so they're untouched.

## 4. Verification

- `eslint e2e/pdf-preview.spec.ts` ✓ · `tsc -p e2e/tsconfig.json` ✓.
- CI (linux/amd64) is authoritative for the runtime behaviour; watching the
  Playwright matrix on the PR. Note: a single green run can't *prove* a timing
  flake is gone (it may simply not have flaked), but the change directly raises
  the budget for the only operation that ever exceeded it.
