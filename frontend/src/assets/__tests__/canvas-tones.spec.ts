/**
 * Settings → Appearance's page tones are values of the design system's
 * `canvas` role. Two copies of each colour exist on purpose: the CSS that
 * paints the page (main.css) and the swatch the picker shows (api/settings.ts).
 * This keeps them equal, and keeps the default tone equal to the system's own
 * canvas in tokens.json, so the picker never previews a colour the page
 * does not paint.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { BACKGROUND_TONES, DEFAULT_BACKGROUND_TONE } from '@/api/settings'

// vitest.config.ts pins the root to frontend/.
const FRONTEND = process.cwd()
const mainCss = readFileSync(join(FRONTEND, 'src', 'assets', 'main.css'), 'utf8')

function canvasToken(): string {
  const tokens = JSON.parse(
    readFileSync(join(FRONTEND, '..', 'brand', 'design-system', 'project', 'tokens.json'), 'utf8'),
  ) as { color: { tokens: { name: string; value: { light: string } }[] } }
  return tokens.color.tokens.find((t) => t.name === 'canvas')!.value.light.toLowerCase()
}

/** The --canvas value main.css gives `[data-canvas='<tone>']`, if any. */
function cssTone(tone: string): string | undefined {
  const rule = new RegExp(
    `:root\\[data-canvas='${tone}'\\]:not\\(\\.dark\\)\\s*\\{\\s*--canvas:\\s*(#[0-9a-f]{6})`,
    'i',
  )
  return rule.exec(mainCss)?.[1]?.toLowerCase()
}

describe('page canvas tones', () => {
  it("makes the default tone the system's canvas, with no override in main.css", () => {
    const fallback = BACKGROUND_TONES.find((t) => t.value === DEFAULT_BACKGROUND_TONE)!
    expect(fallback.swatch.toLowerCase()).toBe(canvasToken())
    expect(cssTone(DEFAULT_BACKGROUND_TONE)).toBeUndefined()
  })

  it.each(BACKGROUND_TONES.filter((t) => t.value !== DEFAULT_BACKGROUND_TONE))(
    'paints $value in the colour its swatch shows',
    ({ value, swatch }) => {
      expect(cssTone(value)).toBe(swatch.toLowerCase())
    },
  )
})
