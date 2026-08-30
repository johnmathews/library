import { describe, expect, it } from 'vitest'
import { SPLIT_PALETTE, deriveSlot, resolveSplitColour, slotForStored } from '../splitPalette'

describe('splitPalette', () => {
  it('offers six slots, each with a light and a dark step', () => {
    expect(SPLIT_PALETTE).toHaveLength(6)
    for (const slot of SPLIT_PALETTE) {
      expect(slot.light).toMatch(/^#[0-9a-f]{6}$/)
      expect(slot.dark).toMatch(/^#[0-9a-f]{6}$/)
      expect(slot.name).toBeTruthy()
    }
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

  it('resolves a null colour to the derived slot for the mode', () => {
    const slot = deriveSlot('parking')
    expect(resolveSplitColour(null, 'parking', false)).toBe(slot.light)
    expect(resolveSplitColour(null, 'parking', true)).toBe(slot.dark)
  })

  it('resolves a stored palette colour to that slot, dark step included', () => {
    // A stored override is one hex, but the owner picked a *slot*; rendering
    // its light step on a dark chart would be the bug this branch prevents.
    const slot = SPLIT_PALETTE[3]!
    expect(resolveSplitColour(slot.light, 'anything', false)).toBe(slot.light)
    expect(resolveSplitColour(slot.light, 'anything', true)).toBe(slot.dark)
  })

  it('returns a colour from outside the palette verbatim in both modes', () => {
    expect(resolveSplitColour('#123456', 'anything', false)).toBe('#123456')
    expect(resolveSplitColour('#123456', 'anything', true)).toBe('#123456')
  })

  it('matches a stored palette colour case-insensitively', () => {
    const slot = SPLIT_PALETTE[0]!
    expect(resolveSplitColour(slot.light.toUpperCase(), 'anything', true)).toBe(slot.dark)
  })

  describe('slotForStored', () => {
    // The single definition of "is this stored hex this slot" — SplitColourPicker
    // and resolveSplitColour both go through this, rather than each deciding it
    // their own way (the two-definitions bug this function was extracted to close).

    it('identifies the slot a stored light hex belongs to', () => {
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
