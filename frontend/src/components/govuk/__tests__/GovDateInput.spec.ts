import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import GovDateInput from '../GovDateInput.vue'

function mountDateInput(modelValue: string | null = null) {
  return mount(GovDateInput, {
    props: { id: 'received', legend: 'Date received', modelValue },
  })
}

describe('GovDateInput', () => {
  it('renders three labelled fields following the GOV.UK pattern', () => {
    const wrapper = mountDateInput()

    expect(wrapper.find('fieldset[role="group"]').exists()).toBe(true)
    expect(wrapper.find('.govuk-date-input').attributes('id')).toBe('received')

    const inputs = wrapper.findAll('input.govuk-date-input__input')
    expect(inputs).toHaveLength(3)
    expect(inputs[0]!.attributes('id')).toBe('received-day')
    expect(inputs[0]!.classes()).toContain('govuk-input--width-2')
    expect(inputs[2]!.attributes('id')).toBe('received-year')
    expect(inputs[2]!.classes()).toContain('govuk-input--width-4')
    expect(inputs[0]!.attributes('inputmode')).toBe('numeric')
    expect(wrapper.find('label[for="received-day"]').text()).toBe('Day')
  })

  it('emits a zero-padded ISO date once all three fields are valid', async () => {
    const wrapper = mountDateInput()

    await wrapper.find('#received-day').setValue('9')
    await wrapper.find('#received-month').setValue('6')
    // incomplete date: the model stays null, so nothing is emitted yet
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    await wrapper.find('#received-year').setValue('2026')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['2026-06-09'])
  })

  it('emits null when a valid date becomes impossible', async () => {
    const wrapper = mountDateInput()

    await wrapper.find('#received-day').setValue('31')
    await wrapper.find('#received-month').setValue('1')
    await wrapper.find('#received-year').setValue('2026')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['2026-01-31'])

    await wrapper.find('#received-month').setValue('2') // 31 February
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([null])
  })

  it('populates the fields from an initial model value', () => {
    const wrapper = mountDateInput('2025-12-01')

    expect((wrapper.find('#received-day').element as HTMLInputElement).value).toBe('1')
    expect((wrapper.find('#received-month').element as HTMLInputElement).value).toBe('12')
    expect((wrapper.find('#received-year').element as HTMLInputElement).value).toBe('2025')
  })
})
