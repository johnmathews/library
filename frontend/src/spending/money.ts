/**
 * Exact arithmetic over the decimal strings the spending API sends.
 *
 * Money crosses the wire as a string (`"1284.50"`), never a JSON number, and
 * the two places this view does arithmetic — ranking split values for the fold,
 * and the headline's period-over-period delta — are both places where a float
 * result would be rendered. `1284.50 - 1142.20` is `142.29999999999998` in
 * IEEE754, so everything is done in integer cents and formatted back.
 */

/** Parse a decimal amount string to integer cents. Throws on anything else. */
export function toCents(amount: string): number {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(amount.trim())
  if (!match) throw new Error(`not a decimal amount: ${JSON.stringify(amount)}`)
  const [, sign, whole, frac = ''] = match
  const cents = Number(whole) * 100 + Number((frac + '00').slice(0, 2))
  return sign === '-' ? -cents : cents
}

/** Render integer cents back as a 2dp decimal string. */
export function fromCents(cents: number): string {
  const negative = cents < 0
  const abs = Math.abs(cents)
  return `${negative ? '-' : ''}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`
}

/**
 * A money amount with its currency, grouped for reading: `EUR 1,284.50`.
 * Currency goes in front as a plain code rather than a symbol — the display
 * currency is a chart-level choice the toolbar names, and a symbol would imply
 * the underlying documents were in it.
 */
export function formatMoney(amount: string, currency: string): string {
  const cents = toCents(amount)
  const negative = cents < 0
  const abs = Math.abs(cents)
  const whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  // An empty `currency` (the null-currency amounts on the spending footer)
  // contributes no prefix AND no separating space — `${currency} ` alone
  // would leave a floating space that only the SIGN sits in front of once
  // trimmed (`"- 45.00"`.trim() is still `"- 45.00"`, since trim only strips
  // the string's own leading/trailing edges, not an internal gap).
  const prefix = currency ? `${currency} ` : ''
  return `${negative ? '-' : ''}${prefix}${whole}.${String(abs % 100).padStart(2, '0')}`
}
