import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppBadge from '../AppBadge.vue'

describe('AppBadge', () => {
  it('renders default badge text with pill shape', () => {
    const wrapper = mount(AppBadge, { slots: { default: 'Beta' } })
    expect(wrapper.text()).toBe('Beta')
    expect(wrapper.classes().join(' ')).toContain('rounded-full')
  })
  it('maps green colour to green classes', () => {
    const wrapper = mount(AppBadge, { props: { colour: 'green' }, slots: { default: 'OK' } })
    expect(wrapper.classes().join(' ')).toContain('text-green-700')
  })
  it('falls back to grey classes for an unmapped/absent colour', () => {
    const wrapper = mount(AppBadge, { slots: { default: 'Default' } })
    expect(wrapper.classes().join(' ')).toContain('text-gray-600')
  })
})
