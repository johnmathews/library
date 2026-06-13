import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AppDateInput from '../AppDateInput.vue'

function mountDateInput(modelValue: string | null = null) {
  return mount(AppDateInput, {
    props: { id: 'received', legend: 'Date received', modelValue },
  })
}

describe('AppDateInput', () => {
  it('renders three labelled fields following the day/month/year pattern', () => {
    const wrapper = mountDateInput()

    expect(wrapper.find('fieldset[role="group"]').exists()).toBe(true)
    expect(wrapper.find('#received').exists()).toBe(true)

    const inputs = wrapper.findAll('input')
    expect(inputs).toHaveLength(3)
    expect(inputs[0]!.attributes('id')).toBe('received-day')
    expect(inputs[0]!.classes()).toContain('w-14')
    expect(inputs[2]!.attributes('id')).toBe('received-year')
    expect(inputs[2]!.classes()).toContain('w-20')
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

  it('shows the error state with red borders and an error message', () => {
    const wrapper = mount(AppDateInput, {
      props: { id: 'received', legend: 'Date received', errorMessage: 'Enter a date' },
    })
    expect(wrapper.find('#received-error').text()).toContain('Enter a date')
    expect(wrapper.find('#received-day').classes()).toContain('border-red-300')
  })
})
