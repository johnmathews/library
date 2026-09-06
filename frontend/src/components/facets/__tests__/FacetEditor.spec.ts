import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import FacetEditor from '../FacetEditor.vue'
import type { FacetRef } from '@/api/facets'
import { ApiError } from '@/api/client'

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
    await flushPromises()
    // No Save button: choosing the value IS the save, exactly like every other
    // field in the metadata panel this editor is a section of.
    expect(updateDocumentLabels).toHaveBeenCalledWith(7, { category: 'software' })
    expect(wrapper.emitted('saved')?.at(-1)).toEqual([{ category: 'software' }])
  })

  it('sends null for a cleared facet so the label is removed', async () => {
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: { category: 'software' } },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('')
    await flushPromises()
    // Explicit null, never omission: the PUT applies exactly the keys it is
    // given, so omitting a cleared facet would leave the old label in place and
    // clearing would silently do nothing.
    expect(updateDocumentLabels).toHaveBeenCalledWith(7, { category: null })
  })

  it('surfaces a save failure instead of silently discarding the edit', async () => {
    updateDocumentLabels.mockRejectedValue(new Error('nope'))
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')
    await flushPromises()
    expect(wrapper.get('[data-testid="facet-error"]').text()).toContain('Could not save')
    // The draft survives the failure — the selection is never thrown away
    // without saying so.
    const select = wrapper.get('[data-testid="facet-edit-category"]')
      .element as HTMLSelectElement
    expect(select.value).toBe('software')
  })

  it("reports the server's own message on a 422, not a fixed string", async () => {
    // The metadata fields beside this one surface `detail`; this editor used to
    // replace it with 'Could not save these labels', throwing away the only
    // part of the response that tells the owner what to change.
    updateDocumentLabels.mockRejectedValue(
      new ApiError(422, 'value "software" is not in the Category vocabulary', null),
    )
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')
    await flushPromises()
    expect(wrapper.get('[data-testid="facet-error"]').text()).toBe(
      'value "software" is not in the Category vocabulary',
    )
  })

  it('omits an unrelated, already-set facet from the PUT when only a different facet changes', async () => {
    // Per-facet autosave must send ONLY the facet that changed — not the whole
    // draft (which would needlessly re-send 'category', and would send an
    // explicit null for it if it ever sent draft-minus-blanks instead of the
    // one key the user just touched).
    updateDocumentLabels.mockResolvedValue({ category: 'software', priority: 'low' })
    const wrapper = mount(FacetEditor, {
      props: {
        documentId: 7,
        facets: TWO_VALUE_FACETS,
        labels: { category: 'software', priority: 'high' },
      },
    })
    await wrapper.get('[data-testid="facet-edit-priority"]').setValue('low')
    await flushPromises()

    expect(updateDocumentLabels).toHaveBeenCalledWith(7, { priority: 'low' })
    const payload = updateDocumentLabels.mock.calls.at(-1)?.[1] as Record<string, unknown>
    // Explicitly rule out 'category' appearing at all, in either form.
    expect(Object.keys(payload)).toEqual(['priority'])
    expect(payload).not.toHaveProperty('category')
  })
  // --- The late-labels race (#144) ------------------------------------------
  //
  // DocumentDetailView feeds this component from TWO independent fetches:
  // `facets` from `fetchFacets` in onMounted, `labels` from
  // `fetchDocumentLabels` in the route watcher. Nothing orders them, so on a
  // cold backend the label map can land AFTER the user has already picked a
  // value. Every test above mounts with both props already settled, so none of
  // them exercises that ordering — which is exactly how the bug shipped.
  //
  // The user-visible failure is silent: the selection disappears and Save goes
  // disabled with no error, permanently. In CI the same race burned the full
  // 180s e2e timeout roughly once per run.

  it('keeps the user selection when the label map arrives mid-save', async () => {
    // The race is specifically the window BEFORE the write settles: the user
    // has picked a value, the PUT is in flight, and the unrelated label fetch
    // resolves with the server's (still empty) map. Note there is no
    // `flushPromises` before `setProps` — adding one would settle the save
    // first and test a different, easier thing.
    let resolveSave: (v: Record<string, string>) => void = () => {}
    updateDocumentLabels.mockReturnValue(
      new Promise<Record<string, string>>((r) => {
        resolveSave = r
      }),
    )
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')

    // The detail fetch finally resolves — with the server's (empty) labels.
    await wrapper.setProps({ labels: {} })

    const select = wrapper.get('[data-testid="facet-edit-category"]')
      .element as HTMLSelectElement
    expect(select.value).toBe('software')

    resolveSave({ category: 'software' })
    await flushPromises()
    expect(select.value).toBe('software')
  })

  it('writes the selection exactly once, even when a late label map lands mid-flight', async () => {
    // The observable outcome that matters: the value reaches the server, and a
    // late map arriving mid-save does not trigger a second, contradictory write.
    let resolveSave: (v: Record<string, string>) => void = () => {}
    updateDocumentLabels.mockReturnValue(
      new Promise<Record<string, string>>((r) => {
        resolveSave = r
      }),
    )
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')
    await wrapper.setProps({ labels: {} })
    resolveSave({ category: 'software' })
    await flushPromises()

    expect(updateDocumentLabels).toHaveBeenCalledTimes(1)
    expect(updateDocumentLabels).toHaveBeenCalledWith(7, { category: 'software' })
  })

  it('re-hydrates from the server after a save, rather than staying pinned to the draft', async () => {
    // The flip side of not clobbering: once a save round-trips, the parent's
    // label map IS the truth again and the draft must follow it. Otherwise the
    // fix for the race would freeze the editor on the first edit.
    updateDocumentLabels.mockResolvedValue({ category: 'software' })
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')
    await flushPromises()

    // The parent assigns what `saved` carried; a later refresh then clears it
    // server-side. The editor must reflect that, not the stale draft.
    await wrapper.setProps({ labels: { category: 'software' } })
    await wrapper.setProps({ labels: {} })
    const select = wrapper.get('[data-testid="facet-edit-category"]')
      .element as HTMLSelectElement
    expect(select.value).toBe('')
  })

  it('drops the previous document\'s draft when the document changes', async () => {
    // Navigating to another document must not carry an unsaved selection over
    // onto it — that would offer to save one document's label onto another.
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    await wrapper.get('[data-testid="facet-edit-category"]').setValue('software')
    await wrapper.setProps({ documentId: 8, labels: {} })

    const select = wrapper.get('[data-testid="facet-edit-category"]')
      .element as HTMLSelectElement
    expect(select.value).toBe('')
    // Under autosave the selection was written to document 7 as it was made —
    // that is correct. What must never happen is a write landing on document 8
    // as a side effect of the switch, labelling the wrong document.
    for (const [id] of updateDocumentLabels.mock.calls) expect(id).toBe(7)
  })

  it('has no Save button — every facet autosaves on selection', async () => {
    // The panel this editor sits in autosaves every other field on commit; a
    // section that needed a button press was the inconsistency the 2026-09-06
    // consolidation removed.
    const wrapper = mount(FacetEditor, {
      props: { documentId: 7, facets: FACETS, labels: {} },
    })
    expect(wrapper.find('[data-testid="facet-save"]').exists()).toBe(false)
  })
})
