import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DOCUMENT_STATUSES,
  documentQueryString,
  fetchDocumentMarkdown,
  listDocuments,
  listJobs,
  listJobTaskNames,
  originalUrl,
  searchablePdfUrl,
  thumbnailUrl,
  updateDocument,
  uploadDocument,
  verifyDocument,
} from '../documents'
import { ApiError } from '../client'

describe('documentQueryString', () => {
  it('serialises scalar filters and repeats tag', () => {
    const qs = documentQueryString({
      q: 'rekening',
      kind: 'invoice',
      tag: ['energie', 'wonen'],
      limit: 25,
      offset: 50,
    })
    const params = new URLSearchParams(qs)
    expect(params.get('q')).toBe('rekening')
    expect(params.get('kind')).toBe('invoice')
    expect(params.getAll('tag')).toEqual(['energie', 'wonen'])
    expect(params.get('limit')).toBe('25')
    expect(params.get('offset')).toBe('50')
  })

  it('omits undefined and empty values', () => {
    expect(documentQueryString({ q: '', kind: undefined })).toBe('')
  })

  it('serialises the scalar project filter', () => {
    const params = new URLSearchParams(documentQueryString({ project: 'house-purchase' }))
    expect(params.get('project')).toBe('house-purchase')
  })
})

describe('file URL helpers', () => {
  it('build attachment (download) URLs by default', () => {
    expect(originalUrl(12)).toBe('/api/documents/12/original')
    expect(searchablePdfUrl(12)).toBe('/api/documents/12/searchable.pdf')
    expect(thumbnailUrl(12)).toBe('/api/documents/12/thumbnail')
  })

  it('append ?disposition=inline for in-browser rendering', () => {
    expect(originalUrl(12, { inline: true })).toBe('/api/documents/12/original?disposition=inline')
    expect(searchablePdfUrl(12, { inline: true })).toBe(
      '/api/documents/12/searchable.pdf?disposition=inline',
    )
    expect(originalUrl(12, { inline: false })).toBe('/api/documents/12/original')
  })
})

describe('listDocuments', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  it('GETs /api/documents with the filter query string', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, limit: 25, offset: 0 }), { status: 200 }),
    )
    await listDocuments({ q: 'rekening', kind: 'invoice', limit: 25, offset: 0 })
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/documents?q=rekening&kind=invoice&limit=25&offset=0')
  })

  it('sends review_status as a query param', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, limit: 25, offset: 0 }), { status: 200 }),
    )
    await listDocuments({ review_status: 'needs_review' })
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('review_status=needs_review')
  })
})

describe('updateDocument', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  it('PATCHes a projects full-replacement list through the body', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: 7 }), { status: 200 }))
    await updateDocument(7, { projects: ['House purchase', 'Taxes'] })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/documents/7')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({ projects: ['House purchase', 'Taxes'] })
  })
})

describe('verifyDocument', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  it('POSTs to /api/documents/{id}/verify', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ id: 7, status: 'indexed' }), { status: 200 }),
    )
    await verifyDocument(7)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/documents/7/verify')
    expect(init.method).toBe('POST')
  })
})

describe('listJobs', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockResolvedValue(new Response('[]', { status: 200 }))
  })

  afterEach(() => vi.unstubAllGlobals())

  it('GETs /api/jobs with no params by default', async () => {
    await listJobs()
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/jobs')
  })

  it('passes limit and include_system', async () => {
    await listJobs({ limit: 200, includeSystem: true })
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('limit=200')
    expect(url).toContain('include_system=true')
  })

  it('passes document_id for history mode', async () => {
    await listJobs({ documentId: 42 })
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('document_id=42')
  })

  it('passes task_name to filter by task type', async () => {
    await listJobs({ taskName: 'library.jobs.poll_email_inbox' })
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toContain('task_name=library.jobs.poll_email_inbox')
  })
})

describe('listJobTaskNames', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  it('GETs /api/jobs/task-names', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(['library.jobs.process_document']), { status: 200 }),
    )
    const names = await listJobTaskNames()
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/jobs/task-names')
    expect(names).toEqual(['library.jobs.process_document'])
  })
})

