import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import AskView from '../AskView.vue'
import {
  askQuestion,
  deleteThread,
  getThread,
  listThreads,
  renameThread,
  type AskResponse,
  type ThreadDetail,
} from '@/api/ask'
import { getLLMBackends, type LLMBackends } from '@/api/settings'
import { ApiError } from '@/api/client'
import { usePageTitle } from '@/composables/usePageTitle'
import { ASK_VIEW_MODE_STORAGE_KEY } from '@/composables/useAskViewMode'

vi.mock('@/api/ask', () => ({
  askQuestion: vi.fn(),
  getThread: vi.fn(),
  listThreads: vi.fn(),
  deleteThread: vi.fn(),
  renameThread: vi.fn(),
}))
vi.mock('@/api/settings', () => ({ getLLMBackends: vi.fn() }))

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
    turn_id: 101,
    ...overrides,
  }
}

function threadWith(queries: string[], id = 7): ThreadDetail {
  return {
    id,
    title: 'Energy',
    turns: queries.map((query, i) => ({
      id: 100 + i,
      query,
      answer: `answer ${i + 1}`,
      citations: [],
      used_tools: [],
      cost_usd: 0.02,
      created_at: '',
    })),
  }
}

function backends(model = 'claude-opus-4-8'): LLMBackends {
  return {
    surfaces: [
      {
        surface: 'ask',
        label: 'Ask',
        description: '',
        backend: 'subscription',
        default: 'subscription',
        overridden: false,
        model,
      },
    ],
    credentials_status: 'healthy',
    credentials_detail: '',
    api_key_configured: true,
    editable: false,
  }
}

