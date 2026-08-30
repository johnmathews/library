import { describe, expect, it } from 'vitest'
import { fromCents, toCents } from '../money'

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

  it('round-trips negatives and sub-unit values', () => {
    expect(fromCents(-4900)).toBe('-49.00')
    expect(fromCents(-5)).toBe('-0.05')
    expect(fromCents(0)).toBe('0.00')
  })
})
