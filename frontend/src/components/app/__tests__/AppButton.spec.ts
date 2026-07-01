import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppButton from '../AppButton.vue'

describe('AppButton', () => {
  it('renders a <button> with violet primary styling and emits click', async () => {
    const wrapper = mount(AppButton, { slots: { default: 'Save' } })
    const btn = wrapper.get('button')
    expect(btn.classes()).toContain('btn')
    expect(btn.classes().join(' ')).toContain('bg-violet-500')
    await btn.trigger('click')
    expect(wrapper.emitted('click')).toBeTruthy()
  })
  it('renders a RouterLink when "to" is provided', () => {
    const wrapper = mount(AppButton, {
      props: { to: '/upload' },
      slots: { default: 'Go' },
      global: { stubs: { RouterLink: { template: '<a><slot/></a>' } } },
    })
    expect(wrapper.find('a').exists()).toBe(true)
  })
  it('renders an <a> when "href" is provided', () => {
    const wrapper = mount(AppButton, { props: { href: 'https://x' }, slots: { default: 'Go' } })
    expect(wrapper.find('a').attributes('href')).toBe('https://x')
  })
  it('passes target through and pairs _blank with rel="noopener" on the <a> branch', () => {
    const wrapper = mount(AppButton, {
      props: { href: 'https://x', target: '_blank' },
      slots: { default: 'Go' },
    })
    const a = wrapper.get('a')
    expect(a.attributes('target')).toBe('_blank')
    expect(a.attributes('rel')).toBe('noopener')
  })
  it('does not add rel when target is not _blank', () => {
    const wrapper = mount(AppButton, {
      props: { href: 'https://x', target: '_self' },
      slots: { default: 'Go' },
    })
    const a = wrapper.get('a')
    expect(a.attributes('target')).toBe('_self')
    expect(a.attributes('rel')).toBeUndefined()
  })
  it('maps the secondary variant to grey border styling', () => {
    const wrapper = mount(AppButton, { props: { variant: 'secondary' }, slots: { default: 'X' } })
    expect(wrapper.get('button').classes().join(' ')).toContain('border-gray-200')
  })
  it('forwards disabled to the button', () => {
    const wrapper = mount(AppButton, { props: { disabled: true }, slots: { default: 'X' } })
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
  })
  it('preventDoubleClick swallows a second click within 1000ms', async () => {
    const wrapper = mount(AppButton, {
      props: { preventDoubleClick: true },
      slots: { default: 'Save' },
    })
    const btn = wrapper.get('button')
    await btn.trigger('click')
    await btn.trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
  })
  it('honors disabled on the <a> branch (aria-disabled + inert styling)', async () => {
    const wrapper = mount(AppButton, {
      props: { href: 'https://x', disabled: true },
      slots: { default: 'Go' },
    })
    const a = wrapper.get('a')
    expect(a.attributes('aria-disabled')).toBe('true')
    expect(a.classes().join(' ')).toContain('pointer-events-none')
    await a.trigger('click')
    expect(wrapper.emitted('click')).toBeFalsy()
  })
})
