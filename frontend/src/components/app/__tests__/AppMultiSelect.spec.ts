import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AppMultiSelect from '@/components/app/AppMultiSelect.vue'

function mountSelect(modelValue: string[] = [], options: string[] = ['House purchase', 'Taxes']) {
  return mount(AppMultiSelect, {
    props: {
      id: 'ms',
      label: 'Projects',
      options,
      modelValue,
      'onUpdate:modelValue': () => {},
    },
  })
}

describe('AppMultiSelect', () => {
  it('renders selected values as chips', () => {
    const w = mountSelect(['House purchase'])
    const chips = w.findAll('[data-testid="ms-chip"]')
    expect(chips).toHaveLength(1)
    expect(chips[0]!.text()).toContain('House purchase')
  })

  it('offers existing options (minus already-selected) and adds on click', async () => {
    const w = mountSelect([])
    await w.find('[data-testid="ms-input"]').trigger('focus')
    const options = w.findAll('[data-testid="ms-option"]')
    expect(options.map((o) => o.text())).toEqual(['House purchase', 'Taxes'])
    await options[0]!.trigger('mousedown')
    expect(w.emitted('update:modelValue')!.at(-1)).toEqual([['House purchase']])
    expect(w.emitted('change')).toHaveLength(1)
  })

  it('offers a create affordance for an unknown name and adds it', async () => {
    const w = mountSelect([])
    await w.find('[data-testid="ms-input"]').setValue('Renovation')
    await w.find('[data-testid="ms-input"]').trigger('focus')
    expect(w.find('[data-testid="ms-create"]').text()).toContain('Renovation')
    await w.find('[data-testid="ms-input"]').trigger('keydown.enter')
    expect(w.emitted('update:modelValue')!.at(-1)).toEqual([['Renovation']])
  })

  it('does not offer create for a name that already exists as an option', async () => {
    const w = mountSelect([])
    await w.find('[data-testid="ms-input"]').setValue('taxes') // case-insensitive match
    await w.find('[data-testid="ms-input"]').trigger('focus')
    expect(w.find('[data-testid="ms-create"]').exists()).toBe(false)
  })

  it('will not add a duplicate (case-insensitive)', async () => {
    const w = mountSelect(['Taxes'])
    await w.find('[data-testid="ms-input"]').setValue('taxes')
    await w.find('[data-testid="ms-input"]').trigger('keydown.enter')
    // No change emitted — the value is already selected.
    expect(w.emitted('change')).toBeUndefined()
  })

  it('removes a chip via its remove button', async () => {
    const w = mountSelect(['House purchase', 'Taxes'])
    await w.findAll('[data-testid="ms-remove"]')[0]!.trigger('click')
    expect(w.emitted('update:modelValue')!.at(-1)).toEqual([['Taxes']])
    expect(w.emitted('change')).toHaveLength(1)
  })

  it('backspace on an empty query removes the last chip', async () => {
    const w = mountSelect(['House purchase', 'Taxes'])
    await w.find('[data-testid="ms-input"]').trigger('keydown.delete')
    expect(w.emitted('update:modelValue')!.at(-1)).toEqual([['House purchase']])
  })
})
