/**
 * The categorical palette for chart split values (facet values and senders).
 *
 * A value's colour is a nullable override over a slot derived from its key
 * (charts-view design §2.5): null is the normal state, so every legend is
 * stably coloured before anyone has chosen anything, and the migration invents
 * no data.
 *
 * The six slots were validated with the `dataviz` skill's validate_palette.js
 * on the ALL-PAIRS pairlist — the correct list here, because a slot is derived
 * by hashing the key, so any two hues can end up side by side in a legend and
 * there is no ordering to check adjacency against. Both modes report ALL CHECKS
 * PASS: light worst CVD ΔE 9.9 (protan) and normal-vision ΔE 19.8; dark worst
 * CVD ΔE 9.3 (deutan) and normal-vision ΔE 17.2.
 *
 * Two light slots and one dark slot fall below 3:1 against the chart surface,
 * so the relief rule applies: **a swatch is never shown alone**. In this panel
 * and in a chart legend it always carries the value's text label beside it.
 * Re-run the validator and update these numbers before changing any hex.
 *
 * Six rather than eight: the eight-hue reference set clears the adjacent
 * pairlist but fails all-pairs (worst normal-vision ΔE 7.1), and no ordering
 * fixes that, because with all pairs in play the pairlist does not depend on
 * order.
 */

export interface PaletteSlot {
  /** Display name in the picker. */
  name: string
  /** The slot's stored identity: this is the hex written to the database. */
  light: string
  /** The same hue re-stepped for the dark chart surface — selected, not flipped. */
  dark: string
}

export const SPLIT_PALETTE: readonly PaletteSlot[] = [
  { name: 'Blue', light: '#1283dc', dark: '#5791ca' },
  { name: 'Orange', light: '#ff6f42', dark: '#b93b09' },
  { name: 'Green', light: '#51ae7f', dark: '#19825f' },
  { name: 'Indigo', light: '#4423da', dark: '#584fcc' },
  { name: 'Plum', light: '#993375', dark: '#ed3297' },
  { name: 'Olive', light: '#876708', dark: '#b08923' },
]

/**
 * The palette slot a value falls in when it has no stored colour.
 *
 * FNV-1a over the key's code units: stable across renders, sessions, machines
 * and releases, and independent of document counts and of how many values the
 * facet has — so a value's colour never moves because the archive changed.
 * Keyed on the value's `key`, never its ordinal or its rank in a chart.
 */
export function deriveSlot(key: string): PaletteSlot {
  let hash = 0x811c9dc5
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return SPLIT_PALETTE[hash % SPLIT_PALETTE.length]!
}

/**
 * The palette slot a stored hex identifies, or null if it isn't one.
 *
 * The single definition of "is this stored hex this slot" — a stored colour
 * is a slot only when it matches that slot's `light` step (case-insensitively;
 * `light` is the stored identity per the header above). `SplitColourPicker.vue`
 * used to make this same decision a second way (`normalized === slot.light`
 * inline) and `resolveSplitColour` a third; two definitions that happen to
 * agree today are one definition away from silently disagreeing — see the
 * repository's standing rule on removing the second copy rather than testing
 * that copies agree. Both callers now go through this function.
 */
export function slotForStored(stored: string | null): PaletteSlot | null {
  if (!stored) return null
  const lower = stored.toLowerCase()
  return SPLIT_PALETTE.find((candidate) => candidate.light === lower) ?? null
}

/**
 * The colour to paint a split value, for the current theme.
 *
 * Three cases, in order: no stored colour derives a slot from the key; a stored
 * colour that *is* a palette slot's light step resolves to that slot, so an
 * override picked from the palette is theme-aware even though the database
 * holds one hex; anything else is an arbitrary colour from outside the palette
 * (a script, a data migration) with no theme pair to look up, returned as-is.
 */
export function resolveSplitColour(stored: string | null, key: string, dark: boolean): string {
  if (!stored) {
    const slot = deriveSlot(key)
    return dark ? slot.dark : slot.light
  }
  const slot = slotForStored(stored)
  if (!slot) return stored
  return dark ? slot.dark : slot.light
}
