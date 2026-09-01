import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { FacetRef } from '@/api/facets'

const fetchFacets = vi.fn()
vi.mock('@/api/facets', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/facets')>()),
  fetchFacets: (...args: unknown[]) => fetchFacets(...args),
}))

const { useFacetVocabulary } = await import('../facetVocabulary')

const FACETS: FacetRef[] = [{ key: 'category', label: 'Category', ordinal: 0, values: [] }]

describe('useFacetVocabulary', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    fetchFacets.mockReset()
    fetchFacets.mockResolvedValue(FACETS)
  })

  it('exposes the vocabulary after ensureLoaded', async () => {
    const vocabulary = useFacetVocabulary()
    await vocabulary.ensureLoaded()
    expect(vocabulary.facets.value).toEqual(FACETS)
  })

  // The point of the whole module: the drill panel and the rule editor can be
  // mounted on the same screen, and two snapshots of a mutable vocabulary can
  // disagree about which values still exist.
  it('fetches once across repeated ensureLoaded calls', async () => {
    const first = useFacetVocabulary()
    const second = useFacetVocabulary()
    await Promise.all([first.ensureLoaded(), second.ensureLoaded(), first.ensureLoaded()])
    expect(fetchFacets).toHaveBeenCalledTimes(1)
  })

  it('leaves the list empty and does not throw when the fetch fails', async () => {
    fetchFacets.mockRejectedValue(new Error('offline'))
    const vocabulary = useFacetVocabulary()
    await expect(vocabulary.ensureLoaded()).resolves.toBeUndefined()
    expect(vocabulary.facets.value).toEqual([])
  })

  it('refresh invalidates the cache and fetches again', async () => {
    const vocabulary = useFacetVocabulary()
    await vocabulary.ensureLoaded()
    await vocabulary.refresh()
    expect(fetchFacets).toHaveBeenCalledTimes(2)
  })
})
