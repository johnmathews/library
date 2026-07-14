import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import HeldEmailsView from '../HeldEmailsView.vue'
import type { HeldEmailDetail, HeldEmailItem } from '@/api/heldEmails'
import { INGEST_POLL_INTERVAL_MS } from '@/stores/heldEmails'

vi.mock('@/api/heldEmails', async (importOriginal) => {
  // Keep the real types/constants (HELD_EMAILS_MAX_LIMIT); mock the calls.
  const actual = await importOriginal<typeof import('@/api/heldEmails')>()
  return {
    ...actual,
    listHeldEmails: vi.fn(),
    getHeldEmail: vi.fn(),
    ingestHeldEmail: vi.fn(),
    dismissHeldEmail: vi.fn(),
  }
})

import { dismissHeldEmail, getHeldEmail, ingestHeldEmail, listHeldEmails } from '@/api/heldEmails'

function makeHeld(overrides: Partial<HeldEmailItem> = {}): HeldEmailItem {
  return {
    id: 5,
    message_id: '<msg-5@example.com>',
    sender: 'billing@acme.example',
    subject: 'Your May invoice',
    received_at: '2026-07-10T08:00:00Z',
    created_at: '2026-07-10T08:01:00Z',
    verdict: 'llm_hold',
    reason: 'Looks like a newsletter',
    status: 'held',
    owner_id: 1,
    owner: 'John',
    resolved_at: null,
    document_ids: [],
    last_error: null,
    ...overrides,
  }
}

function makeDetail(overrides: Partial<HeldEmailDetail> = {}): HeldEmailDetail {
  return { ...makeHeld(), trace: {}, ...overrides }
}

function listBody(items: HeldEmailItem[], total = items.length) {
  return { items, total, limit: 100, offset: 0 }
}

const Stub = { template: '<div />' }

