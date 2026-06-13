import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AppTextarea from '../AppTextarea.vue'

describe('AppTextarea', () => {
  it('binds the label for/id to the textarea', () => {
    const wrapper = mount(AppTextarea, {
      props: { id: 'notes', label: 'Notes' },
    })

    expect(wrapper.find('label').attributes('for')).toBe('notes')
    expect(wrapper.find('textarea').attributes('id')).toBe('notes')
  })

  it('v-model emits update:modelValue on input', async () => {
    const wrapper = mount(AppTextarea, {
      props: { id: 'notes', label: 'Notes', modelValue: '' },
    })

    await wrapper.find('textarea').setValue('hello')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['hello'])
  })

  it('renders the error message with red border styling', () => {
    const wrapper = mount(AppTextarea, {
      props: { id: 'notes', label: 'Notes', errorMessage: 'Required' },
    })

    expect(wrapper.text()).toContain('Required')
    expect(wrapper.find('#notes-error').exists()).toBe(true)
    expect(wrapper.get('textarea').classes()).toContain('border-red-300')
  })
})
