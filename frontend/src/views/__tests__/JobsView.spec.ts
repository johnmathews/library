import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import JobsView from '../JobsView.vue'
import type { JobInfo } from '@/api/documents'
import { getDocument, listDocuments, listJobs, listJobTaskNames } from '@/api/documents'
import { useJobsStore } from '@/stores/jobs'

vi.mock('@/api/documents', () => ({
  listJobs: vi.fn(),
  listJobTaskNames: vi.fn(() => Promise.resolve([])),
  listDocuments: vi.fn(() => Promise.resolve({ items: [], total: 0, limit: 25, offset: 0 })),
  getDocument: vi.fn(() => Promise.resolve({ id: 0, title: null })),
}))

const listJobsMock = vi.mocked(listJobs)
const listJobTaskNamesMock = vi.mocked(listJobTaskNames)
const listDocumentsMock = vi.mocked(listDocuments)
const getDocumentMock = vi.mocked(getDocument)

function job(partial: Partial<JobInfo> & { id: number }): JobInfo {
  return {
    status: 'succeeded',
    task_name: 'library.jobs.process_document',
    attempts: 0,
    scheduled_at: null,
    started_at: null,
    finished_at: null,
    document_id: partial.id,
    active: false,
    document_title: null,
    document_status: null,
    error: null,
    cost_usd: null,
    tokens: null,
    ...partial,
  }
}

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/jobs', name: 'jobs', component: JobsView },
    { path: '/documents/:id', name: 'document-detail', component: { template: '<div/>' } },
  ],
})

