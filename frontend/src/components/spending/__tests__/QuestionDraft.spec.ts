import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'

vi.mock('vue-chartjs', () => ({
  Bar: { name: 'Bar', props: ['data', 'options'], template: '<canvas />' },
}))

const draftQuestion = vi.fn()
const createChart = vi.fn()
vi.mock('@/api/spending', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/spending')>()),
  draftQuestion: (...args: unknown[]) => draftQuestion(...args),
  createChart: (...args: unknown[]) => createChart(...args),
}))

import QuestionDraft from '../QuestionDraft.vue'
import { ApiError } from '@/api/client'
import type { Chart, ChartData, Draft, Footer, Rule } from '@/api/spending'

// --- Fixtures --------------------------------------------------------------
//
// Amounts, names and questions are invented; this repository is public.

function emptyFooter(): Footer {
  return {
    netted_refunds: '0.00',
    refund_count: 0,
    excluded: [],
    unclassified: null,
    uncategorised: null,
    undated: null,
    unaccounted: null,
    unconvertible: [],
  }
}

const RULE: Rule = { all: [{ facet: 'category', op: 'in', values: ['software'] }] }

const PREVIEW: ChartData = {
  chart_id: null,
  grain: 'month',
  split: null,
  currency: 'USD',
  since: null,
  until: null,
  cells: [{ period: '2026-07-01', split_value: null, total: '500.00', payments: 3 }],
  splits: [],
  total: '500.00',
  payments: 3,
  documents: 3,
  footer: emptyFooter(),
}

const SAVED_CHART: Chart = {
  id: 9,
  name: 'How much do we spend on software?',
  question_text: 'How much do we spend on software?',
  rule: RULE,
  default_grain: 'month',
  default_split: 'category',
  display_currency: 'USD',
  ordinal: 0,
}

function draftOf(overrides: Partial<Draft> = {}): Draft {
  return {
    question: 'How much do we spend on software?',
    expressible: true,
    rule: RULE,
    proposed_split: 'category',
    unknown_terms: [],
    message: null,
    preview: PREVIEW,
    ...overrides,
  }
}

// --- Query helpers -----------------------------------------------------

function askInput(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="question-draft-input"]')
}
function askButton(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="question-draft-ask"]')
}
function saveButton(wrapper: VueWrapper) {
  return wrapper.get('[data-testid="question-draft-save"]')
}
function previewChart(wrapper: VueWrapper) {
  return wrapper.find('[data-testid="spending-chart"]')
}

/** Mount, type a question, submit, and wait for the resolved draft to render. */
async function drafted(overrides: Partial<Draft> = {}, currency = 'USD'): Promise<VueWrapper> {
  draftQuestion.mockResolvedValueOnce(draftOf(overrides))
  const wrapper = mount(QuestionDraft, { props: { currency } })
  await askInput(wrapper).setValue('How much do we spend on software?')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  draftQuestion.mockReset()
  createChart.mockReset()
})

