<script setup lang="ts">
/**
 * Document detail page (route `/documents/:id`).
 *
 * Two-column on desktop (metadata left, preview right), stacked on
 * mobile/iPad-portrait. Metadata follows the GOV.UK
 * summary-list "Change" pattern with an inline reveal per row (a full
 * one-thing-per-page flow would be heavy for single-field edits): the
 * row's Change button swaps the value cell for the right input with
 * Save/Cancel. Each save PATCHes only that row's field(s) and replaces
 * local state with the server response — no optimistic updates.
 *
 * PDF preview uses the browser's native viewer in an <iframe> (the
 * searchable PDF when present, the original otherwise) — every modern
 * browser ships one, and pdf.js would add a heavyweight dependency for
 * no gain at family scale. An "open in new tab" link covers browsers
 * with the inline viewer disabled.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  AppBackLink,
  AppBadge,
  AppBanner,
  AppButton,
  AppDateInput,
  AppDetails,
  AppErrorSummary,
  AppInput,
  AppSelect,
  AppTextarea,
} from '@/components/app'
import type { ErrorSummaryItem, SelectItem } from '@/components/app'
import {
  DOCUMENT_LANGUAGES,
  getDocument,
  originalUrl,
  requestExtraction,
  searchablePdfUrl,
  thumbnailUrl,
  updateDocument,
  verifyDocument,
  type DocumentDetail,
  type DocumentLanguage,
  type DocumentUpdate,
  type ValidationFinding,
} from '@/api/documents'
import { listKinds, listSenders, type KindOption, type SenderOption } from '@/api/taxonomy'
import { ApiError } from '@/api/client'
import { renderHighlighted } from '@/utils/snippet'

const props = withDefaults(
  defineProps<{
    /** Delay between re-extraction status polls; tests pass 0. */
    pollIntervalMs?: number
    /** Stop polling for re-extraction results after this long. */
    extractTimeoutMs?: number
  }>(),
  { pollIntervalMs: 2000, extractTimeoutMs: 60_000 },
)

const route = useRoute()

// --- Document loading --------------------------------------------------------

const doc = ref<DocumentDetail | null>(null)
const notFound = ref(false)
const loadError = ref(false)

let unmounted = false
onBeforeUnmount(() => {
  unmounted = true
})

// --- Taxonomy options (kind select, sender autocomplete) ----------------------

const kinds = ref<KindOption[]>([])
const senders = ref<SenderOption[]>([])

onMounted(async () => {
  // Best-effort: without options the kind select still offers the current
  // value and "Not set", and the sender input works without suggestions.
  const [kindResult, senderResult] = await Promise.allSettled([listKinds(), listSenders()])
  if (kindResult.status === 'fulfilled') kinds.value = kindResult.value
  if (senderResult.status === 'fulfilled') senders.value = senderResult.value
})

const kindItems = computed<SelectItem[]>(() => {
  const items: SelectItem[] = [
    { value: '', text: 'Not set' },
    ...kinds.value.map((kind) => ({ value: kind.slug, text: kind.name })),
  ]
  const current = doc.value?.kind
  if (current && !kinds.value.some((kind) => kind.slug === current.slug)) {
    items.push({ value: current.slug, text: current.name })
  }
  return items
})

const languageItems: SelectItem[] = DOCUMENT_LANGUAGES.map((language) => ({
  value: language.value,
  text: language.text,
}))

// --- Summary rows -------------------------------------------------------------

type EditableField =
  | 'title'
  | 'kind'
  | 'sender'
  | 'document_date'
  | 'language'
  | 'tags'
  | 'amount'
  | 'due_date'
  | 'expiry_date'
  | 'summary'

interface RowConfig {
  field: EditableField
  label: string
  display: (d: DocumentDetail) => string | null
}

const EMPTY = '—'

const rowConfigs: RowConfig[] = [
  { field: 'title', label: 'Title', display: (d) => d.title },
  { field: 'kind', label: 'Kind', display: (d) => d.kind?.name ?? null },
  { field: 'sender', label: 'Sender', display: (d) => d.sender?.name ?? null },
  { field: 'document_date', label: 'Document date', display: (d) => formatDate(d.document_date) },
  { field: 'language', label: 'Language', display: (d) => languageName(d.language) },
  {
    field: 'tags',
    label: 'Tags',
    display: (d) => (d.tags.length ? d.tags.map((tag) => tag.name).join(', ') : null),
  },
  {
    field: 'amount',
    label: 'Amount',
    display: (d) =>
      d.amount_total === null ? null : [d.amount_total, d.currency].filter(Boolean).join(' '),
  },
  { field: 'due_date', label: 'Due date', display: (d) => formatDate(d.due_date) },
  { field: 'expiry_date', label: 'Expiry date', display: (d) => formatDate(d.expiry_date) },
  { field: 'summary', label: 'Summary', display: (d) => d.summary },
]

