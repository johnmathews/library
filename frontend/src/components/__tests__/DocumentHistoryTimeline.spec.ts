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

describe('DocumentHistoryTimeline — extraction breakdown', () => {
  function extractionItem(detail: Record<string, unknown>) {
    const w = mount(DocumentHistoryTimeline, {
      props: { events: [ev('extraction_completed', '2026-06-10T10:00:10Z', detail)] },
    })
    return w
  }

  it('narrates the VISION fallback when the low-confidence retry re-read the file', () => {
    const w = extractionItem({
      model: 'claude-opus-4-8',
      confidence: 'high',
      escalated: true,
      input_mode: 'document',
      cost_usd: 0.0123,
    })
    const method = w.find('[data-testid="history-extraction-method"]')
    expect(method.exists()).toBe(true)
    expect(method.text().toLowerCase()).toContain('vision fallback')
    // It gets a violet accent so the important case is unmissable.
    expect(method.classes().some((c) => c.includes('violet'))).toBe(true)
  })

  it('image input on the escalated retry is also the vision fallback', () => {
    const w = extractionItem({ escalated: true, input_mode: 'image', confidence: 'low' })
    expect(w.find('[data-testid="history-extraction-method"]').text().toLowerCase()).toContain(
      'vision fallback',
    )
  })

  it('narrates a model-only escalation (file could not be sent)', () => {
    const w = extractionItem({ escalated: true, input_mode: 'text', confidence: 'high' })
    const t = w.find('[data-testid="history-extraction-method"]').text().toLowerCase()
    expect(t).toContain('stronger model')
    expect(t).not.toContain('vision')
  })

  it('narrates an original-file-first read when OCR was unusable (no escalation)', () => {
    const w = extractionItem({ escalated: false, input_mode: 'document', confidence: 'high' })
    const t = w.find('[data-testid="history-extraction-method"]').text().toLowerCase()
    expect(t).toContain('original file')
    expect(t).not.toContain('vision fallback')
  })

  it('narrates a normal OCR-text read', () => {
    const w = extractionItem({ escalated: false, input_mode: 'text', confidence: 'high' })
    const t = w.find('[data-testid="history-extraction-method"]').text().toLowerCase()
    expect(t).toContain('ocr text')
  })

  it('shows model, confidence and cost as chips (but not raw token counts)', () => {
    const w = extractionItem({
      model: 'claude-opus-4-8',
      confidence: 'high',
      escalated: false,
      input_mode: 'text',
      input_tokens: 5000,
      output_tokens: 200,
      cost_usd: 0.0123,
    })
    const chips = w.findAll('[data-testid="history-extraction-chip"]')
    const chipText = chips.map((c) => c.text()).join(' | ')
    expect(chipText).toContain('claude-opus-4-8')
    expect(chipText.toLowerCase()).toContain('high')
    expect(chipText).toContain('$0.0123')
    // Raw token counts stay in the "Show all" JSON, not the curated chips.
    expect(chipText).not.toContain('5000')
  })

  it('omits the cost chip when no cost was recorded', () => {
    const w = extractionItem({ model: 'claude-opus-4-8', confidence: 'high', input_mode: 'text' })
    const chipText = w
      .findAll('[data-testid="history-extraction-chip"]')
      .map((c) => c.text())
      .join(' | ')
    expect(chipText).not.toContain('$')
  })
})

describe('DocumentHistoryTimeline — skips and failures', () => {
  it('surfaces extraction_skipped as a milestone with a labelled reason', () => {
    const w = mount(DocumentHistoryTimeline, {
      props: { events: [ev('extraction_skipped', '2026-06-10T10:00:10Z', { reason: 'disabled' })] },
    })
    const items = w.findAll('[data-testid="history-item"]')
    expect(items).toHaveLength(1)
    expect(items[0]!.text()).toContain('Extraction skipped')
    expect(items[0]!.text().toLowerCase()).toContain('disabled')
  })

  it('shows spent-of-budget for a budget skip', () => {
    const w = mount(DocumentHistoryTimeline, {
      props: {
        events: [
          ev('extraction_skipped', '2026-06-10T10:00:10Z', {
            reason: 'budget',
            spent_usd: 5,
            budget_usd: 5,
          }),
        ],
      },
    })
    const t = w.find('[data-testid="history-secondary"]').text()
    expect(t.toLowerCase()).toContain('budget')
    expect(t).toContain('$5.00')
  })

  it('shows the detail string for an input/file skip', () => {
    const w = mount(DocumentHistoryTimeline, {
      props: {
        events: [
          ev('extraction_skipped', '2026-06-10T10:00:10Z', {
            reason: 'input_unusable',
            detail: 'no usable OCR text and mime image/heic cannot be sent directly',
          }),
        ],
      },
    })
    expect(w.find('[data-testid="history-secondary"]').text()).toContain('image/heic')
  })

  it('surfaces the error message on extraction_failed', () => {
    const w = mount(DocumentHistoryTimeline, {
      props: {
        events: [
          ev('extraction_failed', '2026-06-10T10:00:10Z', {
            error: 'overloaded_error: server busy',
            prompt_version: 7,
          }),
        ],
      },
    })
    const items = w.findAll('[data-testid="history-item"]')
    expect(items[0]!.text()).toContain('Extraction failed')
    expect(w.find('[data-testid="history-secondary"]').text()).toContain('overloaded_error')
  })

  it('surfaces the error on stage failures (ocr/markdown/embedding)', () => {
    const w = mount(DocumentHistoryTimeline, {
      props: {
        events: [ev('ocr_failed', '2026-06-10T10:00:10Z', { error: 'tesseract exited 1' })],
      },
    })
    expect(w.find('[data-testid="history-secondary"]').text()).toContain('tesseract exited 1')
  })

  it('keeps the email labelling budget event hidden from the curated view', () => {
    const w = mount(DocumentHistoryTimeline, {
      props: {
        events: [
          ev('received', '2026-06-10T10:00:00Z'),
          ev('email_label_completed', '2026-06-10T10:00:02Z', { model: 'claude-haiku-4', cost_usd: 0.0002 }),
        ],
      },
    })
    // Only "received" is a milestone; the billing event stays in "Show all" only.
    expect(w.findAll('[data-testid="history-item"]')).toHaveLength(1)
    expect(w.find('ol').text()).not.toContain('Email label completed')
    expect(w.find('[data-testid="history-raw-list"]').text()).toContain('email_label_completed')
  })

  it('keeps low-signal skips (embedding_skipped) hidden from the curated view', () => {
    const w = mount(DocumentHistoryTimeline, {
      props: {
        events: [
          ev('received', '2026-06-10T10:00:00Z'),
          ev('embedding_skipped', '2026-06-10T10:00:11Z', { reason: 'disabled' }),
        ],
      },
    })
    // Only "received" is a milestone; embedding_skipped stays in "Show all" only.
    expect(w.findAll('[data-testid="history-item"]')).toHaveLength(1)
    expect(w.find('ol').text()).not.toContain('Embedding skipped')
  })
})

