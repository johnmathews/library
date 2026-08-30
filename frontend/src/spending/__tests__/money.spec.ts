import { describe, expect, it } from 'vitest'
import { formatMoney, fromCents, toCents } from '../money'

describe('money', () => {
  it('parses the decimal strings the API actually sends', () => {
    expect(toCents('1284.50')).toBe(128450)
    expect(toCents('-49.00')).toBe(-4900)
    expect(toCents('0')).toBe(0)
    expect(toCents('7.5')).toBe(750)
  })

  it('rejects anything that is not a decimal amount', () => {
    expect(() => toCents('1,284.50')).toThrow()
    expect(() => toCents('')).toThrow()
  })

  // The reason this module exists: a float subtraction of two 2dp decimals
  // prints 142.29999999999998, and that number would reach a headline.
  it('stays exact where floats do not', () => {
    expect(fromCents(toCents('0.10') + toCents('0.20'))).toBe('0.30')
    expect(fromCents(toCents('1284.50') - toCents('1142.20'))).toBe('142.30')
  })

  // These values are the ones that actually catch a `Number(amount) * 100`
  // implementation: 0.29 * 100 is 28.999999999999996 and 0.57 * 100 is
  // 56.99999999999999 in IEEE754 — both floor to a cent short. (0.10, 0.20,
  // 1284.50 and 1142.20 above do not drift under that multiplication, so they
  // cannot detect this particular bug on their own.)
  it('parses amounts a naive float multiply would round down', () => {
    expect(toCents('0.29')).toBe(29)
    expect(toCents('0.57')).toBe(57)
  })

  it('round-trips negatives and sub-unit values', () => {
    expect(fromCents(-4900)).toBe('-49.00')
    expect(fromCents(-5)).toBe('-0.05')
    expect(fromCents(0)).toBe('0.00')
  })

  describe('formatMoney', () => {
    it('groups thousands with commas', () => {
      expect(formatMoney('1284.50', 'EUR')).toBe('EUR 1,284.50')
    })

    it('does not add a separator under 1000', () => {
      expect(formatMoney('284.50', 'EUR')).toBe('EUR 284.50')
    })

    it('puts the sign before the currency code', () => {
      expect(formatMoney('-49.00', 'USD')).toBe('-USD 49.00')
    })

    it('formats a sub-unit value', () => {
      expect(formatMoney('0.05', 'GBP')).toBe('GBP 0.05')
    })

    it('formats a whole-number input with no decimal part', () => {
      expect(formatMoney('0', 'GBP')).toBe('GBP 0.00')
    })

    // A null-currency amount (`SpendingFooter`'s `bareAmount`, `DrillBucketBody`)
    // calls `formatMoney(amount, '')`. §4.5's own "an unconvertible payment and
    // an equal unconvertible refund" case can net negative, so this must render
    // the bare digits with no floating space where the currency prefix would
    // have gone — `"- 45.00"` reads as a stray leading space once the sign is
    // in front of it, and `.trim()` cannot fix an INTERNAL gap, only the
    // string's own leading/trailing edges.
    it('formats an empty currency with no leading space, positive or negative', () => {
      expect(formatMoney('45.00', '')).toBe('45.00')
      expect(formatMoney('-45.00', '')).toBe('-45.00')
    })
  })
})