/** Editable rows keyed by field, so the grouped layout can look each up. */
const rowByField = Object.fromEntries(rowConfigs.map((row) => [row.field, row])) as Record<
  EditableField,
  RowConfig
>

/**
 * Metadata is split into themed groups so different kinds of metadata read as
 * visually distinct rather than one undifferentiated list. Each group carries an
 * accent colour (used on its heading, left rail and a faint tint) and lays its
 * fields out in a two-column grid to use the width and cut the vertical sprawl.
 */
type Accent = 'violet' | 'sky' | 'green' | 'yellow' | 'gray'

interface FieldGroup {
  key: string
  label: string
  accent: Accent
  fields: EditableField[]
}

const fieldGroups: FieldGroup[] = [
  { key: 'content', label: 'Content', accent: 'violet', fields: ['title', 'summary', 'tags'] },
  { key: 'classification', label: 'Classification', accent: 'yellow', fields: ['kind', 'language'] },
  {
    key: 'parties',
    label: 'Sender & dates',
    accent: 'sky',
    fields: ['sender', 'document_date', 'due_date', 'expiry_date'],
  },
  { key: 'financial', label: 'Financial', accent: 'green', fields: ['amount'] },
]

/** Fields that read better spanning the full width of the two-column grid. */
const WIDE_FIELDS = new Set<EditableField>(['title', 'summary', 'tags', 'amount'])

/** Static Tailwind class strings per accent (kept literal so the build's content
 * scan keeps them). `border` is left-only so it never fights a shorthand. */
const ACCENT: Record<Accent, { bar: string; text: string; border: string; bg: string }> = {
  violet: {
    bar: 'bg-violet-400 dark:bg-violet-500',
    text: 'text-violet-700 dark:text-violet-300',
    border: 'border-l-violet-400 dark:border-l-violet-500',
    bg: 'bg-violet-50/50 dark:bg-violet-500/[0.06]',
  },
  sky: {
    bar: 'bg-sky-400 dark:bg-sky-500',
    text: 'text-sky-700 dark:text-sky-300',
    border: 'border-l-sky-400 dark:border-l-sky-500',
    bg: 'bg-sky-50/50 dark:bg-sky-500/[0.06]',
  },
  green: {
    bar: 'bg-green-500 dark:bg-green-500',
    text: 'text-green-700 dark:text-green-300',
    border: 'border-l-green-500',
    bg: 'bg-green-50/50 dark:bg-green-500/[0.06]',
  },
  yellow: {
    bar: 'bg-yellow-400 dark:bg-yellow-500',
    text: 'text-yellow-700 dark:text-yellow-400',
    border: 'border-l-yellow-400 dark:border-l-yellow-500',
    bg: 'bg-yellow-50/60 dark:bg-yellow-500/[0.06]',
  },
  gray: {
    bar: 'bg-gray-300 dark:bg-gray-600',
    text: 'text-gray-500 dark:text-gray-400',
    border: 'border-l-gray-300 dark:border-l-gray-600',
    bg: 'bg-gray-50/70 dark:bg-gray-900/20',
  },
}

/** Status pill colour: green when fully processed, red on failure, neutral
 * otherwise. */
function statusAccent(status: string): string {
  if (status === 'indexed') return 'bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-300'
  if (status === 'failed') return 'bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-300'
  return 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300'
}

const dateFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})

function formatDate(iso: string | null): string | null {
  if (!iso) return null
  const parsed = new Date(`${iso}T00:00:00Z`)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormat.format(parsed)
}

function formatDateTime(iso: string): string {
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return new Intl.DateTimeFormat('en-GB', { dateStyle: 'long', timeStyle: 'short' }).format(parsed)
}

function languageName(language: DocumentLanguage): string {
  return DOCUMENT_LANGUAGES.find((item) => item.value === language)?.text ?? language
}

function sourceLabel(source: string): string {
  return source.charAt(0).toUpperCase() + source.slice(1)
}

// --- Hero header (title + key stats + tags) -----------------------------------

/** AppBadge colours that read as visually distinct in the Mosaic palette.
 * A tag's colour is derived from its name so it stays stable across renders
 * and pages without storing a colour on the tag itself. */
const TAG_COLOURS = ['purple', 'blue', 'green', 'yellow', 'red', 'turquoise', 'pink'] as const

function tagColour(name: string): (typeof TAG_COLOURS)[number] {
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  return TAG_COLOURS[hash % TAG_COLOURS.length]!
}

