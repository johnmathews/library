import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'

// This repository is public — every facet, value, chart name and amount below
// is invented.

vi.mock('vue-chartjs', () => ({
  Bar: { name: 'Bar', props: ['data', 'options'], template: '<canvas />' },
}))

const updateChart = vi.fn()
const postPreview = vi.fn()
vi.mock('@/api/spending', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/spending')>()),
  updateChart: (...args: unknown[]) => updateChart(...args),
  postPreview: (...args: unknown[]) => postPreview(...args),
}))

const fetchFacets = vi.fn()
vi.mock('@/api/facets', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/facets')>()),
  fetchFacets: (...args: unknown[]) => fetchFacets(...args),
}))

import ChartRuleEditor from '../ChartRuleEditor.vue'
import type { Chart, ChartData, Rule } from '@/api/spending'
import type { FacetRef } from '@/api/facets'
import { ApiError } from '@/api/client'

const FACETS: FacetRef[] = [
  {
    key: 'category',
    label: 'Category',
    ordinal: 0,
    values: [
      { key: 'software', label: 'Software', parent_id: null, aliases: [], colour: null },
      { key: 'services', label: 'Services', parent_id: null, aliases: [], colour: null },
    ],
  },
  {
    key: 'cost_type',
    label: 'Cost type',
    ordinal: 1,
    values: [
      { key: 'subscription', label: 'Subscription', parent_id: null, aliases: [], colour: null },
    ],
  },
]

const SOFTWARE_RULE: Rule = { all: [{ facet: 'category', op: 'in', values: ['software'] }] }

function chart(overrides: Partial<Chart> = {}): Chart {
  return {
    id: 7,
    name: 'Tooling',
    question_text: 'how much do we spend on tooling',
    rule: SOFTWARE_RULE,
    default_grain: 'month',
    default_split: null,
    display_currency: 'EUR',
    ordinal: 0,
    ...overrides,
  }
}

const PREVIEW: ChartData = {
  chart_id: null,
  grain: 'month',
  split: null,
  currency: 'EUR',
  since: null,
  until: null,
  cells: [],
  splits: [],
  total: '41.00',
  payments: 0,
  documents: 0,
  footer: {
    netted_refunds: '0.00',
    refund_count: 0,
    excluded: [],
    unclassified: null,
    uncategorised: null,
    undated: null,
    unaccounted: null,
    unconvertible: [],
  },
}

async function mountEditor(
  overrides: Partial<Chart> = {},
  window: Partial<{ grain: 'month'; from: string; to: string; currency: string }> = {},
): Promise<VueWrapper> {
  const wrapper = mount(ChartRuleEditor, {
    props: {
      chart: chart(overrides),
      grain: 'month' as const,
      from: '',
      to: '',
      currency: 'EUR',
      ...window,
    },
  })
  await flushPromises()
  return wrapper
}

function rows(wrapper: VueWrapper) {
  return wrapper.findAll('[data-testid="rule-editor-row"]')
}

