import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AppCheckboxes from '../AppCheckboxes.vue'

const items = [
  { value: 'email', text: 'Email', conditional: true },
  { value: 'sms', text: 'SMS' },
  { value: 'post', text: 'Post' },
]

describe('AppCheckboxes', () => {
  it('renders a fieldset/legend with form-checkbox inputs', () => {
    const wrapper = mount(AppCheckboxes, {
      props: { id: 'contact', legend: 'Contact methods', items, modelValue: [] },
    })

    expect(wrapper.find('fieldset').exists()).toBe(true)
    expect(wrapper.find('legend').text()).toBe('Contact methods')
    expect(wrapper.findAll('input.form-checkbox')).toHaveLength(3)
  })

  it('checking two items v-models a string[] with both values', async () => {
    const wrapper = mount(AppCheckboxes, {
      props: {
        id: 'contact',
        legend: 'Contact',
        items,
        modelValue: [],
        'onUpdate:modelValue': (value: string[]) => wrapper.setProps({ modelValue: value }),
      },
    })

    await wrapper.find('input#contact').setValue(true)
    await flushPromises()
    await wrapper.find('input#contact-2').setValue(true)
    await flushPromises()

    expect(wrapper.props('modelValue')).toEqual(['email', 'sms'])
  })

  it('reveals the conditional when checked and keeps it (hidden) when unchecked', async () => {
    const wrapper = mount(AppCheckboxes, {
      props: {
        id: 'contact',
        legend: 'Contact',
        items,
        modelValue: [],
        'onUpdate:modelValue': (value: string[]) => wrapper.setProps({ modelValue: value }),
      },
      slots: { 'conditional-email': '<input id="email-address" />' },
    })

    // Always rendered; hidden until the item is checked.
    expect(wrapper.find('#conditional-contact').exists()).toBe(true)
    expect((wrapper.get('#conditional-contact').element as HTMLElement).style.display).toBe('none')

    await wrapper.find('input#contact').setValue(true)
    await flushPromises()
    expect((wrapper.get('#conditional-contact').element as HTMLElement).style.display).not.toBe('none')
    expect(wrapper.find('#conditional-contact').find('#email-address').exists()).toBe(true)

    // Unchecking keeps the wrapper in the DOM, hidden (state preserved).
    await wrapper.find('input#contact').setValue(false)
    await flushPromises()
    expect(wrapper.find('#conditional-contact').exists()).toBe(true)
    expect((wrapper.get('#conditional-contact').element as HTMLElement).style.display).toBe('none')
  })

  it('renders the error message', () => {
    const wrapper = mount(AppCheckboxes, {
      props: { id: 'contact', legend: 'Contact', items, errorMessage: 'Select one' },
    })

    expect(wrapper.text()).toContain('Select one')
    const err = wrapper.find('#contact-error')
    expect(err.classes().join(' ')).toContain('text-red-500')
    expect(wrapper.find('fieldset').attributes('aria-describedby')).toBe('contact-error')
  })
})