describe('QuestionDraft', () => {
  it('does nothing on an empty question', async () => {
    const wrapper = mount(QuestionDraft, { props: { currency: 'USD' } })
    await wrapper.get('form').trigger('submit')
    expect(draftQuestion).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="question-draft-result"]').exists()).toBe(false)
  })

  it('sends the typed question and the currency prop, never one it chose', async () => {
    await drafted({}, 'GBP')
    expect(draftQuestion).toHaveBeenCalledWith({
      question: 'How much do we spend on software?',
      display_currency: 'GBP',
    })
  })

  it('shows the rule, the split, the preview and an enabled save when expressible', async () => {
    const wrapper = await drafted({ expressible: true, rule: RULE, preview: PREVIEW, proposed_split: 'category' })
    expect(wrapper.get('[data-testid="question-draft-rule"]').text()).toContain('category')
    expect(wrapper.get('[data-testid="question-draft-split"]').text()).toContain('category')
    expect(previewChart(wrapper).exists()).toBe(true)
    expect(wrapper.find('[data-testid="question-draft-approximate"]').exists()).toBe(false)
    expect(saveButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  it('labels a partial draft an approximation and still allows saving', async () => {
    const wrapper = await drafted({
      expressible: false,
      rule: RULE,
      preview: PREVIEW,
      unknown_terms: ['vibes'],
    })
    expect(wrapper.text()).toContain('approximation')
    expect(wrapper.text()).toContain('vibes')
    expect(previewChart(wrapper).exists()).toBe(true)
    expect(saveButton(wrapper).attributes('disabled')).toBeUndefined()
  })

  // The one that matters: an empty rule matches the whole archive, so
  // previewing it answers a narrow question with the archive's total.
  it('shows NO preview and disables save when every clause was dropped', async () => {
    const wrapper = await drafted({
      expressible: false,
      rule: null,
      preview: null,
      unknown_terms: ['vibes'],
      message: 'not in the vocabulary: vibes',
    })
    expect(previewChart(wrapper).exists()).toBe(false)
    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('not in the vocabulary')
  })

  // unknown_terms is model-authored text, already capped server-side.
  it('renders unknown terms as text, never as markup', async () => {
    const wrapper = await drafted({
      unknown_terms: ['<img src=x onerror=alert(1)>'],
      rule: null,
      preview: null,
      expressible: false,
    })
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
  })

  it('shows an unapproximated label for a fully unsplit, fully expressible draft', async () => {
    const wrapper = await drafted({ proposed_split: null, unknown_terms: [] })
    expect(wrapper.get('[data-testid="question-draft-split"]').text()).toBe('Not split.')
    expect(wrapper.find('[data-testid="question-draft-unknown-terms"]').exists()).toBe(false)
  })

  it('summarises an empty rule as matching every document', async () => {
    const wrapper = await drafted({ rule: { all: [] }, preview: PREVIEW })
    expect(wrapper.get('[data-testid="question-draft-rule"]').text()).toContain('Every document')
  })

  it('summarises a not_in clause distinctly from an in clause', async () => {
    const wrapper = await drafted({
      rule: { all: [{ facet: 'sender', op: 'not_in', values: ['acme'] }] },
    })
    expect(wrapper.get('[data-testid="question-draft-rule"]').text()).toContain('is not')
  })

  it('shows a drafting error from the API and lets the user retry', async () => {
    draftQuestion.mockRejectedValueOnce(new ApiError(422, 'Could not understand that question.'))
    const wrapper = mount(QuestionDraft, { props: { currency: 'USD' } })
    await askInput(wrapper).setValue('nonsense')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[data-testid="question-draft-error"]').text()).toBe(
      'Could not understand that question.',
    )
    expect(wrapper.find('[data-testid="question-draft-result"]').exists()).toBe(false)
  })

  it('falls back to a generic drafting error for a non-API failure', async () => {
    draftQuestion.mockRejectedValueOnce(new Error('network down'))
    const wrapper = mount(QuestionDraft, { props: { currency: 'USD' } })
    await askInput(wrapper).setValue('anything')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[data-testid="question-draft-error"]').text()).toBe('Could not draft this question.')
  })

  it('saves the drafted rule and split, emits the chart, and clears the draft', async () => {
    createChart.mockResolvedValueOnce(SAVED_CHART)
    const wrapper = await drafted({}, 'GBP')
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(createChart).toHaveBeenCalledWith({
      name: 'How much do we spend on software?',
      question_text: 'How much do we spend on software?',
      rule: RULE,
      default_split: 'category',
      display_currency: 'GBP',
    })
    expect(wrapper.emitted('saved')).toEqual([[SAVED_CHART]])
    expect(wrapper.find('[data-testid="question-draft-result"]').exists()).toBe(false)
  })

  it('shows a save error from the API and keeps the draft visible to retry', async () => {
    createChart.mockRejectedValueOnce(new ApiError(409, 'A chart with this name already exists.'))
    const wrapper = await drafted()
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="question-draft-save-error"]').text()).toBe(
      'A chart with this name already exists.',
    )
    expect(wrapper.emitted('saved')).toBeUndefined()
    expect(wrapper.find('[data-testid="question-draft-result"]').exists()).toBe(true)
  })

  it('falls back to a generic save error for a non-API failure', async () => {
    createChart.mockRejectedValueOnce(new Error('network down'))
    const wrapper = await drafted()
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="question-draft-save-error"]').text()).toBe('Could not save this chart.')
  })

  // A save failure attached to an earlier draft must not survive onto a
  // fresh, unrelated one.
  it('clears a stale save error when a new question is submitted', async () => {
    createChart.mockRejectedValueOnce(new ApiError(409, 'A chart with this name already exists.'))
    const wrapper = await drafted()
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="question-draft-save-error"]').exists()).toBe(true)

    draftQuestion.mockResolvedValueOnce(draftOf({ question: 'How much do we spend on hosting?' }))
    await askInput(wrapper).setValue('How much do we spend on hosting?')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="question-draft-save-error"]').exists()).toBe(false)
  })

  it('does nothing when save is clicked while the rule is null', async () => {
    const wrapper = await drafted({ rule: null, preview: null, expressible: false })
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(createChart).not.toHaveBeenCalled()
  })

  it('does not re-submit a question while a draft request is in flight', async () => {
    let resolveDraft!: (value: Draft) => void
    draftQuestion.mockReturnValueOnce(new Promise<Draft>((resolve) => { resolveDraft = resolve }))
    const wrapper = mount(QuestionDraft, { props: { currency: 'USD' } })
    await askInput(wrapper).setValue('slow question')
    await wrapper.get('form').trigger('submit')
    await wrapper.get('form').trigger('submit')
    expect(draftQuestion).toHaveBeenCalledTimes(1)
    resolveDraft(draftOf())
    await flushPromises()
  })

  it('the Ask button is disabled while drafting is in flight and once the input is empty', async () => {
    let resolveDraft!: (value: Draft) => void
    draftQuestion.mockReturnValueOnce(new Promise<Draft>((resolve) => { resolveDraft = resolve }))
    const wrapper = mount(QuestionDraft, { props: { currency: 'USD' } })
    expect(askButton(wrapper).attributes('disabled')).toBeDefined()
    await askInput(wrapper).setValue('a question')
    expect(askButton(wrapper).attributes('disabled')).toBeUndefined()
    await wrapper.get('form').trigger('submit')
    expect(askButton(wrapper).attributes('disabled')).toBeDefined()
    resolveDraft(draftOf())
    await flushPromises()
  })
})