describe('HeldEmailsView', () => {
  let router: Router
  let pinia: Pinia
  let wrapper: VueWrapper | undefined

  beforeEach(async () => {
    vi.clearAllMocks()
    vi.mocked(listHeldEmails).mockResolvedValue(listBody([]))
    pinia = createPinia()
    setActivePinia(pinia)
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'documents', component: Stub },
        { path: '/held-emails', name: 'held-emails', component: HeldEmailsView },
        { path: '/documents/:id', name: 'document-detail', component: Stub },
      ],
    })
    await router.push('/held-emails')
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.useRealTimers()
  })

  async function mountView(): Promise<VueWrapper> {
    wrapper = mount(HeldEmailsView, { global: { plugins: [router, pinia] } })
    await flushPromises()
    return wrapper
  }

  it('loads the held filter by default and renders sender, subject, verdict and reason', async () => {
    vi.mocked(listHeldEmails).mockResolvedValue(listBody([makeHeld()]))
    const w = await mountView()

    expect(listHeldEmails).toHaveBeenCalledWith(
      expect.objectContaining({ status: 'held', limit: 100 }),
    )
    expect(w.find('[data-testid="held-emails-view"]').exists()).toBe(true)
    const row = w.find('[data-testid="held-email-row"]')
    expect(row.exists()).toBe(true)
    expect(row.text()).toContain('billing@acme.example')
    expect(row.text()).toContain('Your May invoice')
    expect(row.text()).toContain('Looks like a newsletter')
    expect(row.text()).toContain('Held 10 July 2026')
    // llm_hold gets the violet (purple) chip.
    const chip = row.find('[data-testid="held-email-verdict"]')
    expect(chip.text()).toBe('LLM hold')
    expect(chip.classes().join(' ')).toContain('violet')
  })

  it('renders deterministic verdicts with a neutral gray chip', async () => {
    vi.mocked(listHeldEmails).mockResolvedValue(
      listBody([makeHeld({ verdict: 'below_substance', reason: 'below_substance:8w' })]),
    )
    const w = await mountView()
    const chip = w.find('[data-testid="held-email-verdict"]')
    expect(chip.text()).toBe('Below substance')
    expect(chip.classes().join(' ')).toContain('gray')
    expect(chip.classes().join(' ')).not.toContain('violet')
  })

  it('shows the empty state when nothing is held', async () => {
    const w = await mountView()
    const empty = w.find('[data-testid="held-emails-empty"]')
    expect(empty.exists()).toBe(true)
    expect(empty.text()).toContain('No held emails — everything filed itself.')
  })

  it('changing the status filter reloads with that status', async () => {
    const w = await mountView()
    vi.mocked(listHeldEmails).mockResolvedValue(
      listBody([makeHeld({ status: 'dismissed', resolved_at: '2026-07-11T00:00:00Z' })]),
    )

    await w.find('[data-testid="held-emails-status-filter"]').setValue('dismissed')
    await flushPromises()

    expect(listHeldEmails).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: 'dismissed' }),
    )
    expect(w.find('[data-testid="held-email-row"]').text()).toContain('Dismissed')
  })

  it('expanding a row loads and renders the structured trace', async () => {
    vi.mocked(listHeldEmails).mockResolvedValue(listBody([makeHeld()]))
    vi.mocked(getHeldEmail).mockResolvedValue(
      makeDetail({
        trace: {
          email_from: 'billing@acme.example',
          email_subject: 'Your May invoice',
          items: [
            {
              kind: 'attachment',
              filename: 'scan.pdf',
              mime: 'application/pdf',
              size: 1234,
              stage: 'llm_label',
              verdict: 'flagged_ambiguous',
              reason: 'looks promotional',
            },
            {
              kind: 'body',
              filename: null,
              mime: 'text/markdown',
              size: 40,
              stage: 'body_substance',
              verdict: 'skipped',
              reason: 'below_substance:8w',
            },
          ],
        },
      }),
    )
    const w = await mountView()

    expect(w.find('[data-testid="held-email-trace"]').exists()).toBe(false)
    await w.find('[data-testid="held-email-trace-toggle"]').trigger('click')
    await flushPromises()

    expect(getHeldEmail).toHaveBeenCalledWith(5)
    const trace = w.find('[data-testid="held-email-trace"]')
    expect(trace.exists()).toBe(true)
    const lines = trace.findAll('[data-testid="held-email-trace-item"]')
    expect(lines).toHaveLength(2)
    expect(lines[0]!.text()).toContain('scan.pdf')
    expect(lines[0]!.text()).toContain('llm_label')
    expect(lines[0]!.text()).toContain('Flagged ambiguous')
    expect(lines[0]!.text()).toContain('looks promotional')
    expect(lines[1]!.text()).toContain('<body>')
    expect(lines[1]!.text()).toContain('below_substance:8w')
    // Provenance chips.
    expect(trace.text()).toContain('billing@acme.example')

    // Collapsing hides it again without another fetch.
    await w.find('[data-testid="held-email-trace-toggle"]').trigger('click')
    expect(w.find('[data-testid="held-email-trace"]').exists()).toBe(false)
    expect(getHeldEmail).toHaveBeenCalledTimes(1)
  })

  it('ingest-anyway shows the queued state, then the row leaves the held list once resolved', async () => {
    vi.useFakeTimers()
    vi.mocked(listHeldEmails).mockResolvedValue(listBody([makeHeld()]))
    vi.mocked(ingestHeldEmail).mockResolvedValue({ queued: true, job_id: 7 })
    vi.mocked(getHeldEmail).mockResolvedValue(
      makeDetail({ status: 'ingested', document_ids: [42], resolved_at: '2026-07-14T10:00:00Z' }),
    )
    const w = await mountView()

    await w.find('[data-testid="held-email-ingest"]').trigger('click')
    await flushPromises()

    // Observable outcome: the action buttons give way to the queued indicator.
    expect(ingestHeldEmail).toHaveBeenCalledWith(5)
    expect(w.find('[data-testid="held-email-queued"]').exists()).toBe(true)
    expect(w.find('[data-testid="held-email-ingest"]').exists()).toBe(false)

    // The poll refetches the row and finds it resolved → it leaves the held filter.
    await vi.advanceTimersByTimeAsync(INGEST_POLL_INTERVAL_MS + 10)
    await flushPromises()
    expect(getHeldEmail).toHaveBeenCalled()
    expect(w.find('[data-testid="held-email-row"]').exists()).toBe(false)
    expect(w.find('[data-testid="held-emails-empty"]').exists()).toBe(true)
  })

  it('dismiss removes the row from the held filter', async () => {
    vi.mocked(listHeldEmails).mockResolvedValue(listBody([makeHeld()]))
    vi.mocked(dismissHeldEmail).mockResolvedValue(
      makeDetail({ status: 'dismissed', resolved_at: '2026-07-14T10:00:00Z' }),
    )
    const w = await mountView()

    await w.find('[data-testid="held-email-dismiss"]').trigger('click')
    await flushPromises()

    expect(dismissHeldEmail).toHaveBeenCalledWith(5)
    expect(w.find('[data-testid="held-email-row"]').exists()).toBe(false)
    expect(w.find('[data-testid="held-emails-empty"]').exists()).toBe(true)
  })

  it('resolved rows show the outcome, document links and any last error — no actions', async () => {
    vi.mocked(listHeldEmails).mockResolvedValue(
      listBody([
        makeHeld({
          id: 9,
          status: 'ingested',
          resolved_at: '2026-07-12T09:00:00Z',
          document_ids: [42, 43],
          last_error: 'Held → Processed move failed; documents are safe',
        }),
      ]),
    )
    const w = await mountView()
    const row = w.find('[data-testid="held-email-row"]')

    expect(row.text()).toContain('Ingested')
    const links = row.findAll('[data-testid="held-email-document-link"]')
    expect(links).toHaveLength(2)
    expect(links[0]!.attributes('href')).toBe('/documents/42')
    expect(links[1]!.attributes('href')).toBe('/documents/43')
    const error = row.find('[data-testid="held-email-error"]')
    expect(error.exists()).toBe(true)
    expect(error.text()).toContain('move failed')
    // Resolved rows expose no ingest/dismiss actions.
    expect(row.find('[data-testid="held-email-ingest"]').exists()).toBe(false)
    expect(row.find('[data-testid="held-email-dismiss"]').exists()).toBe(false)
  })
})
