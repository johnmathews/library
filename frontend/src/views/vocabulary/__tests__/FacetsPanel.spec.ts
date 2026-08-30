import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import FacetsPanel from '../FacetsPanel.vue'
import { ApiError } from '@/api/client'
import { SPLIT_PALETTE } from '@/utils/splitPalette'

vi.mock('@/api/facets', () => ({
  fetchFacets: vi.fn(),
  fetchFacetCounts: vi.fn(),
  fetchLabelCounts: vi.fn(),
  createFacet: vi.fn(),
  createValue: vi.fn(),
  renameValue: vi.fn(),
  setValueColour: vi.fn(),
  addAlias: vi.fn(),
  deleteValue: vi.fn(),
}))
import * as api from '@/api/facets'

const VOCAB = [
  {
    key: 'category',
    label: 'Category',
    ordinal: 0,
    values: [
      { key: 'alpha', label: 'Alpha', parent_id: null, aliases: ['a-one'], colour: null },
      { key: 'beta', label: 'Beta', parent_id: null, aliases: [], colour: SPLIT_PALETTE[2]!.light },
    ],
  },
]

beforeEach(() => {
  vi.mocked(api.fetchFacets).mockResolvedValue(structuredClone(VOCAB))
  vi.mocked(api.fetchFacetCounts).mockResolvedValue([
    { facet_key: 'category', value_key: 'alpha', documents: 2, first_date: '2026-01-01', last_date: '2026-02-01' },
  ])
  vi.mocked(api.fetchLabelCounts).mockResolvedValue([
    { facet_key: 'category', value_key: 'alpha', labelled: 7 },
  ])
})

const open = async () => {
  const wrapper = mount(FacetsPanel, {
    props: { active: true },
    global: { stubs: { SplitColourPicker: true } },
  })
  await flushPromises()
  return wrapper
}

