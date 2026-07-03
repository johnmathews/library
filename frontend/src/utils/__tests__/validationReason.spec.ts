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