/** Minimal scriptable XMLHttpRequest double. */
class FakeXHR {
  static instances: FakeXHR[] = []

  method = ''
  url = ''
  status = 0
  responseText = ''
  headers: Record<string, string> = {}
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

  open(method: string, url: string): void {
    this.method = method
    this.url = url
  }

  setRequestHeader(name: string, value: string): void {
    this.headers[name] = value
  }

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

describe('uploadDocument', () => {
  beforeEach(() => {
    FakeXHR.instances = []
    vi.stubGlobal('XMLHttpRequest', FakeXHR)
    document.cookie = 'library_csrftoken=csrf-123'
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    document.cookie = 'library_csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT'
  })

  function makeFile(): File {
    return new File(['%PDF-1.4 test'], 'scan.pdf', { type: 'application/pdf' })
  }

  it('POSTs multipart form data with the CSRF header and reports progress', async () => {
    const progress: number[] = []
    const promise = uploadDocument(makeFile(), (fraction) => progress.push(fraction))
    const xhr = FakeXHR.instances[0]!

    expect(xhr.method).toBe('POST')
    expect(xhr.url).toBe('/api/documents')
    expect(xhr.headers['X-CSRF-Token']).toBe('csrf-123')
    expect(xhr.sentBody).toBeInstanceOf(FormData)
    expect((xhr.sentBody as FormData).get('file')).toBeInstanceOf(File)

    xhr.emitUploadProgress(50, 100)
    xhr.respond(201, { id: 7, sha256: 'abc', status: 'received', duplicate: false })

    await expect(promise).resolves.toEqual({
      id: 7,
      sha256: 'abc',
      status: 'received',
      duplicate: false,
    })
    expect(progress).toEqual([0.5, 1])
  })

  it('resolves duplicates (200) with duplicate: true', async () => {
    const promise = uploadDocument(makeFile())
    FakeXHR.instances[0]!.respond(200, {
      id: 3,
      sha256: 'abc',
      status: 'indexed',
      duplicate: true,
    })
    await expect(promise).resolves.toMatchObject({ id: 3, duplicate: true })
  })

  it('rejects 415 with an ApiError carrying the backend detail', async () => {
    const promise = uploadDocument(makeFile())
    FakeXHR.instances[0]!.respond(415, { detail: 'unsupported media type text/csv' })
    await expect(promise).rejects.toMatchObject({
      status: 415,
      detail: 'unsupported media type text/csv',
    })
    await expect(promise).rejects.toBeInstanceOf(ApiError)
  })

  it('rejects network failures with ApiError status 0', async () => {
    const promise = uploadDocument(makeFile())
    FakeXHR.instances[0]!.failNetwork()
    await expect(promise).rejects.toMatchObject({ status: 0 })
  })
})

describe('fetchDocumentMarkdown', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  it('calls the markdown endpoint and returns typed response', async () => {
    const body = {
      page_count: 1,
      pages: [{ page_number: 1, markdown: '# Hello\n\nworld' }],
    }
    fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }))
    const result = await fetchDocumentMarkdown(42)
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/documents/42/markdown')
    expect(result.page_count).toBe(1)
    expect(result.pages[0]!.page_number).toBe(1)
    expect(result.pages[0]!.markdown).toBe('# Hello\n\nworld')
  })

  it('returns an empty page list when page_count is 0', async () => {
    const body = { page_count: 0, pages: [] }
    fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }))
    const result = await fetchDocumentMarkdown(7)
    expect(result.page_count).toBe(0)
    expect(result.pages).toHaveLength(0)
  })
})

describe('DOCUMENT_STATUSES', () => {
  it('lists every document status with a human label', () => {
    expect(DOCUMENT_STATUSES.map((s) => s.value)).toEqual([
      'received',
      'ocr',
      'extract',
      'indexed',
      'failed',
    ])
    expect(DOCUMENT_STATUSES.find((s) => s.value === 'indexed')?.text).toBe('Indexed')
  })
})