describe('FacetsPanel', () => {
  it('does not load until its tab is opened', async () => {
    mount(FacetsPanel, { props: { active: false }, global: { stubs: { SplitColourPicker: true } } })
    await flushPromises()
    expect(api.fetchFacets).not.toHaveBeenCalled()
  })

  it('shows both counts, distinctly labelled', async () => {
    // The money count and the label count answer different questions and
    // routinely differ; showing one number would misrepresent the other.
    const wrapper = await open()
    const row = wrapper.find('[data-testid="value-category-alpha"]')
    expect(row.text()).toContain('7 labelled')
    expect(row.text()).toContain('2 in charts')
  })

  it('shows a value no document carries as zero labelled, not blank', async () => {
    const wrapper = await open()
    expect(wrapper.find('[data-testid="value-category-beta"]').text()).toContain('0 labelled')
  })

  it('renames a value with a label-only request', async () => {
    vi.mocked(api.renameValue).mockResolvedValue({
      key: 'alpha', label: 'Renamed', parent_id: null, aliases: [], colour: null,
    })
    const wrapper = await open()
    await wrapper.find('[data-testid="value-category-alpha-rename-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-category-alpha-rename-input"]').setValue('Renamed')
    await wrapper.find('[data-testid="value-category-alpha-rename-save"]').trigger('click')
    await flushPromises()
    expect(api.renameValue).toHaveBeenCalledWith('category', 'alpha', 'Renamed')
  })

  it('refuses to add an alias the value already has, without calling the API', async () => {
    // The route is idempotent (ON CONFLICT DO NOTHING), so it would answer 200
    // and the panel would report a phantom addition.
    const wrapper = await open()
    await wrapper.find('[data-testid="value-category-alpha-alias-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-category-alpha-alias-input"]').setValue('a-one')
    await wrapper.find('[data-testid="value-category-alpha-alias-save"]').trigger('click')
    await flushPromises()
    expect(api.addAlias).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="value-category-alpha-error"]').text())
      .toContain('already an alias')
  })

  it('adds an alias the value does not have', async () => {
    vi.mocked(api.addAlias).mockResolvedValue({ alias: 'a-two' })
    const wrapper = await open()
    await wrapper.find('[data-testid="value-category-alpha-alias-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-category-alpha-alias-input"]').setValue('a-two')
    await wrapper.find('[data-testid="value-category-alpha-alias-save"]').trigger('click')
    await flushPromises()
    expect(api.addAlias).toHaveBeenCalledWith('category', 'alpha', 'a-two')
  })

  it("renders the server's reason when a delete is refused", async () => {
    vi.mocked(api.deleteValue).mockRejectedValue(
      new ApiError(409, 'category=alpha is on 7 documents', {
        detail: 'category=alpha is on 7 documents',
      }),
    )
    const wrapper = await open()
    await wrapper.find('[data-testid="value-category-alpha-delete-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-category-alpha-delete-confirm"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="value-category-alpha-error"]').text())
      .toContain('category=alpha is on 7 documents')
  })

  it('sets a colour through the picker', async () => {
    vi.mocked(api.setValueColour).mockResolvedValue({
      key: 'alpha', label: 'Alpha', parent_id: null, aliases: ['a-one'],
      colour: SPLIT_PALETTE[1]!.light,
    })
    const wrapper = await open()
    wrapper.findComponent({ name: 'SplitColourPicker' }).vm.$emit(
      'update:modelValue', SPLIT_PALETTE[1]!.light,
    )
    await flushPromises()
    expect(api.setValueColour).toHaveBeenCalledWith('category', 'alpha', SPLIT_PALETTE[1]!.light)
  })

  it('clears a colour when the picker emits null', async () => {
    vi.mocked(api.setValueColour).mockResolvedValue({
      key: 'alpha', label: 'Alpha', parent_id: null, aliases: ['a-one'], colour: null,
    })
    const wrapper = await open()
    wrapper.findComponent({ name: 'SplitColourPicker' }).vm.$emit('update:modelValue', null)
    await flushPromises()
    expect(api.setValueColour).toHaveBeenCalledWith('category', 'alpha', null)
  })

  it('marks two values in one facet that resolve to the same colour', async () => {
    // Six slots over nineteen values makes collisions arithmetic, and a picker
    // alone never tells the owner two values look identical.
    vi.mocked(api.fetchFacets).mockResolvedValue([
      {
        key: 'category', label: 'Category', ordinal: 0,
        values: [
          { key: 'one', label: 'One', parent_id: null, aliases: [], colour: SPLIT_PALETTE[0]!.light },
          { key: 'two', label: 'Two', parent_id: null, aliases: [], colour: SPLIT_PALETTE[0]!.light },
          { key: 'three', label: 'Three', parent_id: null, aliases: [], colour: SPLIT_PALETTE[1]!.light },
        ],
      },
    ])
    const wrapper = await open()
    expect(wrapper.find('[data-testid="value-category-one-collision"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="value-category-two-collision"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="value-category-three-collision"]').exists()).toBe(false)
  })

  it('says a new facet carries no documents until a labelling pass runs', async () => {
    // Creating a facet is free and changes nothing; reporting only success
    // would be silently untrue.
    vi.mocked(api.createFacet).mockResolvedValue({ key: 'newfacet' })
    const wrapper = await open()
    await wrapper.find('[data-testid="create-facet-key"]').setValue('newfacet')
    await wrapper.find('[data-testid="create-facet-label"]').setValue('New facet')
    await wrapper.find('[data-testid="create-facet-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="create-facet-note"]').text()).toContain('label-archive')
  })

  it('prefills a new value key from its label but leaves it editable', async () => {
    const wrapper = await open()
    await wrapper.find('[data-testid="create-value-category-btn"]').trigger('click')
    await wrapper.find('[data-testid="create-value-category-label"]').setValue('EV charging (home)!')
    await flushPromises()
    const key = wrapper.find('[data-testid="create-value-category-key"]')
    expect((key.element as HTMLInputElement).value).toBe('ev-charging-home')
    await key.setValue('something-else')
    await wrapper.find('[data-testid="create-value-category-save"]').trigger('click')
    await flushPromises()
    expect(api.createValue).toHaveBeenCalledWith('category', 'something-else', 'EV charging (home)!')
  })

  it('renders a 422 on an unusable key as the server states it', async () => {
    vi.mocked(api.createValue).mockRejectedValue(
      new ApiError(422, 'nothing matching [a-z0-9_-] remains', {}),
    )
    const wrapper = await open()
    await wrapper.find('[data-testid="create-value-category-btn"]').trigger('click')
    await wrapper.find('[data-testid="create-value-category-label"]').setValue('!!!')
    await wrapper.find('[data-testid="create-value-category-key"]').setValue('x')
    await wrapper.find('[data-testid="create-value-category-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="create-value-category-error"]').text())
      .toContain('nothing matching')
  })
})
