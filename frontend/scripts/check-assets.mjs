#!/usr/bin/env node
/**
 * Govuk-residue gate: the app has been reskinned from govuk-frontend to
 * Mosaic/Tailwind. No govuk classes, the GDS Transport typeface, or
 * crown/crest imagery should leak into the production build.
 *
 * Scans every file in dist/ and fails if:
 *   - a file NAME matches /transport|crown|crest|govuk/i
 *   - a TEXT file (css/js/html/svg/json/map) CONTAINS "GDS Transport",
 *     a "govuk-" class/identifier, or a "crown copyright" reference
 *
 * Usage: npm run check:assets   (after `npm run build`)
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { extname, join, relative, basename } from 'node:path'
import process from 'node:process'

const distDir = new URL('../dist', import.meta.url).pathname

let stats
try {
  stats = statSync(distDir)
} catch {
  console.error(`check-assets: ${distDir} does not exist — run \`npm run build\` first.`)
  process.exit(2)
}
if (!stats.isDirectory()) {
  console.error(`check-assets: ${distDir} is not a directory.`)
  process.exit(2)
}

const FORBIDDEN_NAME = /transport|crown|crest|govuk/i
const TEXT_EXTENSIONS = new Set(['.css', '.js', '.mjs', '.html', '.svg', '.json', '.map', '.txt'])
const FORBIDDEN_CONTENT = [/GDS[ -]?Transport/i, /govuk-/i, /crown copyright/i]

/** @returns {string[]} all file paths under dir */
function walk(dir) {
  const out = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...walk(full))
    else out.push(full)
  }
  return out
}

const failures = []
const files = walk(distDir)

for (const file of files) {
  const rel = relative(distDir, file)
  const ext = extname(file).toLowerCase()

  if (FORBIDDEN_NAME.test(basename(file))) {
    failures.push(`${rel}: file name matches forbidden pattern ${FORBIDDEN_NAME}`)
  }

  if (TEXT_EXTENSIONS.has(ext)) {
    const content = readFileSync(file, 'utf8')
    for (const pattern of FORBIDDEN_CONTENT) {
      if (pattern.test(content)) {
        failures.push(`${rel}: content matches forbidden pattern ${pattern}`)
      }
    }
  }
}

if (failures.length) {
  console.error('check-assets: FAILED — govuk residue detected in dist/:\n')
  for (const failure of failures) console.error(`  - ${failure}`)
  process.exit(1)
}

console.log(
  `check-assets: OK — ${files.length} files in dist/ contain no govuk residue.`,
)
