import { computed, type ComputedRef, type Ref } from 'vue'
import { useStorage } from '@vueuse/core'

/**
 * Currency options for the charts create form.
 *
 * A small built-in set (EUR / GBP / USD) covers the common cases; users can add
 * their own 3-letter ISO codes, which persist per-machine under a single storage
 * key so a code added once stays available in the dropdown afterwards.
 *
 * Codes are normalised to uppercase A–Z and deduplicated against the built-ins.
 */
export const CURRENCY_OPTIONS_STORAGE_KEY = 'library:currency-options'

/** Built-in codes offered before any user additions. */
export const DEFAULT_CURRENCIES: readonly string[] = ['EUR', 'GBP', 'USD']

/** A well-formed 3-letter currency code (already uppercased). */
export function normaliseCurrency(raw: string): string | null {
  const code = raw.trim().toUpperCase()
  return /^[A-Z]{3}$/.test(code) ? code : null
}

export interface CurrencyOptions {
  /** Built-ins followed by the user's saved custom codes (deduped, ordered). */
  options: ComputedRef<string[]>
  /** The persisted custom codes only. */
  custom: Ref<string[]>
  /** Add a code; returns the normalised code on success, or null if malformed. */
  addOption: (raw: string) => string | null
}

export function useCurrencyOptions(): CurrencyOptions {
  const custom = useStorage<string[]>(CURRENCY_OPTIONS_STORAGE_KEY, [])

  const options = computed<string[]>(() => {
    const seen = new Set(DEFAULT_CURRENCIES)
    const extra = custom.value.filter((c) => {
      if (seen.has(c)) return false
      seen.add(c)
      return true
    })
    return [...DEFAULT_CURRENCIES, ...extra]
  })

  function addOption(raw: string): string | null {
    const code = normaliseCurrency(raw)
    if (code === null) return null
    if (!DEFAULT_CURRENCIES.includes(code) && !custom.value.includes(code)) {
      custom.value = [...custom.value, code]
    }
    return code
  }

  return { options, custom, addOption }
}
