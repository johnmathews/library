import { beforeEach, describe, expect, it } from 'vitest'
import { nextTick, ref } from 'vue'
import {
  DASHBOARD_LAYOUT_STORAGE_KEY,
  useDashboardLayout,
} from '../useDashboardLayout'

describe('useDashboardLayout', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to tiles', () => {
    const { layout, effectiveLayout } = useDashboardLayout(ref(true))
    expect(layout.value).toBe('tiles')
    expect(effectiveLayout.value).toBe('tiles')
  })

  it('remembers the chosen layout in localStorage', async () => {
    const { layout } = useDashboardLayout(ref(true))
    layout.value = 'rows'
    await nextTick()
    expect(localStorage.getItem(DASHBOARD_LAYOUT_STORAGE_KEY)).toBe('rows')
    expect(useDashboardLayout(ref(true)).layout.value).toBe('rows')
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

  it('renders an unknown stored value as tiles', () => {
    localStorage.setItem(DASHBOARD_LAYOUT_STORAGE_KEY, 'mosaic')
    expect(useDashboardLayout(ref(true)).effectiveLayout.value).toBe('tiles')
  })
})
