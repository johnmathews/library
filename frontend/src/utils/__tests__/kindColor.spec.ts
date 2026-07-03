import { describe, expect, it } from 'vitest'
import { isHexColor, resolveKindColor } from '../kindColor'
import { DEFAULT_KIND_COLORS } from '@/api/settings'

describe('resolveKindColor', () => {
  it('prefers a user override over the default palette', () => {
    expect(resolveKindColor('invoice', { invoice: '#123456' })).toBe('#123456')
  })

  it('falls back to the built-in default when there is no override', () => {
    expect(resolveKindColor('invoice', {})).toBe(DEFAULT_KIND_COLORS.invoice)
    expect(resolveKindColor('receipt')).toBe(DEFAULT_KIND_COLORS.receipt)
  })

  it('returns null for a kind with no default and no override (neutral)', () => {
    expect(resolveKindColor('other', {})).toBeNull()
    expect(resolveKindColor('certificate')).toBeNull()
  })

  it('lets a user colour an otherwise-neutral kind', () => {
    expect(resolveKindColor('other', { other: '#abcdef' })).toBe('#abcdef')
  })

  it('returns null for an absent slug', () => {
    expect(resolveKindColor(null)).toBeNull()
    expect(resolveKindColor(undefined)).toBeNull()
  })
})

describe('isHexColor', () => {
  it('accepts #rrggbb', () => {
    expect(isHexColor('#56b1f3')).toBe(true)
    expect(isHexColor('#ABCDEF')).toBe(true)
  })

  it('rejects short, named, or malformed values', () => {
    expect(isHexColor('#fff')).toBe(false)
    expect(isHexColor('blue')).toBe(false)
    expect(isHexColor('56b1f3')).toBe(false)
  })
})