describe('ChartRuleEditor', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    fetchFacets.mockReset().mockResolvedValue(FACETS)
    updateChart.mockReset().mockImplementation(async (_id: number, patch: Partial<Chart>) =>
      chart(patch),
    )
    postPreview.mockReset().mockResolvedValue(PREVIEW)
  })

  it('renders one row per clause, showing vocabulary labels rather than keys', async () => {
    const wrapper = await mountEditor()
    expect(rows(wrapper)).toHaveLength(1)
    expect(wrapper.get('[data-testid="rule-editor-summary"]').text()).toBe('Category is Software')
  })

  it('offers every vocabulary facet in the split picker, including for a chart with no split', async () => {
    const wrapper = await mountEditor({ default_split: null })
    const options = wrapper.get('[data-testid="rule-editor-split"]').findAll('option')
    expect(options.map((option) => option.text())).toEqual([
      'No split',
      'Category',
      'Cost type',
    ])
  })

  it('adds a row', async () => {
    const wrapper = await mountEditor()
    await wrapper.get('[data-testid="rule-add-clause"]').trigger('click')
    await flushPromises()
    expect(rows(wrapper)).toHaveLength(2)
  })

  // Vue sets selectedIndex = -1 when the bound value matches no <option>, so a
  // state with no matching option renders as a BLANK control. A newly added row
  // holds facet '' — the most common thing this editor does — so that state
  // needs an option of its own.
  it('gives a newly added row a placeholder option rather than rendering blank', async () => {
    const wrapper = await mountEditor()
    await wrapper.get('[data-testid="rule-add-clause"]').trigger('click')
    await flushPromises()

    const select = wrapper.get('[data-testid="rule-row-1-facet"]')
    expect(select.findAll('option')[0]!.element.value).toBe('')
    expect((select.element as HTMLSelectElement).selectedIndex).toBe(0)
  })

  // The same staleness the clause rows handle, one control over: a chart's
  // split axis can name a facet the vocabulary has since lost. Rendering blank
  // would hide which axis is broken, and Apply would resend the stale key and
  // earn a 422 naming something never shown.
  it('renders a split axis missing from the vocabulary as a flagged option', async () => {
    const wrapper = await mountEditor({ default_split: 'gone_facet' })

    const select = wrapper.get('[data-testid="rule-editor-split"]')
    expect(select.findAll('option')[0]!.text()).toContain('no longer in the vocabulary')
    expect((select.element as HTMLSelectElement).value).toBe('gone_facet')
  })

  it('removes a row without touching its siblings', async () => {
    const wrapper = await mountEditor({
      rule: {
        all: [
          { facet: 'category', op: 'in', values: ['software'] },
          { facet: 'cost_type', op: 'in', values: ['subscription'] },
        ],
      },
    })
    expect(rows(wrapper)).toHaveLength(2)
    await rows(wrapper)[0]!.get('[aria-label="Remove filter 1"]').trigger('click')
    await flushPromises()
    expect(rows(wrapper)).toHaveLength(1)
    expect(wrapper.get('[data-testid="rule-editor-summary"]').text()).toBe(
      'Cost type is Subscription',
    )
  })

  // Values are facet-scoped and the API matches value keys exactly, so a value
  // carried across a facet change is a guaranteed 422.
  it('clears a row values when its facet changes', async () => {
    const wrapper = await mountEditor()
    await wrapper.get('[data-testid="rule-row-0-facet"]').setValue('cost_type')
    await flushPromises()
    expect(wrapper.get('[data-testid="rule-editor-summary"]').text()).toBe('Every document.')
  })

  // --- preview ---------------------------------------------------------------

  it('previews the edited rule without saving it', async () => {
    const wrapper = await mountEditor()
    await wrapper.get('[data-testid="rule-editor-preview"]').trigger('click')
    await flushPromises()

    expect(postPreview).toHaveBeenCalledWith(
      expect.objectContaining({ rule: SOFTWARE_RULE, display_currency: 'EUR', split: null }),
    )
    expect(updateChart).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="rule-editor-preview-total"]').text()).toBe('41.00')
  })

  // A preview computed over all time, while the chart underneath shows one
  // quarter, reports a different number for the same rule.
  it('previews against the window the workspace is showing', async () => {
    const wrapper = await mountEditor({}, { from: '2026-01-01', to: '2026-03-31' })
    await wrapper.get('[data-testid="rule-editor-preview"]').trigger('click')
    await flushPromises()
    expect(postPreview).toHaveBeenCalledWith(
      expect.objectContaining({ from: '2026-01-01', to: '2026-03-31', grain: 'month' }),
    )
  })

  it('surfaces a preview error verbatim from the API', async () => {
    postPreview.mockRejectedValue(new ApiError(422, "unknown value(s) ['ghost'] for facet 'category'"))
    const wrapper = await mountEditor()
    await wrapper.get('[data-testid="rule-editor-preview"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="rule-editor-preview-error"]').text()).toContain('ghost')
  })

  // --- apply -----------------------------------------------------------------

  it('applies the edited rule and emits the chart the server returned', async () => {
    const wrapper = await mountEditor({ default_split: 'category' })
    await wrapper.get('[data-testid="rule-editor-apply"]').trigger('click')
    await flushPromises()

    expect(updateChart).toHaveBeenCalledWith(7, {
      rule: SOFTWARE_RULE,
      default_split: 'category',
    })
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  // ChartPatch is a Partial, so an omitted key leaves the old axis in place —
  // "clear the split" would be a silent no-op.
  it('sends default_split as null rather than omitting it when the split is cleared', async () => {
    const wrapper = await mountEditor({ default_split: 'category' })
    await wrapper.get('[data-testid="rule-editor-split"]').setValue('')
    await wrapper.get('[data-testid="rule-editor-apply"]').trigger('click')
    await flushPromises()

    const patch = updateChart.mock.calls[0]![1] as Record<string, unknown>
    expect('default_split' in patch).toBe(true)
    expect(patch.default_split).toBeNull()
  })

  it('surfaces an apply error WITHOUT discarding the edited rows', async () => {
    updateChart.mockRejectedValue(new ApiError(409, 'that name is taken'))
    const wrapper = await mountEditor()
    await wrapper.get('[data-testid="rule-add-clause"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="rule-row-1-facet"]').setValue('cost_type')
    await flushPromises()

    await wrapper.get('[data-testid="rule-editor-apply"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="rule-editor-save-error"]').text()).toContain('taken')
    expect(rows(wrapper)).toHaveLength(2)
    expect(
      (wrapper.get('[data-testid="rule-row-1-facet"]').element as HTMLSelectElement).value,
    ).toBe('cost_type')
  })

  it('discards every edit on cancel', async () => {
    const wrapper = await mountEditor()
    await wrapper.get('[data-testid="rule-add-clause"]').trigger('click')
    await wrapper.get('[data-testid="rule-editor-cancel"]').trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(updateChart).not.toHaveBeenCalled()
  })

  // --- the two rule hazards --------------------------------------------------

  it('warns and requires a second confirmation before applying an empty rule', async () => {
    const wrapper = await mountEditor()
    await rows(wrapper)[0]!.get('[aria-label="Remove filter 1"]').trigger('click')
    await flushPromises()

    await wrapper.get('[data-testid="rule-editor-apply"]').trigger('click')
    await flushPromises()
    expect(updateChart).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="rule-editor-empty-warning"]').exists()).toBe(true)

    await wrapper.get('[data-testid="rule-editor-apply"]').trigger('click')
    await flushPromises()
    expect(updateChart).toHaveBeenCalledWith(7, { rule: { all: [] }, default_split: null })
  })

  // Warned about, never auto-merged: merging turns the AND into an OR, which
  // answers a different question and moves money into the chart.
  it('warns when two is-filters name the same facet', async () => {
    const wrapper = await mountEditor({
      rule: {
        all: [
          { facet: 'category', op: 'in', values: ['software'] },
          { facet: 'category', op: 'in', values: ['services'] },
        ],
      },
    })
    expect(wrapper.get('[data-testid="rule-editor-conflict-warning"]').text()).toContain('Category')
  })

  // --- unresolvable vocabulary ------------------------------------------------

  it('renders a value missing from the vocabulary as a checked, flagged, removable chip', async () => {
    const wrapper = await mountEditor({
      rule: { all: [{ facet: 'category', op: 'in', values: ['software', 'ghost'] }] },
    })

    // The values live in a FilterPill popover, which renders its panel only
    // while open — so opening it is part of reading the row.
    await wrapper.get('[data-testid="filter-pill-button"]').trigger('click')
    await flushPromises()

    const boxes = wrapper.findAll('input[type="checkbox"]')
    const ghost = boxes.find((box) => (box.element as HTMLInputElement).value === 'ghost')
    expect(ghost, 'the lost value must still be offered, not filtered away').toBeTruthy()
    expect((ghost!.element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.get('[data-testid="rule-row-unknown-value"]').text()).toContain('ghost')
    expect(wrapper.get('[data-testid="rule-editor-unknown-warning"]').text()).toContain('ghost')

    await ghost!.setValue(false)
    await wrapper.get('[data-testid="rule-editor-apply"]').trigger('click')
    await flushPromises()
    expect(updateChart).toHaveBeenCalledWith(7, {
      rule: SOFTWARE_RULE,
      default_split: null,
    })
  })

  it('renders a clause whose facet no longer exists without dropping the row', async () => {
    const wrapper = await mountEditor({
      rule: { all: [{ facet: 'gone', op: 'in', values: ['x'] }] },
    })
    expect(rows(wrapper)).toHaveLength(1)
    const options = wrapper.get('[data-testid="rule-row-0-facet"]').findAll('option')
    expect(options[0]!.text()).toContain('no longer in the vocabulary')
    expect(wrapper.get('[data-testid="rule-editor-unknown-warning"]').text()).toContain('gone')
  })

  // With an empty vocabulary every value is unknown. The rule must still be
  // fully visible — this is the test that reddens if anyone filters the rows
  // against the live vocabulary.
  it('renders every clause even when the vocabulary is empty', async () => {
    fetchFacets.mockResolvedValue([])
    const wrapper = await mountEditor({
      rule: {
        all: [
          { facet: 'category', op: 'in', values: ['software'] },
          { facet: 'cost_type', op: 'not_in', values: ['subscription'] },
        ],
      },
    })
    expect(rows(wrapper)).toHaveLength(2)
    expect(wrapper.get('[data-testid="rule-editor-summary"]').text()).toBe(
      'category is software and cost_type is not subscription',
    )
  })
})
