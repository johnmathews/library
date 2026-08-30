import { describe, expect, it } from 'vitest'
import { deriveSlot, resolveSplitColour, SPLIT_PALETTE } from '../splitPalette'

// `fnv1a` and `deriveSlot` are already exercised indirectly through
// `@/spending/palette`'s own spec (`palette.spec.ts`), via `bands()`'s
// derived-colour walk. `resolveSplitColour` is not: it is unused by plan 4b
// (see the file's own header + the function's docblock) and would otherwise
// be silently dead code in this branch, so it gets a direct spec of its own.
describe('resolveSplitColour', () => {
  it('derives a slot from the key when nothing is stored', () => {
    const key = 'hosting'
    const expected = deriveSlot(key)
    expect(resolveSplitColour(null, key, false)).toBe(expected.light)
    expect(resolveSplitColour(null, key, true)).toBe(expected.dark)
  })

  it('resolves a stored LIGHT hex to the same slot, in either theme', () => {
    const slot = SPLIT_PALETTE[2]!
    expect(resolveSplitColour(slot.light, 'anything', false)).toBe(slot.light)
    expect(resolveSplitColour(slot.light, 'anything', true)).toBe(slot.dark)
  })

  it('returns a stored hex matching no slot verbatim, in either theme', () => {
    const custom = '#123456'
    expect(resolveSplitColour(custom, 'anything', false)).toBe(custom)
    expect(resolveSplitColour(custom, 'anything', true)).toBe(custom)
  })

  it('is a pure, deterministic function of the key (no de-collision — see docblock)', () => {
    const a = resolveSplitColour(null, 'category-a', false)
    const b = resolveSplitColour(null, 'category-a', false)
    expect(a).toBe(b)
  })
})
