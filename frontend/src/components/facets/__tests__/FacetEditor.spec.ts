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
    values: [{ key: 'software', label: 'Software', parent_id: null, aliases: [], colour: null }],
  },
  { key: 'vehicle', label: 'Vehicle', ordinal: 1, values: [] },
]

// Two facets that can BOTH hold a value, used to prove `dirty` actually does
// its job: changing one must not drag an unrelated, already-set facet along
// for the ride (neither with its unchanged value nor as an accidental null).
const TWO_VALUE_FACETS: FacetRef[] = [
  {
    key: 'category',
    label: 'Category',
    ordinal: 0,
    values: [
      { key: 'software', label: 'Software', parent_id: null, aliases: [], colour: null },
      { key: 'hardware', label: 'Hardware', parent_id: null, aliases: [], colour: null },
    ],
  },
  {
    key: 'priority',
    label: 'Priority',
    ordinal: 1,
    values: [
      { key: 'high', label: 'High', parent_id: null, aliases: [], colour: null },
      { key: 'low', label: 'Low', parent_id: null, aliases: [], colour: null },
    ],
  },
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

  // TWO_VALUE_FACETS stores category as Software, Hardware — so this fails
  // against an editor that renders the vocabulary's own order. The sort is
  // display-only: the value KEYS submitted on save are unaffected, which the
  // dirty-tracking tests below still cover.
  it("lists a facet's values alphabetically by label, not in stored order", () => {
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: TWO_VALUE_FACETS, labels: {} },
    })
    const labels = wrapper
      .get('[data-testid="facet-edit-category"]')
      .findAll('option')
      .map((option) => option.text())
    expect(labels).toEqual(['—', 'Hardware', 'Software'])
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

  it('omits an unrelated, already-set facet from the PUT when only a different facet changes', async () => {
    // This is the whole reason `dirty` exists: with two facets both already
    // labelled, changing just one must send ONLY that one — not the whole
    // draft (which would needlessly re-send 'category', and would send an
    // explicit null for it if `save` ever sent draft-minus-blanks instead of
    // an actual before/after diff).
    updateDocumentLabels.mockResolvedValue({ category: 'software', priority: 'low' })
    const wrapper = mount(FacetEditor, {
      props: {
        documentId: 7,
        facets: TWO_VALUE_FACETS,
        labels: { category: 'software', priority: 'high' },
      },
    })
    await wrapper.get('[data-testid="facet-edit-priority"]').setValue('low')
    await wrapper.get('[data-testid="facet-save"]').trigger('click')
    await flushPromises()

    expect(updateDocumentLabels).toHaveBeenCalledWith(7, { priority: 'low' })
    const payload = updateDocumentLabels.mock.calls.at(-1)?.[1] as Record<string, unknown>
    // Explicitly rule out 'category' appearing at all, in either form.
    expect(Object.keys(payload)).toEqual(['priority'])
    expect(payload).not.toHaveProperty('category')
  })
})
