/**
 * The shared six-slot categorical palette for split-value colouring
 * (docs/superpowers/specs/2026-08-30-charts-view-design.md §4.12).
 *
 * OWNERSHIP: this module is owned by plan 4c (the facet-vocabulary panel),
 * which is being built in a parallel worktree at the same time as this one
 * (plan 4b, the `/charts` spending view). Both plans derive a colour for a
 * split value whose `colour` is null (spec §2.5), and they must derive the
 * *same* colour for the same value wherever it appears — so there is meant
 * to be exactly one definition of this mapping, not two. Because the two
 * branches cannot see each other's commits, this file is duplicated here
 * verbatim (same contract, same hex values) purely so plan 4b's branch
 * builds and tests standalone. Whichever branch merges second deletes its
 * copy of this file and keeps the other's — since the values are identical
 * by construction, that reconciliation is a deletion, not a merge. If you
 * are reading this after both branches have landed and two copies of this
 * file still exist, that is the cleanup that was missed.
 *
 * The six slots were chosen by a colour-science validator run against this
 * app's own chart surfaces (`.card` is `bg-white` / `dark:bg-gray-800`) on
 * the all-pairs pairlist in both light and dark mode: worst CVD (colour
 * vision deficiency) ΔE 9.9 light / 9.3 dark, normal-vision ΔE 19.8 light /
 * 17.2 dark. Do not change a hex value — that revalidation is expensive to
 * redo and silently invalidated by any edit here.
 */

export interface PaletteSlot {
  name: string
  light: string
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

/** FNV-1a hash of `key`, folded into an unsigned 32-bit integer. */
export function fnv1a(key: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h
}

/** The palette slot a value key hashes to, before any de-collision walk. */
export const deriveSlot = (key: string): PaletteSlot =>
  SPLIT_PALETTE[fnv1a(key) % SPLIT_PALETTE.length]!

/**
 * Resolve the colour to render for a SINGLE split value.
 *
 * - `stored === null` derives a slot from `key` and returns that slot's step
 *   for the current theme.
 * - `stored` matching a slot's **light** hex resolves to that slot and
 *   returns its step for `dark` — the database holds one hex (the light
 *   step), so this is what stops an owner's override rendering as a
 *   light-mode colour on a dark chart.
 * - Anything else (a stored hex that matches no slot) is returned verbatim.
 *
 * UNUSED by plan 4b (the `/charts` spending view) as of this branch — its
 * own colour resolution always goes through `@/spending/palette`'s
 * `bands()`, which resolves a whole SET of survivors together and de-collides
 * a stored colour that lands on a slot an earlier survivor already claimed
 * (a case this single-value function has no way to detect, since it never
 * sees its siblings). Calling this instead of `bands()` anywhere in 4b would
 * silently drop that de-collision guarantee. This function exists for the
 * parallel plan-4c vocabulary panel, which resolves one facet value's
 * colour in isolation (no sibling set to de-collide against) — see the file
 * header's ownership note. Left in place, not deleted, so it is not silently
 * dead: covered directly by `splitPalette.spec.ts`.
 */
export function resolveSplitColour(stored: string | null, key: string, dark: boolean): string {
  if (stored === null) {
    const slot = deriveSlot(key)
    return dark ? slot.dark : slot.light
  }
  const slot = SPLIT_PALETTE.find((s) => s.light === stored)
  if (slot) return dark ? slot.dark : slot.light
  return stored
}
