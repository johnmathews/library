import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// pdfjs-dist can't run its worker/canvas in jsdom — mock the whole module.
// vi.hoisted ensures getDocument is initialised before vi.mock() hoisting runs.
const { getDocument } = vi.hoisted(() => ({ getDocument: vi.fn() }))
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument,
}))

import DocumentPdfPreview from '../DocumentPdfPreview.vue'

/** A fake PDFDocumentProxy whose pages resolve immediately. */
function fakePdf(numPages: number) {
  return {
    numPages,
    getPage: vi.fn(async (n: number) => ({
      getViewport: ({ scale }: { scale: number }) => ({ width: 100 * scale, height: 140 * scale }),
      render: () => ({ promise: Promise.resolve() }),
    })),
    destroy: vi.fn(),
  }
}

const props = {
  src: '/api/documents/1/searchable.pdf?disposition=inline',
  poster: '/api/documents/1/thumbnail',
  openHref: '/api/documents/1/searchable.pdf?disposition=inline',
  downloadHref: '/api/documents/1/searchable.pdf',
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('DocumentPdfPreview state machine', () => {
  it('shows the loading poster before the document resolves', () => {
    getDocument.mockReturnValue({ promise: new Promise(() => {}) })
    const wrapper = mount(DocumentPdfPreview, { props })
    expect(wrapper.find('[data-testid="pdf-preview-loading"]').exists()).toBe(true)
  })

  it('renders the page container once the document resolves', async () => {
    getDocument.mockReturnValue({ promise: Promise.resolve(fakePdf(2)) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-pages"]').exists()).toBe(true)
  })

  it('falls back to the error state when loading rejects', async () => {
    getDocument.mockReturnValue({ promise: Promise.reject(new Error('network')) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-error"]').exists()).toBe(true)
  })

  it('falls back to the password state on a PasswordException', async () => {
    const err = Object.assign(new Error('locked'), { name: 'PasswordException' })
    getDocument.mockReturnValue({ promise: Promise.reject(err) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-password"]').exists()).toBe(true)
  })
})