/** The at-a-glance facts shown as a labelled stat row in the hero header.
 * These mirror (read-only) the most important editable rows below; empty
 * values render as the em-dash so the row layout stays stable. */
const heroStats = computed<{ label: string; value: string }[]>(() => {
  const d = doc.value
  if (!d) return []
  const amount = d.amount_total === null ? null : [d.amount_total, d.currency].filter(Boolean).join(' ')
  return [
    { label: 'Kind', value: d.kind?.name ?? EMPTY },
    { label: 'Sender', value: d.sender?.name ?? EMPTY },
    { label: 'Document date', value: formatDate(d.document_date) ?? EMPTY },
    { label: 'Amount', value: amount ?? EMPTY },
  ]
})

// --- Inline editing -----------------------------------------------------------

const editing = ref<EditableField | null>(null)
const editText = ref('')
const editCurrency = ref('')
const editDate = ref<string | null>(null)
const editSelect = ref('')
const editError = ref<string | null>(null)
const saving = ref(false)

/** The success / progress notification shown at the top of the page. */
const notice = ref<{ variant?: 'success'; text: string } | null>(null)
/** Failure of a page-level action (re-extraction request). */
const actionError = ref<string | null>(null)

const errorItems = computed<ErrorSummaryItem[]>(() => {
  const items: ErrorSummaryItem[] = []
  if (editError.value && editing.value) {
    items.push({ text: editError.value, href: `#${editorInputId(editing.value)}` })
  }
  if (actionError.value) items.push({ text: actionError.value })
  return items
})

function editorInputId(field: EditableField): string {
  if (field === 'amount') return 'edit-amount'
  const id = `edit-${field.replaceAll('_', '-')}`
  // AppDateInput puts the id on the container; its first field is -day.
  return field.endsWith('date') ? `${id}-day` : id
}

function startEdit(field: EditableField): void {
  const d = doc.value
  if (!d) return
  editing.value = field
  editError.value = null
  switch (field) {
    case 'title':
      editText.value = d.title ?? ''
      break
    case 'summary':
      editText.value = d.summary ?? ''
      break
    case 'sender':
      editText.value = d.sender?.name ?? ''
      break
    case 'tags':
      editText.value = d.tags.map((tag) => tag.slug).join(', ')
      break
    case 'kind':
      editSelect.value = d.kind?.slug ?? ''
      break
    case 'language':
      editSelect.value = d.language
      break
    case 'amount':
      editText.value = d.amount_total ?? ''
      editCurrency.value = d.currency ?? ''
      break
    case 'document_date':
      editDate.value = d.document_date
      break
    case 'due_date':
      editDate.value = d.due_date
      break
    case 'expiry_date':
      editDate.value = d.expiry_date
      break
  }
}

function cancelEdit(): void {
  editing.value = null
  editError.value = null
  saving.value = false
}

/** The PATCH body for the open editor — exactly that row's field(s). */
function buildPatch(field: EditableField): DocumentUpdate | null {
  switch (field) {
    case 'title':
      return { title: editText.value.trim() || null }
    case 'summary':
      return { summary: editText.value.trim() || null }
    case 'sender':
      return { sender: editText.value.trim() || null }
    case 'kind':
      return { kind_slug: editSelect.value || null }
    case 'language':
      return { language: editSelect.value as DocumentLanguage }
    case 'tags':
      return {
        tags: editText.value
          .split(',')
          .map((tag) => tag.trim())
          .filter(Boolean),
      }
    case 'document_date':
      return { document_date: editDate.value }
    case 'due_date':
      return { due_date: editDate.value }
    case 'expiry_date':
      return { expiry_date: editDate.value }
    case 'amount': {
      const amount = editText.value.trim().replace(',', '.')
      const currency = editCurrency.value.trim()
      if (!amount) return { amount_total: null, currency: null }
      if (!/^\d+(\.\d+)?$/.test(amount)) {
        editError.value = 'Enter the amount as a number, like 123.45'
        return null
      }
      if (currency && !/^[A-Za-z]{3}$/.test(currency)) {
        editError.value = 'Enter a 3-letter currency code, like EUR'
        return null
      }
      return { amount_total: amount, currency: currency || null }
    }
  }
}

async function save(row: RowConfig): Promise<void> {
  if (!doc.value || saving.value) return
  editError.value = null
  const patch = buildPatch(row.field)
  if (!patch) return
  saving.value = true
  try {
    doc.value = await updateDocument(doc.value.id, patch)
    notice.value = { variant: 'success', text: `${row.label} updated.` }
    cancelEdit()
  } catch (error: unknown) {
    editError.value =
      error instanceof ApiError && error.status !== 0
        ? error.detail
        : 'Could not save the change — check your connection and try again'
  } finally {
    saving.value = false
  }
}

