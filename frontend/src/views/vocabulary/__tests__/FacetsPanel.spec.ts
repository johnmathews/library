import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useDark } from '@vueuse/core'
import FacetsPanel from '../FacetsPanel.vue'
import { ApiError } from '@/api/client'
import { SPLIT_PALETTE, deriveSlot } from '@/utils/splitPalette'

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

/** jsdom renders an inline `background-color` hex as `rgb(r, g, b)` when the
 * style attribute is read back, so a swatch colour assertion has to compare
 * in that form. */
function hexToRgb(hex: string): string {
  const value = hex.replace('#', '')
  const r = Number.parseInt(value.slice(0, 2), 16)
  const g = Number.parseInt(value.slice(2, 4), 16)
  const b = Number.parseInt(value.slice(4, 6), 16)
  return `rgb(${r}, ${g}, ${b})`
}

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
    global: { stubs: { SplitColourPicker: true, RouterLink: RouterLinkStub } },
  })
  await flushPromises()
  return wrapper
}

describe('FacetsPanel', () => {
  it('does not load until its tab is opened', async () => {
    mount(FacetsPanel, {
      props: { active: false },
      global: { stubs: { SplitColourPicker: true, RouterLink: RouterLinkStub } },
    })
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

  it('refuses to add an alias the value already has (case-insensitively), without calling the API', async () => {
    // The route itself is NOT case-insensitive (facet_value_aliases stores
    // aliases as typed and add_alias's ON CONFLICT DO NOTHING does no
    // lowering), so this is not simply mirroring server idempotency — the
    // block exists because the labeller resolves values/aliases casefolded
    // (docs/facets.md §3), so a case-only variant would add a dead row the
    // labeller can never reach. Submitting a DIFFERENT-CASE variant of an
    // existing alias exercises that specifically.
    const wrapper = await open()
    await wrapper.find('[data-testid="value-category-alpha-alias-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-category-alpha-alias-input"]').setValue('A-One')
    await wrapper.find('[data-testid="value-category-alpha-alias-save"]').trigger('click')
    await flushPromises()
    expect(api.addAlias).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="value-category-alpha-error"]').text())
      .toContain("Already covered by the alias 'a-one'")
  })

  it('does NOT block a diacritic variant of an existing alias — casefold does not fold diacritics', async () => {
    // Same boundary as the backend's
    // tests/test_facet_labeller.py::test_casefold_does_not_fold_diacritics:
    // Python's casefold() (and JS's toLowerCase()) fold case but not
    // accents, so "Skoda" and "Škoda" compare unequal even case-insensitively.
    // A value whose only alias is the accented form genuinely still needs the
    // unaccented form added — this must reach the API, not be blocked as a
    // phantom duplicate.
    vi.mocked(api.fetchFacets).mockResolvedValue([
      {
        key: 'vehicle', label: 'Vehicle', ordinal: 0,
        values: [
          { key: 'koda', label: 'Koda', parent_id: null, aliases: ['Škoda'], colour: null },
        ],
      },
    ])
    vi.mocked(api.fetchFacetCounts).mockResolvedValue([])
    vi.mocked(api.fetchLabelCounts).mockResolvedValue([])
    vi.mocked(api.addAlias).mockResolvedValue({ alias: 'Skoda' })
    const wrapper = await open()
    await wrapper.find('[data-testid="value-vehicle-koda-alias-btn"]').trigger('click')
    await wrapper.find('[data-testid="value-vehicle-koda-alias-input"]').setValue('Skoda')
    await wrapper.find('[data-testid="value-vehicle-koda-alias-save"]').trigger('click')
    await flushPromises()
    expect(api.addAlias).toHaveBeenCalledWith('vehicle', 'koda', 'Skoda')
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

  it('shows an empty state when the vocabulary has no facets at all', async () => {
    vi.mocked(api.fetchFacets).mockResolvedValue([])
    vi.mocked(api.fetchFacetCounts).mockResolvedValue([])
    vi.mocked(api.fetchLabelCounts).mockResolvedValue([])
    const wrapper = await open()
    expect(wrapper.find('[data-testid="facets-empty"]').text()).toContain('No facets yet')
  })

  it('repaints a derived swatch reactively when dark mode toggles elsewhere on the page', async () => {
    // FacetsPanel must react to the SAME shared dark-mode state
    // `ThemeToggle.vue` owns (`useDark({ selector: 'html' })`), not read it
    // once at setup. A second `useDark()` call here is a different composable
    // instance backed by the same storage key — exactly what happens when
    // ThemeToggle's own checkbox is toggled elsewhere on the page. A plain
    // `document.documentElement.classList.contains('dark')` read once at
    // mount can never observe that: `useDark` WRITES the class, it never
    // reads it back, so a raw classList mutation would not exercise the
    // real mechanism this test pins (verified against @vueuse/core's source:
    // its state is driven from localStorage/system preference, with no
    // MutationObserver on the element).
    localStorage.clear()
    document.documentElement.classList.remove('dark')
    if (!window.matchMedia) {
      window.matchMedia = (() => ({
        matches: false,
        media: '',
        addEventListener() {},
        removeEventListener() {},
        addListener() {},
        removeListener() {},
        dispatchEvent() {
          return false
        },
      })) as unknown as typeof window.matchMedia
    }

    const wrapper = await open()
    const swatch = () => wrapper.find('[aria-label="Colour for Alpha"]')
    const slot = deriveSlot('alpha')
    // jsdom normalises a `background-color` written as a hex string to
    // `rgb(r, g, b)` when the style attribute is read back, so the DOM
    // outcome is asserted in that form rather than against the hex literal.
    expect(swatch().attributes('style')).toContain(hexToRgb(slot.light))

    const isDark = useDark({ selector: 'html' })
    isDark.value = true
    await flushPromises()

    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(swatch().attributes('style')).toContain(hexToRgb(slot.dark))

    isDark.value = false
    await flushPromises()
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
