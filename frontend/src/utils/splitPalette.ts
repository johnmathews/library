/**
 * The chart colours: the categorical palette for chart split values (facet
 * values and senders), and the one helper that turns a colour role into a
 * colour a canvas can paint.
 *
 * Every colour here is a design-system role (`brand/design-system/project`,
 * generated into `assets/tokens.css`), named as a CSS reference such as
 * `var(--category-1)`. A DOM element paints that reference directly and so
 * follows the theme by itself. Chart.js paints a `<canvas>`, which cannot
 * resolve `var()`, so it goes through `chartColour()`, which reads the role's
 * value for a given theme at runtime. No colour value is written in this file:
 * changing a hex is a change to tokens.json, not here.
 *
 * A value's colour is a nullable override over a slot derived from its key
 * (charts-view design §2.5): null is the normal state, so every legend is
 * stably coloured before anyone has chosen anything, and the migration invents
 * no data.
 *
 * Five slots, `category-1` to `category-5`, then `category-other` for the
 * folded tail — the same chart palette as Family Finances (ChartStyle). The
 * relief rule applies: **a swatch is never shown alone**. In the vocabulary
 * panel and in a chart legend it always carries the value's text label beside
 * it, so no hue has to carry a value's identity by itself.
 */

export interface PaletteSlot {
  /** Display name in the picker. */
  name: string
  /** What the slot paints: a CSS reference to its design-system role. */
  colour: string
  /**
   * The slot's stored identity: the hex the picker writes to the database
   * (`facet_values.colour` / `senders.colour`, which only accept `#rrggbb`).
   *
   * It is an identifier, not a colour anything paints. Each is the hex an
   * earlier six-slot palette stored for the nearest hue, so an override an
   * owner picked before the design system keeps its slot (and its hue family)
   * without a data migration. The old sixth slot (olive, `#876708`) has no
   * successor and is painted as stored, like any colour from outside the
   * palette. Named `light` for that history: the earlier palette stored its
   * light-theme step.
   */
  light: string
}

const role = (name: string): string => `var(--${name})`

export const SPLIT_PALETTE: readonly PaletteSlot[] = [
  { name: 'Purple', colour: role('category-1'), light: '#4423da' },
  { name: 'Teal', colour: role('category-2'), light: '#51ae7f' },
  { name: 'Blue', colour: role('category-3'), light: '#1283dc' },
  { name: 'Amber', colour: role('category-4'), light: '#ff6f42' },
  { name: 'Pink', colour: role('category-5'), light: '#993375' },
]

/** The folded "Other (N)" band. It never claims a palette slot. */
export const OTHER_COLOUR = role('category-other')

/** A chart with no split: one series of spending (design system, Charts). */
export const SPENDING_COLOUR = role('chart-spending')

/** A net refund below the zero line of an unsplit chart. */
export const INCOME_COLOUR = role('chart-income')

// One hidden probe element per theme. Reading the role off the probe rather
// than off <html> gives the value for the theme asked for, not whichever
// class <html> carries at that instant: useDark flips the class after the
// render that reacts to the flip, so a read off <html> would paint the
// previous theme for one frame and then never repaint. tokens.css declares
// the light roles on `[data-theme="light"]` and the dark ones on `.dark`, so
// each probe carries its own theme whatever <html> says.
const probes: Partial<Record<'light' | 'dark', HTMLElement>> = {}

function probeFor(dark: boolean): HTMLElement | null {
  if (typeof document === 'undefined' || !document.body) return null
  const key = dark ? 'dark' : 'light'
  let probe = probes[key]
  if (!probe || !probe.isConnected) {
    probe = document.createElement('span')
    if (dark) probe.className = 'dark'
    else probe.dataset.theme = 'light'
    probe.setAttribute('aria-hidden', 'true')
    probe.style.display = 'none'
    document.body.appendChild(probe)
    probes[key] = probe
  }
  return probe
}

/**
 * The colour to paint on a canvas for `colour` in the given theme.
 *
 * A `var(--role)` reference resolves to that role's value in tokens.css for
 * the theme; anything else (an owner's stored hex) is returned as it is. When
 * the stylesheet is not loaded (jsdom in unit tests, where computed custom
 * properties are empty) the reference comes back unchanged — still distinct
 * per role, and still valid CSS for a DOM element.
 */
export function chartColour(colour: string, dark: boolean): string {
  const match = /^var\(--([a-z0-9-]+)\)$/.exec(colour)
  if (!match) return colour
  const probe = probeFor(dark)
  const value = probe ? getComputedStyle(probe).getPropertyValue(`--${match[1]}`).trim() : ''
  return value || colour
}

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
 * is a slot only when it matches that slot's stored identity
 * (case-insensitively). `SplitColourPicker.vue`, `resolveSplitColour` and
 * `spending/palette.ts`'s `bands()` all go through this function, rather than
 * each deciding it — see the repository's standing rule on removing the
 * second copy rather than testing that copies agree.
 */
export function slotForStored(stored: string | null): PaletteSlot | null {
  if (!stored) return null
  const lower = stored.toLowerCase()
  return SPLIT_PALETTE.find((candidate) => candidate.light === lower) ?? null
}

/**
 * The colour to paint a split value, for the given theme.
 *
 * Three cases, in order: no stored colour derives a slot from the key; a stored
 * colour that *is* a palette slot's identity resolves to that slot's role, so
 * an override picked from the palette follows the theme even though the
 * database holds one hex; anything else is an arbitrary colour from outside the
 * palette (a script, a data migration, the retired olive slot), returned as-is.
 */
export function resolveSplitColour(stored: string | null, key: string, dark: boolean): string {
  const slot = stored ? slotForStored(stored) : deriveSlot(key)
  if (!slot) return stored!
  return chartColour(slot.colour, dark)
}
