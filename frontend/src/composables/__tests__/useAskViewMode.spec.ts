import { beforeEach, describe, expect, it } from 'vitest'
import { nextTick, ref } from 'vue'
import { ASK_VIEW_MODE_STORAGE_KEY, useAskViewMode } from '../useAskViewMode'

describe('useAskViewMode', () => {
  beforeEach(() => {
    // No vi.resetModules() here, and that is the point: the composable builds
    // its storage ref inside the function, so clearing storage is enough to
    // isolate a test. If this ever stops being true, something has become a
    // module-level singleton and should be moved back inside.
    localStorage.clear()
  })

  it('defaults to conversation mode', () => {
    const { viewMode, effectiveViewMode, isDocumentMode } = useAskViewMode(ref(true))
    expect(viewMode.value).toBe('conversation')
    expect(effectiveViewMode.value).toBe('conversation')
    expect(isDocumentMode.value).toBe(false)
  })

  it('renders document mode on a large screen once selected', () => {
    const { viewMode, effectiveViewMode, isDocumentMode } = useAskViewMode(ref(true))
    viewMode.value = 'document'
    expect(effectiveViewMode.value).toBe('document')
    expect(isDocumentMode.value).toBe(true)
  })

  it('clamps to conversation on a small screen while KEEPING the preference', () => {
    // The whole reason preference and render decision are separate refs: a
    // phone must not render document mode, but must not forget the choice
    // either, or the desktop toggle silently resets on every phone visit.
    const { viewMode, effectiveViewMode } = useAskViewMode(ref(false))
    viewMode.value = 'document'
    expect(effectiveViewMode.value).toBe('conversation')
    expect(viewMode.value).toBe('document')
  })

  it('follows the screen ref reactively in both directions', async () => {
    const isLargeScreen = ref(true)
    const { viewMode, effectiveViewMode } = useAskViewMode(isLargeScreen)
    viewMode.value = 'document'
    expect(effectiveViewMode.value).toBe('document')

    isLargeScreen.value = false
    await nextTick()
    expect(effectiveViewMode.value).toBe('conversation')

    isLargeScreen.value = true
    await nextTick()
    expect(effectiveViewMode.value).toBe('document')
  })

  it('persists the preference under the library: key convention', async () => {
    const { viewMode } = useAskViewMode(ref(true))
    viewMode.value = 'document'
    await nextTick()
    expect(ASK_VIEW_MODE_STORAGE_KEY).toBe('library:ask-view-mode')
    expect(localStorage.getItem(ASK_VIEW_MODE_STORAGE_KEY)).toContain('document')
  })

  it('honours a preference stored before the composable is called', () => {
    localStorage.setItem(ASK_VIEW_MODE_STORAGE_KEY, 'document')
    const { viewMode, effectiveViewMode } = useAskViewMode(ref(true))
    expect(viewMode.value).toBe('document')
    expect(effectiveViewMode.value).toBe('document')
  })

  it('shares the preference across instances', async () => {
    const first = useAskViewMode(ref(true))
    first.viewMode.value = 'document'
    await nextTick()
    const second = useAskViewMode(ref(true))
    expect(second.viewMode.value).toBe('document')
  })

  it('exposes the two modes in render order', () => {
    const { modes } = useAskViewMode(ref(true))
    expect(modes.map((m) => m.value)).toEqual(['conversation', 'document'])
  })
})
