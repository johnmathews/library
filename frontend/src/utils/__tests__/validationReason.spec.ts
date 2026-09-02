import { describe, expect, it } from 'vitest'
import type { ValidationFindingSummary } from '@/api/documents'
import {
  fieldLabel,
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

  it('maps the storage field to a friendly attribute label', () => {
    expect(resolveReviewReason(finding('date_plausibility', 'x', 'document_date')).fieldLabel).toBe(
      'Date on document',
    )
    // FK-id and resolved-name variants both label as the human attribute.
    expect(resolveReviewReason(finding('missing_sender', 'x', 'sender_id')).fieldLabel).toBe('Sender')
    expect(resolveReviewReason(finding('amount_currency_coupling', 'x', 'currency')).fieldLabel).toBe(
      'Currency',
    )
  })

  it('has no attribute label for document-level or unmapped findings', () => {
    expect(resolveReviewReason(finding('empty_extraction', 'x', null)).fieldLabel).toBeNull()
    expect(resolveReviewReason(finding('date_plausibility', 'x', 'mystery_field')).fieldLabel).toBeNull()
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

  it('titles the textless and decoration rules rather than falling back', () => {
    // A textless document still reaches `indexed`, so the review queue is the
    // only place it surfaces — a generic "Needs a quick check" would tell the
    // user nothing about why it cannot be found by search.
    const textless = resolveReviewReason(
      finding('no_text_extracted', 'no text could be extracted from this document, so it cannot be found by search'),
    )
    expect(textless.title).toBe('No text could be read')
    expect(textless.detail).toContain('cannot be found by search')

    // decoration_image has fired in validation.py since the thin-OCR work but
    // had no title here, so it rendered as the generic fallback.
    const decoration = resolveReviewReason(
      finding('decoration_image', 'this image produced almost no text and is likely a logo'),
    )
    expect(decoration.title).toBe('Looks like a logo, not a document')
    expect(decoration.title).not.toBe('Needs a quick check')
  })

  it('titles the email-labeller ambiguity flag', () => {
    const ambiguous = resolveReviewReason(
      finding('email_item_ambiguous', 'the email labeller flagged this item as possible noise'),
    )
    expect(ambiguous.title).toBe('Might not be a real document')
    expect(ambiguous.detail).toBe('the email labeller flagged this item as possible noise')
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

  it('summarizes the email ambiguity flag with its friendly title', () => {
    expect(summarizeReviewReasons([finding('email_item_ambiguous')])).toBe(
      'Might not be a real document',
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

describe('fieldLabel', () => {
  it('maps a known storage column to its friendly label', () => {
    expect(fieldLabel('amount_total')).toBe('Amount')
    expect(fieldLabel('amount_kind')).toBe('Amount kind')
  })

  it('falls back to the raw name for an unmapped field', () => {
    // Deliberately NOT null, unlike `resolveReviewReason`'s `fieldLabel`: a
    // finding can omit an attribute chip, but a list of withheld fields cannot
    // omit its members. Silence about a write that did not happen is the whole
    // defect this helper was exported to fix.
    expect(fieldLabel('some_future_column')).toBe('some_future_column')
  })

  it('is not fooled by inherited Object keys', () => {
    // The reason the map is a `Map` and not an object literal. On a literal,
    // `LABELS['constructor']` is a FUNCTION and `LABELS['__proto__']` is the
    // prototype — neither nullish, so a `?? field` fallback never fires and the
    // timeline renders `function Object() { [native code] }`. These names reach
    // the helper from API JSON, and `Record<string, string>` hides it from
    // vue-tsc, so only a test can hold this.
    for (const key of ['constructor', '__proto__', 'toString', 'valueOf', 'hasOwnProperty']) {
      expect(fieldLabel(key)).toBe(key)
    }
  })
})
