import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import DocumentDeleteView from '../DocumentDeleteView.vue'
import { useFlashStore } from '@/stores/flash'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const DETAIL = {
  id: 12,
  title: 'Energierekening mei 2026',
  summary: null,
  kind: null,
  sender: null,
  tags: [],
  document_date: null,
  language: 'nld',
  status: 'indexed',
  mime_type: 'application/pdf',
  page_count: 1,
  created_at: '2026-06-10T12:00:00Z',
  has_searchable_pdf: false,
  has_thumbnail: false,
  ocr_text: null,
  ocr_confidence: null,
  amount_total: null,
  currency: null,
  due_date: null,
  expiry_date: null,
  source: 'upload',
  original_filename: null,
  sha256: 'abc',
  extraction: null,
  user_edited_fields: [],
  events: [],
}

const Stub = { template: '<div />' }

describe('DocumentDeleteView', () => {
  const fetchMock = vi.fn()
  let router: Router
  let pinia: Pinia
  let wrapper: VueWrapper | undefined
  let deleteStatus: number

  beforeEach(async () => {
    deleteStatus = 204
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/documents/12' && method === 'GET') {
        return Promise.resolve(jsonResponse(DETAIL))
      }
      if (url === '/api/documents/12' && method === 'DELETE') {
        return Promise.resolve(
          deleteStatus === 204
            ? new Response(null, { status: 204 })
            : jsonResponse({ detail: 'boom' }, deleteStatus),
        )
      }
      return Promise.resolve(jsonResponse({ detail: 'document not found' }, 404))
    })
    pinia = createPinia()
    setActivePinia(pinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/documents/:id', name: 'document-detail', component: Stub },
        { path: '/documents/:id/delete', name: 'document-delete', component: DocumentDeleteView },
      ],
    })
    await router.push('/documents/12/delete')
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.unstubAllGlobals()
  })

  async function mountView(): Promise<VueWrapper> {
    wrapper = mount(DocumentDeleteView, { global: { plugins: [router, pinia] } })
    await flushPromises()
    return wrapper
  }

  it('shows a warning naming the document, with confirm and cancel', async () => {
    const w = await mountView()
    expect(w.find('h1').text()).toContain('Are you sure')
    expect(w.find('[data-testid="delete-warning"]').text()).toContain('Energierekening mei 2026')
    expect(w.find('[data-testid="confirm-delete"]').exists()).toBe(true)
    expect(w.find('[data-testid="cancel-delete"]').attributes('href')).toBe('/documents/12')
    const backLink = w.findAll('a').find((a) => a.text().includes('Back to the document'))
    expect(backLink?.attributes('href')).toBe('/documents/12')
  })

  it('confirm sends DELETE, sets the flash message and redirects to the list', async () => {
    const w = await mountView()
    await w.find('[data-testid="confirm-delete"]').trigger('click')
    await flushPromises()

    const deleteCall = fetchMock.mock.calls.find(
      (call) => (call[1] as RequestInit | undefined)?.method === 'DELETE',
    )
    expect(deleteCall).toBeDefined()
    expect(String(deleteCall![0])).toBe('/api/documents/12')
    expect(router.currentRoute.value.name).toBe('documents')
    expect(useFlashStore().consume()).toBe('Energierekening mei 2026 has been deleted.')
  })

  it('stays on the page with an error summary when the DELETE fails', async () => {
    deleteStatus = 500
    const w = await mountView()
    await w.find('[data-testid="confirm-delete"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('document-delete')
    expect(w.find('[role="alert"]').text()).toContain('Could not delete the document')
    expect(useFlashStore().message).toBeNull()
  })

  it('shows the not-found state when the document does not exist', async () => {
    await router.push('/documents/999/delete')
    const w = await mountView()
    expect(w.text()).toContain('Document not found')
    expect(w.find('[data-testid="confirm-delete"]').exists()).toBe(false)
  })
})
