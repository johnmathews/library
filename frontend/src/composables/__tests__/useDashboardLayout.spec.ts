import { beforeEach, describe, expect, it } from 'vitest'
import { nextTick, ref } from 'vue'
import {
  DASHBOARD_LAYOUT_STORAGE_KEY,
  TABLE_WIDTH_MARGIN,
  nextIsWide,
  tableMinWidth,
  useDashboardLayout,
} from '../useDashboardLayout'

describe('useDashboardLayout', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to rows', () => {
    const { layout, effectiveLayout } = useDashboardLayout(ref(true))
    expect(layout.value).toBe('rows')
    expect(effectiveLayout.value).toBe('rows')
  })

  it('stores the choice under a versioned key, ignoring the trial key', async () => {
    // Every browser that opened the dashboard during the trial holds `tiles`
    // under the first key, chosen or not; the new default must reach it.
    expect(DASHBOARD_LAYOUT_STORAGE_KEY).toBe('library:doc-layout-v2')
    localStorage.setItem('library:doc-layout', 'tiles')
    const { layout } = useDashboardLayout(ref(true))
    expect(layout.value).toBe('rows')
    layout.value = 'list'
    await nextTick()
    expect(localStorage.getItem('library:doc-layout-v2')).toBe('list')
    expect(localStorage.getItem('library:doc-layout')).toBe('tiles')
  })

  it('remembers the chosen layout in localStorage', async () => {
    const { layout } = useDashboardLayout(ref(true))
    layout.value = 'tiles'
    await nextTick()
    expect(localStorage.getItem(DASHBOARD_LAYOUT_STORAGE_KEY)).toBe('tiles')
    expect(useDashboardLayout(ref(true)).layout.value).toBe('tiles')
  })

  it('renders the table as the list when narrow, without changing the stored choice', async () => {
    const isWide = ref(true)
    const { layout, effectiveLayout } = useDashboardLayout(isWide)
    layout.value = 'table'
    expect(effectiveLayout.value).toBe('table')

    isWide.value = false
    expect(effectiveLayout.value).toBe('list')
    await nextTick()
    expect(layout.value).toBe('table')
    expect(localStorage.getItem(DASHBOARD_LAYOUT_STORAGE_KEY)).toBe('table')

    isWide.value = true
    expect(effectiveLayout.value).toBe('table')
  })

  it('leaves the other layouts alone when narrow', () => {
    const isWide = ref(false)
    const { layout, effectiveLayout } = useDashboardLayout(isWide)
    for (const value of ['tiles', 'rows', 'list'] as const) {
      layout.value = value
      expect(effectiveLayout.value).toBe(value)
    }
  })

  it('renders an unknown stored value as rows', () => {
    localStorage.setItem(DASHBOARD_LAYOUT_STORAGE_KEY, 'mosaic')
    expect(useDashboardLayout(ref(true)).effectiveLayout.value).toBe('rows')
  })

  it('needs more width for the table as field columns are added, never under 640px', () => {
    expect(tableMinWidth(0)).toBe(640)
    expect(tableMinWidth(3)).toBe(640)
    // Six fields fitted a 960px area exactly in the browser and scrolled at 880px.
    expect(tableMinWidth(6)).toBeGreaterThan(880)
    expect(tableMinWidth(6)).toBeLessThanOrEqual(960)
    expect(tableMinWidth(8)).toBeGreaterThan(tableMinWidth(6))
  })

  describe('nextIsWide', () => {
    const MIN = 912

    it('is wide before the area is measured', () => {
      expect(nextIsWide(0, MIN, false)).toBe(true)
    })

    it('is narrow below the minimum, whatever it was', () => {
      expect(nextIsWide(MIN - 1, MIN, true)).toBe(false)
      expect(nextIsWide(MIN - 1, MIN, false)).toBe(false)
    })

    it('is wide from the minimum plus the margin, whatever it was', () => {
      expect(nextIsWide(MIN + TABLE_WIDTH_MARGIN, MIN, false)).toBe(true)
      expect(nextIsWide(MIN + TABLE_WIDTH_MARGIN, MIN, true)).toBe(true)
    })

    it('keeps its last answer inside the band', () => {
      expect(nextIsWide(MIN, MIN, true)).toBe(true)
      expect(nextIsWide(MIN, MIN, false)).toBe(false)
      expect(nextIsWide(MIN + TABLE_WIDTH_MARGIN - 1, MIN, false)).toBe(false)
    })

    it('settles when a classic scrollbar comes and goes with the layout', () => {
      // The page scrolls under the table but not under the list: showing the
      // table adds a 17px scrollbar and showing the list removes it again.
      const SCROLLBAR = 17
      expect(TABLE_WIDTH_MARGIN).toBeGreaterThan(SCROLLBAR)
      for (const start of [MIN - 5, MIN, MIN + 5, MIN + 16, MIN + 17, MIN + 30]) {
        let wide = false
        const seen: boolean[] = []
        for (let i = 0; i < 6; i++) {
          const width = wide ? start - SCROLLBAR : start
          wide = nextIsWide(width, MIN, wide)
          seen.push(wide)
        }
        // After the first step nothing changes.
        expect(new Set(seen.slice(1)).size, `start ${start}: ${seen.join(',')}`).toBe(1)
      }
    })
  })
})
