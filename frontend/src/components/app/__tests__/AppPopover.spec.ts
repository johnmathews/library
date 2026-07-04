import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import AppPopover from '../AppPopover.vue'

// A minimal host: a trigger button (bound to `triggerRef`) + a panel body.
// `open` is controlled, so the host mirrors `update:open` back into the prop —
// the same contract every real call site uses.
const Host = defineComponent({
  components: { AppPopover },
  props: {
    initialOpen: { type: Boolean, default: false },
    align: { type: String, default: 'left' },
    panelClass: { type: String, default: '' },
  },
  data() {
    return { open: this.initialOpen }
  },
  template: `
    <AppPopover
      :open="open"
      :align="align"
      :panel-class="panelClass"
      :panel-attrs="{ 'data-testid': 'panel' }"
      @update:open="open = $event"
    >
      <template #trigger="{ open, toggle, triggerRef }">
        <button :ref="triggerRef" data-testid="trigger" :aria-expanded="open" @click="toggle">
          Menu
        </button>
      </template>
      <div data-testid="panel-body">body</div>
    </AppPopover>
  `,
})

function stubRect(el: HTMLElement, left: number, width: number): void {
  vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({
    left,
    width,
    right: left + width,
    top: 0,
    bottom: 0,
    height: 0,
    x: left,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect)
}

function mountHost(props: Record<string, unknown> = {}): VueWrapper {
  return mount(Host, { attachTo: document.body, props })
}

describe('AppPopover', () => {
  let wrapper: VueWrapper | undefined
  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    document.body.replaceChildren()
    vi.restoreAllMocks()
  })

  it('renders the panel only when open', async () => {
    wrapper = mountHost({ initialOpen: false })
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(false)
    await wrapper.get('[data-testid="trigger"]').trigger('click')
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(true)
  })

  it('toggles open on trigger click (both directions)', async () => {
    wrapper = mountHost({ initialOpen: false })
    const trigger = wrapper.get('[data-testid="trigger"]')
    await trigger.trigger('click')
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(true)
    await trigger.trigger('click')
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(false)
  })

  it('closes on Escape and returns focus to the trigger', async () => {
    wrapper = mountHost({ initialOpen: true })
    await wrapper.get('[data-testid="trigger"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(false)
    expect(document.activeElement).toBe(wrapper.get('[data-testid="trigger"]').element)
  })

  it('closes on an outside mousedown', async () => {
    wrapper = mountHost({ initialOpen: true })
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(true)
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(false)
  })

  it('does not close on a mousedown inside the popover', async () => {
    wrapper = mountHost({ initialOpen: true })
    const panel = wrapper.get('[data-testid="panel-body"]').element
    panel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(true)
  })

  it('passes panel attrs and z-token/chrome through to the panel', async () => {
    wrapper = mountHost({ initialOpen: true, panelClass: 'w-64 p-3' })
    const panel = wrapper.get('[data-testid="panel"]')
    expect(panel.classes()).toContain('z-popover')
    expect(panel.classes()).toContain('w-64')
    expect(panel.classes()).toContain('p-3')
  })

  it('align="right" pins the panel right, "left" pins it left', async () => {
    wrapper = mountHost({ initialOpen: true, align: 'right' })
    expect(wrapper.get('[data-testid="panel"]').classes()).toContain('right-0')
    wrapper.unmount()

    wrapper = mountHost({ initialOpen: true, align: 'left' })
    expect(wrapper.get('[data-testid="panel"]').classes()).toContain('left-0')
  })

  it('align="auto" right-aligns in the right half of the viewport', async () => {
    // jsdom innerWidth = 1024; centre past 512 is the "right half".
    wrapper = mountHost({ initialOpen: false, align: 'auto' })
    const root = wrapper.get('[data-testid="trigger"]').element.parentElement as HTMLElement
    stubRect(root, 800, 80) // centre 840 > 512
    await wrapper.get('[data-testid="trigger"]').trigger('click')
    await wrapper.vm.$nextTick()
    const panel = wrapper.get('[data-testid="panel"]')
    expect(panel.classes()).toContain('right-0')
    expect(panel.classes()).not.toContain('left-0')
  })

  it('align="auto" left-aligns in the left half of the viewport', async () => {
    wrapper = mountHost({ initialOpen: false, align: 'auto' })
    const root = wrapper.get('[data-testid="trigger"]').element.parentElement as HTMLElement
    stubRect(root, 20, 80) // centre 60 < 512
    await wrapper.get('[data-testid="trigger"]').trigger('click')
    await wrapper.vm.$nextTick()
    const panel = wrapper.get('[data-testid="panel"]')
    expect(panel.classes()).toContain('left-0')
    expect(panel.classes()).not.toContain('right-0')
  })

  it('align="none" adds neither alignment class', async () => {
    wrapper = mountHost({ initialOpen: true, align: 'none' })
    const panel = wrapper.get('[data-testid="panel"]')
    expect(panel.classes()).not.toContain('left-0')
    expect(panel.classes()).not.toContain('right-0')
  })
})
