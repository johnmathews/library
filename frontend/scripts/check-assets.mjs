#!/usr/bin/env node
/**
 * Licensing gate: the GDS Transport typeface and crown/crest imagery are
 * licence-restricted to gov.uk services and must never appear in our build.
 *
 * Scans every file in dist/ and fails if:
 *   - a file NAME matches /transport|crown|crest|gds/i
 *   - a web font (.woff/.woff2/.ttf/.otf/.eot) is anything other than a
 *     self-hosted Inter file (inter-*)
 *   - a TEXT file (css/js/html/svg/json/map) CONTAINS "GDS Transport",
 *     "govuk-crest", a crown reference, or a url() pointing at GOV.UK's
 *     /assets/fonts|images/ paths
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

const FORBIDDEN_NAME = /transport|crown|crest|gds/i
const FONT_EXTENSIONS = new Set(['.woff', '.woff2', '.ttf', '.otf', '.eot'])
const TEXT_EXTENSIONS = new Set(['.css', '.js', '.mjs', '.html', '.svg', '.json', '.map', '.txt'])
const FORBIDDEN_CONTENT = [
  /GDS[ -]?Transport/i,
  /govuk-crest/i,
  /crown copyright logotype|govuk-logotype-crown|crown\.svg/i,
  /url\([^)]*\/assets\/(?:fonts|images)\/(?:light|bold|govuk-)/i,
]

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

  if (FONT_EXTENSIONS.has(ext) && !/^inter-/i.test(basename(file))) {
    failures.push(`${rel}: web font that is not a self-hosted Inter file`)
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
  console.error('check-assets: FAILED — licence-restricted GOV.UK assets detected:\n')
  for (const failure of failures) console.error(`  - ${failure}`)
  process.exit(1)
}

console.log(
  `check-assets: OK — ${files.length} files in dist/ contain no GDS Transport or crown/crest assets.`,
)
