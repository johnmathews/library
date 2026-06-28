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

  it('renders the title and body inputs and the preview panel', async () => {
    const w = await mountView()
    expect(w.find('#note-title').exists()).toBe(true)
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

  it('disables save while the title or body is empty', async () => {
    const w = await mountView()
    const save = w.find('#note-save')
    expect(save.attributes('disabled')).toBeDefined()

    await w.find('#note-title').setValue('My note')
    await flushPromises()
    expect(w.find('#note-save').attributes('disabled')).toBeDefined()

    await w.find('#note-body').setValue('Some body')
    await flushPromises()
    expect(w.find('#note-save').attributes('disabled')).toBeUndefined()
  })

  it('creates the note and navigates to its detail page on save', async () => {
    createNoteMock.mockResolvedValue({ id: 99 } as never)
    const push = vi.spyOn(router, 'push')
    const w = await mountView()

    await w.find('#note-title').setValue('My note')
    await w.find('#note-body').setValue('Note body')
    await flushPromises()
    await w.find('#note-save').trigger('click')
    await flushPromises()

    expect(createNoteMock).toHaveBeenCalledWith({
      title: 'My note',
      body_markdown: 'Note body',
    })
    expect(push).toHaveBeenCalledWith({ name: 'document-detail', params: { id: 99 } })
  })

  it('surfaces an API error and does not navigate', async () => {
    createNoteMock.mockRejectedValue(new ApiError(500, 'boom'))
    const push = vi.spyOn(router, 'push')
    const w = await mountView()

    await w.find('#note-title').setValue('My note')
    await w.find('#note-body').setValue('Note body')
    await flushPromises()
    await w.find('#note-save').trigger('click')
    await flushPromises()

    expect(w.find('[data-testid="error-summary"]').exists()).toBe(true)
    expect(push).not.toHaveBeenCalledWith(
      expect.objectContaining({ name: 'document-detail' }),
    )
  })
})
