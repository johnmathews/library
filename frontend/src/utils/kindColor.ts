/**
 * Resolve the tile-border colour for a document kind.
 *
 * Precedence: the user's per-kind override, else the built-in default palette,
 * else `null` — meaning "no accent", so the tile keeps its neutral default
 * border. Colour is keyed by kind *slug* so it never shifts as document counts
 * change and a newly-created kind gets a stable (default or overridden) colour.
 */
import { DEFAULT_KIND_COLORS } from '@/api/settings'

export function resolveKindColor(
  slug: string | null | undefined,
  overrides: Record<string, string> = {},
): string | null {
  if (!slug) return null
  return overrides[slug] ?? DEFAULT_KIND_COLORS[slug] ?? null
}

/** Whether a string is a `#rrggbb` hex colour (the only form we store). */
export function isHexColor(value: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test(value)
}