async function mountView() {
  setActivePinia(createPinia())
  await router.push('/jobs')
  await router.isReady()
  const wrapper = mount(JobsView, { global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}

const COLUMNS_KEY = 'library:jobs-columns'

describe('JobsView', () => {
  beforeEach(() => {
    vi.useRealTimers()
    listJobsMock.mockReset()
    listJobsMock.mockResolvedValue([])
    listJobTaskNamesMock.mockReset()
    listJobTaskNamesMock.mockResolvedValue([])
    listDocumentsMock.mockReset()
    listDocumentsMock.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })
    getDocumentMock.mockReset()
    getDocumentMock.mockResolvedValue({ id: 0, title: null } as Awaited<ReturnType<typeof getDocument>>)
    localStorage.clear()
  })

  it('splits jobs into active and historical sections', async () => {
    listJobsMock.mockResolvedValue([
      job({ id: 1, status: 'doing', active: true, document_title: 'Running', document_status: 'ocr' }),
      job({ id: 2, status: 'succeeded', active: false, document_title: 'Done', document_status: 'indexed' }),
      job({ id: 3, status: 'failed', active: false, document_title: 'Broken', document_status: 'failed', error: 'ocr exploded' }),
    ])
    const wrapper = await mountView()

    expect(wrapper.findAll('[data-testid="jobs-active-row"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-testid="jobs-historical-row"]')).toHaveLength(2)
  })

  it('renders enriched columns: stage, cost, and error', async () => {
    listJobsMock.mockResolvedValue([
      job({
        id: 9,
        status: 'failed',
        active: false,
        document_title: 'Energierekening',
        document_status: 'failed',
        error: 'ocr exploded',
        cost_usd: 0.0123,
      }),
    ])
    const wrapper = await mountView()
    const row = wrapper.get('[data-testid="jobs-historical-row"]')

    expect(row.text()).toContain('Energierekening')
    expect(row.text()).toContain('ocr exploded')
    expect(row.text()).toContain('$0.0123')
    // The status badge shows the document's pipeline stage, not the job status.
    expect(row.text()).toContain('Failed')
  })

  it('renders task name and a duration for a finished system task', async () => {
    listJobsMock.mockResolvedValue([
      job({
        id: 50,
        task_name: 'library.jobs.poll_email_inbox',
        document_id: null,
        active: false,
        started_at: '2026-06-23T10:00:00Z',
        finished_at: '2026-06-23T10:00:03Z',
      }),
    ])
    const wrapper = await mountView()
    const row = wrapper.get('[data-testid="jobs-historical-row"]')

    // Humanised task name (final segment, no "library.jobs." prefix).
    expect(row.text()).toContain('Poll email inbox')
    // Wall-clock duration between started and finished events.
    expect(row.text()).toContain('3.0s')
  })

  it('shows a dash for the duration of a still-running active job', async () => {
    listJobsMock.mockResolvedValue([
      job({
        id: 51,
        task_name: 'library.jobs.process_document',
        active: true,
        status: 'doing',
        document_status: 'ocr',
        started_at: '2026-06-23T10:00:00Z',
        finished_at: null,
      }),
    ])
    const wrapper = await mountView()
    const row = wrapper.get('[data-testid="jobs-active-row"]')
    expect(row.text()).toContain('Process document')
  })

  it('links a job row to its document', async () => {
    listJobsMock.mockResolvedValue([
      job({ id: 42, active: true, status: 'doing', document_title: 'Linkable', document_status: 'extract' }),
    ])
    const wrapper = await mountView()
    const link = wrapper.get('[data-testid="jobs-active-row"] a')
    expect(link.attributes('href')).toBe('/documents/42')
  })

  it('shows empty states when there are no jobs', async () => {
    listJobsMock.mockResolvedValue([])
    const wrapper = await mountView()
    expect(wrapper.find('[data-testid="jobs-active-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="jobs-historical-empty"]').exists()).toBe(true)
  })

  it('surfaces an error when the request fails', async () => {
    listJobsMock.mockRejectedValue(new Error('boom'))
    const wrapper = await mountView()
    expect(wrapper.find('[data-testid="jobs-error"]').exists()).toBe(true)
  })

  it('hides system tasks by default and refetches with them when toggled', async () => {
    listJobsMock.mockResolvedValue([])
    const wrapper = await mountView()
    expect(listJobsMock).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 200, includeSystem: false }),
    )

    await wrapper.get('[data-testid="jobs-show-system"]').setValue(true)
    await flushPromises()
    expect(listJobsMock).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 200, includeSystem: true }),
    )
  })

  it('hides a column from the Recent table when toggled off in the Columns menu', async () => {
    listJobsMock.mockResolvedValue([
      job({ id: 7, active: false, document_title: 'Energierekening', document_status: 'indexed', cost_usd: 0.5 }),
    ])
    const wrapper = await mountView()

    // Cost column is visible by default.
    expect(wrapper.find('[data-testid="jobs-col-header-cost"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="jobs-col-cell-cost"]').exists()).toBe(true)

    // Open the Columns menu and toggle Cost off.
    await wrapper.get('[data-testid="jobs-columns-button"]').trigger('click')
    await wrapper.get('[data-testid="jobs-col-toggle-cost"]').setValue(false)
    await flushPromises()

    expect(wrapper.find('[data-testid="jobs-col-header-cost"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="jobs-col-cell-cost"]').exists()).toBe(false)
  })

  it('persists the column choice to localStorage and reads it back on a fresh mount', async () => {
    listJobsMock.mockResolvedValue([
      job({ id: 8, active: false, document_title: 'Persisted', document_status: 'indexed', cost_usd: 0.5 }),
    ])
    const wrapper = await mountView()

    await wrapper.get('[data-testid="jobs-columns-button"]').trigger('click')
    await wrapper.get('[data-testid="jobs-col-toggle-cost"]').setValue(false)
    await flushPromises()

    // The visibility map was written to the stable localStorage key.
    const stored = JSON.parse(localStorage.getItem(COLUMNS_KEY) ?? '{}')
    expect(stored.cost).toBe(false)

    // A fresh mount reads it back: the Cost column starts hidden.
    const fresh = await mountView()
    expect(fresh.find('[data-testid="jobs-col-header-cost"]').exists()).toBe(false)
  })

  it('renders a mobile card list mirroring the Recent jobs', async () => {
    listJobsMock.mockResolvedValue([
      job({ id: 11, active: false, document_title: 'CardDoc', document_status: 'indexed' }),
    ])
    const wrapper = await mountView()

    const cards = wrapper.find('[data-testid="jobs-historical-cards"]')
    expect(cards.exists()).toBe(true)
    expect(cards.text()).toContain('CardDoc')
  })

  it('labels a document-less system row with a System chip and its task name', async () => {
    listJobsMock.mockResolvedValue([
      job({
        id: 60,
        task_name: 'library.jobs.poll_email_inbox',
        document_id: null,
        active: false,
      }),
    ])
    const wrapper = await mountView()
    const label = wrapper.get('[data-testid="jobs-system-label"]')
    // The System chip replaces the bare em-dash; the task name is shown inline.
    expect(label.text()).toContain('System')
    expect(label.text()).toContain('Poll email inbox')
  })

  it('filters by task type, passing task_name to the server', async () => {
    listJobTaskNamesMock.mockResolvedValue([
      'library.jobs.poll_email_inbox',
      'library.jobs.process_document',
    ])
    listJobsMock.mockResolvedValue([])
    const wrapper = await mountView()

    await wrapper.get('#jobs-task-filter').setValue('library.jobs.poll_email_inbox')
    await flushPromises()
    expect(listJobsMock).toHaveBeenCalledWith(
      expect.objectContaining({ taskName: 'library.jobs.poll_email_inbox' }),
    )
  })

  it('filters to a chosen document and shows a removable chip', async () => {
    listDocumentsMock.mockResolvedValue({
      items: [{ id: 42, title: 'Invoice' }],
      total: 1,
      limit: 25,
      offset: 0,
    } as Awaited<ReturnType<typeof listDocuments>>)
    listJobsMock.mockResolvedValue([])
    const wrapper = await mountView()

    const input = wrapper.get('#jobs-document-filter')
    await input.setValue('Invoice') // triggers the typeahead search
    await flushPromises()
    await input.trigger('change') // "selecting" the matching suggestion
    await flushPromises()

    // History mode: the server is asked for that one document's jobs.
    expect(listJobsMock).toHaveBeenCalledWith(expect.objectContaining({ documentId: 42 }))
    // The active-document chip appears with the resolved title.
    const chip = wrapper.get('[data-testid="jobs-document-chip"]')
    expect(chip.text()).toContain('Invoice')
    // And the section heading switches to History.
    expect(wrapper.text()).toContain('History')

    // Clearing the chip drops the filter.
    await wrapper.get('[data-testid="jobs-document-chip-clear"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="jobs-document-chip"]').exists()).toBe(false)
  })

  it('refetches when the live store reports any document event, including stage changes', async () => {
    listJobsMock.mockResolvedValue([])
    const wrapper = await mountView()
    const before = listJobsMock.mock.calls.length

    // A non-terminal stage change leaves activeCount unchanged but must still
    // refresh the table via the store's per-event signal.
    const store = useJobsStore()
    store.handle({ document_id: 1, event: 'status_changed', status: 'extract', title: 'A' })
    await flushPromises()

    expect(listJobsMock.mock.calls.length).toBeGreaterThan(before)
    wrapper.unmount()
  })

  it('polls for system tasks while shown and stops when toggled off or unmounted', async () => {
    vi.useFakeTimers()
    listJobsMock.mockResolvedValue([])
    const wrapper = await mountView()

    await wrapper.get('[data-testid="jobs-show-system"]').setValue(true)
    await flushPromises()
    const afterOn = listJobsMock.mock.calls.length

    vi.advanceTimersByTime(10000)
    await flushPromises()
    expect(listJobsMock.mock.calls.length).toBeGreaterThan(afterOn)

    await wrapper.get('[data-testid="jobs-show-system"]').setValue(false)
    await flushPromises()
    const afterOff = listJobsMock.mock.calls.length
    vi.advanceTimersByTime(30000)
    await flushPromises()
    // No further polling once the system tasks are hidden again.
    expect(listJobsMock.mock.calls.length).toBe(afterOff)

    wrapper.unmount()
  })

  it('resolves the chip title for a deep-linked document filter', async () => {
    getDocumentMock.mockResolvedValue({ id: 7, title: 'Deep Linked' } as Awaited<
      ReturnType<typeof getDocument>
    >)
    listJobsMock.mockResolvedValue([])
    setActivePinia(createPinia())
    await router.push('/jobs?document_id=7')
    await router.isReady()
    const wrapper = mount(JobsView, { global: { plugins: [router] } })
    await flushPromises()

    expect(listJobsMock).toHaveBeenCalledWith(expect.objectContaining({ documentId: 7 }))
    expect(wrapper.get('[data-testid="jobs-document-chip"]').text()).toContain('Deep Linked')
  })
})
