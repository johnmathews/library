import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import AskView from '../AskView.vue'
import { askQuestion, type AskResponse } from '@/api/ask'
import { ApiError } from '@/api/client'

vi.mock('@/api/ask', () => ({
  askQuestion: vi.fn(),
}))

const askQuestionMock = vi.mocked(askQuestion)

const Stub = { template: '<div />' }

function sampleResponse(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    answer: 'Two invoices are due this month.',
    citations: [
      { document_id: 7, title: 'Energy bill', page_number: null },
      { document_id: 12, title: null, page_number: null },
    ],
    used_tools: ['search'],
    cost_usd: 0.0123,
    ...overrides,
  }
}

describe('AskView', () => {
  let router: Router
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    askQuestionMock.mockReset()
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/ask', name: 'ask', component: AskView },
        { path: '/documents/:id', name: 'document-detail', component: Stub },
      ],
    })
    await router.push('/ask')
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
  })

  function mountView(): VueWrapper {
    wrapper = mount(AskView, { global: { plugins: [router] }, attachTo: document.body })
    return wrapper
  }

  async function ask(w: VueWrapper, question = 'which invoices are due?'): Promise<void> {
    await w.find('#ask-question').setValue(question)
    await w.find('#ask-form').trigger('submit')
  }

  it('renders the page heading', () => {
    const w = mountView()
    expect(w.find('h1').text()).toBe('Ask')
  })

  it('renders the answer and citation links after a successful ask', async () => {
    askQuestionMock.mockResolvedValue(sampleResponse())
    const w = mountView()
    await ask(w)
    await flushPromises()

    expect(askQuestionMock).toHaveBeenCalledWith('which invoices are due?')

    const result = w.find('[data-testid="ask-result"]')
    expect(result.exists()).toBe(true)
    expect(w.find('[data-testid="ask-answer"]').text()).toContain('Two invoices are due')

    const citations = w.findAll('[data-testid="ask-citation"]')
    expect(citations).toHaveLength(2)
    expect(citations[0]!.attributes('href')).toBe('/documents/7')
    expect(citations[0]!.text()).toContain('Energy bill')
    expect(citations[0]!.text()).toContain('#7')
    // null title falls back to "Untitled"
    expect(citations[1]!.attributes('href')).toBe('/documents/12')
    expect(citations[1]!.text()).toContain('Untitled')

    // tools / cost shown subtly
    const meta = w.find('[data-testid="ask-meta"]')
    expect(meta.text()).toContain('search')
    expect(meta.text()).toContain('$0.0123')
  })

  it('renders markdown in the answer as HTML', async () => {
    askQuestionMock.mockResolvedValue(
      sampleResponse({ answer: 'Your supplier was **PWN** [#67].\n\n- one\n- two' }),
    )
    const w = mountView()
    await ask(w)
    await flushPromises()

    const answer = w.find('[data-testid="ask-answer"]')
    // bold becomes <strong>, not literal asterisks
    expect(answer.find('strong').text()).toBe('PWN')
    expect(answer.html()).not.toContain('**PWN**')
    // list items render
    expect(answer.findAll('li')).toHaveLength(2)
  })

  it('disables the button and shows progress while the request is pending', async () => {
    let resolve!: (value: AskResponse) => void
    askQuestionMock.mockReturnValue(
      new Promise<AskResponse>((r) => {
        resolve = r
      }),
    )
    const w = mountView()
    await ask(w)
    await flushPromises()

    const button = w.find('#ask-submit')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toContain('Asking')
    expect(w.find('[data-testid="ask-result"]').exists()).toBe(false)

    resolve(sampleResponse())
    await flushPromises()

    expect(w.find('#ask-submit').attributes('disabled')).toBeUndefined()
    expect(w.find('#ask-submit').text()).toBe('Ask')
    expect(w.find('[data-testid="ask-result"]').exists()).toBe(true)
  })

  it('shows a friendly error summary when the API returns 503', async () => {
    askQuestionMock.mockRejectedValue(new ApiError(503, 'no API key configured'))
    const w = mountView()
    await ask(w)
    await flushPromises()

    const summary = w.find('[data-testid="error-summary"]')
    expect(summary.exists()).toBe(true)
    expect(summary.text()).toContain('no AI API key is configured')
    expect(w.find('[data-testid="ask-result"]').exists()).toBe(false)
  })

  it('validates an empty question without calling the API', async () => {
    const w = mountView()
    await w.find('#ask-form').trigger('submit')
    await flushPromises()

    expect(askQuestionMock).not.toHaveBeenCalled()
    expect(w.find('[data-testid="error-summary"]').text()).toContain('Enter a question')
  })

  it('renders the page number on a citation and links with a page query', async () => {
    askQuestionMock.mockResolvedValue(
      sampleResponse({
        citations: [{ document_id: 42, title: 'Energy bill', page_number: 3 }],
      }),
    )
    const w = mountView()
    await ask(w)
    await flushPromises()

    const link = w.get('[data-testid="ask-citation"]')
    expect(link.text()).toContain('p. 3')
    expect(link.attributes('href')).toContain('page=3')
  })

  it('omits the page label when page_number is null', async () => {
    askQuestionMock.mockResolvedValue(
      sampleResponse({
        citations: [{ document_id: 7, title: 'Note', page_number: null }],
      }),
    )
    const w = mountView()
    await ask(w)
    await flushPromises()

    expect(w.get('[data-testid="ask-citation"]').text()).not.toContain('p.')
  })
})
