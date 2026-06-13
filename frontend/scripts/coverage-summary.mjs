#!/usr/bin/env node
/**
 * Emit a Markdown coverage table from Vitest's json-summary report.
 *
 * CI feeds the output to both the GitHub job summary and the sticky PR
 * comment (see .github/workflows/ci.yml, the `frontend` job). Prints a
 * placeholder rather than failing when no report exists, so the calling
 * step is safe to run even if the coverage run produced nothing.
 *
 * Usage: node scripts/coverage-summary.mjs   (run from frontend/)
 */
import { readFileSync, existsSync } from 'node:fs'

const path = new URL('../coverage/coverage-summary.json', import.meta.url)

if (!existsSync(path)) {
  console.log('## Frontend coverage\n\n_No coverage report found._')
  process.exit(0)
}

const total = JSON.parse(readFileSync(path, 'utf8')).total
const metrics = ['lines', 'statements', 'functions', 'branches']
const rows = metrics
  .map((m) => `| ${m} | ${total[m].pct}% | ${total[m].covered}/${total[m].total} |`)
  .join('\n')

console.log(
  `## Frontend coverage\n\n| metric | % | covered |\n| --- | --- | --- |\n${rows}`,
)
