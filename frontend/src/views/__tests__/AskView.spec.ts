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
  renameThread: vi.fn(),
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
    // Some tests stub matchMedia to simulate the lg (desktop) breakpoint; reset
    // so others fall back to the mobile default (jsdom leaves matchMedia unset).
    vi.unstubAllGlobals()
  })

  /** Simulate the lg+ (desktop) breakpoint for useMediaQuery. jsdom leaves
   * matchMedia undefined, which vueuse treats as "no match" (mobile). */
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

  it('renders the page heading', () => {
    const w = mountView()
    expect(w.find('h1').text()).toBe('Ask')
  })

  it('disables New conversation in the fresh state and enables it after an ask (item 2)', async () => {
    // Desktop: the composer is always docked, so from a fresh state "New
    // conversation" is redundant and greyed out.
    stubDesktopViewport()
    askQuestionMock.mockResolvedValue(sampleResponse())
    const w = mountView()
    await flushPromises()
    const btn = () => w.find('[data-testid="new-conversation"]').element as HTMLButtonElement
    expect(btn().disabled).toBe(true)
    // Once a conversation exists it is enabled again (starting a new one is real).
    await typeAndSubmit(w, 'which invoices are due?')
    expect(btn().disabled).toBe(false)
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
    expect(askQuestionMock).toHaveBeenLastCalledWith('and then?', 42, expect.anything(), undefined)
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

  it('renders the answer on a distinct surface card at lg+, flat on mobile (item 4 / P4)', async () => {
    askQuestionMock.mockResolvedValueOnce(sampleResponse())
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')
    // At lg+ the answer sits on a bordered, shaded surface card (distinct from the
    // two-pane panel). On mobile that card is dropped — the answer is flat text
    // under the violet question bubble — so the classes are lg-gated.
    const surface = w.find('[data-testid="ask-answer-surface"]')
    expect(surface.exists()).toBe(true)
    expect(surface.classes()).toContain('lg:bg-gray-50')
    expect(surface.classes()).toContain('lg:border')
    // The mobile (unprefixed) card classes are gone.
    expect(surface.classes()).not.toContain('bg-gray-50')
    expect(surface.classes()).not.toContain('border')
    // The answer body lives inside it either way.
    expect(surface.find('[data-testid="ask-answer"]').exists()).toBe(true)
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

  it('collapses citations behind a disclosure showing the count by default', async () => {
    askQuestionMock.mockResolvedValueOnce(
      sampleResponse({
        citations: [
          { document_id: 42, title: 'Energy bill', page_number: 3 },
          { document_id: 43, title: 'Water bill', page_number: null },
        ],
        thread_id: 1,
      }),
    )
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    const details = w.get('[data-testid="ask-citations-disclosure"] details')
    // Collapsed by default: the native <details> has no `open` attribute.
    expect(details.attributes('open')).toBeUndefined()
    // Summary shows the citation count.
    expect(details.get('summary').text()).toBe('Citations (2)')
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

    // jsdom renders <details> content regardless of the open state, so the
    // citation link is queryable even though the disclosure is collapsed.
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

  it('optimistically shows the question with a thinking indicator while pending (W2)', async () => {
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

    // The user's question is on screen immediately (optimistic) and the input
    // has been cleared — "sending" is instant, distinct from the LLM thinking.
    const turns = w.findAll('[data-testid="ask-turn"]')
    expect(turns).toHaveLength(1)
    expect(turns[0]!.text()).toContain('which invoices are due?')
    expect(w.find('[data-testid="ask-thinking"]').exists()).toBe(true)
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe('')

    // The primary action is a live Stop control, not a greyed-out button.
    const button = w.find('[data-testid="ask-submit"]')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.text()).toContain('Stop')

    resolve(sampleResponse({ thread_id: 1 }))
    await flushPromises()

    expect(w.find('[data-testid="ask-thinking"]').exists()).toBe(false)
    expect(w.find('[data-testid="ask-submit"]').text()).toBe('Send')
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)
    expect(w.find('[data-testid="ask-answer"]').text()).toContain('Two invoices are due')
  })

  it('aborts an in-flight request and removes the optimistic turn when Stop is clicked (W2)', async () => {
    let reject!: (reason: unknown) => void
    askQuestionMock.mockReturnValue(
      new Promise<AskResponse>((_resolve, r) => {
        reject = r
      }),
    )
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)

    await w.find('[data-testid="ask-submit"]').trigger('click')
    // The component aborts; simulate the fetch rejecting with an AbortError.
    reject(new DOMException('aborted', 'AbortError'))
    await flushPromises()

    // The optimistic turn is gone and no error alert is shown for a user abort.
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(0)
    expect(w.find('[data-testid="error-summary"]').exists()).toBe(false)
    expect(w.find('[data-testid="ask-submit"]').text()).toBe('Send')
  })

  it('removes the optimistic turn and restores the question on an API error (W2)', async () => {
    askQuestionMock.mockRejectedValueOnce(new ApiError(500, 'boom'))
    const w = mountView()
    await typeAndSubmit(w, 'which invoices are due?')

    // No dangling question turn, error surfaced, and the text is back in the box.
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(0)
    expect(w.find('[data-testid="error-summary"]').exists()).toBe(true)
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe(
      'which invoices are due?',
    )
  })

  it('submits on Cmd/Ctrl+Enter in the textarea (W2)', async () => {
    askQuestionMock.mockResolvedValueOnce(sampleResponse({ thread_id: 1 }))
    const w = mountView()
    await w.find('#ask-question').setValue('which invoices are due?')
    await w.find('#ask-question').trigger('keydown', { key: 'Enter', metaKey: true })
    await flushPromises()
    expect(askQuestionMock).toHaveBeenCalledTimes(1)
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)
  })

  it('sends on plain Enter (desktop), not on Shift+Enter / Ctrl+J / while composing', async () => {
    // Plain Enter sends only on desktop (lg+); stub that viewport.
    stubDesktopViewport()
    askQuestionMock.mockResolvedValue(sampleResponse({ thread_id: 1 }))
    const w = mountView()
    const ta = w.find('#ask-question')
    await ta.setValue('hello')

    await ta.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(askQuestionMock).toHaveBeenCalledTimes(1) // plain Enter sends on desktop

    await ta.trigger('keydown', { key: 'Enter', shiftKey: true })
    await flushPromises()
    expect(askQuestionMock).toHaveBeenCalledTimes(1) // shift+enter does not send

    // Ctrl+J inserts a newline at the caret and restores the caret just after
    // it (on nextTick), rather than sending — a regression in the splice/caret
    // math, or a dropped nextTick, would otherwise pass silently here.
    const textarea = ta.element as HTMLTextAreaElement
    await ta.setValue('hello')
    textarea.selectionStart = textarea.selectionEnd = 2
    await ta.trigger('keydown', { key: 'j', ctrlKey: true })
    await flushPromises()
    expect(askQuestionMock).toHaveBeenCalledTimes(1) // ctrl+j does not send
    expect(textarea.value).toBe('he\nllo')
    expect(textarea.selectionStart).toBe(3)
    expect(textarea.selectionEnd).toBe(3)

    await ta.trigger('keydown', { key: 'Enter', isComposing: true })
    await flushPromises()
    expect(askQuestionMock).toHaveBeenCalledTimes(1) // IME compose does not send

    await ta.setValue('hello again')
    await ta.trigger('keydown', { key: 'Enter', metaKey: true })
    await flushPromises()
    expect(askQuestionMock).toHaveBeenCalledTimes(2) // cmd/ctrl+enter still sends
  })

  it('on a phone, plain Enter does not send; Cmd/Ctrl+Enter still does (P3)', async () => {
    // Default jsdom viewport = mobile (matchMedia unset → useMediaQuery false).
    askQuestionMock.mockResolvedValue(sampleResponse({ thread_id: 1 }))
    const w = mountView()
    const ta = w.find('#ask-question')
    await ta.setValue('hello')

    // Plain Enter inserts a newline (browser default), so onSubmit is NOT called.
    await ta.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(askQuestionMock).not.toHaveBeenCalled()

    // The Send button still works on a phone…
    await w.find('[data-testid="ask-form"]').trigger('submit')
    await flushPromises()
    expect(askQuestionMock).toHaveBeenCalledTimes(1)

    // …and a hardware keyboard's Cmd/Ctrl+Enter still sends.
    await ta.setValue('again')
    await ta.trigger('keydown', { key: 'Enter', metaKey: true })
    await flushPromises()
    expect(askQuestionMock).toHaveBeenCalledTimes(2)
  })

  it('uses the shared PageHeader and imposes no max-width cap (W10)', () => {
    const w = mountView()
    expect(w.find('[data-testid="page-header"]').exists()).toBe(true)
    expect(w.find('#ask-page').classes()).not.toContain('max-w-6xl')
  })

  it('places the title/description above the sidebar working area, not inside it', () => {
    const w = mountView()
    // The header is a sibling above #ask-page (which holds the sidebar +
    // answer column), so it spans full width on top — the standard layout.
    expect(w.find('#ask-page [data-testid="page-header"]').exists()).toBe(false)
    const html = w.html()
    expect(html.indexOf('data-testid="page-header"')).toBeLessThan(html.indexOf('id="ask-page"'))
  })

  it('docks the composer at the bottom: footer on mobile, sticky at lg+ (W4/K1)', () => {
    const w = mountView()
    const form = w.find('[data-testid="ask-form"]')
    expect(form.exists()).toBe(true)
    // On mobile it is a shrink-0 FOOTER of the fixed-height chat column (not
    // sticky — sticky only pins on overflow, which floated it mid-page on short
    // chats). At lg+ it is a sticky bottom bar over the page-scrolling transcript.
    expect(form.classes()).toContain('shrink-0')
    expect(form.classes()).toContain('lg:sticky')
    expect(form.classes()).not.toContain('sticky') // base (mobile) is not sticky
    // Never hidden behind a reveal step.
    expect(form.classes()).not.toContain('max-lg:hidden')
    // The transcript is the internal scroll area on mobile; at lg+ it keeps the
    // sticky-composer clearance.
    const transcript = w.find('[data-testid="ask-transcript"]')
    expect(transcript.classes()).toContain('max-lg:overflow-y-auto')
    expect(transcript.classes()).toContain('max-lg:flex-1')
    expect(transcript.classes()).toContain('lg:pb-28')
    expect(w.find('[data-testid="ask-submit"]').text()).toBe('Send')
  })

  it('fills the viewport as a fixed-height column on the mobile chat screen (K1)', async () => {
    // On /ask/new (a chat screen) the view is a fixed-height flex column so the
    // composer footer docks at the bottom; on /ask (the list) it is not.
    await router.push('/ask/new')
    const w = mountView()
    await flushPromises()
    const root = w.find('#ask-page').element.parentElement as HTMLElement
    expect(root.className).toContain('max-lg:h-[calc(100dvh-4rem)]')
    expect(root.className).toContain('max-lg:flex-col')
    // The chat pane and transcript participate in the flex-height chain.
    expect(w.find('[data-testid="ask-thread-pane"]').classes()).toContain('min-h-0')
    expect(w.find('#ask-page').classes()).toContain('max-lg:flex-1')
  })

  it('is full-bleed on mobile and carded at lg+ (P1)', () => {
    const w = mountView()
    const page = w.find('#ask-page')
    // Breaks out of the shell's side padding and drops the card chrome on mobile…
    expect(page.classes()).toContain('-mx-4')
    expect(page.classes()).toContain('rounded-none')
    expect(page.classes()).toContain('border-0')
    // …but is the bordered two-pane card again at lg+.
    expect(page.classes()).toContain('lg:mx-0')
    expect(page.classes()).toContain('lg:rounded-xl')
    expect(page.classes()).toContain('lg:border')
  })

  it('composer is one full-width pill: borderless field, actions on their own row (P2)', () => {
    const w = mountView()
    const field = w.find('#ask-question')
    // A raw, borderless, full-width textarea (no boxed .form-textarea).
    expect(field.element.tagName).toBe('TEXTAREA')
    expect(field.classes()).toContain('w-full')
    expect(field.classes()).toContain('border-0')
    expect(field.classes()).not.toContain('form-textarea')
    // Attach and Send both exist (on their own row inside the pill).
    expect(w.find('[data-testid="ask-image-attach"]').exists()).toBe(true)
    expect(w.find('[data-testid="ask-submit"]').exists()).toBe(true)
    // The old two-line hint is gone.
    expect(w.text()).not.toContain('Shift+Enter for new line')
  })

  it('shows the list on /ask and the chat pane on /ask/new (mobile screen model, W1/W3/W4)', async () => {
    await router.push('/ask')
    const w = mountView()
    await flushPromises()
    // List screen: the conversation list is on-screen; the chat pane is the
    // mobile-hidden one. The mobile ＋ starts a new chat.
    expect(w.find('[data-testid="conversation-sidebar"]').classes()).not.toContain('max-lg:hidden')
    expect(w.find('[data-testid="ask-thread-pane"]').classes()).toContain('max-lg:hidden')
    expect(w.find('[data-testid="ask-new-mobile"]').exists()).toBe(true)

    // The ＋ navigates to a fresh chat screen.
    await w.find('[data-testid="ask-new-mobile"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('ask-new')
    // Chat screen: the pane is visible on mobile, the list is the hidden one.
    expect(w.find('[data-testid="ask-thread-pane"]').classes()).not.toContain('max-lg:hidden')
    expect(w.find('[data-testid="conversation-sidebar"]').classes()).toContain('max-lg:hidden')
  })

  it('shows a greeting with example prompts on a new chat and fills the composer (W4)', async () => {
    await router.push('/ask/new')
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="ask-greeting"]').exists()).toBe(true)
    const prompts = w.findAll('[data-testid="ask-example-prompt"]')
    expect(prompts.length).toBeGreaterThan(0)
    await prompts[0]!.trigger('click')
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe(prompts[0]!.text())
  })

  it('returns to the list when the chat back arrow is tapped (W4)', async () => {
    getThreadMock.mockResolvedValue({ id: 7, title: 'Energy', turns: [] })
    await router.push('/ask/7')
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="ask-thread-bar"]').exists()).toBe(true)
    await w.find('[data-testid="ask-back"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('ask')
  })

  it('renames the active thread from the chat title bar ⋯ menu (W5)', async () => {
    const { renameThread } = await import('@/api/ask')
    vi.mocked(renameThread).mockResolvedValue({
      id: 7,
      title: 'Renamed',
      created_at: '',
      updated_at: '',
      turn_count: 0,
      total_cost_usd: 0,
    })
    getThreadMock.mockResolvedValue({ id: 7, title: 'Energy', turns: [] })
    await router.push('/ask/7')
    const w = mountView()
    await flushPromises()
    await w.find('[data-testid="ask-title-actions"]').trigger('click')
    await w.find('[data-testid="thread-rename"]').trigger('click')
    const input = w.find('[data-testid="ask-title-rename-input"]')
    expect((input.element as HTMLInputElement).value).toBe('Energy')
    await input.setValue('Renamed')
    await w.find('[data-testid="ask-title-rename-save"]').trigger('click')
    await flushPromises()
    expect(vi.mocked(renameThread)).toHaveBeenCalledWith(7, 'Renamed')
  })

  it('deletes the active thread from the chat title bar and returns to the list (W5)', async () => {
    const { deleteThread } = await import('@/api/ask')
    vi.mocked(deleteThread).mockResolvedValue()
    getThreadMock.mockResolvedValue({ id: 7, title: 'Energy', turns: [] })
    await router.push('/ask/7')
    const w = mountView()
    await flushPromises()
    await w.find('[data-testid="ask-title-actions"]').trigger('click')
    await w.find('[data-testid="thread-delete"]').trigger('click')
    // Two-step: nothing deleted until confirm.
    expect(vi.mocked(deleteThread)).not.toHaveBeenCalled()
    await w.find('[data-testid="ask-title-delete-confirm"]').trigger('click')
    await flushPromises()
    expect(vi.mocked(deleteThread)).toHaveBeenCalledWith(7)
    expect(router.currentRoute.value.name).toBe('ask')
  })

  it('lets the transcript grow with the page rather than scrolling internally', () => {
    const w = mountView()
    const transcript = w.find('[data-testid="ask-transcript"]')
    expect(transcript.exists()).toBe(true)
    // The transcript now flows at its natural height and grows with the
    // conversation; the whole page scrolls instead of a fixed-height internal
    // scroller. So it must NOT be an overflow-y-auto/fixed-height column.
    expect(transcript.classes()).not.toContain('lg:overflow-y-auto')
    expect(transcript.classes()).not.toContain('lg:flex-1')
    // Likewise the outer view no longer pins itself to the viewport height.
    expect(w.find('#ask-page').classes()).not.toContain('lg:flex-1')
  })

  it('shows the no-threads empty-state prompt when no conversations exist (W10)', async () => {
    vi.mocked(listThreads).mockResolvedValue([])
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="ask-empty"]').exists()).toBe(true)
    expect(w.find('[data-testid="ask-select-thread"]').exists()).toBe(false)
  })

  it('shows a "select a conversation" prompt when threads exist but none is selected (W5)', async () => {
    vi.mocked(listThreads).mockResolvedValue([
      { id: 1, title: 'Energy bills', created_at: '', updated_at: '', turn_count: 3, total_cost_usd: 0.05 },
    ])
    const w = mountView()
    await flushPromises()
    expect(w.find('[data-testid="ask-select-thread"]').exists()).toBe(true)
    expect(w.find('[data-testid="ask-select-thread"]').text()).toContain(
      'Select a conversation from the sidebar',
    )
    expect(w.find('[data-testid="ask-empty"]').exists()).toBe(false)
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

  it('seeds the composer from ?q= and reveals + focuses it (W1)', async () => {
    // The "Ask about this document" button opens /ask?q=… in a new tab; the
    // (already URL-decoded) query pre-fills the composer, ready to send.
    await router.push('/ask?q=Hello%20world')
    const w = mountView()
    await flushPromises()

    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe('Hello world')
    // The composer is revealed (not hidden on mobile) and focused.
    expect(w.find('[data-testid="ask-form"]').classes()).not.toContain('max-lg:hidden')
    expect(document.activeElement).toBe(w.find('#ask-question').element)
  })

  it('leaves the composer empty when no ?q= is present (W1)', async () => {
    const w = mountView()
    await flushPromises()
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe('')
  })

  it('resumes a thread on /ask/:threadId and ignores an absent ?q= (W1)', async () => {
    getThreadMock.mockResolvedValue({
      id: 7,
      title: 'Energy',
      turns: [
        {
          id: 1,
          query: 'who?',
          answer: 'Vattenfall [#3].',
          citations: [],
          used_tools: [],
          cost_usd: 0.02,
          created_at: '',
        },
      ],
    })
    await router.push('/ask/7')
    const w = mountView()
    await flushPromises()

    expect(getThreadMock).toHaveBeenCalledWith(7)
    // The thread resumes; the composer is not seeded (no q) and stays empty.
    expect((w.find('#ask-question').element as HTMLTextAreaElement).value).toBe('')
    expect(w.text()).toContain('Vattenfall')
  })

  it('drops a stale in-flight answer when the user switches threads mid-request (W4)', async () => {
    // Ask on a new chat, then open a different thread before the answer lands.
    // The stale answer must not corrupt the now-active thread's transcript or
    // force-navigate back to the thread it belonged to.
    let resolveAsk!: (value: AskResponse) => void
    askQuestionMock.mockReturnValue(
      new Promise<AskResponse>((r) => {
        resolveAsk = r
      }),
    )
    getThreadMock.mockResolvedValue({
      id: 7,
      title: 'Energy',
      turns: [
        {
          id: 1,
          query: 'who?',
          answer: 'Vattenfall.',
          citations: [],
          used_tools: [],
          cost_usd: 0.02,
          created_at: '',
        },
      ],
    })
    await router.push('/ask/new')
    const w = mountView()
    await flushPromises()
    await typeAndSubmit(w, 'first question') // optimistic turn, request in flight

    // Navigate to thread 7 while the first answer is still generating.
    await router.push('/ask/7')
    await flushPromises()
    expect(w.text()).toContain('Vattenfall')

    // The stale answer resolves — it must be dropped, not applied to thread 7.
    resolveAsk(sampleResponse({ answer: 'STALE ANSWER', thread_id: 1 }))
    await flushPromises()
    expect(w.text()).toContain('Vattenfall')
    expect(w.text()).not.toContain('STALE ANSWER')
    expect(w.findAll('[data-testid="ask-turn"]')).toHaveLength(1)
    expect(router.currentRoute.value.name).toBe('ask-thread')
    expect(router.currentRoute.value.params.threadId).toBe('7')
  })
})
