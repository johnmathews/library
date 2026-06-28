import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import UploadView from '../UploadView.vue'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Scriptable XMLHttpRequest double (one instance per upload). */
class FakeXHR {
  static instances: FakeXHR[] = []

  status = 0
  responseText = ''
  sentBody: unknown = null

  private listeners: Record<string, (event: ProgressEvent) => void> = {}
  upload = {
    addEventListener: (type: string, callback: (event: ProgressEvent) => void): void => {
      this.listeners[`upload:${type}`] = callback
    },
  }

  constructor() {
    FakeXHR.instances.push(this)
  }

  open(): void {}
  setRequestHeader(): void {}
  addEventListener(type: string, callback: (event: ProgressEvent) => void): void {
    this.listeners[type] = callback
  }
  send(body: unknown): void {
    this.sentBody = body
  }

  emitUploadProgress(loaded: number, total: number): void {
    this.listeners['upload:progress']?.({
      lengthComputable: true,
      loaded,
      total,
    } as ProgressEvent)
  }

  respond(status: number, body: unknown): void {
    this.status = status
    this.responseText = JSON.stringify(body)
    this.listeners['load']?.(new ProgressEvent('load'))
  }

  failNetwork(): void {
    this.listeners['error']?.(new ProgressEvent('error'))
  }
}

const Stub = { template: '<div />' }

