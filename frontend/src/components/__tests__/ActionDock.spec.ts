import { beforeEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ActionDock from '../ActionDock.vue'
import { useAuthStore } from '@/stores/auth'
import { useMetadataEditMode } from '@/composables/useMetadataEditMode'
import type { DockPosition } from '@/api/settings'

/** Mounts ActionDock with the auth store's dockPosition set to `pos`. */
function mountDock(pos: DockPosition) {
  const auth = useAuthStore()
  auth.user = {
    id: 1,
    username: 'a',
    display_name: 'A',
    is_admin: false,
    preferences: { dashboard_fields: ['kind'], dock_position: pos },
  }
  return mount(ActionDock, { props: { askHref: '/ask?q=hello' } })
}

describe('ActionDock', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    // useMetadataEditMode is a module singleton — reset between tests so a
    // toggle in one test never leaks into the next.
    useMetadataEditMode().setEditMode(false)
  })

  // AppHeader is sticky with a fixed 4rem (h-16) height in the same scroll
  // container, so top-anchored positions need a top-16 offset to clear it;
  // bottom-anchored positions have no header to clear and stick flush to
  // bottom-0 (see the header-offset note in ActionDock.vue).
  it.each([
    ['top-left', 'top-16', 'justify-start'],
    ['top-middle', 'top-16', 'justify-center'],
    ['top-right', 'top-16', 'justify-end'],
    ['bottom-left', 'bottom-0', 'justify-start'],
    ['bottom-right', 'bottom-0', 'justify-end'],
  ])('positions the dock wrapper for %s', (pos, edgeClass, justifyClass) => {
    const w = mountDock(pos as DockPosition)
    const wrapper = w.find('[data-testid="action-dock-wrapper"]')
    expect(wrapper.classes()).toContain(edgeClass)
    expect(wrapper.classes()).toContain(justifyClass)
  })

  it('renders the Ask anchor pointing at askHref and toggles metadata edit mode', async () => {
    const w = mountDock('top-right')
    const ask = w.find('[data-testid="action-dock-ask"]')
    expect(ask.attributes('href')).toBe('/ask?q=hello')
    expect(ask.attributes('target')).toBe('_blank')

    expect(useMetadataEditMode().editMode.value).toBe(false)
    await w.find('[data-testid="action-dock-edit-toggle"]').trigger('click')
    expect(useMetadataEditMode().editMode.value).toBe(true)
    expect(w.find('[data-testid="action-dock-edit-toggle"]').text()).toContain('Done')
  })
})
