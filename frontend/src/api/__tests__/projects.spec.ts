import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createProject, listProjects } from '../projects'

describe('projects API', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  function respondWith(body: unknown): void {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  }

  it('GETs /api/projects and returns the option list', async () => {
    respondWith([{ slug: 'house-purchase', name: 'House purchase', document_count: 4 }])
    const projects = await listProjects()
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/projects')
    expect(projects).toEqual([
      { slug: 'house-purchase', name: 'House purchase', document_count: 4 },
    ])
  })

  it('POSTs /api/projects with the name and returns the created project', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ slug: 'house-purchase', name: 'House purchase', document_count: 0 }),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const project = await createProject('House purchase')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/projects')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ name: 'House purchase' })
    expect(project).toMatchObject({ slug: 'house-purchase', name: 'House purchase' })
  })
})
