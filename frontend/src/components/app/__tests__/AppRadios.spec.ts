import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AppRadios from '../AppRadios.vue'

const items = [
  { value: 'email', text: 'Email', conditional: true },
  { value: 'phone', text: 'Phone' },
]

describe('AppRadios', () => {
  it('renders a fieldset/legend with form-radio inputs', () => {
    const wrapper = mount(AppRadios, {
      props: { id: 'contact', legend: 'How should we contact you?', items, modelValue: '' },
    })

    expect(wrapper.find('fieldset').exists()).toBe(true)
    expect(wrapper.find('legend').text()).toBe('How should we contact you?')

    const inputs = wrapper.findAll('input.form-radio')
    expect(inputs).toHaveLength(2)
    expect(inputs[0]!.attributes('id')).toBe('contact')
    expect(inputs[1]!.attributes('id')).toBe('contact-2')
    expect(inputs[0]!.attributes('name')).toBe('contact')
    // Mosaic wraps each input in its label; assert the radio sits inside a <label>.
    expect(inputs[0]!.element.closest('label')).not.toBeNull()
    expect(wrapper.findAll('label')[0]!.text()).toBe('Email')
  })

  it('selecting a radio emits update:modelValue and reveals the conditional', async () => {
    const wrapper = mount(AppRadios, {
      props: {
        id: 'contact',
        legend: 'Contact',
        items,
        modelValue: '',
        'onUpdate:modelValue': (value: string) => wrapper.setProps({ modelValue: value }),
      },
      slots: { 'conditional-email': '<input id="email-address" />' },
    })

    // The conditional wrapper is always in the DOM; hidden (v-show) until selected.
    expect(wrapper.find('#conditional-contact').exists()).toBe(true)
    expect((wrapper.get('#conditional-contact').element as HTMLElement).style.display).toBe('none')

    await wrapper.find('input#contact').setValue(true)
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['email'])
    expect((wrapper.get('#conditional-contact').element as HTMLElement).style.display).not.toBe('none')
    expect(wrapper.find('#conditional-contact').find('#email-address').exists()).toBe(true)

    // Deselecting keeps the wrapper in the DOM but hides it (state preserved).
    await wrapper.find('input#contact-2').setValue(true)
    await flushPromises()
    expect(wrapper.find('#conditional-contact').exists()).toBe(true)
    expect((wrapper.get('#conditional-contact').element as HTMLElement).style.display).toBe('none')
  })

  it('marks the group as errored', () => {
    const wrapper = mount(AppRadios, {
      props: { id: 'contact', legend: 'Contact', items, errorMessage: 'Select one' },
    })
    expect(wrapper.text()).toContain('Select one')
    const err = wrapper.find('#contact-error')
    expect(err.classes().join(' ')).toContain('text-red-500')
    expect(wrapper.find('fieldset').attributes('aria-describedby')).toBe('contact-error')
  })
})
