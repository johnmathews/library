import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppBadge from '../AppBadge.vue'

describe('AppBadge', () => {
  it('renders default badge text with pill shape', () => {
    const wrapper = mount(AppBadge, { slots: { default: 'Beta' } })
    expect(wrapper.text()).toBe('Beta')
    expect(wrapper.classes().join(' ')).toContain('rounded-full')
  })
  it('shows a finished (green) state as the neutral chip with a check icon', () => {
    const wrapper = mount(AppBadge, { props: { colour: 'green' }, slots: { default: 'OK' } })
    expect(wrapper.classes()).toContain('bg-tile')
    expect(wrapper.classes()).toContain('text-ink')
    expect(wrapper.find('[data-testid="badge-check"]').exists()).toBe(true)
  })
  it('keeps colour for the two states that mean something: needs you and failed', () => {
    const yellow = mount(AppBadge, { props: { colour: 'yellow' }, slots: { default: 'Needs review' } })
    expect(yellow.classes()).toContain('bg-attention-tint')
    const red = mount(AppBadge, { props: { colour: 'red' }, slots: { default: 'Failed' } })
    expect(red.classes()).toContain('bg-deficit-tint')
    expect(red.find('[data-testid="badge-check"]').exists()).toBe(false)
  })
  it('falls back to the neutral chip for an unmapped/absent colour', () => {
    const wrapper = mount(AppBadge, { slots: { default: 'Default' } })
    expect(wrapper.classes()).toContain('bg-tile')
    expect(wrapper.find('[data-testid="badge-check"]').exists()).toBe(false)
  })
})
