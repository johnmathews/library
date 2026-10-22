import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import AskTurn, { type AskTurnVM } from '../AskTurn.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [{ path: '/documents/:id', name: 'document-detail', component: { template: '<div />' } }],
})

function turn(overrides: Partial<AskTurnVM> = {}): AskTurnVM {
  return {
    id: 5,
    query: 'who supplies my power?',
    answer: 'It is **Vattenfall** [#3].',
    citations: [
      { document_id: 3, title: 'Contract', page_number: 2 },
      { document_id: 9, title: null, page_number: null },
    ],
    usedTools: ['semantic_search', 'get_document'],
    costUsd: 0.0123,
    ...overrides,
  }
}

function mountTurn(props: Partial<InstanceType<typeof AskTurn>['$props']> = {}) {
  return mount(AskTurn, {
    props: { turn: turn(), isDocumentMode: false, isLast: true, laterCount: 0, busy: false, ...props },
    global: { plugins: [router] },
    attachTo: document.body,
  })
}

describe('AskTurn', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('copies the markdown answer, not the rendered HTML', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    const w = mountTurn()

    await w.get('[data-testid="ask-copy"]').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('It is **Vattenfall** [#3].')
    expect(w.get('[data-testid="ask-copy"]').attributes('aria-label')).toBe('Copied')
  })

  it('folds citations into a counted disclosure whose links carry the page', () => {
    const w = mountTurn()
    const disclosure = w.get('[data-testid="ask-citations-disclosure"]')
    expect(disclosure.element.hasAttribute('open')).toBe(false)
    expect(disclosure.get('summary').text()).toBe('Citations (2)')

    const [first, second] = w.findAll('[data-testid="ask-citation"]')
    expect(first!.text()).toContain('Contract, p. 2')
    expect(first!.attributes('href')).toBe('/documents/3?page=2')
    expect(second!.text()).toContain('Untitled')
    expect(second!.text()).not.toContain('p.')
  })

  it('folds the tools it used into one line', () => {
    const w = mountTurn()
    const tools = w.get('[data-testid="ask-tools"]')
    expect(tools.get('summary').text()).toBe('Used 2 tools')
    expect(tools.text()).toContain('semantic_search')
  })

  it('hides retry and edit while an answer is generating anywhere in the thread', () => {
    const w = mountTurn({ busy: true })
    expect(w.get('[data-testid="ask-retry"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-testid="ask-edit-start"]').exists()).toBe(false)
  })

  it('shows only a thinking indicator while pending — nothing to copy or retry', () => {
    const w = mountTurn({ turn: turn({ id: null, pending: true, answer: '' }) })
    expect(w.find('[data-testid="ask-thinking"]').exists()).toBe(true)
    expect(w.find('[data-testid="ask-actions"]').exists()).toBe(false)
    expect(w.find('[data-testid="ask-edit-start"]').exists()).toBe(false)
  })

  it('emits the edited question, and refuses a blank one', async () => {
    const w = mountTurn({ isLast: false, laterCount: 3 })
    await w.get('[data-testid="ask-edit-start"]').trigger('click')
    expect(w.get('[data-testid="ask-edit-note"]').text()).toContain('the 3 after it')

    await w.get('[data-testid="ask-edit-input"]').setValue('   ')
    expect(w.get('[data-testid="ask-edit-submit"]').attributes('disabled')).toBeDefined()

    await w.get('[data-testid="ask-edit-input"]').setValue('who supplies gas?')
    await w.get('[data-testid="ask-edit-submit"]').trigger('click')
    expect(w.emitted('edit')).toEqual([['who supplies gas?']])
  })

  it('labels both sides in document view', () => {
    const w = mountTurn({ isDocumentMode: true })
    const roles = w.findAll('[data-testid="ask-doc-role"]').map((r) => r.text())
    expect(roles).toEqual(['You', 'Library'])
    expect(w.find('[data-testid="ask-question-bubble"]').exists()).toBe(false)
  })
})
