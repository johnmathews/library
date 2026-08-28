import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FacetFilterBar from '../FacetFilterBar.vue'
import type { FacetRef } from '@/api/facets'

const FACETS: FacetRef[] = [
  {
    key: 'category',
    label: 'Category',
    ordinal: 0,
    values: [
      { key: 'software', label: 'Software', parent_id: null, aliases: [] },
      { key: 'energy', label: 'Energy', parent_id: null, aliases: [] },
    ],
  },
  { key: 'vehicle', label: 'Vehicle', ordinal: 1, values: [] },
]

describe('FacetFilterBar', () => {
  it('renders one select per facet that has values', () => {
    const wrapper = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    expect(wrapper.findAll('[data-facet-select]')).toHaveLength(1)
  })

  it('omits a facet with no values rather than rendering an empty select', () => {
    const wrapper = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    expect(wrapper.find('[data-testid="facet-select-vehicle"]').exists()).toBe(false)
  })

  it('emits the chosen value keyed by facet', async () => {
    const wrapper = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    await wrapper.find('[data-testid="facet-select-category"]').setValue('software')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([{ category: 'software' }])
  })

  it('choosing the blank option removes that facet from the selection', async () => {
    const wrapper = mount(FacetFilterBar, {
      props: { facets: FACETS, modelValue: { category: 'software' } },
    })
    await wrapper.find('[data-testid="facet-select-category"]').setValue('')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([{}])
  })

  it('shows a clear control only when something is selected', async () => {
    const empty = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    expect(empty.find('[data-testid="facet-clear"]').exists()).toBe(false)
    const chosen = mount(FacetFilterBar, {
      props: { facets: FACETS, modelValue: { category: 'energy' } },
    })
    expect(chosen.find('[data-testid="facet-clear"]').exists()).toBe(true)
  })
})