// --- Re-extraction ------------------------------------------------------------

const extracting = ref(false)

/**
 * Change marker for "extraction ran again": the provenance block plus the
 * number of extraction audit events (a skipped/failed run leaves the
 * provenance untouched but still appends an event).
 */
function extractionFingerprint(d: DocumentDetail): string {
  const eventCount = d.events.filter((event) => event.event.startsWith('extraction')).length
  return `${JSON.stringify(d.extraction)}|${eventCount}`
}

async function rerunExtraction(): Promise<void> {
  if (!doc.value || extracting.value) return
  const id = doc.value.id
  const before = extractionFingerprint(doc.value)
  actionError.value = null
  try {
    await requestExtraction(id)
  } catch {
    actionError.value = 'Could not queue the extraction — try again later'
    return
  }
  notice.value = { text: 'Extraction queued — this page will refresh when it finishes.' }
  extracting.value = true
  const deadline = Date.now() + props.extractTimeoutMs
  while (!unmounted && Date.now() <= deadline) {
    await sleep(props.pollIntervalMs)
    if (unmounted) return
    try {
      const fresh = await getDocument(id)
      if (extractionFingerprint(fresh) !== before) {
        doc.value = fresh
        notice.value = { variant: 'success', text: 'Extraction finished — metadata refreshed.' }
        break
      }
    } catch {
      // transient poll error: keep trying until the deadline
    }
  }
  extracting.value = false
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// --- Validation findings ------------------------------------------------------

/**
 * Map from storage field name to UI field name so findings can be shown beside
 * the right row. The backend uses `kind_id` and `sender_id`; the UI groups them
 * as `kind` and `sender`.
 */
const STORAGE_TO_UI_FIELD: Record<string, string> = {
  amount_total: 'amount',
  currency: 'amount',
  document_date: 'document_date',
  due_date: 'due_date',
  expiry_date: 'expiry_date',
  title: 'title',
  summary: 'summary',
  kind_id: 'kind',
  sender_id: 'sender',
}

/** Findings indexed by UI field name. */
const findingsByField = computed<Record<string, ValidationFinding[]>>(() => {
  const findings = doc.value?.validation?.findings
  if (!findings?.length) return {}
  const result: Record<string, ValidationFinding[]> = {}
  for (const finding of findings) {
    const uiField = STORAGE_TO_UI_FIELD[finding.field] ?? finding.field
    ;(result[uiField] ??= []).push(finding)
  }
  return result
})

// --- Mark verified ------------------------------------------------------------

const verifying = ref(false)

async function markVerified(): Promise<void> {
  if (!doc.value || verifying.value) return
  verifying.value = true
  actionError.value = null
  try {
    doc.value = await verifyDocument(doc.value.id)
    notice.value = { variant: 'success', text: 'Document marked as verified.' }
  } catch (error: unknown) {
    actionError.value =
      error instanceof ApiError && error.status !== 0
        ? error.detail
        : 'Could not mark verified — check your connection and try again'
  } finally {
    verifying.value = false
  }
}

// --- Preview and OCR text -----------------------------------------------------

const preview = computed<'image' | 'pdf' | 'none'>(() => {
  if (!doc.value) return 'none'
  if (doc.value.mime_type.startsWith('image/')) return 'image'
  if (doc.value.mime_type === 'application/pdf' || doc.value.has_searchable_pdf) return 'pdf'
  return 'none'
})

/** Preview the searchable PDF when the pipeline produced one (it has a
 * text layer, so in-viewer text selection/search works), else the original.
 * Inline disposition: the attachment default would blank the iframe and
 * trigger a download instead of rendering. */
const pdfPreviewUrl = computed(() =>
  doc.value
    ? doc.value.has_searchable_pdf
      ? searchablePdfUrl(doc.value.id, { inline: true })
      : originalUrl(doc.value.id, { inline: true })
    : '',
)

/** Same PDF, but with native-viewer open parameters. `toolbar=0&navpanes=0`
 * hides the viewer chrome (Chrome/Edge honour it; some Firefox builds ignore
 * it — best effort, the page provides its own Open/Download buttons), and
 * `view=FitH` fits the page to the iframe width so a portrait page is not
 * clipped on a narrow (mobile) viewport. The plain `pdfPreviewUrl` (no
 * fragment) is kept for the open-in-new-tab button. */
const pdfPreviewIframeUrl = computed(() =>
  pdfPreviewUrl.value ? `${pdfPreviewUrl.value}#toolbar=0&navpanes=0&view=FitH` : '',
)

/** Firefox's built-in viewer ignores `#toolbar=0` and shows its own toolbar.
 * There's no URL fragment that hides it, so on Firefox we nudge the iframe up
 * behind an overflow-hidden wrapper to clip the toolbar off the top edge.
 * Chrome/Edge honour the fragment and need no clip (clipping there would just
 * eat document content). */
const hidePdfToolbar = computed(
  () => typeof navigator !== 'undefined' && /firefox/i.test(navigator.userAgent),
)

/** Where the preview header's "Open" button points (open the inline preview in
 * a new tab): the PDF for PDFs, the original image for images. */
const previewOpenUrl = computed(() => {
  if (!doc.value) return ''
  if (preview.value === 'pdf') return pdfPreviewUrl.value
  if (preview.value === 'image') return originalUrl(doc.value.id, { inline: true })
  return ''
})

/** Where the preview header's "Download" button points (attachment download):
 * the searchable PDF when present, otherwise the original file. */
const previewDownloadUrl = computed(() => {
  if (!doc.value) return ''
  return doc.value.has_searchable_pdf ? searchablePdfUrl(doc.value.id) : originalUrl(doc.value.id)
})

const highlight = computed(() => {
  const value = route.query.highlight
  return typeof value === 'string' ? value : ''
})

const latestExtractionEvent = computed(() => {
  if (!doc.value) return null
  return (
    doc.value.events.filter((event) => event.event.startsWith('extraction')).at(-1) ?? null
  )
})

// --- Load on navigation (registered last: the handler runs immediately and
// --- touches the edit/notice state declared above) ----------------------------

watch(
  () => route.params.id,
  async (id) => {
    if (route.name !== 'document-detail') return
    doc.value = null
    notFound.value = false
    loadError.value = false
    cancelEdit()
    notice.value = null
    actionError.value = null
    const numericId = Number(id)
    if (!Number.isInteger(numericId) || numericId < 1) {
      notFound.value = true
      return
    }
    try {
      doc.value = await getDocument(numericId)
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 404) notFound.value = true
      else loadError.value = true
    }
  },
  { immediate: true },
)
</script>

