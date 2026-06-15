import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import FilterPill from '../FilterPill.vue'

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

function mountPill(props: Record<string, unknown> = {}): VueWrapper {
  return mount(FilterPill, {
    attachTo: document.body,
    props: { label: 'Kind', open: false, ...props },
    slots: { default: '<div data-testid="panel-body">panel</div>' },
  })
}

describe('FilterPill', () => {
  let wrapper: VueWrapper | undefined
  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    document.body.replaceChildren()
    vi.restoreAllMocks()
  })

  it('shows the label, and the value label + active styling when active', () => {
    wrapper = mountPill({ active: true, valueLabel: 'Invoice' })
    const button = wrapper.get('[data-testid="filter-pill-button"]')
    expect(button.text()).toContain('Kind')
    expect(button.text()).toContain('Invoice')
    expect(button.text()).toContain('(active)')
  })

  it('emits update:open when the button is clicked', async () => {
    wrapper = mountPill({ open: false })
    await wrapper.get('[data-testid="filter-pill-button"]').trigger('click')
    expect(wrapper.emitted('update:open')?.[0]).toEqual([true])
  })

  it('renders the panel only when open', async () => {
    wrapper = mountPill({ open: false })
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(false)
    await wrapper.setProps({ open: true })
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(true)
  })

  it('emits update:open=false on Escape and on outside click', async () => {
    wrapper = mountPill({ open: true })
    await wrapper.get('[data-testid="filter-pill-button"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
    expect(document.activeElement).toBe(
      wrapper.get('[data-testid="filter-pill-button"]').element,
    )

    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
  })

  // jsdom reports innerWidth = 1024; centre past 512 is the "right half".
  it('right-aligns the panel when the pill is in the right half of the viewport', async () => {
    wrapper = mountPill({ open: false })
    stubRect(wrapper.element as HTMLElement, 800, 80) // centre 840 > 512
    await wrapper.setProps({ open: true })
    await wrapper.vm.$nextTick()
    const panel = wrapper.get('[data-testid="filter-pill-panel"]')
    expect(panel.classes()).toContain('right-0')
    expect(panel.classes()).not.toContain('left-0')
  })

  it('left-aligns the panel when the pill is in the left half of the viewport', async () => {
    wrapper = mountPill({ open: false })
    stubRect(wrapper.element as HTMLElement, 20, 80) // centre 60 < 512
    await wrapper.setProps({ open: true })
    await wrapper.vm.$nextTick()
    const panel = wrapper.get('[data-testid="filter-pill-panel"]')
    expect(panel.classes()).toContain('left-0')
    expect(panel.classes()).not.toContain('right-0')
  })
})
