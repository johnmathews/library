import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import FacetEditor from '../FacetEditor.vue'
import type { FacetRef } from '@/api/facets'

const updateDocumentLabels = vi.fn()
vi.mock('@/api/facets', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/facets')>()),
  updateDocumentLabels: (...args: unknown[]) => updateDocumentLabels(...args),
}))

const FACETS: FacetRef[] = [
  {
    key: 'category',
    label: 'Category',
    ordinal: 0,
    values: [{ key: 'software', label: 'Software', parent_id: null, aliases: [] }],
  },
  { key: 'vehicle', label: 'Vehicle', ordinal: 1, values: [] },
]

beforeEach(() => {
  updateDocumentLabels.mockReset()
  updateDocumentLabels.mockResolvedValue({ category: 'software' })
})

describe('FacetEditor', () => {
  it('renders an empty facet as a disabled select rather than hiding it', () => {
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    const empty = wrapper.get('[data-testid="facet-edit-vehicle"]')
    expect(empty.attributes('disabled')).toBeDefined()
  })

  it('saves the changed label and emits what the server returned', async () => {
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')
    await wrapper.get('[data-testid="facet-save"]').trigger('click')
    await flushPromises()
    expect(updateDocumentLabels).toHaveBeenCalledWith(7, { category: 'software' })
    expect(wrapper.emitted('saved')?.at(-1)).toEqual([{ category: 'software' }])
  })

  it('sends null for a cleared facet so the label is removed', async () => {
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: { category: 'software' } },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('')
    await wrapper.get('[data-testid="facet-save"]').trigger('click')
    await flushPromises()
    expect(updateDocumentLabels).toHaveBeenCalledWith(7, { category: null })
  })

  it('surfaces a save failure instead of silently discarding the edit', async () => {
    updateDocumentLabels.mockRejectedValue(new Error('nope'))
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')
    await wrapper.get('[data-testid="facet-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="facet-error"]').text()).toContain('Could not save')
  })
})
