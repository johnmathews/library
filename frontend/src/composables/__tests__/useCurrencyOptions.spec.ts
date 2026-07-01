import { describe, it, expect, beforeEach } from 'vitest'
import { nextTick } from 'vue'
import {
  useCurrencyOptions,
  normaliseCurrency,
  DEFAULT_CURRENCIES,
  CURRENCY_OPTIONS_STORAGE_KEY,
} from '../useCurrencyOptions'

beforeEach(() => {
  localStorage.clear()
})

describe('normaliseCurrency', () => {
  it('uppercases and accepts a 3-letter code', () => {
    expect(normaliseCurrency('chf')).toBe('CHF')
    expect(normaliseCurrency('  eur ')).toBe('EUR')
  })

  it('rejects malformed codes', () => {
    expect(normaliseCurrency('')).toBeNull()
    expect(normaliseCurrency('EU')).toBeNull()
    expect(normaliseCurrency('EURO')).toBeNull()
    expect(normaliseCurrency('E1R')).toBeNull()
  })
})

describe('useCurrencyOptions', () => {
  it('offers the built-in currencies by default', () => {
    const { options } = useCurrencyOptions()
    expect(options.value).toEqual([...DEFAULT_CURRENCIES])
  })

  it('appends a custom code and persists it', async () => {
    const { options, addOption } = useCurrencyOptions()
    expect(addOption('chf')).toBe('CHF')
    expect(options.value).toContain('CHF')
    await nextTick()
    expect(JSON.parse(localStorage.getItem(CURRENCY_OPTIONS_STORAGE_KEY)!)).toEqual(['CHF'])
  })

  it('does not duplicate a built-in or an existing custom code', () => {
    const { options, addOption } = useCurrencyOptions()
    expect(addOption('EUR')).toBe('EUR')
    addOption('CHF')
    addOption('chf')
    expect(options.value.filter((c) => c === 'CHF')).toHaveLength(1)
    expect(options.value.filter((c) => c === 'EUR')).toHaveLength(1)
  })

  it('returns null for a malformed code without persisting', () => {
    const { addOption, custom } = useCurrencyOptions()
    expect(addOption('nope!')).toBeNull()
    expect(custom.value).toEqual([])
  })
})
