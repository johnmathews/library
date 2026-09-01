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
      { key: 'software', label: 'Software', parent_id: null, aliases: [], colour: null },
      { key: 'energy', label: 'Energy', parent_id: null, aliases: [], colour: null },
    ],
  },
  { key: 'vehicle', label: 'Vehicle', ordinal: 1, values: [] },
  {
    key: 'property',
    label: 'Property',
    ordinal: 2,
    // A placeholder, not the real archive's value: this repo is public and a
    // `property` value names a real address.
    values: [
      { key: 'first-address', label: 'First address', parent_id: null, aliases: [], colour: null },
    ],
  },
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

  // A one-option select cannot narrow a comparison: every document it can show
  // carries the same value. `property` sits at exactly one value in the real
  // archive, which is what motivated the threshold.
  it('omits a facet with only one value, which cannot usefully filter', () => {
    const wrapper = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    expect(wrapper.find('[data-testid="facet-select-property"]').exists()).toBe(false)
  })

  // Keyed on the count, not on the key: a facet that grows a second value must
  // come back without anyone editing this component.
  it('renders a facet once it reaches two values', () => {
    const grown = FACETS.map((facet) =>
      facet.key === 'property'
        ? {
            ...facet,
            values: [
              ...facet.values,
              { key: 'second', label: 'Second address', parent_id: null, aliases: [], colour: null },
            ],
          }
        : facet,
    )
    const wrapper = mount(FacetFilterBar, { props: { facets: grown, modelValue: {} } })
    expect(wrapper.find('[data-testid="facet-select-property"]').exists()).toBe(true)
  })

  // The fixture is deliberately stored out of order (Software before Energy),
  // so this fails against a component that renders the vocabulary's own order.
  it('lists a facet\'s values alphabetically by label, not in stored order', () => {
    const wrapper = mount(FacetFilterBar, { props: { facets: FACETS, modelValue: {} } })
    const labels = wrapper
      .find('[data-testid="facet-select-category"]')
      .findAll('option')
      .map((option) => option.text())
    expect(labels).toEqual(['Any', 'Energy', 'Software'])
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
