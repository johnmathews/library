import { describe, expect, it } from 'vitest'
import type { ValidationFindingSummary } from '@/api/documents'
import {
  resolveReviewReason,
  resolveReviewReasons,
  summarizeReviewReasons,
} from '@/utils/validationReason'

function finding(rule: string, message = 'msg', field: string | null = null): ValidationFindingSummary {
  return { rule, field, message }
}

describe('resolveReviewReason', () => {
  it('gives a known rule a friendly title and keeps the message as detail', () => {
    const reason = resolveReviewReason(finding('date_plausibility', 'document_date is in the future', 'document_date'))
    expect(reason.title).toBe('Unlikely date')
    expect(reason.detail).toBe('document_date is in the future')
    expect(reason.field).toBe('document_date')
  })

  it('falls back to a generic title for an unknown rule', () => {
    expect(resolveReviewReason(finding('brand_new_rule')).title).toBe('Needs a quick check')
  })

  it('titles the new rules and keeps their specific message as detail', () => {
    const dropped = resolveReviewReason(
      finding('email_attachments_dropped', 'the email included 3 other attachments that could not be added: a.pdf, b.pdf, c.pdf'),
    )
    expect(dropped.title).toBe('Some email attachments could not be added')
    expect(dropped.detail).toContain('a.pdf')

    const sender = resolveReviewReason(finding('missing_sender', 'sender could not be identified', 'sender_id'))
    expect(sender.title).toBe('Sender not identified')

    // self_reported_low now carries the model's own note as the detail line.
    const unsure = resolveReviewReason(finding('self_reported_low', 'the extractor was unsure: two candidate totals'))
    expect(unsure.title).toBe('Extraction was unsure')
    expect(unsure.detail).toBe('the extractor was unsure: two candidate totals')
  })
})

describe('resolveReviewReasons', () => {
  it('maps in order', () => {
    const reasons = resolveReviewReasons([finding('date_plausibility'), finding('ocr_confidence_gate')])
    expect(reasons.map((r) => r.title)).toEqual([
      'Unlikely date',
      'Hard-to-read text (low OCR quality)',
    ])
  })
})

describe('summarizeReviewReasons', () => {
  it('is empty with no findings', () => {
    expect(summarizeReviewReasons([])).toBe('')
  })

  it('joins titles up to the limit', () => {
    expect(summarizeReviewReasons([finding('date_plausibility'), finding('empty_extraction')])).toBe(
      'Unlikely date, Little information found',
    )
  })

  it('adds "+N more" past the limit', () => {
    const many = [
      finding('date_plausibility'),
      finding('empty_extraction'),
      finding('ocr_confidence_gate'),
    ]
    expect(summarizeReviewReasons(many, 2)).toBe('Unlikely date, Little information found +1 more')
  })
})
