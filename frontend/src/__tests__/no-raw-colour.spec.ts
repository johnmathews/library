/**
 * The app paints only with the design system's roles (docs/brand.md).
 *
 * Colour comes from role utilities (bg-panel, text-ink, border-control-border,
 * ...) that flip under .dark by themselves, or from var(--role) in CSS. A
 * Tailwind palette class (text-gray-500, bg-violet-500), a white or black
 * utility, or a literal colour (#fff, #1f2937, #11223344, rgb(), hsl(),
 * oklch(), ...) bypasses the system and its contrast gate: two AA failures
 * shipped that way before the restyle. A literal is allowed only where it is
 * data (a colour the owner picked, stored ids) or generated from tokens.json,
 * in the files listed below.
 *
 * Not covered: named colours in CSS or canvas code (`color: white`,
 * `ctx.fillStyle = 'black'`). None are in the tree.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

import { describe, expect, it } from 'vitest'

// vitest.config.ts pins the root to frontend/.
const SRC = join(process.cwd(), 'src')

const UTILITY =
  '(?:bg|text|border(?:-[trblxyse])?|divide|ring|ring-offset|inset-ring|outline|placeholder|fill|stroke|from|via|to|accent|decoration|caret|shadow|inset-shadow|drop-shadow|text-shadow)'
const PALETTE = new RegExp(
  `(?<![\\w-])(?:[a-z0-9-]+:)*${UTILITY}-(?:gray|slate|zinc|neutral|stone|violet|purple|indigo|blue|sky|cyan|teal|emerald|green|lime|yellow|amber|orange|red|rose|pink|fuchsia)-\\d{2,3}\\b`,
  'g',
)
const WHITE_BLACK = new RegExp(`(?<![\\w-])(?:[a-z0-9-]+:)*${UTILITY}-(?:white|black)(?![\\w-])`, 'g')
// 3, 4, 6 or 8 hex digits, not part of a longer word or an id selector
// (#add-note). Issue references (#140) live in comments, stripped below.
const HEX = /(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![\w-])/g
const FUNCTIONAL = /(?<![\w-])(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(/gi

/** Files where a literal colour is data or generated, with the reason. */
const LITERAL_ALLOWED: Record<string, string> = {
  'assets/tokens.css': 'generated from tokens.json',
  'assets/main.css': 'the page tones, values of the canvas role (canvas-tones.spec.ts)',
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

/**
 * Strip comments so a sentence about the old palette, or an issue number, does
 * not count. A block comment starts only at the beginning or after whitespace
 * or a CSS delimiter, so `accept="image/*"` or a glob does not hide what follows.
 */
function code(text: string): string {
  return text
    .replace(/(^|[\s;{}])\/\*[\s\S]*?\*\//g, '$1')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/(^|[^:'"`])\/\/.*$/gm, '$1')
}

function classHits(text: string): string[] {
  return [...(text.match(PALETTE) ?? []), ...(text.match(WHITE_BLACK) ?? [])]
}

function literalHits(text: string): string[] {
  return [...(text.match(HEX) ?? []), ...(text.match(FUNCTIONAL) ?? [])]
}

describe('the patterns catch each raw colour form the guard covers', () => {
  it.each([
    'class="text-gray-500"',
    'class="dark:hover:bg-violet-600"',
    'class="ring-offset-gray-100"',
    'class="shadow-black"',
    'class="fill-white"',
    'class="stroke-black"',
    'class="ring-offset-white"',
    'class="bg-white"',
    'class="border-s-gray-200"',
    'class="inset-ring-black"',
    'class="drop-shadow-black"',
  ])('flags the class in %s', (sample) => {
    expect(classHits(code(sample))).not.toEqual([])
  })

  it.each([
    'color: #fff;',
    'color: #ffff;',
    'color: #1f2937;',
    'color: #11223344;',
    'color: rgb(1 2 3);',
    'background: rgba(0, 0, 0, 0.5);',
    'color: hsl(0 0% 50%);',
    'color: oklch(70% 0.1 250);',
    'class="bg-[#abc]"',
    'class="bg-[rgb(0_0_0)]"',
    'color: RGB(1 2 3);',
    'color: color(display-p3 1 0 0);',
    // A `/*` inside a string is not a comment, so the colour after it counts.
    '<input accept="image/*" /> <i style="color: #fff" /> */',
  ])('flags the literal in %s', (sample) => {
    expect(literalHits(code(sample))).not.toEqual([])
  })

  it.each([
    '// walks the active filter (#140)',
    '/* fixed in #144 */',
    "querySelector('#add-note')",
    'background: var(--tile);',
    'class="bg-panel text-ink-secondary border-line"',
    'class="text-white-ish"',
    'const value = props.rgbValue(x)',
  ])('leaves %s alone', (sample) => {
    const text = code(sample)
    expect([...classHits(text), ...literalHits(text)]).toEqual([])
  })
})

describe('colours come from the design system', () => {
  const files = sourceFiles(SRC).map((path) => ({ rel: relative(SRC, path), text: code(readFileSync(path, 'utf8')) }))

  it('finds the source files', () => {
    expect(files.length).toBeGreaterThan(80)
  })

  it('uses no Tailwind palette, white or black colour classes', () => {
    const hits = files.flatMap(({ rel, text }) => classHits(text).map((m) => `${rel}: ${m}`))
    expect(hits).toEqual([])
  })

  it('keeps literal colours (hex, rgb(), hsl(), oklch(), ...) to the files where they are data', () => {
    const hits = files
      .filter(({ rel }) => !(rel in LITERAL_ALLOWED))
      .flatMap(({ rel, text }) => literalHits(text).map((m) => `${rel}: ${m}`))
    expect(hits).toEqual([])
  })
})
