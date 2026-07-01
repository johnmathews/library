import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createProject, deleteProject, listProjects, updateProject } from '../projects'

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

  it('GETs /api/projects?include_archived when asked', async () => {
    respondWith([])
    await listProjects(true)
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/projects?include_archived=true')
  })

  it('POSTs /api/projects with the name/description and returns the created project', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ slug: 'house-purchase', name: 'House purchase', document_count: 0 }),
        { status: 201, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const project = await createProject('House purchase', 'Buying a flat')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/projects')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({
      name: 'House purchase',
      description: 'Buying a flat',
    })
    expect(project).toMatchObject({ slug: 'house-purchase', name: 'House purchase' })
  })

  it('PATCHes /api/projects/{slug} with the provided fields', async () => {
    respondWith({ slug: 'house-purchase', name: 'Home', document_count: 0 })
    await updateProject('house-purchase', { name: 'Home', archived: true })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/projects/house-purchase')
    expect(init.method).toBe('PATCH')
    expect(JSON.parse(String(init.body))).toEqual({ name: 'Home', archived: true })
  })

  it('DELETEs /api/projects/{slug}', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await deleteProject('house-purchase')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/projects/house-purchase')
    expect(init.method).toBe('DELETE')
  })
})
