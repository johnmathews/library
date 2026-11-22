/**
 * Formatting for a document's dashboard fields (the ones the Fields menu
 * chooses), shared by every dashboard layout: tiles, rows, table and list.
 *
 * One copy on purpose: each layout arranges the same fields differently, and a
 * field formatted in one place cannot drift between layouts.
 */
import {
  DOCUMENT_LANGUAGES,
  type DocumentLanguage,
  type DocumentListItem,
} from '@/api/documents'
import type { DashboardField } from '@/api/settings'

const dateFormatLong = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})

// en-GB abbreviates September as "Sept", so each month token is clamped to 3
// letters for a uniform short form (Jan…Dec).
const dateFormatShort = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

/**
 * Format an ISO date (`YYYY-MM-DD`) as "17 September 2019", or "17 Sep 2019"
 * when `compact`. Empty for null; an unparseable value comes back as given.
 */
export function formatListDate(iso: string | null, compact: boolean): string {
  if (!iso) return ''
  const parsed = new Date(`${iso}T00:00:00Z`)
  if (Number.isNaN(parsed.getTime())) return iso
  if (!compact) return dateFormatLong.format(parsed)
  return dateFormatShort
    .formatToParts(parsed)
    .map((part) => (part.type === 'month' ? part.value.slice(0, 3) : part.value))
    .join('')
}

export function languageName(language: DocumentLanguage): string {
  return DOCUMENT_LANGUAGES.find((item) => item.value === language)?.text ?? language
}

export function fileTypeLabel(item: DocumentListItem): string {
  if (item.mime_type === 'application/pdf') return 'PDF'
  if (item.mime_type.startsWith('image/')) return 'Image'
  if (item.mime_type.startsWith('text/')) return 'Text'
  return 'File'
}

/** The amount in the document's currency ("€ 12.50"), or null when there is none. */
export function formatAmount(item: DocumentListItem): string | null {
  if (item.amount_total === null) return null
  if (item.currency) {
    try {
      return new Intl.NumberFormat('en-GB', {
        style: 'currency',
        currency: item.currency,
        // amount_total is a decimal STRING (backend preserves precision).
        // Number() is safe for the bill/invoice magnitudes this app stores;
        // it would lose precision only beyond ~15 significant digits.
      }).format(Number(item.amount_total))
    } catch {
      return `${item.currency} ${item.amount_total}`
    }
  }
  return item.amount_total
}

/**
 * The configurable dates beyond the document's own date. Each carries a short
 * prefix so several dates side by side stay unambiguous; `created_at` and
 * `updated_at` are datetimes, sliced to their date before formatting.
 */
export const EXTRA_DATE_FIELDS: Partial<
  Record<DashboardField, { label: string; value: (item: DocumentListItem) => string | null }>
> = {
  due_date: { label: 'Due', value: (i) => i.due_date },
  expiry_date: { label: 'Expires', value: (i) => i.expiry_date },
  added_date: { label: 'Added', value: (i) => i.created_at },
  last_edited: { label: 'Edited', value: (i) => i.updated_at },
}

export function extraDate(
  field: DashboardField,
  item: DocumentListItem,
  compact: boolean,
): string | null {
  const spec = EXTRA_DATE_FIELDS[field]
  const iso = spec?.value(item) ?? null
  return iso ? formatListDate(iso.slice(0, 10), compact) : null
}

/** Column headings for the table layout: short, sentence case. */
export const FIELD_COLUMN_LABELS: Record<DashboardField, string> = {
  kind: 'Type',
  sender: 'From',
  tags: 'Tags',
  date: 'Date',
  due_date: 'Due',
  expiry_date: 'Expires',
  added_date: 'Added',
  last_edited: 'Edited',
  language: 'Language',
  status: 'Status',
  amount: 'Amount',
  file_type: 'File type',
}

/** How many tags a document shows before "+N". */
export const MAX_TAGS = 4