<template>
  <AppBackLink to="/" text="Back to documents" class="mb-4" />

  <template v-if="doc">
    <AppBanner v-if="notice" :variant="notice.variant" data-testid="detail-banner" class="mb-6">
      {{ notice.text }}
    </AppBanner>
    <AppErrorSummary v-if="errorItems.length" :errors="errorItems" data-testid="error-summary" />

    <div
      id="document-hero"
      class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5 sm:p-6 mb-6"
    >
      <h1
        id="document-title"
        class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold break-words app-detail-title"
      >
        {{ doc.title ?? 'Untitled document' }}
      </h1>
      <dl
        class="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3"
        data-testid="hero-stats"
      >
        <div v-for="stat in heroStats" :key="stat.label">
          <dt class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
            {{ stat.label }}
          </dt>
          <dd class="mt-0.5 text-sm font-medium text-gray-800 dark:text-gray-100 break-words">
            {{ stat.value }}
          </dd>
        </div>
      </dl>
      <div
        v-if="doc.tags.length"
        class="mt-5 flex flex-wrap gap-2"
        data-testid="hero-tags"
      >
        <AppBadge v-for="tag in doc.tags" :key="tag.slug" :colour="tagColour(tag.name)">
          {{ tag.name }}
        </AppBadge>
      </div>
    </div>

    <div id="document-detail-grid" class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Preview: right column on desktop (lg:order-2), first on mobile.
           min-w-0 lets this grid column shrink below its content's intrinsic
           width so long tokens wrap instead of widening the page (iOS zoom). -->
      <div id="document-preview-column" class="min-w-0 space-y-4 lg:order-2">
        <div
          id="document-preview-card"
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 overflow-hidden"
        >
          <!-- Preview header: keeps the document window itself clean (the native
               PDF toolbar is hidden) while giving an unambiguous way to open the
               file full-size or download it. -->
          <div
            v-if="preview !== 'none'"
            class="flex items-center justify-between gap-3 border-b border-gray-200 dark:border-gray-700/60 px-4 py-2.5"
          >
            <span class="text-sm font-medium text-gray-500 dark:text-gray-400">Document</span>
            <div class="flex items-center gap-2">
              <a
                :href="previewOpenUrl"
                target="_blank"
                rel="noopener"
                class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300 gap-1.5"
                data-testid="preview-open"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke-width="1.5"
                  stroke="currentColor"
                  class="w-4 h-4"
                  aria-hidden="true"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"
                  />
                </svg>
                Open
              </a>
              <a
                :href="previewDownloadUrl"
                class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300 gap-1.5"
                data-testid="preview-download"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke-width="1.5"
                  stroke="currentColor"
                  class="w-4 h-4"
                  aria-hidden="true"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"
                  />
                </svg>
                Download
              </a>
            </div>
          </div>
          <!-- Inline disposition: Firefox refuses to render <img> responses
               served as attachment, and other browsers would download them. -->
          <img
            v-if="preview === 'image'"
            class="w-full object-contain bg-gray-100 dark:bg-gray-900/40"
            :src="originalUrl(doc.id, { inline: true })"
            :alt="`Preview of ${doc.title ?? 'this document'}`"
            data-testid="preview-image"
          />
          <template v-else-if="preview === 'pdf'">
            <!-- On small screens the browser-native PDF viewer renders wider
                 than the viewport and ignores #view=FitH (notably iOS Safari),
                 so show the fit-width first-page thumbnail there instead. The
                 native <iframe> stays on lg+ (scroll/zoom/text-selection). -->
            <a
              v-if="doc.has_thumbnail"
              :href="pdfPreviewUrl"
              target="_blank"
              rel="noopener"
              class="block lg:hidden"
              data-testid="preview-pdf-image-link"
            >
              <img
                class="w-full object-contain bg-gray-100 dark:bg-gray-900/40"
                :src="thumbnailUrl(doc.id)"
                :alt="`First page of ${doc.title ?? 'this document'} — tap to open the PDF`"
                data-testid="preview-pdf-image"
              />
            </a>
            <!-- No thumbnail for a PDF means it couldn't be rendered — almost
                 always password-protected. Show a clickable padlock that opens
                 the PDF (the browser then prompts for the password). Mobile only;
                 the desktop iframe can prompt inline. -->
            <a
              v-else
              :href="pdfPreviewUrl"
              target="_blank"
              rel="noopener"
              class="flex lg:hidden aspect-[3/4] w-full flex-col items-center justify-center gap-3 bg-gray-100 dark:bg-gray-900/40 p-6 text-center text-gray-400 dark:text-gray-500"
              data-testid="preview-pdf-locked"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                stroke-width="1.5"
                stroke="currentColor"
                class="w-12 h-12"
                aria-hidden="true"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z"
                />
              </svg>
              <span class="text-sm font-medium">Protected PDF — tap to open</span>
            </a>
            <!-- Browser-native PDF viewing; see the component docblock. The
                 overflow-hidden wrapper lets us clip the viewer's own toolbar on
                 Firefox (which ignores #toolbar=0) by nudging the iframe up. -->
            <div class="hidden lg:block overflow-hidden">
              <iframe
                class="w-full border-0 hidden lg:block"
                :class="hidePdfToolbar ? 'h-[calc(70vh+2.6rem)] -mt-[2.6rem]' : 'h-[70vh]'"
                :src="pdfPreviewIframeUrl"
                title="Document preview"
                data-testid="preview-pdf"
              ></iframe>
            </div>
          </template>
          <div
            v-else
            class="p-4 text-sm text-gray-500 dark:text-gray-400"
            data-testid="preview-fallback"
          >
            No preview is available for this file type.
            <a class="text-violet-600 hover:underline" :href="originalUrl(doc.id)"
              >Download the original file</a
            >
            to view it.
          </div>
        </div>

        <div
          v-if="doc.ocr_text"
          id="document-ocr-card"
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
        >
          <AppDetails
            summary="View extracted text"
            :open="Boolean(highlight)"
            data-testid="ocr-details"
          >
            <!-- eslint-disable-next-line vue/no-v-html -- renderHighlighted escapes every input character; only its own <mark> wrappers survive (docs/api.md §1.3.3) -->
            <pre
              v-if="highlight"
              class="app-ocr-text mt-1 max-h-[28rem] overflow-auto rounded-lg border border-gray-200 dark:border-gray-700/50 bg-gray-50 dark:bg-gray-900/50 p-4 font-mono text-sm leading-relaxed text-gray-700 dark:text-gray-300 whitespace-pre-wrap break-words"
              data-testid="ocr-text"
              v-html="renderHighlighted(doc.ocr_text, highlight)"
            ></pre>
            <pre
              v-else
              class="app-ocr-text mt-1 max-h-[28rem] overflow-auto rounded-lg border border-gray-200 dark:border-gray-700/50 bg-gray-50 dark:bg-gray-900/50 p-4 font-mono text-sm leading-relaxed text-gray-700 dark:text-gray-300 whitespace-pre-wrap break-words"
              data-testid="ocr-text"
              >{{ doc.ocr_text }}</pre
            >
          </AppDetails>
        </div>
      </div>

      <!-- Metadata: left column on desktop (lg:order-1). min-w-0 (as above)
           lets long metadata values wrap rather than widen the page. -->
      <div id="document-metadata-column" class="min-w-0 space-y-6 lg:order-1">
        <div
          id="document-details-card"
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
        >
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Details</h2>

          <div id="document-details-list" class="space-y-3">
            <!-- One themed panel per metadata group: accent rail + tint + heading
                 make each kind of metadata distinguishable at a glance, and the
                 two-column grid uses the width instead of one tall column. -->
            <section
              v-for="group in fieldGroups"
              :key="group.key"
              class="rounded-lg border-l-4 px-4 py-3.5"
              :class="[ACCENT[group.accent].border, ACCENT[group.accent].bg]"
            >
              <div class="mb-3 flex items-center gap-2">
                <span class="h-3.5 w-1 rounded-full" :class="ACCENT[group.accent].bar"></span>
                <h3
                  class="text-xs font-semibold uppercase tracking-wider"
                  :class="ACCENT[group.accent].text"
                >
                  {{ group.label }}
                </h3>
              </div>
              <dl class="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                <div
                  v-for="field in group.fields"
                  :key="field"
                  :data-testid="`row-${field}`"
                  :class="WIDE_FIELDS.has(field) || editing === field ? 'sm:col-span-2' : ''"
                >
                  <dt class="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    {{ rowByField[field].label }}
                    <template v-if="findingsByField[field]?.length">
                      <AppBadge
                        v-for="finding in findingsByField[field]"
                        :key="finding.rule"
                        colour="yellow"
                        :title="finding.message"
                        data-testid="validation-badge"
                      >⚠</AppBadge>
                    </template>
                  </dt>
                  <div v-if="editing !== field" class="mt-1 flex items-start justify-between gap-3">
                    <dd
                      class="min-w-0 break-words leading-snug text-gray-800 dark:text-gray-100"
                      :class="field === 'amount' ? 'text-2xl font-semibold tracking-tight' : 'text-base'"
                      data-testid="row-value"
                      >{{ rowByField[field].display(doc) ?? EMPTY }}</dd
                    >
                    <button
                      type="button"
                      class="mt-0.5 shrink-0 text-xs font-medium text-violet-600 hover:underline app-link-button"
                      @click="startEdit(field)"
                    >
                      Change<span class="sr-only"> {{ rowByField[field].label.toLowerCase() }}</span>
                    </button>
                  </div>
                  <dd v-else class="mt-2">
                    <form class="space-y-3" novalidate @submit.prevent="save(rowByField[field])">
                      <AppInput
                        v-if="field === 'title'"
                        id="edit-title"
                        v-model="editText"
                        label="New title"
                        :error-message="editError ?? undefined"
                      />
                      <AppTextarea
                        v-else-if="field === 'summary'"
                        id="edit-summary"
                        v-model="editText"
                        label="New summary"
                        :rows="4"
                        :error-message="editError ?? undefined"
                      />
                      <AppSelect
                        v-else-if="field === 'kind'"
                        id="edit-kind"
                        v-model="editSelect"
                        label="New kind"
                        :items="kindItems"
                        :error-message="editError ?? undefined"
                      />
                      <template v-else-if="field === 'sender'">
                        <AppInput
                          id="edit-sender"
                          v-model="editText"
                          label="New sender"
                          hint="Start typing to see known senders"
                          list="sender-options"
                          :error-message="editError ?? undefined"
                        />
                        <datalist id="sender-options">
                          <option v-for="sender in senders" :key="sender.id" :value="sender.name" />
                        </datalist>
                      </template>
                      <AppSelect
                        v-else-if="field === 'language'"
                        id="edit-language"
                        v-model="editSelect"
                        label="New language"
                        :items="languageItems"
                        :error-message="editError ?? undefined"
                      />
                      <AppInput
                        v-else-if="field === 'tags'"
                        id="edit-tags"
                        v-model="editText"
                        label="New tags"
                        hint="Separate tags with commas"
                        :error-message="editError ?? undefined"
                      />
                      <template v-else-if="field === 'amount'">
                        <AppInput
                          id="edit-amount"
                          v-model="editText"
                          label="New amount"
                          inputmode="decimal"
                          width-class="w-40"
                          :error-message="editError ?? undefined"
                        />
                        <AppInput
                          id="edit-currency"
                          v-model="editCurrency"
                          label="Currency"
                          hint="3-letter code, like EUR"
                          width-class="w-24"
                        />
                      </template>
                      <AppDateInput
                        v-else
                        :id="`edit-${field.replaceAll('_', '-')}`"
                        v-model="editDate"
                        :legend="`New ${rowByField[field].label.toLowerCase()}`"
                        :error-message="editError ?? undefined"
                      />
                      <div class="flex gap-3">
                        <AppButton type="submit" :disabled="saving">Save</AppButton>
                        <AppButton type="button" variant="secondary" @click="cancelEdit">
                          Cancel
                        </AppButton>
                      </div>
                    </form>
                  </dd>
                </div>
              </dl>
            </section>

            <!-- System: read-only provenance, set apart with a neutral accent. -->
            <section
              class="rounded-lg border-l-4 px-4 py-3.5"
              :class="[ACCENT.gray.border, ACCENT.gray.bg]"
            >
              <div class="mb-3 flex items-center gap-2">
                <span class="h-3.5 w-1 rounded-full" :class="ACCENT.gray.bar"></span>
                <h3
                  class="text-xs font-semibold uppercase tracking-wider"
                  :class="ACCENT.gray.text"
                >
                  System
                </h3>
              </div>
              <dl class="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                <div>
                  <dt class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    Status
                  </dt>
                  <dd class="mt-1.5">
                    <span
                      class="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize"
                      :class="statusAccent(doc.status)"
                      >{{ doc.status }}</span
                    >
                  </dd>
                </div>
                <div>
                  <dt class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    OCR confidence
                  </dt>
                  <dd class="mt-1 text-base text-gray-800 dark:text-gray-100">
                    {{ doc.ocr_confidence === null ? EMPTY : `${Math.round(doc.ocr_confidence)}%` }}
                  </dd>
                </div>
                <div>
                  <dt class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    Source
                  </dt>
                  <dd class="mt-1 text-base text-gray-800 dark:text-gray-100">
                    {{ sourceLabel(doc.source) }}
                  </dd>
                </div>
              </dl>

              <AppDetails
                v-if="doc.extraction"
                id="document-extraction-details"
                summary="Extraction details"
                data-testid="extraction-details"
                class="mt-4"
              >
                <dl class="space-y-2">
                  <div class="flex justify-between gap-4">
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-400">Model</dt>
                    <dd class="text-sm text-gray-800 dark:text-gray-100">
                      {{ doc.extraction.model ?? EMPTY }}
                    </dd>
                  </div>
                  <div class="flex justify-between gap-4">
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-400">Confidence</dt>
                    <dd class="text-sm text-gray-800 dark:text-gray-100">
                      {{ doc.extraction.confidence ?? EMPTY }}
                    </dd>
                  </div>
                  <div v-if="latestExtractionEvent" class="flex justify-between gap-4">
                    <dt class="text-sm font-medium text-gray-500 dark:text-gray-400">When</dt>
                    <dd class="text-sm text-gray-800 dark:text-gray-100">
                      {{ formatDateTime(latestExtractionEvent.created_at) }}
                    </dd>
                  </div>
                </dl>
              </AppDetails>
            </section>
          </div>
        </div>

        <div
          id="document-actions-card"
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
        >
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Actions</h2>
          <p class="text-sm mb-2">
            <a
              class="text-violet-600 hover:underline"
              :href="originalUrl(doc.id)"
              data-testid="download-original"
            >
              Download the original file
            </a>
          </p>
          <p v-if="doc.has_searchable_pdf" class="text-sm mb-4">
            <a
              class="text-violet-600 hover:underline"
              :href="searchablePdfUrl(doc.id)"
              data-testid="download-searchable"
            >
              Download the searchable PDF
            </a>
          </p>
          <div class="flex flex-wrap gap-3">
            <AppButton
              v-if="doc.review_status !== 'verified'"
              type="button"
              :disabled="verifying"
              data-testid="mark-verified"
              @click="markVerified"
            >
              {{ verifying ? 'Saving…' : 'Mark verified' }}
            </AppButton>
            <AppButton
              type="button"
              variant="secondary"
              :disabled="extracting"
              data-testid="rerun-extraction"
              @click="rerunExtraction"
            >
              {{ extracting ? 'Extraction running…' : 'Re-run extraction' }}
            </AppButton>
            <AppButton
              variant="warning"
              :to="`/documents/${doc.id}/delete`"
              data-testid="delete-link"
            >
              Delete this document
            </AppButton>
          </div>
        </div>
      </div>
    </div>
  </template>

  <template v-else-if="notFound">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-2">
      Document not found
    </h1>
    <p class="text-gray-600 dark:text-gray-300">It may have been deleted, or the link is wrong.</p>
  </template>
  <div
    v-else-if="loadError"
    class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-4 text-gray-600 dark:text-gray-300"
  >
    Sorry, the document could not be loaded. Try again later.
  </div>
</template>
