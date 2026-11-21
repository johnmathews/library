/**
 * The app paints only with the design system's roles (docs/brand.md).
 *
 * Colour comes from role utilities (bg-panel, text-ink, border-control-border,
 * ...) that flip under .dark by themselves. A Tailwind palette class
 * (text-gray-500, bg-violet-500) or a dark: colour twin bypasses the system and
 * its contrast gate: two AA failures shipped that way before the restyle. Raw
 * hex is allowed only where it is data (a colour the owner picked, stored ids)
 * or generated from tokens.json, in the files listed below.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

import { describe, expect, it } from 'vitest'

// vitest.config.ts pins the root to frontend/.
const SRC = join(process.cwd(), 'src')

const PALETTE =
  /(?<![\w-])(?:[a-z0-9-]+:)*(?:bg|text|border(?:-[trblxy])?|divide|ring|outline|placeholder|fill|stroke|from|via|to|accent|decoration|caret)-(?:gray|slate|zinc|neutral|stone|violet|purple|indigo|blue|sky|cyan|teal|emerald|green|lime|yellow|amber|orange|red|rose|pink|fuchsia)-\d{2,3}\b/g
const WHITE_BLACK = /(?<![\w-])(?:[a-z0-9-]+:)*(?:bg|text|border|from|to)-(?:white|black)\b/g
const HEX = /#[0-9a-fA-F]{6}\b/g

/** Files where a raw hex is data or generated, with the reason. */
const HEX_ALLOWED: Record<string, string> = {
  'assets/tokens.css': 'generated from tokens.json',
  'assets/main.css': "the page tones, values of the canvas role (canvas-tones.spec.ts)",
  'api/settings.ts': 'default and suggested document-type colours, and tone swatches: data the owner can pick',
  'utils/splitPalette.ts': 'stored slot identities written to the database',
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === '__tests__' ? [] : sourceFiles(path)
    return /\.(vue|ts|css)$/.test(name) && !name.endsWith('.spec.ts') ? [path] : []
  })
}

/** Strip comments so a sentence about the old palette does not count. */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/(^|[^:'"`])\/\/.*$/gm, '$1')
}

describe('colours come from the design system', () => {
  const files = sourceFiles(SRC).map((path) => ({ rel: relative(SRC, path), text: code(readFileSync(path, 'utf8')) }))

  it('finds the source files', () => {
    expect(files.length).toBeGreaterThan(80)
  })

  it('uses no Tailwind palette, white or black colour classes', () => {
    const hits = files.flatMap(({ rel, text }) =>
      [...(text.match(PALETTE) ?? []), ...(text.match(WHITE_BLACK) ?? [])].map((m) => `${rel}: ${m}`),
    )
    expect(hits).toEqual([])
  })

  it('keeps raw hex to the files where it is data', () => {
    const hits = files
      .filter(({ rel }) => !(rel in HEX_ALLOWED))
      .flatMap(({ rel, text }) => (text.match(HEX) ?? []).map((m) => `${rel}: ${m}`))
    expect(hits).toEqual([])
  })
})
