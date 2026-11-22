import { computed, type ComputedRef, type Ref } from 'vue'
import { useStorage } from '@vueuse/core'

/**
 * How the Documents dashboard lays documents out: `tiles` (the default grid with
 * a page preview), `rows` (one column of wide, thin cards), `table` (one column
 * per chosen field) or `list` (plain lines, no cards).
 *
 * The four are a trial (2026-11): the preview tiles stopped helping the owner
 * stay organised, and the layout that does is decided by using each one. It is
 * a choice about this screen, so it lives in localStorage under the app's
 * `library:` key convention (docs/frontend-view-principles.md §4).
 *
 * Two refs, as in `useAskViewMode`:
 *
 *   - `layout` is the stored *preference*; the Layout control reflects it and
 *     it keeps its value at any width.
 *   - `effectiveLayout` is the *render decision*: the table needs room, so
 *     where the list's container is narrow it renders as the list. The stored
 *     value is never rewritten, so widening the window brings the table back.
 *
 * The caller passes `isWide` from a measurement of the container the list sits
 * in, not a viewport media query: that container is the viewport minus the
 * sidebar, so a viewport breakpoint would switch at the wrong width.
 */
export type DashboardLayout = 'tiles' | 'rows' | 'table' | 'list'

/** Storage key for the per-machine dashboard layout. */
export const DASHBOARD_LAYOUT_STORAGE_KEY = 'library:doc-layout'

/** Below this container width (px) the table renders as the list. */
export const TABLE_MIN_WIDTH = 640

export const DASHBOARD_LAYOUTS: readonly { value: DashboardLayout; label: string }[] = [
  { value: 'tiles', label: 'Tiles' },
  { value: 'rows', label: 'Rows' },
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
  const layout = useStorage<DashboardLayout>(DASHBOARD_LAYOUT_STORAGE_KEY, 'tiles')

  const effectiveLayout = computed<DashboardLayout>(() => {
    // A value this build does not know (hand-edited, or from a later build that
    // was rolled back) renders as the default rather than as nothing.
    const chosen: DashboardLayout = KNOWN.has(layout.value) ? layout.value : 'tiles'
    return chosen === 'table' && !isWide.value ? 'list' : chosen
  })

  return { layout, effectiveLayout }
}
