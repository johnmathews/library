#!/usr/bin/env node
/**
 * e2e non-skip floor: fail when the suite reported success without running.
 *
 * `playwright test` exits 0 when every test skips, and there is no
 * `--fail-on-skip`. So dropping `E2E_BASE_URL` from the workflow used to make all
 * 19 specs skip and the job report **green having launched nothing** — the same
 * class of hole as the OCR-engine skips (`scripts/check_engine_skips.py`) and the
 * golden-corpus tier.
 *
 * `requireStack()` already throws in CI, which is the primary defence. This is
 * the second one, and it is not redundant: a reporter change, a `--grep` that
 * matches nothing, a project filter typo, or a spec file that fails to collect
 * all produce a green run with too few tests and none of them trip a throw at
 * module scope.
 *
 * Fails when:
 *   - the JSON report is missing or unparseable (never a silent pass)
 *   - fewer than MIN_EXPECTED tests actually executed
 *   - any skip reason mentions E2E_BASE_URL (the stack gate fired in CI)
 *
 * Usage: node scripts/assert-e2e-ran.mjs [path-to-report.json]
 */
import { readFileSync } from 'node:fs'
import process from 'node:process'

/**
 * Floor on executed tests, taken from a real green run and deliberately set
 * BELOW it so adding or removing a test does not red the gate — it is a
 * "did the suite run at all" check, not a test-count assertion. The unset
 * default of 40 is the floor for a whole five-project run (which executes ~137).
 *
 * Overridable because CI shards the matrix across parallel jobs, and a shard
 * that runs one project honestly executes far fewer than the whole matrix — so
 * each shard sets its own floor (see `e2e` in .github/workflows/ci.yml, where
 * they sum to more than this default). The override is a floor, never a
 * disable: an unparseable or missing value exits 2 rather than falling back to
 * something permissive, so a typo in the workflow cannot quietly turn the gate
 * off — which is the failure mode this whole script exists for.
 */
const MIN_EXPECTED = (() => {
  const raw = process.env.E2E_MIN_EXPECTED
  if (raw === undefined || raw === '') return 40
  const parsed = Number(raw)
  if (!Number.isInteger(parsed) || parsed < 1) {
    console.error(
      `assert-e2e-ran: E2E_MIN_EXPECTED must be a positive integer, got ${JSON.stringify(raw)}. ` +
        'Refusing to run with an unusable floor.',
    )
    process.exit(2)
  }
  return parsed
})()

const reportPath = process.argv[2] ?? 'e2e-report.json'

let report
try {
  report = JSON.parse(readFileSync(reportPath, 'utf8'))
} catch (error) {
  console.error(
    `assert-e2e-ran: cannot read ${reportPath} (${error.message}).\n` +
      'Playwright must run with the json reporter for this gate to mean anything. ' +
      'A missing report is a failure, not a pass: "no report" and "nothing wrong" ' +
      'must not share an exit code.',
  )
  process.exit(2)
}

/** Walk the nested suite tree and collect every test result. */
function collectTests(suites, out = []) {
  for (const suite of suites ?? []) {
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) {
        for (const result of test.results ?? []) {
          out.push({ title: spec.title, file: suite.file, status: result.status, result })
        }
      }
    }
    collectTests(suite.suites, out)
  }
  return out
}

const tests = collectTests(report.suites)
const executed = tests.filter((t) => t.status !== 'skipped')
const skipped = tests.filter((t) => t.status === 'skipped')

// A stack-gate skip in CI means requireStack() did not throw but the stack was
// absent anyway — worth catching separately, with a clearer message than a count.
const stackSkips = skipped.filter((t) => {
  const blob = JSON.stringify(t.result ?? {})
  return /E2E_BASE_URL/.test(blob)
})

if (stackSkips.length > 0) {
  console.error(
    `assert-e2e-ran: ${stackSkips.length} test(s) skipped because E2E_BASE_URL was unset.\n` +
      'The stack was not reachable, so this run proves nothing. Fix the job that ' +
      'starts the stack — do not weaken the gate.',
  )
  for (const test of stackSkips.slice(0, 5)) {
    console.error(`  - ${test.file} › ${test.title}`)
  }
  process.exit(1)
}

if (executed.length < MIN_EXPECTED) {
  console.error(
    `assert-e2e-ran: only ${executed.length} test(s) executed (floor ${MIN_EXPECTED}), ` +
      `${skipped.length} skipped.\n` +
      'The suite reported success without really running. Likely causes: the ' +
      'stack never came up, a project filter or --grep matched nothing, or a spec ' +
      'file failed to collect.',
  )
  process.exit(1)
}

console.log(
  `assert-e2e-ran: ok — ${executed.length} test(s) executed, ${skipped.length} skipped ` +
    `(floor ${MIN_EXPECTED})`,
)
