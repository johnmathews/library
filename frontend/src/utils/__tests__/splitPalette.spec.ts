import { afterEach, describe, expect, it } from 'vitest'
import {
  OTHER_COLOUR,
  SPLIT_PALETTE,
  chartColour,
  deriveSlot,
  resolveSplitColour,
  slotForStored,
} from '../splitPalette'

// jsdom does not load tokens.css. Tests that need a resolved value declare the
// roles themselves, with fake values that differ per theme, so reading the
// wrong theme cannot pass by coincidence.
function declareRoles(): HTMLStyleElement {
  const style = document.createElement('style')
  style.textContent =
    '[data-theme="light"] { --category-3: #100003; --category-other: #10000f }' +
    ' .dark { --category-3: #200003; --category-other: #20000f }'
  document.head.appendChild(style)
  return style
}

afterEach(() => {
  document.head.querySelectorAll('style').forEach((el) => el.remove())
  document.documentElement.classList.remove('dark')
})

describe('splitPalette', () => {
  it('offers five slots, each a category role with a stored identity hex', () => {
    expect(SPLIT_PALETTE).toHaveLength(5)
    SPLIT_PALETTE.forEach((slot, i) => {
      expect(slot.colour).toBe(`var(--category-${i + 1})`)
      expect(slot.light).toMatch(/^#[0-9a-f]{6}$/)
      expect(slot.name).toBeTruthy()
    })
    expect(new Set(SPLIT_PALETTE.map((slot) => slot.light)).size).toBe(5)
    expect(OTHER_COLOUR).toBe('var(--category-other)')
  })

  // The identities are what the database already holds for overrides picked
  // from the earlier palette; changing one silently orphans those picks.
  it('keeps the stored identities an earlier palette wrote', () => {
    expect(SPLIT_PALETTE.map((slot) => slot.light)).toEqual([
      '#4423da',
      '#51ae7f',
      '#1283dc',
      '#ff6f42',
      '#993375',
    ])
  })

  it('derives the same slot for the same key every time', () => {
    expect(deriveSlot('vehicle-service')).toBe(deriveSlot('vehicle-service'))
  })

  it('derives a slot that is one of the palette slots', () => {
    for (const key of ['a', 'bb', 'ccc', 'alpha-beta', 'x9', '', 'ünïcode']) {
      expect(SPLIT_PALETTE).toContain(deriveSlot(key))
    }
  })

  it('spreads keys across every slot rather than favouring one', () => {
    // A hash that returned a constant, or ignored all but the first character,
    // would pass every test above. This one fails on both.
    const keys = Array.from({ length: 240 }, (_, i) => `value-${i}`)
    const used = new Set(keys.map((k) => deriveSlot(k).name))
    expect(used.size).toBe(SPLIT_PALETTE.length)
  })

  it('resolves a null colour to the derived slot\'s role', () => {
    const slot = deriveSlot('parking')
    // No stylesheet: the role reference itself comes back.
    expect(resolveSplitColour(null, 'parking', false)).toBe(slot.colour)
  })

  it('resolves a stored palette colour to that slot\'s role, per theme', () => {
    // A stored override is one hex, but the owner picked a *slot*; painting
    // the stored hex itself would ignore the theme.
    declareRoles()
    const slot = SPLIT_PALETTE[2]!
    expect(resolveSplitColour(slot.light, 'anything', false)).toBe('#100003')
    expect(resolveSplitColour(slot.light, 'anything', true)).toBe('#200003')
  })

  it('returns a colour from outside the palette verbatim in both modes', () => {
    expect(resolveSplitColour('#123456', 'anything', false)).toBe('#123456')
    expect(resolveSplitColour('#123456', 'anything', true)).toBe('#123456')
    // The retired sixth slot of the earlier palette is such a colour now.
    expect(resolveSplitColour('#876708', 'anything', true)).toBe('#876708')
  })

  it('matches a stored palette colour case-insensitively', () => {
    const slot = SPLIT_PALETTE[0]!
    expect(resolveSplitColour(slot.light.toUpperCase(), 'anything', true)).toBe(slot.colour)
  })

  describe('chartColour', () => {
    it('reads a role for the theme asked for, whatever <html> currently says', () => {
      declareRoles()
      expect(chartColour('var(--category-other)', false)).toBe('#10000f')
      expect(chartColour('var(--category-other)', true)).toBe('#20000f')
      // useDark flips the class on <html> after the render that reacts to a
      // theme change; the theme argument must win over the class.
      document.documentElement.classList.add('dark')
      expect(chartColour('var(--category-other)', false)).toBe('#10000f')
    })

    it('passes a stored hex through untouched', () => {
      expect(chartColour('#876708', true)).toBe('#876708')
    })

    it('falls back to the reference when the stylesheet is not loaded', () => {
      expect(chartColour('var(--category-3)', false)).toBe('var(--category-3)')
    })
  })

  describe('slotForStored', () => {
    // The single definition of "is this stored hex this slot" — SplitColourPicker
    // and resolveSplitColour both go through this, rather than each deciding it
    // their own way (the two-definitions bug this function was extracted to close).

    it('identifies the slot a stored identity hex belongs to', () => {
      const slot = SPLIT_PALETTE[3]!
      expect(slotForStored(slot.light)).toBe(slot)
    })

    it('matches case-insensitively', () => {
      const slot = SPLIT_PALETTE[0]!
      expect(slotForStored(slot.light.toUpperCase())).toBe(slot)
    })

    it('returns null for a colour outside the palette', () => {
      expect(slotForStored('#123456')).toBeNull()
    })

    it('returns null for a null or absent stored colour', () => {
      expect(slotForStored(null)).toBeNull()
    })
  })
})
