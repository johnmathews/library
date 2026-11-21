/**
 * The app icon's files and colours (design system: AppIcon).
 *
 * `pwa.spec.ts` checks that the links and manifest entries point at files that
 * exist. This suite checks what those files are: each raster at the size its
 * link or manifest entry states, and both SVG sources drawn in the design
 * system's own `brand-fill` and `on-brand`, read from `tokens.json`, so a
 * change to either token fails here until the icon (and its rasters) follow.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

// vitest.config.ts pins the root to frontend/ (see pwa.spec.ts).
const FRONTEND = process.cwd()
const PUBLIC = join(FRONTEND, 'public')

/** Width and height from a PNG's IHDR chunk. */
function pngSize(file: string): [number, number] {
  const bytes = readFileSync(file)
  expect(bytes.subarray(1, 4).toString('ascii')).toBe('PNG')
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)]
}

/** The sizes packed in a .ico: one 16-byte directory entry per image (0 means 256). */
function icoSizes(file: string): number[] {
  const bytes = readFileSync(file)
  const count = bytes.readUInt16LE(4)
  return Array.from({ length: count }, (_, i) => bytes[6 + i * 16] || 256)
}

function lightValue(role: string): string {
  const tokens = JSON.parse(
    readFileSync(join(FRONTEND, '..', 'brand', 'design-system', 'project', 'tokens.json'), 'utf8'),
  ) as { color: { tokens: { name: string; value: { light: string } }[] } }
  const value = tokens.color.tokens.find((t) => t.name === role)?.value.light
  if (!value) throw new Error(`tokens.json has no ${role}`)
  return value.toLowerCase()
}

describe('app icon', () => {
  it('makes the touch icon at 180px, the size iOS asks for', () => {
    expect(pngSize(join(PUBLIC, 'apple-touch-icon.png'))).toEqual([180, 180])
  })

  it('packs 16, 32 and 48px images in favicon.ico', () => {
    expect(icoSizes(join(PUBLIC, 'favicon.ico'))).toEqual([16, 32, 48])
  })

  it('has every manifest icon at its stated size', () => {
    const manifest = JSON.parse(readFileSync(join(PUBLIC, 'manifest.webmanifest'), 'utf8')) as {
      icons: { src: string; sizes: string }[]
    }
    for (const icon of manifest.icons) {
      const [w, h] = icon.sizes.split('x').map(Number)
      expect(pngSize(join(PUBLIC, icon.src.replace(/^\//, ''))), icon.src).toEqual([w, h])
    }
  })

  it.each(['favicon.svg', 'app-icon.svg'])("draws %s in the system's brand-fill and on-brand", (name) => {
    const svg = readFileSync(join(PUBLIC, name), 'utf8').toLowerCase()
    expect(svg).toContain(`fill="${lightValue('brand-fill')}"`)
    expect(svg).toContain(`stroke="${lightValue('on-brand')}"`)
  })

  it("paints the browser chrome in the system's canvas", () => {
    const manifest = JSON.parse(readFileSync(join(PUBLIC, 'manifest.webmanifest'), 'utf8')) as {
      theme_color: string
      background_color: string
    }
    expect(manifest.theme_color).toBe(lightValue('canvas'))
    expect(manifest.background_color).toBe(lightValue('canvas'))
  })
})
