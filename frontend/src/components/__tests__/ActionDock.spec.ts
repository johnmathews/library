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

  // The sticky rail anchors to `top-16` (clearing AppHeader's fixed 4rem
  // height) or `bottom-0`; the absolutely-positioned pill row anchors to the
  // matching edge of the zero-height rail and carries the horizontal justify.
  // See the positioning note in ActionDock.vue.
  it.each([
    ['top-left', 'top-16', 'top-0', 'justify-start'],
    ['top-middle', 'top-16', 'top-0', 'justify-center'],
    ['top-right', 'top-16', 'top-0', 'justify-end'],
    ['bottom-left', 'bottom-0', 'bottom-0', 'justify-start'],
    ['bottom-right', 'bottom-0', 'bottom-0', 'justify-end'],
  ])('positions the dock rail and row for %s', (pos, edgeClass, rowAnchor, justifyClass) => {
    const w = mountDock(pos as DockPosition)
    const wrapper = w.find('[data-testid="action-dock-wrapper"]')
    expect(wrapper.classes()).toContain(edgeClass)
    // The rail reserves no vertical space, so mounting the dock never shifts
    // the surrounding content.
    expect(wrapper.classes()).toContain('h-0')

    const row = w.find('[data-testid="action-dock-row"]')
    expect(row.classes()).toContain(rowAnchor)
    expect(row.classes()).toContain(justifyClass)
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
