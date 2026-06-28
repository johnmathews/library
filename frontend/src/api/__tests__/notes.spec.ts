import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createNote, listNoteVersions, restoreNoteVersion, updateNote } from '../notes'

describe('notes API', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  function respondWith(body: unknown, status = 200): void {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }

  it('POSTs /api/notes with the title and body, returning the document detail', async () => {
    respondWith({ id: 42, source: 'note', mime_type: 'text/markdown' }, 201)
    const detail = await createNote({ title: 'Shopping list', body_markdown: '- milk' })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/notes')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      title: 'Shopping list',
      body_markdown: '- milk',
    })
    expect(detail).toMatchObject({ id: 42, source: 'note' })
  })

  it('PATCHes /api/notes/{id} with the patch body', async () => {
    respondWith({ id: 42, source: 'note' })
    await updateNote(42, { title: 'New title', body_markdown: '# Heading' })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/notes/42')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({
      title: 'New title',
      body_markdown: '# Heading',
    })
  })

  it('GETs /api/notes/{id}/versions and returns the version list', async () => {
    respondWith([
      { version_no: 2, title: 'v2', body: 'b2', created_at: '2026-06-02T00:00:00Z' },
      { version_no: 1, title: 'v1', body: 'b1', created_at: '2026-06-01T00:00:00Z' },
    ])
    const versions = await listNoteVersions(42)
    const [url] = fetchMock.mock.calls[0] as [string]
    expect(url).toBe('/api/notes/42/versions')
    expect(versions).toHaveLength(2)
    expect(versions[0]!.version_no).toBe(2)
  })

  it('POSTs /api/notes/{id}/versions/{version_no}/restore', async () => {
    respondWith({ id: 42, source: 'note' })
    await restoreNoteVersion(42, 1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/notes/42/versions/1/restore')
    expect(init.method).toBe('POST')
  })
})