describe('UploadView', () => {
  const fetchMock = vi.fn()
  let router: Router
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    FakeXHR.instances = []
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/upload', name: 'upload', component: UploadView },
        { path: '/documents/:id', name: 'document-detail', component: Stub },
      ],
    })
    await router.push('/upload')
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
  })

  function mountView(): VueWrapper {
    wrapper = mount(UploadView, {
      props: { pollIntervalMs: 0, pollTimeoutMs: 1000 },
      global: { plugins: [router] },
      attachTo: document.body,
    })
    return wrapper
  }

  async function selectFiles(w: VueWrapper, files: File[]): Promise<void> {
    const input = w.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: files, configurable: true })
    await input.trigger('change')
  }

  function makeFile(name = 'scan.pdf'): File {
    return new File(['%PDF-1.4 test'], name, { type: 'application/pdf' })
  }

  it('renders the page heading', () => {
    const w = mountView()
    expect(w.find('h1').text()).toBe('Upload documents')
  })

  it('does not cap the view root width', () => {
    const w = mountView()
    const root = w.find('#upload-page')
    expect(root.exists()).toBe(true)
    expect(root.classes()).not.toContain('max-w-2xl')
    expect(root.classes().some((c) => c.startsWith('max-w-'))).toBe(false)
  })

  it('renders a PageHeader with the Upload action inside it', () => {
    const w = mountView()
    const header = w.find('[data-testid="page-header"]')
    expect(header.exists()).toBe(true)
    expect(header.find('[data-testid="upload-action"]').exists()).toBe(true)
  })

  it('fires the upload handler when the header Upload button is clicked', async () => {
    const w = mountView()
    await selectFiles(w, [makeFile()])
    await w.find('[data-testid="page-header"] [data-testid="upload-action"]').trigger('click')
    await flushPromises()
    expect(FakeXHR.instances).toHaveLength(1)
  })

  it('exposes a multi-file input accepting images, PDFs and text/markdown without capture', () => {
    const w = mountView()
    const input = w.find('input[type="file"]')
    expect(input.attributes('multiple')).toBeDefined()
    const accept = input.attributes('accept') ?? ''
    expect(accept).toContain('image/*')
    expect(accept).toContain('application/pdf')
    // Text/markdown notes are now accepted too.
    expect(accept).toContain('.md')
    expect(accept).toContain('.txt')
    expect(accept).toContain('text/markdown')
    expect(accept).toContain('text/plain')
    // No capture attribute: phones must offer the photo library too.
    expect(input.attributes('capture')).toBeUndefined()
  })

  it('shows an error when submitting without files', async () => {
    const w = mountView()
    await w.find('form').trigger('submit')
    expect(w.find('#file-upload-error').text()).toContain('Select at least one file')
  })

  it('clears the validation error once a file is selected', async () => {
    const w = mountView()
    await w.find('form').trigger('submit')
    expect(w.find('#file-upload-error').exists()).toBe(true)

    await selectFiles(w, [makeFile()])

    expect(w.find('#file-upload-error').exists()).toBe(false)
  })

  it('shows upload progress from XHR progress events', async () => {
    const w = mountView()
    await selectFiles(w, [makeFile()])
    await w.find('form').trigger('submit')
    await flushPromises()

    const xhr = FakeXHR.instances[0]!
    xhr.emitUploadProgress(30, 100)
    await flushPromises()

    const bar = w.find('[role="progressbar"]')
    expect(bar.exists()).toBe(true)
    expect(bar.attributes('aria-valuenow')).toBe('30')
    expect(bar.attributes('aria-label')).toBe('Uploading scan.pdf')
  })

  it('polls the document after upload and shows a success banner when indexed', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ id: 7, status: 'ocr', title: null }))
      .mockResolvedValueOnce(jsonResponse({ id: 7, status: 'indexed', title: 'Scan' }))
    const w = mountView()
    await selectFiles(w, [makeFile()])
    await w.find('form').trigger('submit')
    await flushPromises()

    FakeXHR.instances[0]!.respond(201, {
      id: 7,
      sha256: 'abc',
      status: 'received',
      duplicate: false,
    })
    await flushPromises()
    expect(w.find('[data-testid="upload-list"]').text()).toContain('Processing')

    // two poll rounds: ocr → indexed
    await vi.waitFor(() => {
      expect(w.find('[data-testid="success-banner"]').exists()).toBe(true)
    })
    const banner = w.find('[data-testid="success-banner"]')
    expect(banner.text()).toContain('scan.pdf')
    expect(banner.find('a').attributes('href')).toBe('/documents/7')
    expect(w.find('[data-testid="upload-list"]').text()).toContain('Indexed')
  })

  it('shows the duplicate banner linking to the existing document', async () => {
    const w = mountView()
    await selectFiles(w, [makeFile('again.pdf')])
    await w.find('form').trigger('submit')
    await flushPromises()

    FakeXHR.instances[0]!.respond(200, {
      id: 3,
      sha256: 'abc',
      status: 'indexed',
      duplicate: true,
    })
    await flushPromises()

    const banner = w.find('[data-testid="duplicate-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('again.pdf is already in your library')
    expect(banner.find('a').attributes('href')).toBe('/documents/3')
    expect(fetchMock).not.toHaveBeenCalled() // no polling for duplicates
  })

  it('puts a 415 rejection in the error summary', async () => {
    const w = mountView()
    await selectFiles(w, [makeFile('notes.csv')])
    await w.find('form').trigger('submit')
    await flushPromises()

    FakeXHR.instances[0]!.respond(415, { detail: 'unsupported media type text/csv' })
    await flushPromises()

    const summary = w.find('[data-testid="error-summary"]')
    expect(summary.exists()).toBe(true)
    expect(summary.text()).toContain('notes.csv: this file type is not supported')
  })

  it('puts a 413 rejection in the error summary', async () => {
    const w = mountView()
    await selectFiles(w, [makeFile('huge.pdf')])
    await w.find('form').trigger('submit')
    await flushPromises()

    FakeXHR.instances[0]!.respond(413, { detail: 'file exceeds the limit' })
    await flushPromises()

    expect(w.find('[data-testid="error-summary"]').text()).toContain('huge.pdf: the file is too large')
  })

  it('reports network failures', async () => {
    const w = mountView()
    await selectFiles(w, [makeFile()])
    await w.find('form').trigger('submit')
    await flushPromises()

    FakeXHR.instances[0]!.failNetwork()
    await flushPromises()

    expect(w.find('[data-testid="error-summary"]').text()).toContain('network problem')
  })

  it('processes multiple files independently', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 9, status: 'indexed', title: null }))
    const w = mountView()
    await selectFiles(w, [makeFile('a.pdf'), makeFile('b.csv')])
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(FakeXHR.instances).toHaveLength(2)
    FakeXHR.instances[0]!.respond(201, {
      id: 9,
      sha256: 'a',
      status: 'received',
      duplicate: false,
    })
    FakeXHR.instances[1]!.respond(415, { detail: 'unsupported media type text/csv' })
    await flushPromises()

    await vi.waitFor(() => {
      expect(w.find('[data-testid="success-banner"]').exists()).toBe(true)
    })
    expect(w.find('[data-testid="success-banner"]').text()).toContain('a.pdf')
    expect(w.find('[data-testid="error-summary"]').text()).toContain('b.csv')
  })
})
