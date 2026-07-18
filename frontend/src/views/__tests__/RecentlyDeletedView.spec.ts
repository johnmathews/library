import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import RecentlyDeletedView from '../RecentlyDeletedView.vue'
import type { DeletedDocumentItem, DeletedDocumentListResponse } from '@/api/documents'
import { useFlashStore } from '@/stores/flash'

vi.mock('@/api/documents', () => ({
  listDeletedDocuments: vi.fn(),
  restoreDocument: vi.fn(),
  permanentlyDeleteDocument: vi.fn(),
}))

import { listDeletedDocuments, permanentlyDeleteDocument, restoreDocument } from '@/api/documents'

const listMock = vi.mocked(listDeletedDocuments)
const restoreMock = vi.mocked(restoreDocument)
const purgeMock = vi.mocked(permanentlyDeleteDocument)

// jsdom lacks HTMLDialogElement.showModal/close (ConfirmDialog uses <dialog>).
beforeAll(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.setAttribute('open', '')
    }
  }
  if (typeof HTMLDialogElement.prototype.close !== 'function') {
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.removeAttribute('open')
      this.dispatchEvent(new Event('close'))
    }
  }
})

function makeItem(overrides: Partial<DeletedDocumentItem> = {}): DeletedDocumentItem {
  return {
    id: 12,
    title: 'Energierekening mei 2026',
    summary: null,
    kind: { slug: 'invoice', name: 'Invoice' },
    sender: { id: 3, name: 'Eneco' },
    recipient: null,
    tags: [],
    projects: [],
    matters: [],
    document_date: '2026-05-15',
    due_date: null,
    expiry_date: null,
    language: 'nld',
    status: 'indexed',
    mime_type: 'application/pdf',
    page_count: 2,
    created_at: '2026-06-10T12:00:00Z',
    updated_at: '2026-06-11T09:30:00Z',
    has_searchable_pdf: true,
    has_thumbnail: true,
    amount_total: null,
    currency: null,
    snippet: null,
    rank: null,
    review_status: 'unreviewed',
    review_findings: [],
    deleted_at: '2026-07-01T09:00:00Z',
    purge_at: '2026-07-31T09:00:00Z',
    days_remaining: 12,
    ...overrides,
  }
}

function listBody(
  items: DeletedDocumentItem[],
  retention_days = 30,
): DeletedDocumentListResponse {
  return { items, total: items.length, limit: 25, offset: 0, retention_days }
}

const Stub = { template: '<div />' }

