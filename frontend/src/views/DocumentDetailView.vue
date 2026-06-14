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
  updateDocument,
  type DocumentDetail,
  type DocumentLanguage,
  type DocumentUpdate,
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

/** Same PDF, but with the `#view=FitH` open parameter so the browser's native
 * viewer fits the page to the iframe width — otherwise a portrait page renders
 * wider than a narrow (mobile) viewport and the right edge is clipped. The
 * plain `pdfPreviewUrl` (no fragment) is kept for the open-in-new-tab link. */
const pdfPreviewIframeUrl = computed(() =>
  pdfPreviewUrl.value ? `${pdfPreviewUrl.value}#view=FitH` : '',
)

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

    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-6 app-detail-title">
      {{ doc.title ?? 'Untitled document' }}
    </h1>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Preview: right column on desktop (lg:order-2), first on mobile. -->
      <div class="space-y-4 lg:order-2">
        <div
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 overflow-hidden"
        >
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
            <!-- Browser-native PDF viewing; see the component docblock. -->
            <iframe
              class="w-full h-[70vh] border-0"
              :src="pdfPreviewIframeUrl"
              title="Document preview"
              data-testid="preview-pdf"
            ></iframe>
            <p class="p-4 text-sm">
              <a
                class="text-violet-600 hover:underline"
                :href="pdfPreviewUrl"
                target="_blank"
                rel="noopener"
              >
                Open the PDF in a new tab
              </a>
            </p>
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

        <AppDetails
          v-if="doc.ocr_text"
          summary="View extracted text"
          :open="Boolean(highlight)"
          data-testid="ocr-details"
        >
          <!-- eslint-disable-next-line vue/no-v-html -- renderHighlighted escapes every input character; only its own <mark> wrappers survive (docs/api.md §1.3.3) -->
          <pre
            v-if="highlight"
            class="app-ocr-text whitespace-pre-wrap text-sm text-gray-700 dark:text-gray-300"
            data-testid="ocr-text"
            v-html="renderHighlighted(doc.ocr_text, highlight)"
          ></pre>
          <pre
            v-else
            class="app-ocr-text whitespace-pre-wrap text-sm text-gray-700 dark:text-gray-300"
            data-testid="ocr-text"
            >{{ doc.ocr_text }}</pre
          >
        </AppDetails>
      </div>

      <!-- Metadata: left column on desktop (lg:order-1). -->
      <div class="space-y-6 lg:order-1">
        <div
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
        >
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-2">Details</h2>
          <dl class="divide-y divide-gray-200 dark:divide-gray-700/60 app-detail-list">
            <div
              v-for="row in rowConfigs"
              :key="row.field"
              class="py-3"
              :data-testid="`row-${row.field}`"
            >
              <dt class="text-sm font-medium text-gray-500 dark:text-gray-400">{{ row.label }}</dt>
              <dd v-if="editing !== row.field" class="flex justify-between gap-4 items-start mt-1">
                <span class="text-sm text-gray-800 dark:text-gray-100" data-testid="row-value">{{
                  row.display(doc) ?? EMPTY
                }}</span>
                <button
                  type="button"
                  class="text-sm text-violet-600 hover:underline app-link-button shrink-0"
                  @click="startEdit(row.field)"
                >
                  Change<span class="sr-only"> {{ row.label.toLowerCase() }}</span>
                </button>
              </dd>
              <dd v-else class="mt-2">
                <form class="space-y-3" novalidate @submit.prevent="save(row)">
                  <AppInput
                    v-if="row.field === 'title'"
                    id="edit-title"
                    v-model="editText"
                    label="New title"
                    :error-message="editError ?? undefined"
                  />
                  <AppTextarea
                    v-else-if="row.field === 'summary'"
                    id="edit-summary"
                    v-model="editText"
                    label="New summary"
                    :rows="4"
                    :error-message="editError ?? undefined"
                  />
                  <AppSelect
                    v-else-if="row.field === 'kind'"
                    id="edit-kind"
                    v-model="editSelect"
                    label="New kind"
                    :items="kindItems"
                    :error-message="editError ?? undefined"
                  />
                  <template v-else-if="row.field === 'sender'">
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
                    v-else-if="row.field === 'language'"
                    id="edit-language"
                    v-model="editSelect"
                    label="New language"
                    :items="languageItems"
                    :error-message="editError ?? undefined"
                  />
                  <AppInput
                    v-else-if="row.field === 'tags'"
                    id="edit-tags"
                    v-model="editText"
                    label="New tags"
                    hint="Separate tags with commas"
                    :error-message="editError ?? undefined"
                  />
                  <template v-else-if="row.field === 'amount'">
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
                    :id="`edit-${row.field.replaceAll('_', '-')}`"
                    v-model="editDate"
                    :legend="`New ${row.label.toLowerCase()}`"
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

            <div class="py-3">
              <dt class="text-sm font-medium text-gray-500 dark:text-gray-400">Status</dt>
              <dd class="text-sm text-gray-800 dark:text-gray-100 mt-1">{{ doc.status }}</dd>
            </div>
            <div class="py-3">
              <dt class="text-sm font-medium text-gray-500 dark:text-gray-400">OCR confidence</dt>
              <dd class="text-sm text-gray-800 dark:text-gray-100 mt-1">
                {{ doc.ocr_confidence === null ? EMPTY : `${Math.round(doc.ocr_confidence)}%` }}
              </dd>
            </div>
            <div class="py-3">
              <dt class="text-sm font-medium text-gray-500 dark:text-gray-400">Source</dt>
              <dd class="text-sm text-gray-800 dark:text-gray-100 mt-1">
                {{ sourceLabel(doc.source) }}
              </dd>
            </div>
          </dl>

          <AppDetails
            v-if="doc.extraction"
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
        </div>

        <div
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
