import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
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
    getPage: vi.fn(async () => ({
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
    expect(wrapper.vm.pageCount).toBe(2)
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

describe('DocumentPdfPreview page rendering', () => {
  // jsdom has no IntersectionObserver — capture the callback so the test can fire it.
  let ioCallback: (entries: Array<{ isIntersecting: boolean; target: Element }>) => void
  beforeEach(() => {
    vi.stubGlobal(
      'IntersectionObserver',
      class {
        constructor(cb: typeof ioCallback) {
          ioCallback = cb
        }
        observe() {}
        disconnect() {}
      },
    )
    // jsdom canvases return null for getContext — hand back a stub so render() runs.
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({})) as never
  })

  it('creates one page slot per page', async () => {
    getDocument.mockReturnValue({ promise: Promise.resolve(fakePdf(3)) })
    const wrapper = mount(DocumentPdfPreview, { props, attachTo: document.body })
    await flushPromises()
    expect(wrapper.findAll('[data-page]')).toHaveLength(3)
  })

  it('renders a page only after it intersects', async () => {
    const pdf = fakePdf(3)
    getDocument.mockReturnValue({ promise: Promise.resolve(pdf) })
    const wrapper = mount(DocumentPdfPreview, { props, attachTo: document.body })
    await flushPromises()
    expect(pdf.getPage).not.toHaveBeenCalled()

    const slot = wrapper.find('[data-page="2"]').element
    ioCallback([{ isIntersecting: true, target: slot }])
    await flushPromises()
    expect(pdf.getPage).toHaveBeenCalledWith(2)
  })

  it('scrolls to initialPage when provided', async () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    getDocument.mockReturnValue({ promise: Promise.resolve(fakePdf(5)) })
    const wrapper = mount(DocumentPdfPreview, { props: { ...props, initialPage: 4 }, attachTo: document.body })
    await flushPromises()
    expect(scrollIntoView).toHaveBeenCalledOnce()
    const page4 = wrapper.find('[data-page="4"]').element
    expect(scrollIntoView.mock.instances[0]).toBe(page4)
  })

  it('renders all pages eagerly when IntersectionObserver is unavailable', async () => {
    vi.stubGlobal('IntersectionObserver', undefined)
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({})) as never
    const pdf = fakePdf(3)
    getDocument.mockReturnValue({ promise: Promise.resolve(pdf) })
    mount(DocumentPdfPreview, { props, attachTo: document.body })
    await flushPromises()
    expect(pdf.getPage).toHaveBeenCalledTimes(3)
  })
})

describe('DocumentPdfPreview fallbacks', () => {
  it('shows the thumbnail poster while loading', () => {
    getDocument.mockReturnValue({ promise: new Promise(() => {}) })
    const wrapper = mount(DocumentPdfPreview, { props })
    const img = wrapper.find('[data-testid="pdf-preview-loading"] img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe(props.poster)
  })

  it('error state links to open and download', async () => {
    getDocument.mockReturnValue({ promise: Promise.reject(new Error('x')) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-open"]').attributes('href')).toBe(props.openHref)
    expect(wrapper.find('[data-testid="pdf-preview-download"]').attributes('href')).toBe(props.downloadHref)
  })

  it('password state links to open', async () => {
    const err = Object.assign(new Error('locked'), { name: 'PasswordException' })
    getDocument.mockReturnValue({ promise: Promise.reject(err) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-password"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pdf-preview-open"]').attributes('href')).toBe(props.openHref)
  })
})

afterEach(() => vi.unstubAllGlobals())
