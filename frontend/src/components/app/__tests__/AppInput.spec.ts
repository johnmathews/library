import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppInput from '../AppInput.vue'

describe('AppInput', () => {
  it('binds label to input and v-models value', async () => {
    const wrapper = mount(AppInput, { props: { id: 'q', label: 'Query', modelValue: '' } })
    expect(wrapper.get('label').attributes('for')).toBe('q')
    expect(wrapper.get('input').classes()).toContain('form-input')
    await wrapper.get('input').setValue('hello')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['hello'])
  })
  it('shows the error message and marks the input', () => {
    // GovInput's error prop is `errorMessage`; AppInput preserves that contract.
    const wrapper = mount(AppInput, {
      props: { id: 'q', label: 'Query', modelValue: '', errorMessage: 'Required' },
    })
    expect(wrapper.text()).toContain('Required')
    expect(wrapper.get('input').classes().join(' ')).toContain('border-red-300')
    expect(wrapper.get('input').attributes('aria-invalid')).toBe('true')
    expect(wrapper.get('input').attributes('aria-describedby')).toBe('q-error')
  })
  it('renders a hint wired via aria-describedby', () => {
    const wrapper = mount(AppInput, {
      props: { id: 'q', label: 'Query', modelValue: '', hint: 'Type a title' },
    })
    expect(wrapper.find('#q-hint').text()).toBe('Type a title')
    expect(wrapper.get('input').attributes('aria-describedby')).toBe('q-hint')
  })
})
