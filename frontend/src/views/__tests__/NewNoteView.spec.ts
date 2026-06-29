import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import NewNoteView from '../NewNoteView.vue'
import { createNote } from '@/api/notes'
import { ApiError } from '@/api/client'

vi.mock('@/api/notes', () => ({
  createNote: vi.fn(),
}))

const createNoteMock = vi.mocked(createNote)

const Stub = { template: '<div />' }

describe('NewNoteView', () => {
  let router: Router
  let wrapper: VueWrapper | undefined

  beforeEach(() => {
    createNoteMock.mockReset()
    localStorage.clear()
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/notes/new', name: 'note-new', component: NewNoteView },
        { path: '/documents/:id', name: 'document-detail', component: Stub },
      ],
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
  })

  async function mountView(): Promise<VueWrapper> {
    await router.push('/notes/new')
    await router.isReady()
    wrapper = mount(NewNoteView, { global: { plugins: [router] } })
    await flushPromises()
    return wrapper
  }

  it('renders the body input and the preview panel, with no separate title input', async () => {
    const w = await mountView()
    expect(w.find('#note-title').exists()).toBe(false)
    expect(w.find('#note-body').exists()).toBe(true)
    expect(w.find('[data-testid="note-preview"]').exists()).toBe(true)
  })

  it('renders a live markdown preview of the body', async () => {
    const w = await mountView()
    await w.find('#note-body').setValue('# Hello\n\nworld')
    await flushPromises()
    const preview = w.find('[data-testid="note-preview"]')
    expect(preview.html()).toContain('<h1')
    expect(preview.text()).toContain('Hello')
  })

  it('disables save until the body has a non-empty first line', async () => {
    const w = await mountView()
    expect(w.find('#note-save').attributes('disabled')).toBeDefined()

    // Whitespace-only body → no derivable title → still disabled.
    await w.find('#note-body').setValue('   \n  ')
    await flushPromises()
    expect(w.find('#note-save').attributes('disabled')).toBeDefined()

    await w.find('#note-body').setValue('My note\nand its body')
    await flushPromises()
    expect(w.find('#note-save').attributes('disabled')).toBeUndefined()
  })

  it('derives the title from the first line of the body on save', async () => {
    createNoteMock.mockResolvedValue({ id: 99 } as never)
    const push = vi.spyOn(router, 'push')
    const w = await mountView()

    await w.find('#note-body').setValue('# My note\nNote body')
    await flushPromises()
    await w.find('#note-save').trigger('click')
    await flushPromises()

    expect(createNoteMock).toHaveBeenCalledWith({
      title: 'My note',
      body_markdown: '# My note\nNote body',
    })
    expect(push).toHaveBeenCalledWith({ name: 'document-detail', params: { id: 99 } })
  })

  it('defaults to split mode with both editor and preview panes present', async () => {
    const w = await mountView()
    expect(w.find('[data-testid="note-editor-pane"]').exists()).toBe(true)
    expect(w.find('[data-testid="note-preview-pane"]').exists()).toBe(true)
  })

  it('shows only the editor in edit mode and only the preview in preview mode', async () => {
    const w = await mountView()

    await w.get('[data-testid="mode-edit"]').trigger('click')
    expect(w.find('[data-testid="note-editor-pane"]').exists()).toBe(true)
    expect(w.find('[data-testid="note-preview-pane"]').exists()).toBe(false)

    await w.get('[data-testid="mode-preview"]').trigger('click')
    expect(w.find('[data-testid="note-editor-pane"]').exists()).toBe(false)
    expect(w.find('[data-testid="note-preview-pane"]').exists()).toBe(true)
  })

  it('persists the chosen editor mode to localStorage', async () => {
    const w = await mountView()
    await w.get('[data-testid="mode-preview"]').trigger('click')
    expect(localStorage.getItem('library:note-editor-mode')).toContain('preview')
  })

  it('renders the Save action in the page header (reachable without scrolling)', async () => {
    const w = await mountView()
    const header = w.get('[data-testid="page-header"]')
    expect(header.find('#note-save').exists()).toBe(true)
  })

  it('surfaces an API error and does not navigate', async () => {
    createNoteMock.mockRejectedValue(new ApiError(500, 'boom'))
    const push = vi.spyOn(router, 'push')
    const w = await mountView()

    await w.find('#note-body').setValue('My note\nNote body')
    await flushPromises()
    await w.find('#note-save').trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="error-summary"]').exists()).toBe(true)
    expect(push).not.toHaveBeenCalledWith(
      expect.objectContaining({ name: 'document-detail' }),
    )
  })
})