describe('DocumentHistoryTimeline — email triage', () => {
  // Shape produced by _selection_event_detail in src/library/email_ingest.py.
  const SELECTION_DETAIL = {
    email_from: 'Alice Accounts <alice@example.com>',
    email_subject: 'Invoice March 2026',
    email_message_id: '<abc123@mail.example.com>',
    email_to: ['john@example.com'],
    items: [
      {
        kind: 'attachment',
        filename: 'invoice.pdf',
        mime: 'application/pdf',
        size: 12345,
        stage: 'classify',
        verdict: 'ingested',
        reason: null,
      },
      {
        kind: 'attachment',
        filename: 'logo.png',
        mime: 'image/png',
        size: 512,
        stage: 'classify',
        verdict: 'filtered',
        reason: 'inline image',
      },
      {
        kind: 'body',
        filename: null,
        mime: 'text/markdown',
        size: 900,
        stage: 'llm_label',
        verdict: 'flagged_ambiguous',
        reason: 'looks like a signature-only body',
      },
    ],
  }

  function triage(detail: Record<string, unknown>) {
    return mount(DocumentHistoryTimeline, {
      props: { events: [ev('email_selection', '2026-06-10T10:00:01Z', detail)] },
    })
  }

  it('renders email_selection as an "Email triage" milestone with one line per item', () => {
    const w = triage(SELECTION_DETAIL)
    const items = w.findAll('[data-testid="history-item"]')
    expect(items).toHaveLength(1)
    expect(items[0]!.text()).toContain('Email triage')

    const lines = w.findAll('[data-testid="history-email-item"]')
    expect(lines).toHaveLength(3)
    // Attachments show their filename and verdict.
    expect(lines[0]!.text()).toContain('invoice.pdf')
    expect(lines[0]!.text()).toContain('Ingested')
    // A reason, when present, is shown alongside the verdict.
    expect(lines[1]!.text()).toContain('logo.png')
    expect(lines[1]!.text()).toContain('Filtered')
    expect(lines[1]!.text()).toContain('inline image')
    // The body item has no filename and falls back to <body>.
    expect(lines[2]!.text()).toContain('<body>')
    expect(lines[2]!.text()).toContain('Flagged ambiguous')
    expect(lines[2]!.text()).toContain('signature-only body')
  })

  it('shows From/Subject provenance as chips', () => {
    const w = triage(SELECTION_DETAIL)
    const chips = w.findAll('[data-testid="history-email-chip"]')
    const chipText = chips.map((c) => c.text()).join(' | ')
    expect(chipText).toContain('alice@example.com')
    expect(chipText).toContain('Invoice March 2026')
  })

  it('renders nothing extra when detail is missing or items is not an array', () => {
    for (const detail of [{}, { items: 'oops' }, { items: 42, email_from: 'x@y.z' }]) {
      const w = triage(detail as Record<string, unknown>)
      // The milestone itself still renders, just without a breakdown.
      expect(w.findAll('[data-testid="history-item"]')).toHaveLength(1)
      expect(w.find('[data-testid="history-item"]').text()).toContain('Email triage')
      expect(w.findAll('[data-testid="history-email-item"]')).toHaveLength(0)
      expect(w.findAll('[data-testid="history-email-chip"]')).toHaveLength(0)
    }
  })

  it('skips malformed entries inside an otherwise valid items array', () => {
    const w = triage({
      email_from: 'alice@example.com',
      items: [null, 'junk', { filename: 'ok.pdf', verdict: 'ingested' }],
    })
    const lines = w.findAll('[data-testid="history-email-item"]')
    expect(lines).toHaveLength(1)
    expect(lines[0]!.text()).toContain('ok.pdf')
  })
})