describe('AskView', () => {
  let router: Router
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    askQuestionMock.mockReset()
    getThreadMock.mockReset()
    vi.mocked(listThreads).mockResolvedValue([])
    vi.mocked(getLLMBackends).mockResolvedValue(backends())
    localStorage.clear()
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/ask', name: 'ask', component: AskView },
        { path: '/ask/new', name: 'ask-new', component: AskView },
        { path: '/ask/:threadId(\\d+)', name: 'ask-thread', component: AskView },
        { path: '/documents/:id', name: 'document-detail', component: Stub },
      ],
    })
    await router.push('/ask')
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
  })

  /** Simulate the lg+ breakpoint for useMediaQuery (jsdom has no matchMedia,
   * which vueuse treats as "no match", i.e. a phone). */
  function stubDesktopViewport(): void {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }))
  }

  function mountView(): VueWrapper {
    wrapper = mount(AskView, { global: { plugins: [router] }, attachTo: document.body })
    return wrapper
  }

  async function typeAndSubmit(w: VueWrapper, question: string): Promise<void> {
    await w.find('#ask-question').setValue(question)
    await w.find('[data-testid="ask-form"]').trigger('submit')
    await flushPromises()
  }

  async function openThread(queries: string[]): Promise<VueWrapper> {
    getThreadMock.mockResolvedValue(threadWith(queries))
    await router.push('/ask/7')
    const w = mountView()
    await flushPromises()
    return w
  }

  // ── Shell ──────────────────────────────────────────────────────────────────

  it('claims the page title for the app bar and renders no heading of its own', () => {
    const w = mountView()
    expect(w.find('h1').exists()).toBe(false)
    expect(usePageTitle().pageTitle.value).toBe('Ask')
  })

  it('has no conversation list beside the transcript; conversations live in the title menu', () => {
    const w = mountView()
    expect(w.find('[data-testid="conversation-sidebar"]').exists()).toBe(false)
    expect(w.find('[data-testid="ask-title"]').exists()).toBe(true)
  })

  it('shows which model and backend will answer under the composer', async () => {
    const w = mountView()
    await flushPromises()
    expect(w.get('[data-testid="ask-model-chip"]').text()).toBe('Opus 4.8 · Subscription')
  })

  it('still works when the model cannot be loaded — the chip is simply absent', async () => {
    vi.mocked(getLLMBackends).mockRejectedValue(new ApiError(500, 'boom'))
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="ask-model-chip"]').exists()).toBe(false)
    expect(w.find('#ask-question').exists()).toBe(true)
  })

  // ── Asking ─────────────────────────────────────────────────────────────────

  it('disables New conversation in the fresh state and enables it after an ask', async () => {
    askQuestionMock.mockResolvedValueOnce(sampleResponse())
    getThreadMock.mockResolvedValue(threadWith(['which invoices are due?'], 1))
    const w = mountView()
    expect(w.get('[data-testid="new-conversation"]').attributes('disabled')).toBeDefined()

    await typeAndSubmit(w, 'which invoices are due?')

    expect(w.get('[data-testid="new-conversation"]').attributes('disabled')).toBeUndefined()
  })

  it('appends a turn: the question as a bubble, the answer as text', async () => {
    askQuestionMock.mockResolvedValueOnce(sampleResponse())
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)
    expect(w.get('[data-testid="ask-question-bubble"]').text()).toBe('which invoices are due?')
    expect(w.get('[data-testid="ask-answer"]').text()).toContain('Two invoices are due')
    expect(w.find('[data-testid="ask-greeting"]').exists()).toBe(false)
  })

  it('posts a follow-up with the thread id', async () => {
    askQuestionMock
      .mockResolvedValueOnce(sampleResponse({ thread_id: 5 }))
      .mockResolvedValueOnce(sampleResponse({ thread_id: 5, turn_id: 102 }))
    getThreadMock.mockResolvedValue(threadWith(['first'], 5))
    const w = mountView()
    await typeAndSubmit(w, 'first')
    await typeAndSubmit(w, 'second')

    expect(askQuestionMock.mock.calls[1]![1]).toBe(5)
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(2)
  })

  it('moves the URL to the thread and shows its generated title after the first answer', async () => {
    askQuestionMock.mockResolvedValueOnce(sampleResponse({ thread_id: 42 }))
    getThreadMock.mockResolvedValue({ ...threadWith(['q'], 42), title: 'Invoices due' })
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('ask-thread')
    expect(router.currentRoute.value.params.threadId).toBe('42')
    expect(w.get('[data-testid="ask-title"]').text()).toContain('Invoices due')
    expect(w.find('[data-testid="error-summary"]').exists()).toBe(false)
  })

  it('shows no error when a successful response omits thread_id', async () => {
    const withoutThreadId = sampleResponse()
    delete (withoutThreadId as Partial<AskResponse>).thread_id
    askQuestionMock.mockResolvedValueOnce(withoutThreadId)
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)
    expect(w.find('[data-testid="error-summary"]').exists()).toBe(false)
  })

  it('validates an empty question without calling the API', async () => {
    const w = mountView()
    await w.find('[data-testid="ask-form"]').trigger('submit')
    await flushPromises()

    expect(askQuestionMock).not.toHaveBeenCalled()
    expect(w.get('[data-testid="error-summary"]').text()).toContain('Enter a question to ask')
  })

  it("shows the server's reason when the API returns 503", async () => {
    const detail =
      'Ask is unavailable: the Claude subscription backend could not authenticate: ' +
      'OAuth session expired'
    askQuestionMock.mockRejectedValue(new ApiError(503, detail, { detail }))
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    const summary = w.get('[data-testid="error-summary"]')
    expect(summary.text()).toContain('could not authenticate')
    expect(summary.text()).not.toContain('API key')
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(0)
  })

  it('shows a generic unavailable message for a 503 with no JSON body', async () => {
    askQuestionMock.mockRejectedValue(new ApiError(503, 'Service Unavailable'))
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    expect(w.get('[data-testid="error-summary"]').text()).toContain(
      'Answering is unavailable right now',
    )
  })

  it('optimistically shows the question with a thinking indicator, and a live Stop', async () => {
    let resolve!: (value: AskResponse) => void
    askQuestionMock.mockReturnValue(new Promise<AskResponse>((r) => (resolve = r)))
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)
    expect(w.find('[data-testid="ask-thinking"]').exists()).toBe(true)
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe('')
    const button = w.get('[data-testid="ask-submit"]')
    expect(button.attributes('aria-label')).toBe('Stop answering')
    expect(button.attributes('disabled')).toBeUndefined()

    resolve(sampleResponse())
    await flushPromises()

    expect(w.find('[data-testid="ask-thinking"]').exists()).toBe(false)
    expect(w.get('[data-testid="ask-submit"]').attributes('aria-label')).toBe('Send')
  })

  it('Stop aborts the request, removes the optimistic turn and restores the question', async () => {
    let reject!: (reason: unknown) => void
    askQuestionMock.mockReturnValue(new Promise<AskResponse>((_r, rj) => (reject = rj)))
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    await w.get('[data-testid="ask-submit"]').trigger('click')
    const signal = askQuestionMock.mock.calls[0]![2] as AbortSignal
    expect(signal.aborted).toBe(true)
    reject(new DOMException('aborted', 'AbortError'))
    await flushPromises()

    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(0)
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe(
      'which invoices are due?',
    )
    expect(w.find('[data-testid="error-summary"]').exists()).toBe(false)
  })

  it('removes the optimistic turn and restores the question on an API error', async () => {
    askQuestionMock.mockRejectedValue(new ApiError(500, 'boom', { detail: 'boom' }))
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(0)
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe(
      'which invoices are due?',
    )
  })

  // ── Scrolling ──────────────────────────────────────────────────────────────

  function trackScroll(el: HTMLElement): { top: () => number; max: () => number } {
    let current = 0
    let max = 0
    Object.defineProperty(el, 'scrollTop', {
      configurable: true,
      get: () => current,
      set: (v: number) => {
        current = v
        max = Math.max(max, v)
      },
    })
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => 5000 })
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 400 })
    return { top: () => current, max: () => max }
  }

  it('starts an opened conversation at the top, not scrolled into the answer', async () => {
    getThreadMock.mockResolvedValue(threadWith(['who is my supplier?']))
    await router.push('/ask/7')
    const w = mountView()
    // Real geometry BEFORE the load resolves, or the test passes vacuously:
    // jsdom reports 0 heights, so scroll-to-bottom would never move either.
    const scroll = trackScroll(w.get('[data-testid="ask-transcript"]').element as HTMLElement)
    await flushPromises()
    await nextTick()

    expect(scroll.top()).toBe(0)
  })

  it('drives the transcript downward when a new answer arrives', async () => {
    askQuestionMock.mockResolvedValueOnce(sampleResponse())
    const w = mountView()
    const scroll = trackScroll(w.get('[data-testid="ask-transcript"]').element as HTMLElement)
    await typeAndSubmit(w, 'which invoices are due?')
    await nextTick()

    expect(scroll.max()).toBeGreaterThan(0)
  })

  // ── Retry and edit ─────────────────────────────────────────────────────────

  it('Retry re-asks the last question in place of its turn', async () => {
    const w = await openThread(['q1', 'q2'])
    askQuestionMock.mockResolvedValueOnce(
      sampleResponse({ thread_id: 7, turn_id: 200, answer: 'a better answer' }),
    )

    await w.get('[data-testid="ask-retry"]').trigger('click')
    await flushPromises()

    const [question, thread, , , replaceFrom] = askQuestionMock.mock.calls[0]!
    expect(question).toBe('q2')
    expect(thread).toBe(7)
    expect(replaceFrom).toBe(101)
    const answers = w.findAll('[data-testid="ask-answer"]').map((a) => a.text())
    expect(answers).toEqual(['answer 1', 'a better answer'])
  })

  it('offers Retry on the latest answer only', async () => {
    const w = await openThread(['q1', 'q2', 'q3'])
    expect(w.findAll('[data-testid="ask-retry"]')).toHaveLength(1)
    const turns = w.findAll('[data-testid="ask-turn"]')
    expect(turns[2]!.find('[data-testid="ask-retry"]').exists()).toBe(true)
  })

  it('a failed retry puts the original answer back', async () => {
    const w = await openThread(['q1', 'q2'])
    const detail = 'Ask is unavailable: the Claude subscription backend could not authenticate'
    askQuestionMock.mockRejectedValueOnce(new ApiError(503, detail, { detail }))

    await w.get('[data-testid="ask-retry"]').trigger('click')
    await flushPromises()

    const answers = w.findAll('[data-testid="ask-answer"]').map((a) => a.text())
    expect(answers).toEqual(['answer 1', 'answer 2'])
    expect(w.get('[data-testid="error-summary"]').text()).toContain('could not authenticate')
  })

  it('editing an earlier question re-asks from there and drops the later turns', async () => {
    const w = await openThread(['q1', 'q2', 'q3'])
    let resolve!: (value: AskResponse) => void
    askQuestionMock.mockReturnValue(new Promise<AskResponse>((r) => (resolve = r)))

    const second = w.findAll('[data-testid="ask-turn"]')[1]!
    await second.get('[data-testid="ask-edit-start"]').trigger('click')
    // The editor says what sending will cost before anything is sent.
    expect(second.get('[data-testid="ask-edit-note"]').text()).toContain('the one after it')
    await second.get('[data-testid="ask-edit-input"]').setValue('q2, but for 2025')
    await second.get('[data-testid="ask-edit-submit"]').trigger('click')
    await flushPromises()

    expect(askQuestionMock.mock.calls[0]![0]).toBe('q2, but for 2025')
    expect(askQuestionMock.mock.calls[0]![4]).toBe(101)
    // On screen at once: q1 stays, the edited question is pending, q3 is gone.
    const bubbles = w.findAll('[data-testid="ask-question-bubble"]').map((b) => b.text())
    expect(bubbles).toEqual(['q1', 'q2, but for 2025'])
    expect(w.find('[data-testid="ask-thinking"]').exists()).toBe(true)

    resolve(sampleResponse({ thread_id: 7, turn_id: 300, answer: 'for 2025: two' }))
    await flushPromises()
    expect(w.findAll('[data-testid="ask-answer"]').map((a) => a.text())).toEqual([
      'answer 1',
      'for 2025: two',
    ])
  })

  it('cancelling an edit changes nothing', async () => {
    const w = await openThread(['q1', 'q2'])
    const first = w.findAll('[data-testid="ask-turn"]')[0]!
    await first.get('[data-testid="ask-edit-start"]').trigger('click')
    await first.get('[data-testid="ask-edit-input"]').setValue('something else')
    await first.get('[data-testid="ask-edit-cancel"]').trigger('click')

    expect(askQuestionMock).not.toHaveBeenCalled()
    expect(w.findAll('[data-testid="ask-question-bubble"]').map((b) => b.text())).toEqual([
      'q1',
      'q2',
    ])
  })

  // ── Conversation actions ───────────────────────────────────────────────────

  it('renames the conversation from the title bar', async () => {
    vi.mocked(renameThread).mockResolvedValue({
      id: 7,
      title: 'Power bills',
      created_at: '',
      updated_at: '',
      turn_count: 1,
      total_cost_usd: 0,
    })
    const w = await openThread(['q1'])
    await w.get('[data-testid="ask-title-actions"]').trigger('click')
    await w.get('[data-testid="thread-rename"]').trigger('click')
    await w.get('[data-testid="ask-title-rename-input"]').setValue('Power bills')
    await w.get('[data-testid="ask-title-rename-save"]').trigger('click')
    await flushPromises()

    expect(renameThread).toHaveBeenCalledWith(7, 'Power bills')
    expect(w.get('[data-testid="ask-title"]').text()).toContain('Power bills')
  })

  it('deletes the conversation after a confirmation and opens a new one', async () => {
    vi.mocked(deleteThread).mockResolvedValue(undefined)
    const w = await openThread(['q1'])
    await w.get('[data-testid="ask-title-actions"]').trigger('click')
    await w.get('[data-testid="thread-delete"]').trigger('click')
    expect(deleteThread).not.toHaveBeenCalled()
    await w.get('[data-testid="ask-title-delete-confirm"]').trigger('click')
    await flushPromises()

    expect(deleteThread).toHaveBeenCalledWith(7)
    expect(router.currentRoute.value.name).toBe('ask-new')
  })

  it('opens a conversation chosen from the title switcher', async () => {
    vi.mocked(listThreads).mockResolvedValue([
      { id: 3, title: 'Car insurance', created_at: '', updated_at: '', turn_count: 1, total_cost_usd: 0 },
    ])
    getThreadMock.mockResolvedValue(threadWith(['insured?'], 3))
    const w = mountView()
    await w.get('[data-testid="ask-title"]').trigger('click')
    await flushPromises()
    await w.get('[data-testid="thread-item"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.params.threadId).toBe('3')
    expect(w.get('[data-testid="ask-question-bubble"]').text()).toBe('insured?')
  })

  // ── New conversation ───────────────────────────────────────────────────────

  it('greets a new conversation with example prompts that fill the composer', async () => {
    const w = mountView()
    expect(w.find('[data-testid="ask-greeting"]').exists()).toBe(true)
    await w.findAll('[data-testid="ask-example-prompt"]')[0]!.trigger('click')
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe(
      'Which invoices are due this month?',
    )
  })

  it('seeds the composer from ?q= and focuses it', async () => {
    await router.push('/ask?q=Hello%20world')
    const w = mountView()
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('ask-new')
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe('Hello world')
    expect(document.activeElement).toBe(w.find('#ask-question').element)
  })

  it('resumes a thread on /ask/:threadId with an empty composer', async () => {
    const w = await openThread(['who?'])
    expect(getThreadMock).toHaveBeenCalledWith(7)
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe('')
    expect(w.text()).toContain('answer 1')
  })

  it('drops a stale in-flight answer when the user switches threads mid-request', async () => {
    let resolveAsk!: (value: AskResponse) => void
    askQuestionMock.mockReturnValue(new Promise<AskResponse>((r) => (resolveAsk = r)))
    getThreadMock.mockResolvedValue(threadWith(['who?']))
    await router.push('/ask/new')
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    await router.push('/ask/7')
    await flushPromises()
    resolveAsk(sampleResponse({ thread_id: 99 }))
    await flushPromises()

    expect(router.currentRoute.value.params.threadId).toBe('7')
    expect(w.findAll('[data-testid="ask-question-bubble"]').map((b) => b.text())).toEqual(['who?'])
  })

  // ── Images ─────────────────────────────────────────────────────────────────

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

  it('previews an attached image and sends it with the question', async () => {
    askQuestionMock.mockResolvedValue(sampleResponse())
    const w = mountView()
    await attachImage(w)
    expect(w.find('[data-testid="ask-image-preview"]').exists()).toBe(true)

    await typeAndSubmit(w, 'what is this?')

    expect(askQuestionMock.mock.calls[0]![3]).toEqual([{ media_type: 'image/png', data: 'aGVsbG8=' }])
    expect(w.find('[data-testid="ask-image-preview"]').exists()).toBe(false)
  })

  it('removes a pending image before sending', async () => {
    const w = mountView()
    await attachImage(w)
    await w.get('[data-testid="ask-image-remove"]').trigger('click')
    expect(w.find('[data-testid="ask-image-preview"]').exists()).toBe(false)
  })

  // ── Layout ─────────────────────────────────────────────────────────────────

  it('renders the conversation layout by default on a desktop', async () => {
    stubDesktopViewport()
    askQuestionMock.mockResolvedValueOnce(sampleResponse())
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    expect(w.find('[data-testid="ask-question-bubble"]').exists()).toBe(true)
    expect(w.find('[data-testid="ask-doc-block"]').exists()).toBe(false)
    expect(w.get('[data-testid="ask-view-mode-conversation"]').attributes('aria-pressed')).toBe(
      'true',
    )
  })

  it('switches to the document layout and remembers it', async () => {
    stubDesktopViewport()
    askQuestionMock.mockResolvedValueOnce(sampleResponse())
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    await w.get('[data-testid="ask-view-mode-document"]').trigger('click')

    expect(w.find('[data-testid="ask-doc-block"]').exists()).toBe(true)
    expect(w.find('[data-testid="ask-question-bubble"]').exists()).toBe(false)
    expect(localStorage.getItem(ASK_VIEW_MODE_STORAGE_KEY)).toBe('document')
  })

  it('offers no layout toggle on a phone', () => {
    const w = mountView()
    expect(w.find('[data-testid="ask-view-mode"]').exists()).toBe(false)
  })
})
