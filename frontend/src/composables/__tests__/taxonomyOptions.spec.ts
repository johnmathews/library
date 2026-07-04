import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { refreshTaxonomyOptions, useTaxonomyOptions } from '@/composables/taxonomyOptions'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const RECIPIENTS_BEFORE = [{ id: 5, name: 'John', document_count: 7 }]
const RECIPIENTS_AFTER = [
  { id: 5, name: 'John', document_count: 7 },
  { id: 9, name: 'Jane', document_count: 1 },
]

describe('taxonomyOptions cache', () => {
  const fetchMock = vi.fn()
  let recipients = RECIPIENTS_BEFORE

  beforeEach(() => {
    // Fresh Pinia per test → a fresh taxonomy-options store (empty lists, no
    // in-flight fetch), replacing the old resetTaxonomyOptionsForTests() reset.
    setActivePinia(createPinia())
    recipients = RECIPIENTS_BEFORE
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: unknown) => {
      const url = String(input)
      if (url === '/api/kinds') return Promise.resolve(jsonResponse([]))
      if (url === '/api/senders') return Promise.resolve(jsonResponse([]))
      if (url === '/api/recipients') return Promise.resolve(jsonResponse(recipients))
      if (url === '/api/tags') return Promise.resolve(jsonResponse([]))
      if (url === '/api/projects') return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: `unexpected ${url}` }, 500))
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('ensureLoaded fetches once and caches across calls', async () => {
    const { recipients: refs, ensureLoaded } = useTaxonomyOptions()
    await ensureLoaded()
    await ensureLoaded()
    await flushPromises()
    expect(refs.value.map((r) => r.name)).toEqual(['John'])
    // One fetch per endpoint, despite two ensureLoaded calls.
    expect(fetchMock.mock.calls.filter(([u]) => String(u) === '/api/recipients')).toHaveLength(1)
  })

  it('refreshTaxonomyOptions invalidates the cache and refetches', async () => {
    const { recipients: refs, ensureLoaded } = useTaxonomyOptions()
    await ensureLoaded()
    await flushPromises()
    expect(refs.value.map((r) => r.name)).toEqual(['John'])

    // A new recipient is created on the backend.
    recipients = RECIPIENTS_AFTER
    await refreshTaxonomyOptions()
    await flushPromises()

    expect(refs.value.map((r) => r.name)).toEqual(['John', 'Jane'])
    expect(fetchMock.mock.calls.filter(([u]) => String(u) === '/api/recipients')).toHaveLength(2)
  })
})
