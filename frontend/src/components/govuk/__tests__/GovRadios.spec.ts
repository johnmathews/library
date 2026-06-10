import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import GovRadios from '../GovRadios.vue'

const items = [
  { value: 'email', text: 'Email', conditional: true },
  { value: 'phone', text: 'Phone' },
]

describe('GovRadios', () => {
  it('renders the GOV.UK radios structure', () => {
    const wrapper = mount(GovRadios, {
      props: { id: 'contact', legend: 'How should we contact you?', items, modelValue: '' },
    })

    expect(wrapper.find('fieldset.govuk-fieldset').exists()).toBe(true)
    expect(wrapper.find('legend.govuk-fieldset__legend').text()).toBe('How should we contact you?')
    expect(wrapper.find('.govuk-radios').attributes('data-module')).toBe('govuk-radios')

    const inputs = wrapper.findAll('input.govuk-radios__input')
    expect(inputs).toHaveLength(2)
    expect(inputs[0]!.attributes('id')).toBe('contact')
    expect(inputs[1]!.attributes('id')).toBe('contact-2')
    expect(inputs[0]!.attributes('name')).toBe('contact')
    expect(wrapper.find('label[for="contact"]').classes()).toContain('govuk-radios__label')
  })

  it('hides the conditional reveal until its radio is selected', async () => {
    const wrapper = mount(GovRadios, {
      props: {
        id: 'contact',
        legend: 'Contact',
        items,
        modelValue: '',
        'onUpdate:modelValue': (value: string) => wrapper.setProps({ modelValue: value }),
      },
      slots: { 'conditional-email': '<input id="email-address" />' },
    })

    const conditional = wrapper.find('#conditional-contact')
    expect(conditional.classes()).toContain('govuk-radios__conditional--hidden')
    expect(wrapper.find('input#contact').attributes('data-aria-controls')).toBe(
      'conditional-contact',
    )

    await wrapper.find('input#contact').setValue(true)

    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['email'])
    expect(conditional.classes()).not.toContain('govuk-radios__conditional--hidden')
    expect(wrapper.find('input#contact').attributes('aria-expanded')).toBe('true')

    await wrapper.find('input#contact-2').setValue(true)
    expect(conditional.classes()).toContain('govuk-radios__conditional--hidden')
  })

  it('marks the group as errored', () => {
    const wrapper = mount(GovRadios, {
      props: { id: 'contact', legend: 'Contact', items, errorMessage: 'Select one' },
    })
    expect(wrapper.find('.govuk-form-group').classes()).toContain('govuk-form-group--error')
    expect(wrapper.find('.govuk-error-message').text()).toContain('Select one')
    expect(wrapper.find('fieldset').attributes('aria-describedby')).toBe('contact-error')
  })
})
