import { computed, type ComputedRef, type Ref } from 'vue'
import { useStorage } from '@vueuse/core'

/**
 * How the Documents dashboard lays documents out: `rows` (the default: one
 * column of wide, thin cards), `tiles` (a grid with a page preview), `table`
 * (one column per chosen field) or `list` (plain lines, no cards).
 *
 * The four began as a trial (2026-11, PR #248). The owner chose Rows as the
 * default on every device and kept the others as options (#246, journal
 * 261127-dashboard-rows-default.md). It is a choice about this screen, so it
 * lives in localStorage under the app's `library:` key convention
 * (docs/frontend-view-principles.md §4).
 *
 * Two refs, as in `useAskViewMode`:
 *
 *   - `layout` is the stored *preference*; the Layout control reflects it and
 *     it keeps its value at any width.
 *   - `effectiveLayout` is the *render decision*: the table needs room (more
 *     with more field columns, `tableMinWidth`), so where the container is
 *     narrower than that it renders as the list. The stored value is never
 *     rewritten, so widening the window brings the table back.
 *
 * The caller passes `isWide` from a measurement of the container the list sits
 * in, not a viewport media query: that container is the viewport minus the
 * sidebar, so a viewport breakpoint would switch at the wrong width. It decides
 * with `nextIsWide`, which holds its answer inside a small band so a scrollbar
 * appearing or disappearing cannot flip the layout back and forth (#247).
 */
export type DashboardLayout = 'tiles' | 'rows' | 'table' | 'list'

/**
 * Storage key for the per-machine dashboard layout. Versioned because the
 * default changed from tiles to rows: the first key holds `tiles` on every
 * browser that ever opened the dashboard, whether or not anyone chose it
 * (docs/frontend-view-principles.md §4). `library:doc-layout` lapses unread.
 */
export const DASHBOARD_LAYOUT_STORAGE_KEY = 'library:doc-layout-v2'

/** What a browser with nothing stored shows. */
export const DEFAULT_DASHBOARD_LAYOUT: DashboardLayout = 'rows'

/**
 * The narrowest container (px) the table fits without scrolling sideways, for
 * a given number of field columns: room for the title column plus about 112px
 * a field, never under 640. Measured in a browser (2026-11-22): six fields fit
 * a 960px area exactly and scrolled 31px at 880px; this gives 912px for six.
 * Below it the table renders as the list.
 */
export function tableMinWidth(fieldCount: number): number {
  return Math.max(640, 240 + 112 * fieldCount)
}

/**
 * How far past `tableMinWidth` the area must grow before the list turns back
 * into the table. More than a classic scrollbar (17px on Windows, 15px on
 * macOS with "Show scroll bars: Always"): switching layouts can change the
 * page's height, which adds or removes the scrollbar, which changes the
 * area's width by that much. Inside the band the last answer stands (#247).
 */
export const TABLE_WIDTH_MARGIN = 24

/**
 * Whether the table fits an area `width` px wide that needs `minWidth`, given
 * the previous answer. Below the minimum: no. At the minimum plus the margin
 * or more: yes. In between: unchanged. A width of 0 means not measured yet
 * (and jsdom, which has no layout), and counts as wide.
 */
export function nextIsWide(width: number, minWidth: number, wasWide: boolean): boolean {
  if (width === 0) return true
  if (width < minWidth) return false
  if (width >= minWidth + TABLE_WIDTH_MARGIN) return true
  return wasWide
}

export const DASHBOARD_LAYOUTS: readonly { value: DashboardLayout; label: string }[] = [
  { value: 'rows', label: 'Rows' },
  { value: 'tiles', label: 'Tiles' },
  { value: 'table', label: 'Table' },
  { value: 'list', label: 'List' },
]

const KNOWN = new Set<string>(DASHBOARD_LAYOUTS.map((l) => l.value))

export interface DashboardLayoutApi {
  /** The persisted, two-way bindable preference. Not clamped. */
  layout: Ref<DashboardLayout>
  /** What the dashboard renders: the preference, with the table clamped to the list when narrow. */
  effectiveLayout: ComputedRef<DashboardLayout>
}

export function useDashboardLayout(isWide: Ref<boolean>): DashboardLayoutApi {
  const layout = useStorage<DashboardLayout>(DASHBOARD_LAYOUT_STORAGE_KEY, DEFAULT_DASHBOARD_LAYOUT)

  const effectiveLayout = computed<DashboardLayout>(() => {
    // A value this build does not know (hand-edited, or from a later build that
    // was rolled back) renders as the default rather than as nothing.
    const chosen: DashboardLayout = KNOWN.has(layout.value) ? layout.value : DEFAULT_DASHBOARD_LAYOUT
    return chosen === 'table' && !isWide.value ? 'list' : chosen
  })

  return { layout, effectiveLayout }
}
