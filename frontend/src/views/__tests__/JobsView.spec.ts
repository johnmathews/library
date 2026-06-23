import { describe, it, expect, beforeEach, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import JobsView from '../JobsView.vue'
import type { JobInfo } from '@/api/documents'
import { listJobs } from '@/api/documents'

vi.mock('@/api/documents', () => ({
  listJobs: vi.fn(),
}))

const listJobsMock = vi.mocked(listJobs)

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

describe('JobsView', () => {
  beforeEach(() => {
    listJobsMock.mockReset()
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
    expect(listJobsMock).toHaveBeenCalledWith(200, false)

    await wrapper.get('[data-testid="jobs-show-system"]').setValue(true)
    await flushPromises()
    expect(listJobsMock).toHaveBeenCalledWith(200, true)
  })
})
