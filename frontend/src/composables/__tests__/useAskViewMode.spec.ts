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

  it('defaults to document mode on a wide screen', () => {
    const { viewMode, effectiveViewMode, isDocumentMode } = useAskViewMode(ref(true))
    expect(viewMode.value).toBe('document')
    expect(effectiveViewMode.value).toBe('document')
    expect(isDocumentMode.value).toBe(true)
  })

  it('defaults to conversation RENDERING on a narrow screen', () => {
    // The default preference is still `document` — the clamp is what makes a
    // phone render bubbles, not a different stored value. Asserting both halves
    // here is the point: a clamp that wrote the fallback back into storage
    // would pass a naive check and silently reset every desktop user who ever
    // opened the app on a phone.
    const { viewMode, effectiveViewMode, isDocumentMode } = useAskViewMode(ref(false))
    expect(viewMode.value).toBe('document')
    expect(effectiveViewMode.value).toBe('conversation')
    expect(isDocumentMode.value).toBe(false)
  })

  it('renders conversation mode on a large screen once selected', () => {
    const { viewMode, effectiveViewMode, isDocumentMode } = useAskViewMode(ref(true))
    viewMode.value = 'conversation'
    expect(effectiveViewMode.value).toBe('conversation')
    expect(isDocumentMode.value).toBe(false)
  })

  it('keeps an explicit conversation preference on a large screen', () => {
    // The mirror of the clamp: an explicit opt-OUT of the new default must
    // survive too, or the default silently overrides a deliberate choice.
    const { viewMode, effectiveViewMode } = useAskViewMode(ref(true))
    viewMode.value = 'conversation'
    expect(effectiveViewMode.value).toBe('conversation')
    expect(viewMode.value).toBe('conversation')
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
    viewMode.value = 'conversation'
    await nextTick()
    expect(ASK_VIEW_MODE_STORAGE_KEY).toBe('library:ask-view-mode')
    expect(localStorage.getItem(ASK_VIEW_MODE_STORAGE_KEY)).toContain('conversation')
  })

  it('honours a preference stored before the composable is called', () => {
    // Seeds the NON-default so this actually proves storage is read. Seeding
    // 'document' would pass even if the stored value were ignored entirely.
    localStorage.setItem(ASK_VIEW_MODE_STORAGE_KEY, 'conversation')
    const { viewMode, effectiveViewMode } = useAskViewMode(ref(true))
    expect(viewMode.value).toBe('conversation')
    expect(effectiveViewMode.value).toBe('conversation')
  })

  it('shares the preference across instances', async () => {
    const first = useAskViewMode(ref(true))
    first.viewMode.value = 'conversation'
    await nextTick()
    const second = useAskViewMode(ref(true))
    expect(second.viewMode.value).toBe('conversation')
  })

  it('exposes the two modes in render order', () => {
    const { modes } = useAskViewMode(ref(true))
    expect(modes.map((m) => m.value)).toEqual(['conversation', 'document'])
  })
})
