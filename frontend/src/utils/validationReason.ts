/**
 * Turn a validation finding into plain language for humans.
 *
 * The backend `message` on each finding is terse and storage-flavoured
 * ("document_date is in the future"). The UI wants a short, friendly *title*
 * for scanning (dashboard rows, panel headings) plus the fuller message as
 * detail. This is the single source of that wording, shared by the detail
 * page's "Why this needs review" panel (W4), the dashboard row reasons (W5),
 * and the review queue (W6).
 */
import type { ValidationFindingSummary } from '@/api/documents'

export interface ReviewReason {
  rule: string
  field: string | null
  /** Short, human title for scanning, e.g. "Unlikely date". */
  title: string
  /**
   * Friendly name of the metadata attribute the finding is about, e.g.
   * "Document date" for the storage field `document_date`. `null` for
   * document-level findings (`field: null`, e.g. low OCR quality) that don't
   * point at one attribute. Lets the "Why this needs review" panel say
   * explicitly *what* to check, not just why.
   */
  fieldLabel: string | null
  /** The fuller explanation (the backend message), e.g. why it fired. */
  detail: string
}

/**
 * Storage field name → friendly attribute label. Findings carry the raw column
 * name (`document_date`, `amount_total`); the panel shows the label so a reader
 * knows which field to look at. Mirrors the labels in DocumentMetadataEditor's
 * rowConfigs. Unknown/unmapped fields fall through to `null` (no attribute
 * chip) rather than leaking a storage name.
 */
const FIELD_LABELS: Record<string, string> = {
  title: 'Title',
  // Findings target either the FK id (`sender_id`) or the resolved name field
  // (`sender`), depending on the rule — see extraction/validation.py.
  sender: 'Sender',
  sender_id: 'Sender',
  recipient: 'Recipient',
  recipient_id: 'Recipient',
  document_date: 'Date on document',
  due_date: 'Due date',
  expiry_date: 'Expiry date',
  amount_total: 'Amount',
  currency: 'Currency',
}

/** Short title per validation rule code. Unknown rules get a safe generic. */
const RULE_TITLES: Record<string, string> = {
  date_plausibility: 'Unlikely date',
  amount_grounding: 'Amount not found in the text',
  amount_currency_coupling: 'Amount and currency mismatch',
  ocr_confidence_gate: 'Hard-to-read text (low OCR quality)',
  empty_extraction: 'Little information found',
  self_reported_low: 'Extraction was unsure',
  missing_sender: 'Sender not identified',
  email_attachments_dropped: 'Some email attachments could not be added',
  email_item_ambiguous: 'Might not be a real document',
}

const GENERIC_TITLE = 'Needs a quick check'

/** One finding → its human-facing title + detail. */
export function resolveReviewReason(finding: ValidationFindingSummary): ReviewReason {
  return {
    rule: finding.rule,
    field: finding.field,
    title: RULE_TITLES[finding.rule] ?? GENERIC_TITLE,
    fieldLabel: finding.field ? (FIELD_LABELS[finding.field] ?? null) : null,
    detail: finding.message,
  }
}

/** Map a list of findings to reasons, preserving order. */
export function resolveReviewReasons(findings: ValidationFindingSummary[]): ReviewReason[] {
  return findings.map(resolveReviewReason)
}

/**
 * A compact one-line summary of why a document needs review, for tight spaces
 * like dashboard rows: the first `max` titles, with "+N more" when truncated.
 * Empty string when there are no findings.
 */
export function summarizeReviewReasons(
  findings: ValidationFindingSummary[],
  max = 2,
): string {
  if (!findings.length) return ''
  const titles = resolveReviewReasons(findings).map((reason) => reason.title)
  const shown = titles.slice(0, max)
  const extra = titles.length - shown.length
  return extra > 0 ? `${shown.join(', ')} +${extra} more` : shown.join(', ')
}