describe('RecentlyDeletedView', () => {
  let router: Router
  let pinia: Pinia
  let wrapper: VueWrapper | undefined

  beforeEach(() => {
    listMock.mockReset()
    restoreMock.mockReset()
    purgeMock.mockReset()
    pinia = createPinia()
    setActivePinia(pinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/documents/:id', name: 'document-detail', component: Stub },
        { path: '/deleted', name: 'documents-deleted', component: RecentlyDeletedView },
      ],
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
  })

  async function mountView(): Promise<VueWrapper> {
    wrapper = mount(RecentlyDeletedView, { global: { plugins: [router, pinia] } })
    await flushPromises()
    return wrapper
  }

  it('renders a card per deleted item with its countdown and deleted date', async () => {
    listMock.mockResolvedValue(listBody([makeItem(), makeItem({ id: 13, title: 'Contract' })]))
    const w = await mountView()
    const cards = w.findAll('[data-testid="doc-card"]')
    expect(cards).toHaveLength(2)
    expect(w.text()).toContain('Energierekening mei 2026')
    expect(w.find('[data-testid="purge-countdown"]').text()).toBe('Purges in 12 days')
    expect(w.find('[data-testid="deleted-at"]').text()).toContain('Deleted')
    // Intro line surfaces the retention window from the response.
    expect(w.find('[data-testid="deleted-intro"]').text()).toContain('30')
  })

  it('shows "Purges soon" when zero days remain', async () => {
    listMock.mockResolvedValue(listBody([makeItem({ days_remaining: 0 })]))
    const w = await mountView()
    expect(w.find('[data-testid="purge-countdown"]').text()).toBe('Purges soon')
  })

  it('restores a document: calls the API, removes the card, sets a flash', async () => {
    listMock.mockResolvedValue(listBody([makeItem(), makeItem({ id: 13, title: 'Contract' })]))
    restoreMock.mockResolvedValue({ id: 12 } as never)
    const w = await mountView()

    await w.find('[data-testid="restore-12"]').trigger('click')
    await flushPromises()

    expect(restoreMock).toHaveBeenCalledWith(12)
    expect(w.findAll('[data-testid="doc-card"]')).toHaveLength(1)
    expect(w.find('[data-testid="restore-12"]').exists()).toBe(false)
    expect(w.find('[data-testid="flash-banner"]').text()).toContain('restored')
  })

  it('keeps the title a link to the document detail route', async () => {
    // The title link is intentional — clicking it opens the document read-only.
    // The e2e (recently-deleted.spec) guards that the destination actually loads.
    listMock.mockResolvedValue(listBody([makeItem()]))
    const w = await mountView()
    const link = w.find('[data-testid="doc-card"] a')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/documents/12')
  })

  it('permanently deletes after confirmation: calls the API, removes the card, flashes', async () => {
    listMock.mockResolvedValue(listBody([makeItem(), makeItem({ id: 13, title: 'Contract' })]))
    purgeMock.mockResolvedValue(undefined)
    const w = await mountView()

    // Opening the confirm dialog does not delete anything yet.
    await w.find('[data-testid="purge-12"]').trigger('click')
    expect(purgeMock).not.toHaveBeenCalled()
    expect(w.find('[data-testid="confirm-dialog"]').exists()).toBe(true)

    await w.find('[data-testid="confirm-accept"]').trigger('click')
    await flushPromises()

    expect(purgeMock).toHaveBeenCalledWith(12)
    expect(w.findAll('[data-testid="doc-card"]')).toHaveLength(1)
    expect(w.find('[data-testid="purge-12"]').exists()).toBe(false)
    expect(w.find('[data-testid="flash-banner"]').text()).toContain('permanently deleted')
  })

  it('cancelling the confirm dialog deletes nothing', async () => {
    listMock.mockResolvedValue(listBody([makeItem()]))
    const w = await mountView()

    await w.find('[data-testid="purge-12"]').trigger('click')
    await w.find('[data-testid="confirm-cancel"]').trigger('click')
    await flushPromises()

    expect(purgeMock).not.toHaveBeenCalled()
    expect(w.findAll('[data-testid="doc-card"]')).toHaveLength(1)
  })

  it('keeps the card and flags an error when permanent delete fails', async () => {
    listMock.mockResolvedValue(listBody([makeItem()]))
    purgeMock.mockRejectedValue(new Error('nope'))
    const w = await mountView()

    await w.find('[data-testid="purge-12"]').trigger('click')
    await w.find('[data-testid="confirm-accept"]').trigger('click')
    await flushPromises()

    expect(w.findAll('[data-testid="doc-card"]')).toHaveLength(1)
    expect(w.find('[data-testid="flash-banner"]').text()).toContain('could not be deleted')
  })

  it('renders the empty state when nothing is deleted', async () => {
    listMock.mockResolvedValue(listBody([]))
    const w = await mountView()
    expect(w.find('[data-testid="deleted-empty"]').exists()).toBe(true)
    expect(w.find('[data-testid="doc-card"]').exists()).toBe(false)
  })

  it('shows an error state when the list fails to load', async () => {
    listMock.mockRejectedValue(new Error('boom'))
    const w = await mountView()
    expect(w.find('[data-testid="load-error"]').exists()).toBe(true)
  })

  it('keeps the card and flags an error when restore fails', async () => {
    listMock.mockResolvedValue(listBody([makeItem()]))
    restoreMock.mockRejectedValue(new Error('nope'))
    const w = await mountView()

    await w.find('[data-testid="restore-12"]').trigger('click')
    await flushPromises()

    expect(w.findAll('[data-testid="doc-card"]')).toHaveLength(1)
    expect(useFlashStore().message).toBeNull() // consumed into the banner
    expect(w.find('[data-testid="flash-banner"]').text()).toContain('could not be restored')
  })
})
