import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DocumentHistoryTimeline from '@/components/DocumentHistoryTimeline.vue'
import type { IngestionEvent } from '@/api/documents'

function ev(event: string, created_at: string, detail: Record<string, unknown> = {}): IngestionEvent {
  return { event, created_at, detail }
}

const EVENTS: IngestionEvent[] = [
  ev('received', '2026-06-10T10:00:00Z'),
  ev('status_changed', '2026-06-10T10:00:01Z', { from: 'received', to: 'ocr' }),
  ev('ocr_completed', '2026-06-10T10:00:05Z'),
  ev('status_changed', '2026-06-10T10:00:06Z', { from: 'ocr', to: 'extract' }),
  ev('extraction_completed', '2026-06-10T10:00:10Z'),
  ev('embedding_skipped', '2026-06-10T10:00:11Z'),
  ev('status_changed', '2026-06-10T10:00:20Z', { from: 'embed', to: 'indexed' }),
  ev('user_edited', '2026-06-11T09:00:00Z', { fields: ['title', 'summary'] }),
  ev('project_changed', '2026-06-11T09:00:00Z', { projects: ['house-purchase'] }),
]

describe('DocumentHistoryTimeline', () => {
  it('shows humanized milestones and hides per-stage noise by default', () => {
    const w = mount(DocumentHistoryTimeline, { props: { events: EVENTS } })
    const items = w.findAll('[data-testid="history-item"]')
    const text = items.map((i) => i.text())

    // Milestones are present and humanized.
    expect(text.some((t) => t.includes('Ingested'))).toBe(true)
    expect(text.some((t) => t.includes('OCR complete'))).toBe(true)
    expect(text.some((t) => t.includes('Description & metadata added'))).toBe(true)
    expect(text.some((t) => t.includes('Indexed for search'))).toBe(true)
    expect(text.some((t) => t.includes('Edited') && t.includes('title, summary'))).toBe(true)
    expect(text.some((t) => t.includes('Projects changed'))).toBe(true)

    // Noise is hidden from the curated view: only the indexed transition survives,
    // so no intermediate status_changed and no *_skipped rows.
    const curated = w.find('ol').text()
    expect(curated).not.toContain('Status changed')
    expect(curated).not.toContain('Embedding skipped')

    // One row per non-noise event (received, ocr, extraction, indexed, edited, projects = 6).
    expect(items).toHaveLength(6)
  })

  it('reveals every raw event under "show all"', () => {
    const w = mount(DocumentHistoryTimeline, { props: { events: EVENTS } })
    const raw = w.findAll('[data-testid="history-raw-item"]')
    // The raw log is complete — every event, including the noise, appears.
    expect(raw).toHaveLength(EVENTS.length)
    expect(w.find('[data-testid="history-raw-list"]').text()).toContain('status_changed')
    expect(w.find('[data-testid="history-raw-list"]').text()).toContain('embedding_skipped')
  })

  it('orders milestones and the raw log newest-first, even from out-of-order input', () => {
    // Deliberately shuffled input; the component must sort it, not trust order.
    const shuffled: IngestionEvent[] = [
      EVENTS[4]!, // extraction_completed 10:00:10
      EVENTS[0]!, // received           10:00:00
      EVENTS[7]!, // user_edited        2026-06-11T09:00:00
      EVENTS[2]!, // ocr_completed      10:00:05
      EVENTS[6]!, // status_changed→indexed 10:00:20
    ]
    const w = mount(DocumentHistoryTimeline, { props: { events: shuffled } })

    const milestoneText = w.findAll('[data-testid="history-item"]').map((i) => i.text())
    // Newest (user_edited on the 11th) is first; oldest (received) is last.
    expect(milestoneText[0]).toContain('Edited')
    expect(milestoneText[milestoneText.length - 1]).toContain('Ingested')

    // Raw log is likewise newest-first.
    const rawEventNames = w
      .findAll('[data-testid="history-raw-item"]')
      .map((i) => i.text())
    // First raw row is the newest event (user_edited), last is the oldest (received).
    expect(rawEventNames[0]).toContain('user_edited')
    expect(rawEventNames[rawEventNames.length - 1]).toContain('received')
  })

  it('keeps events with equal timestamps in stable (incoming) order', () => {
    // user_edited and project_changed share 2026-06-11T09:00:00Z. Stable sort
    // must preserve their incoming order (user_edited before project_changed)
    // at the top of the newest-first list.
    const w = mount(DocumentHistoryTimeline, { props: { events: EVENTS } })
    const milestoneText = w.findAll('[data-testid="history-item"]').map((i) => i.text())
    const editedIdx = milestoneText.findIndex((t) => t.includes('Edited'))
    const projectsIdx = milestoneText.findIndex((t) => t.includes('Projects changed'))
    expect(editedIdx).toBeGreaterThanOrEqual(0)
    expect(editedIdx).toBeLessThan(projectsIdx)
    // Both are the newest events, so they sit at the very top.
    expect(editedIdx).toBe(0)
  })

  it('renders an empty state when there are no milestones', () => {
    const w = mount(DocumentHistoryTimeline, { props: { events: [] } })
    expect(w.find('[data-testid="history-empty"]').exists()).toBe(true)
  })
})
