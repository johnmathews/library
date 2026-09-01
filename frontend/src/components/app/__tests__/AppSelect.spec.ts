import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AppSelect from '../AppSelect.vue'

const items = [
  { value: '', text: 'Choose' },
  { value: 'a', text: 'Apple' },
  { value: 'b', text: 'Banana' },
]

describe('AppSelect', () => {
  it('renders options from items and binds label for/id', () => {
    const wrapper = mount(AppSelect, {
      props: { id: 'fruit', label: 'Fruit', items },
    })

    const options = wrapper.findAll('option')
    expect(options).toHaveLength(3)
    expect(options[1]!.element.value).toBe('a')
    expect(options[1]!.text()).toBe('Apple')

    expect(wrapper.find('label').attributes('for')).toBe('fruit')
    expect(wrapper.find('select').attributes('id')).toBe('fruit')
  })

  // A bare `data-testid` on <AppSelect> lands on the wrapper <div>, and
  // `.setValue()` on a div fails. AppInput carries the same prop for the same
  // reason; this pins the select's half of that contract.
  it('puts testid on the inner select, not the wrapper', () => {
    const wrapper = mount(AppSelect, {
      props: { id: 'fruit', label: 'Fruit', items, testid: 'fruit-select' },
    })

    expect(wrapper.get('select').attributes('data-testid')).toBe('fruit-select')
    expect(wrapper.element.getAttribute('data-testid')).toBeNull()
  })

  it('renders the error message with red border styling', () => {
    const wrapper = mount(AppSelect, {
      props: { id: 'fruit', label: 'Fruit', items, errorMessage: 'Pick one' },
    })

    expect(wrapper.text()).toContain('Pick one')
    expect(wrapper.find('#fruit-error').exists()).toBe(true)
    expect(wrapper.get('select').classes()).toContain('border-red-300')
  })
})
