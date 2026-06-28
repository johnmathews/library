import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import AskView from '../AskView.vue'
import { askQuestion, getThread, listThreads, type AskResponse } from '@/api/ask'
import { ApiError } from '@/api/client'

vi.mock('@/api/ask', () => ({
  askQuestion: vi.fn(),
  getThread: vi.fn(),
  listThreads: vi.fn(),
  deleteThread: vi.fn(),
}))

const askQuestionMock = vi.mocked(askQuestion)
const getThreadMock = vi.mocked(getThread)

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
    thread_id: 1,
    ...overrides,
  }
}

describe('AskView', () => {
  let router: Router
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    askQuestionMock.mockReset()
    getThreadMock.mockReset()
    vi.mocked(listThreads).mockResolvedValue([])
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/ask', name: 'ask', component: AskView },
        { path: '/ask/:threadId', name: 'ask-thread', component: AskView },
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

  async function typeAndSubmit(w: VueWrapper, question: string): Promise<void> {
    await w.find('#ask-question').setValue(question)
    await w.find('[data-testid="ask-form"]').trigger('submit')
    await flushPromises()
  }

  it('renders the page heading', () => {
    const w = mountView()
    expect(w.find('h1').text()).toBe('Ask')
  })

  it('appends a turn to the transcript after a successful ask', async () => {
    askQuestionMock.mockResolvedValueOnce(
      sampleResponse({
        answer: 'Two invoices are due this month.',
        citations: [{ document_id: 7, title: 'Energy bill', page_number: null }],
        thread_id: 42,
      }),
    )
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    const turns = w.findAll('[data-testid="ask-turn"]')
    expect(turns).toHaveLength(1)
    expect(turns[0]!.find('[data-testid="ask-answer"]').text()).toContain('Two invoices are due')
    expect(turns[0]!.find('[data-testid="ask-citation"]').text()).toContain('Energy bill')
  })

  it('appends a second turn and posts with thread_id on follow-up', async () => {
    getThreadMock.mockResolvedValue({ id: 42, title: '', turns: [] })
    askQuestionMock.mockResolvedValueOnce({
      answer: 'First answer [#1].',
      citations: [{ document_id: 1, title: 'Doc', page_number: null }],
      used_tools: ['semantic_search'],
      cost_usd: 0.01,
      thread_id: 42,
    })
    const w = mountView()
    await typeAndSubmit(w, 'first?')
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)

    askQuestionMock.mockResolvedValueOnce({
      answer: 'Second answer.',
      citations: [],
      used_tools: [],
      cost_usd: 0.01,
      thread_id: 42,
    })
    await typeAndSubmit(w, 'and then?')
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(2)
    expect(askQuestionMock).toHaveBeenLastCalledWith('and then?', 42, expect.anything())
  })

  it('loads a thread when mounted on /ask/:threadId', async () => {
    getThreadMock.mockResolvedValue({
      id: 7,
      title: 'Energy',
      turns: [
        {
          id: 1,
          query: 'who?',
          answer: 'Vattenfall [#3].',
          citations: [{ document_id: 3, title: 'Bill', page_number: 2 }],
          used_tools: ['query_documents'],
          cost_usd: 0.02,
          created_at: '',
        },
      ],
    })
    await router.push('/ask/7')
    const w = mountView()
    await flushPromises()
    expect(getThreadMock).toHaveBeenCalledWith(7)
    expect(w.text()).toContain('Vattenfall')
  })

  it('shows a friendly error summary when the API returns 503', async () => {
    askQuestionMock.mockRejectedValue(new ApiError(503, 'no API key configured'))
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    const summary = w.find('[data-testid="error-summary"]')
    expect(summary.exists()).toBe(true)
    expect(summary.text()).toContain('no AI API key is configured')
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(0)
  })

  it('syncs the URL to /ask/:threadId and shows no error on a successful ask', async () => {
    askQuestionMock.mockResolvedValueOnce(sampleResponse({ thread_id: 42 }))
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)
    expect(w.find('[data-testid="error-summary"]').exists()).toBe(false)
    expect(router.currentRoute.value.name).toBe('ask-thread')
    expect(router.currentRoute.value.params.threadId).toBe('42')
  })

  it('shows no error summary when a successful response omits thread_id', async () => {
    // The real backend always returns thread_id, but a malformed/partial
    // response (or an incomplete test mock) can omit it. A rendered answer
    // must never be turned into a generic "Something went wrong" error by a
    // failed post-success navigation. Regression guard for the spurious
    // error-on-success alert.
    const withoutThreadId = sampleResponse()
    delete (withoutThreadId as Partial<AskResponse>).thread_id
    askQuestionMock.mockResolvedValueOnce(withoutThreadId)
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    // The answer turn renders…
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)
    // …and no error alert appears.
    expect(w.find('[data-testid="error-summary"]').exists()).toBe(false)
  })

  it('validates an empty question without calling the API', async () => {
    const w = mountView()
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(askQuestionMock).not.toHaveBeenCalled()
    expect(w.find('[data-testid="error-summary"]').text()).toContain('Enter a question')
  })

  it('renders markdown in the answer as HTML', async () => {
    askQuestionMock.mockResolvedValueOnce(
      sampleResponse({ answer: 'Your supplier was **PWN** [#67].\n\n- one\n- two', thread_id: 1 }),
    )
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    const turn = w.get('[data-testid="ask-turn"]')
    const answer = turn.get('[data-testid="ask-answer"]')
    expect(answer.find('strong').text()).toBe('PWN')
    expect(answer.html()).not.toContain('**PWN**')
    expect(answer.findAll('li')).toHaveLength(2)
  })

  it('renders the page number on a citation and links with a page query', async () => {
    askQuestionMock.mockResolvedValueOnce(
      sampleResponse({
        citations: [{ document_id: 42, title: 'Energy bill', page_number: 3 }],
        thread_id: 1,
      }),
    )
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    const link = w.get('[data-testid="ask-citation"]')
    expect(link.text()).toContain('p. 3')
    expect(link.attributes('href')).toContain('page=3')
  })

  it('omits the page label when page_number is null', async () => {
    askQuestionMock.mockResolvedValueOnce(
      sampleResponse({
        citations: [{ document_id: 7, title: 'Note', page_number: null }],
        thread_id: 1,
      }),
    )
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    expect(w.get('[data-testid="ask-citation"]').text()).not.toContain('p.')
  })

  it('disables the button and shows progress while the request is pending', async () => {
    let resolve!: (value: AskResponse) => void
    askQuestionMock.mockReturnValue(
      new Promise<AskResponse>((r) => {
        resolve = r
      }),
    )
    const w = mountView()
    await w.find('#ask-question').setValue('which invoices are due?')
    await w.find('[data-testid="ask-form"]').trigger('submit')
    await flushPromises()

    const button = w.find('[data-testid="ask-submit"]')
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.text()).toContain('Sending')
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(0)

    resolve(sampleResponse({ thread_id: 1 }))
    await flushPromises()

    expect(w.find('[data-testid="ask-submit"]').attributes('disabled')).toBeUndefined()
    expect(w.find('[data-testid="ask-submit"]').text()).toBe('Send')
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)
  })

  it('uses the shared PageHeader and imposes no max-width cap (W10)', () => {
    const w = mountView()
    expect(w.find('[data-testid="page-header"]').exists()).toBe(true)
    expect(w.find('#ask-page').classes()).not.toContain('max-w-6xl')
  })

  it('renders a bottom composer with a Send button (W10)', () => {
    const w = mountView()
    // Not position:sticky — a sticky bottom bar overlaps the last turn's
    // citations on short viewports and intercepts their clicks. In the chat
    // layout it is a shrink-0 flex sibling below the scrolling transcript.
    const form = w.find('[data-testid="ask-form"]')
    expect(form.exists()).toBe(true)
    expect(form.classes()).not.toContain('sticky')
    expect(form.classes()).toContain('shrink-0')
    expect(w.find('[data-testid="ask-submit"]').text()).toBe('Send')
  })

  it('renders an internally-scrolling transcript at lg+ (chat layout, Option A)', () => {
    const w = mountView()
    const transcript = w.find('[data-testid="ask-transcript"]')
    expect(transcript.exists()).toBe(true)
    // Internal scroll is gated to lg+ — on mobile/tablet the transcript flows
    // normally (a fixed-height internal-scroll column trapped citation clicks on
    // mobile-webkit).
    expect(transcript.classes()).toContain('lg:overflow-y-auto')
  })

  it('shows an empty-state prompt before any question (W10)', () => {
    const w = mountView()
    expect(w.find('[data-testid="ask-empty"]').exists()).toBe(true)
  })

  async function attachImage(w: VueWrapper, name = 'receipt.png'): Promise<void> {
    const file = new File(['hello'], name, { type: 'image/png' })
    const input = w.find('[data-testid="ask-image-input"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    // FileReader.onload fires as a macrotask in jsdom — poll until the preview lands.
    for (let i = 0; i < 5 && !w.find('[data-testid="ask-image-preview"]').exists(); i++) {
      await new Promise((resolve) => setTimeout(resolve, 0))
      await flushPromises()
    }
  }

  it('previews an attached image and sends it with the question (W11)', async () => {
    askQuestionMock.mockResolvedValue(sampleResponse({ thread_id: 1 }))
    const w = mountView()
    await attachImage(w)
    expect(w.find('[data-testid="ask-image-preview"]').exists()).toBe(true)

    await w.find('#ask-question').setValue('what is this?')
    await w.find('[data-testid="ask-form"]').trigger('submit')
    await flushPromises()

    const call = askQuestionMock.mock.calls[0]!
    // askQuestion(question, threadId, signal, images)
    expect(call[3]).toEqual([{ media_type: 'image/png', data: 'aGVsbG8=' }])
    // Cleared after a successful send.
    expect(w.find('[data-testid="ask-image-preview"]').exists()).toBe(false)
  })

  it('removes a pending image before sending (W11)', async () => {
    const w = mountView()
    await attachImage(w)
    expect(w.find('[data-testid="ask-image-preview"]').exists()).toBe(true)
    await w.find('[data-testid="ask-image-remove"]').trigger('click')
    expect(w.find('[data-testid="ask-image-preview"]').exists()).toBe(false)
  })
})
