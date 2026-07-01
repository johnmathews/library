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
 * PDF preview uses DocumentPdfPreview (pdf.js canvas renderer) for
 * consistent cross-browser rendering on every viewport.
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
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
  AppMultiSelect,
  AppSelect,
  AppTextarea,
} from '@/components/app'
import type { ErrorSummaryItem, SelectItem } from '@/components/app'
import {
  DOCUMENT_LANGUAGES,
  fetchDocumentMarkdown,
  getDocument,
  originalUrl,
  requestExtraction,
  searchablePdfUrl,
  thumbnailUrl,
  updateDocument,
  verifyDocument,
  type DocumentDetail,
  type DocumentLanguage,
  type DocumentMarkdownResponse,
  type DocumentUpdate,
  type ValidationFinding,
} from '@/api/documents'
import {
  listNoteVersions,
  restoreNoteVersion,
  updateNote,
  type NoteVersion,
} from '@/api/notes'
import {
  listKinds,
  createKind,
  listSenders,
  listRecipients,
  type KindOption,
  type SenderOption,
  type RecipientOption,
} from '@/api/taxonomy'
import { refreshTaxonomyOptions, useTaxonomyOptions } from '@/composables/taxonomyOptions'
import { useMarkdownEditorMode } from '@/composables/useMarkdownEditorMode'
import { ApiError } from '@/api/client'
import { useJobsStore } from '@/stores/jobs'
import { deriveNoteTitle } from '@/utils/noteTitle'
import DocumentSeriesTrend from '@/components/DocumentSeriesTrend.vue'
import DocumentPdfPreview from '@/components/DocumentPdfPreview.vue'
import DocumentHistoryTimeline from '@/components/DocumentHistoryTimeline.vue'

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
const recipients = ref<RecipientOption[]>([])

// Existing projects feed the projects multiselect (picking an existing name or
// typing a new one, which the backend upserts on save).
const { projects: projectOptions, ensureLoaded: ensureProjectsLoaded } = useTaxonomyOptions()
void ensureProjectsLoaded()
const projectOptionNames = computed(() => projectOptions.value.map((project) => project.name))

onMounted(async () => {
  // Best-effort: without options the kind/recipient selects still offer the
  // current value and "Not set", and the sender input works without suggestions.
  const [kindResult, senderResult, recipientResult] = await Promise.allSettled([
    listKinds(),
    listSenders(),
    listRecipients(),
  ])
  if (kindResult.status === 'fulfilled') kinds.value = kindResult.value
  if (senderResult.status === 'fulfilled') senders.value = senderResult.value
  if (recipientResult.status === 'fulfilled') recipients.value = recipientResult.value
})

/** Reload the recipient dropdown options (called after an inline add so a freshly
 * created recipient appears in the list). Best-effort. */
async function loadRecipients(): Promise<void> {
  try {
    recipients.value = await listRecipients()
  } catch {
    // Keep the current list; the new recipient still shows via recipientItems.
  }
}

/** Reload the kind dropdown options (called after an inline add so a freshly
 * created kind appears in the list). Best-effort. */
async function loadKinds(): Promise<void> {
  try {
    kinds.value = await listKinds()
  } catch {
    // Keep the current list; the new kind still shows via kindItems.
  }
}

/** Sentinel select value that reveals the inline "add a new kind" input. Kind is
 * a controlled list (a dropdown over the seeded set), but new kinds can be added
 * inline — picking this option swaps the select for a text input + confirm. */
const KIND_ADD = '__add_kind__'

const kindItems = computed<SelectItem[]>(() => {
  const items: SelectItem[] = [
    { value: '', text: 'Not set' },
    ...kinds.value.map((kind) => ({ value: kind.slug, text: kind.name })),
  ]
  const current = doc.value?.kind
  if (current && !kinds.value.some((kind) => kind.slug === current.slug)) {
    items.push({ value: current.slug, text: current.name })
  }
  items.push({ value: KIND_ADD, text: 'Add kind…' })
  return items
})

const languageItems: SelectItem[] = DOCUMENT_LANGUAGES.map((language) => ({
  value: language.value,
  text: language.text,
}))

/** Sentinel select value that reveals the inline "add a new recipient" input.
 * Recipient is a controlled list (a dropdown), but new names can be added
 * inline — picking this option swaps the select for a text input + confirm. */
const RECIPIENT_ADD = '__add_recipient__'

/** Recipient dropdown options: "Not set", every known recipient (by name, the
 * value the PATCH upserts), the current value if it isn't in the list yet, and
 * the inline "Add recipient…" affordance. Mirrors {@link kindItems}. */
const recipientItems = computed<SelectItem[]>(() => {
  const items: SelectItem[] = [
    { value: '', text: 'Not set' },
    ...recipients.value.map((recipient) => ({ value: recipient.name, text: recipient.name })),
  ]
  const current = doc.value?.recipient
  if (current && !recipients.value.some((recipient) => recipient.name === current.name)) {
    items.push({ value: current.name, text: current.name })
  }
  items.push({ value: RECIPIENT_ADD, text: 'Add recipient…' })
  return items
})

// --- Summary rows -------------------------------------------------------------

type EditableField =
  | 'title'
  | 'kind'
  | 'sender'
  | 'recipient'
  | 'document_date'
  | 'language'
  | 'tags'
  | 'projects'
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
  { field: 'recipient', label: 'Recipient', display: (d) => d.recipient?.name ?? null },
  { field: 'document_date', label: 'Document date', display: (d) => formatDate(d.document_date) },
  { field: 'language', label: 'Language', display: (d) => languageName(d.language) },
  {
    field: 'tags',
    label: 'Tags',
    display: (d) => (d.tags.length ? d.tags.map((tag) => tag.name).join(', ') : null),
  },
  {
    field: 'projects',
    label: 'Projects',
    display: (d) =>
      d.projects.length ? d.projects.map((project) => project.name).join(', ') : null,
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
  { key: 'content', label: 'Content', accent: 'violet', fields: ['title', 'summary', 'tags', 'projects'] },
  { key: 'classification', label: 'Classification', accent: 'yellow', fields: ['kind', 'language'] },
  {
    key: 'parties',
    label: 'Sender, recipient & dates',
    accent: 'sky',
    fields: ['sender', 'recipient', 'document_date', 'due_date', 'expiry_date'],
  },
  { key: 'financial', label: 'Financial', accent: 'green', fields: ['amount'] },
]

/** Fields that read better spanning the full width of the two-column grid. */
const WIDE_FIELDS = new Set<EditableField>(['title', 'summary', 'tags', 'projects', 'amount'])

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
 * These mirror (read-only) the most important editable rows below. Only stats
 * that actually have a value are emitted — a value-less stat is dropped rather
 * than shown as a dead em-dash, so a general doc reads cleanly. */
const heroStats = computed<{ label: string; value: string }[]>(() => {
  const d = doc.value
  if (!d) return []
  const stats: { label: string; value: string }[] = []
  if (d.kind?.name) stats.push({ label: 'Kind', value: d.kind.name })
  if (d.sender?.name) stats.push({ label: 'Sender', value: d.sender.name })
  const documentDate = formatDate(d.document_date)
  if (documentDate) stats.push({ label: 'Document date', value: documentDate })
  // Ingestion date (created_at) and last edited (updated_at) are always present
  // and read-only — the three dates read as a distinct trio in the hero.
  stats.push({ label: 'Ingested', value: formatDateTime(d.created_at) })
  stats.push({ label: 'Last edited', value: formatDateTime(d.updated_at) })
  if (d.amount_total !== null) {
    const amount = [d.amount_total, d.currency].filter(Boolean).join(' ')
    if (amount) stats.push({ label: 'Amount', value: amount })
  }
  return stats
})

/** Pre-filled text for the "Ask about this document" button. It just names the
 * current document so the existing Ask RAG retrieval surfaces it — there is no
 * backend change. Kind/sender/date are folded into a parenthetical, and any
 * missing part is omitted gracefully (no empty `()`). */
const askPrompt = computed<string>(() => {
  const d = doc.value
  if (!d) return ''
  const title = d.title?.trim() || d.original_filename?.trim() || ''
  // kind + sender read as one phrase ("Invoice from Eneco"); the date is a
  // separate comma-delimited part ("…, 15 May 2026").
  const descriptor: string[] = []
  if (d.kind?.name) descriptor.push(d.kind.name)
  if (d.sender?.name) descriptor.push(`from ${d.sender.name}`)
  const parenParts = [descriptor.join(' '), formatDate(d.document_date)].filter(
    (part): part is string => Boolean(part),
  )
  const parenthetical = parenParts.length ? ` (${parenParts.join(', ')})` : ''
  return title
    ? `Tell me about the document "${title}"${parenthetical}: `
    : `Tell me about this document${parenthetical}: `
})

// --- Page-wide edit mode (per-field autosave) ---------------------------------
//
// A single "Edit" toggle reveals an inline editor for every field at once,
// replacing the old one-row-at-a-time "Change" reveal (and its ~10 buttons).
// Each field autosaves independently when committed — native `change` fires on
// blur for text inputs and on selection for selects/dates, and bubbles up to
// the field wrapper — via the same per-field PATCH the backend already expects.
// There is no global Save/Cancel; "Done" just leaves edit mode.

const editMode = ref(false)

/** The fields whose draft is a plain string (text inputs + selects). */
type StringDraftField =
  | 'title'
  | 'summary'
  | 'sender'
  | 'recipient'
  | 'tags'
  | 'kind'
  | 'language'
  | 'amount'

/** Draft string values for the text and select fields, keyed by field. */
const drafts = reactive<Record<StringDraftField, string>>({
  title: '',
  summary: '',
  sender: '',
  recipient: '',
  tags: '',
  kind: '',
  language: '',
  amount: '',
})
/** Projects draft as a discrete list (not a comma-joined string): project names
 * are free text and may contain commas, so the multiselect binds this array
 * directly — no encode/decode round-trip that a comma would corrupt. */
const projectsDraft = ref<string[]>([])
/** Draft ISO date strings for the date fields. */
const dateDrafts = reactive<{
  document_date: string | null
  due_date: string | null
  expiry_date: string | null
}>({ document_date: null, due_date: null, expiry_date: null })
/** Draft currency for the amount field (its own input). */
const currencyDraft = ref('')
/** Per-field in-flight guard, validation/save error, and transient "Saved" flag. */
const savingField = reactive<Record<string, boolean>>({})
const fieldError = reactive<Record<string, string | null>>({})
const savedField = reactive<Record<string, boolean>>({})

/** The success / progress notification shown at the top of the page. */
const notice = ref<{ variant?: 'success'; text: string } | null>(null)
/** Failure of a page-level action (re-extraction / verify). */
const actionError = ref<string | null>(null)

const errorItems = computed<ErrorSummaryItem[]>(() => {
  const items: ErrorSummaryItem[] = []
  if (actionError.value) items.push({ text: actionError.value })
  return items
})

/** Load every draft from the current document (called when edit mode opens). */
function hydrateDrafts(): void {
  const d = doc.value
  if (!d) return
  drafts.title = d.title ?? ''
  drafts.summary = d.summary ?? ''
  drafts.sender = d.sender?.name ?? ''
  drafts.recipient = d.recipient?.name ?? ''
  drafts.tags = d.tags.map((tag) => tag.slug).join(', ')
  projectsDraft.value = d.projects.map((project) => project.name)
  drafts.kind = d.kind?.slug ?? ''
  drafts.language = d.language
  drafts.amount = d.amount_total ?? ''
  currencyDraft.value = d.currency ?? ''
  dateDrafts.document_date = d.document_date
  dateDrafts.due_date = d.due_date
  dateDrafts.expiry_date = d.expiry_date
}

/** Re-sync one field's draft from the server response after a save, so a
 * canonicalised value (e.g. slugified tags) is reflected back in the editor. */
function hydrateField(field: EditableField): void {
  const d = doc.value
  if (!d) return
  switch (field) {
    case 'title': drafts.title = d.title ?? ''; break
    case 'summary': drafts.summary = d.summary ?? ''; break
    case 'sender': drafts.sender = d.sender?.name ?? ''; break
    case 'recipient': drafts.recipient = d.recipient?.name ?? ''; break
    case 'tags': drafts.tags = d.tags.map((tag) => tag.slug).join(', '); break
    case 'projects': projectsDraft.value = d.projects.map((project) => project.name); break
    case 'kind': drafts.kind = d.kind?.slug ?? ''; break
    case 'language': drafts.language = d.language; break
    case 'amount':
      drafts.amount = d.amount_total ?? ''
      currencyDraft.value = d.currency ?? ''
      break
    case 'document_date': dateDrafts.document_date = d.document_date; break
    case 'due_date': dateDrafts.due_date = d.due_date; break
    case 'expiry_date': dateDrafts.expiry_date = d.expiry_date; break
  }
}

function toggleEditMode(): void {
  editMode.value = !editMode.value
  if (editMode.value) hydrateDrafts()
  else resetEditState()
}

/** Leave edit mode and clear the transient per-field error / "Saved" state. */
function resetEditState(): void {
  editMode.value = false
  recipientAdding.value = false
  recipientNewName.value = ''
  kindAdding.value = false
  kindNewName.value = ''
  for (const key of Object.keys(fieldError)) fieldError[key] = null
  for (const key of Object.keys(savedField)) savedField[key] = false
}

/** Whether a field's draft differs from the stored value — guards autosave so a
 * plain focus-through (no real edit) never fires a needless PATCH. */
function fieldDirty(field: EditableField): boolean {
  const d = doc.value
  if (!d) return false
  switch (field) {
    case 'title': return (drafts.title.trim() || null) !== (d.title ?? null)
    case 'summary': return (drafts.summary.trim() || null) !== (d.summary ?? null)
    case 'sender': return (drafts.sender.trim() || null) !== (d.sender?.name ?? null)
    case 'recipient': return (drafts.recipient.trim() || null) !== (d.recipient?.name ?? null)
    case 'kind': return (drafts.kind || null) !== (d.kind?.slug ?? null)
    case 'language': return drafts.language !== d.language
    case 'tags': {
      // Tags are an unordered set: compare sorted slugs so a reorder-only edit
      // (or the server returning a different order) isn't seen as a change.
      const next = drafts.tags.split(',').map((t) => t.trim()).filter(Boolean).sort()
      return next.join(',') !== d.tags.map((t) => t.slug).sort().join(',')
    }
    case 'projects': {
      // Projects are an unordered set, full-replaced by name; compare sorted
      // names element-wise (not a joined string, which a comma in a name would
      // make ambiguous) so a reorder-only edit isn't seen as a change.
      const next = [...projectsDraft.value].sort()
      const current = d.projects.map((p) => p.name).sort()
      return next.length !== current.length || next.some((name, i) => name !== current[i])
    }
    case 'document_date': return (dateDrafts.document_date ?? null) !== (d.document_date ?? null)
    case 'due_date': return (dateDrafts.due_date ?? null) !== (d.due_date ?? null)
    case 'expiry_date': return (dateDrafts.expiry_date ?? null) !== (d.expiry_date ?? null)
    case 'amount': {
      const amount = drafts.amount.trim().replace(',', '.') || null
      const currency = currencyDraft.value.trim() || null
      return amount !== (d.amount_total ?? null) || currency !== (d.currency ?? null)
    }
  }
  return false
}

/** The PATCH body for one field — exactly that field's column(s). Returns null
 * (and sets fieldError) when the field fails client-side validation. */
function buildPatch(field: EditableField): DocumentUpdate | null {
  switch (field) {
    case 'title':
      return { title: drafts.title.trim() || null }
    case 'summary':
      return { summary: drafts.summary.trim() || null }
    case 'sender':
      return { sender: drafts.sender.trim() || null }
    case 'recipient':
      return { recipient: drafts.recipient.trim() || null }
    case 'kind':
      return { kind_slug: drafts.kind || null }
    case 'language':
      return { language: drafts.language as DocumentLanguage }
    case 'tags':
      return { tags: drafts.tags.split(',').map((tag) => tag.trim()).filter(Boolean) }
    case 'projects':
      return { projects: [...projectsDraft.value] }
    case 'document_date':
      return { document_date: dateDrafts.document_date }
    case 'due_date':
      return { due_date: dateDrafts.due_date }
    case 'expiry_date':
      return { expiry_date: dateDrafts.expiry_date }
    case 'amount': {
      const amount = drafts.amount.trim().replace(',', '.')
      const currency = currencyDraft.value.trim()
      if (!amount) return { amount_total: null, currency: null }
      if (!/^\d+(\.\d+)?$/.test(amount)) {
        fieldError.amount = 'Enter the amount as a number, like 123.45'
        return null
      }
      if (currency && !/^[A-Za-z]{3}$/.test(currency)) {
        fieldError.amount = 'Enter a 3-letter currency code, like EUR'
        return null
      }
      return { amount_total: amount, currency: currency || null }
    }
  }
}

/** Autosave one field: skip when unchanged or invalid, PATCH just that field,
 * replace the document with the response, and flash a brief "Saved". */
async function saveField(field: EditableField): Promise<void> {
  if (!doc.value || savingField[field]) return
  fieldError[field] = null
  if (!fieldDirty(field)) return
  const patch = buildPatch(field)
  if (!patch) return
  savingField[field] = true
  try {
    doc.value = await updateDocument(doc.value.id, patch)
    hydrateField(field)
    // A projects edit may have created a new project inline; refresh the shared
    // taxonomy cache so it's offered in the multiselect and elsewhere.
    if (field === 'projects') void refreshTaxonomyOptions()
    savedField[field] = true
    window.setTimeout(() => {
      savedField[field] = false
    }, 2000)
  } catch (error: unknown) {
    fieldError[field] =
      error instanceof ApiError && error.status !== 0
        ? error.detail
        : 'Could not save the change — check your connection and try again'
  } finally {
    savingField[field] = false
  }
}

/** Save a date field once, when focus leaves the whole day/month/year group —
 * not on each sub-field's `change`, which would persist intermediate dates. */
function onDateFocusOut(field: EditableField, event: FocusEvent): void {
  const group = event.currentTarget as HTMLElement | null
  const next = event.relatedTarget as Node | null
  // Focus moved between the day/month/year inputs — still inside the group.
  if (group && next && group.contains(next)) return
  void saveField(field)
}

// --- Recipient inline add -----------------------------------------------------
//
// Recipient is a controlled list shown as a dropdown, but a brand-new recipient
// can be created without leaving the page: picking "Add recipient…" reveals an
// inline text input + confirm (no blocking window.prompt). Confirming sets the
// draft to the typed name and runs the normal per-field autosave, which PATCHes
// `{ recipient: <name> }` — the backend upserts case-insensitively by name.

const recipientAdding = ref(false)
const recipientNewName = ref('')

/** Select-change handler for the recipient dropdown. The "Add recipient…"
 * sentinel reveals the inline input (and reverts the select to the current
 * value); any real option autosaves as usual. */
function onRecipientChange(): void {
  if (drafts.recipient === RECIPIENT_ADD) {
    drafts.recipient = doc.value?.recipient?.name ?? ''
    recipientNewName.value = ''
    recipientAdding.value = true
    return
  }
  recipientAdding.value = false
  void saveField('recipient')
}

/** Confirm an inline-added recipient: set the draft to the typed name, autosave
 * (the backend upserts), then refresh the dropdown so it lists the new name. */
async function confirmAddRecipient(): Promise<void> {
  const name = recipientNewName.value.trim()
  if (!name) return
  drafts.recipient = name
  recipientAdding.value = false
  recipientNewName.value = ''
  await saveField('recipient')
  await loadRecipients()
  // Refresh the shared taxonomy cache so the list view's filter bar lists the
  // newly created recipient without a full page reload.
  await refreshTaxonomyOptions()
}

function cancelAddRecipient(): void {
  recipientAdding.value = false
  recipientNewName.value = ''
}

// Kind is a controlled list shown as a dropdown, but a brand-new kind can be
// created without leaving the page: picking "Add kind…" reveals an inline text
// input + confirm. Confirming POSTs /api/kinds (which slugifies, sentence-cases,
// dedupes, and rejects near-duplicates), then selects the returned slug and runs
// the normal per-field autosave (PATCH `{ kind_slug }`).

const kindAdding = ref(false)
const kindNewName = ref('')

/** Select-change handler for the kind dropdown. The "Add kind…" sentinel reveals
 * the inline input (and reverts the select to the current value); any real
 * option autosaves as usual. */
function onKindChange(): void {
  if (drafts.kind === KIND_ADD) {
    drafts.kind = doc.value?.kind?.slug ?? ''
    kindNewName.value = ''
    fieldError.kind = null
    kindAdding.value = true
    return
  }
  kindAdding.value = false
  void saveField('kind')
}

/** Confirm an inline-added kind: create it on the backend, add it to the local
 * options, select its slug, then autosave. A near-duplicate (409) or other error
 * keeps the input open with the surfaced message. */
async function confirmAddKind(): Promise<void> {
  const name = kindNewName.value.trim()
  if (!name) return
  fieldError.kind = null
  let created
  try {
    created = await createKind(name)
  } catch (err) {
    fieldError.kind =
      err instanceof ApiError ? err.detail : 'Could not add the kind — try again later'
    return
  }
  // Surface the new (or deduped existing) kind in the dropdown immediately, then
  // select it and run the normal autosave.
  if (!kinds.value.some((kind) => kind.slug === created.slug)) {
    kinds.value = [...kinds.value, { ...created, document_count: 0 }]
  }
  drafts.kind = created.slug
  kindAdding.value = false
  kindNewName.value = ''
  await saveField('kind')
  await loadKinds()
  // Refresh the shared taxonomy cache so the list view's filter bar lists the
  // newly created kind without a full page reload.
  await refreshTaxonomyOptions()
}

function cancelAddKind(): void {
  kindAdding.value = false
  kindNewName.value = ''
  fieldError.kind = null
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

// --- Live status (SSE) --------------------------------------------------------
//
// While this document is open and processing in the background, refetch it on
// each of its own pipeline events so the status badge (and any metadata the
// pipeline fills in) updates without a manual reload. Skip while a re-extraction
// poll is running — that loop already owns refreshes and a double-fetch would
// race it.
const jobsStore = useJobsStore()
watch(
  () => jobsStore.lastEvent,
  async (event) => {
    if (!event || extracting.value) return
    const current = doc.value
    if (!current || event.document_id !== current.id) return
    try {
      const fresh = await getDocument(current.id)
      if (!unmounted) doc.value = fresh
    } catch {
      // Transient — the next event or a reload recovers the latest state.
    }
  },
)

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
  recipient_id: 'recipient',
}

/** Findings indexed by UI field name. */
const findingsByField = computed<Record<string, ValidationFinding[]>>(() => {
  const findings = doc.value?.validation?.findings
  if (!findings?.length) return {}
  const result: Record<string, ValidationFinding[]> = {}
  for (const finding of findings) {
    if (finding.field === null || !(finding.field in STORAGE_TO_UI_FIELD)) continue
    const uiField = STORAGE_TO_UI_FIELD[finding.field] ?? finding.field
    ;(result[uiField] ??= []).push(finding)
  }
  return result
})

/**
 * Document-level findings: those whose `field` is null, or whose `field` is
 * not mapped to a rendered summary row (e.g. ocr_confidence_gate,
 * empty_extraction, self_reported_low). Shown in a top-level warning banner.
 */
const documentLevelFindings = computed<ValidationFinding[]>(() => {
  const findings = doc.value?.validation?.findings
  if (!findings?.length) return []
  return findings.filter(
    (f) => f.field === null || !(f.field in STORAGE_TO_UI_FIELD),
  )
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
 * Inline disposition so the browser renders rather than downloads. */
const pdfPreviewUrl = computed(() =>
  doc.value
    ? doc.value.has_searchable_pdf
      ? searchablePdfUrl(doc.value.id, { inline: true })
      : originalUrl(doc.value.id, { inline: true })
    : '',
)

/** Positive integer page number from `?page=N` in the route query, or null. */
const pageParam = computed<number | null>(() => {
  const value = route.query.page
  const n = Array.isArray(value) ? Number(value[0]) : Number(value)
  return Number.isInteger(n) && n > 0 ? n : null
})

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

const latestExtractionEvent = computed(() => {
  if (!doc.value) return null
  return (
    doc.value.events.filter((event) => event.event.startsWith('extraction')).at(-1) ?? null
  )
})

// --- Document text reader (markdown, fetched eagerly on load) -----------------

const markdownData = ref<DocumentMarkdownResponse | null>(null)
const markdownLoading = ref(false)
const markdownError = ref(false)

// On small screens the document text sits above the metadata column (summary,
// amount, …), so a long document forces the reader to scroll past all of it to
// reach the metadata. Collapse the text by default below lg and offer a toggle;
// at lg+ the text and metadata are side by side, so it stays expanded (the
// toggle is hidden and the body is forced visible via `lg:!block`). Initialised
// at setup so the first render is already correct on mobile (no expand→collapse
// flash). matchMedia is absent in jsdom → defaults to expanded under test.
const textExpanded = ref(
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(min-width: 1024px)').matches
    : true,
)

function markdownPageHtml(md: string): string {
  return DOMPurify.sanitize(marked.parse(md, { async: false }) as string)
}

/** Whether the document has readable extracted text to show in the reader. */
const hasReadableText = computed(() => (markdownData.value?.pages.length ?? 0) > 0)

/** Fetch the rendered markdown for a document. Called from the load watcher so
 * the reader is the primary content even for files with no PDF/image preview. */
async function loadMarkdown(id: number): Promise<void> {
  markdownLoading.value = true
  markdownError.value = false
  try {
    markdownData.value = await fetchDocumentMarkdown(id)
  } catch {
    markdownError.value = true
  } finally {
    markdownLoading.value = false
  }
}

// --- Notes: in-place editing + version history --------------------------------
//
// Note documents (source === 'note') are authored in-app and carry their body
// in the markdown reader. They get their own edit affordance (separate from the
// generic per-field metadata editor) plus a version-history panel with restore.

const isNote = computed(() => doc.value?.source === 'note')

/** The note's current markdown body, assembled from the reader's pages. */
const noteBody = computed(() =>
  (markdownData.value?.pages ?? []).map((page) => page.markdown).join('\n\n'),
)

const noteEditMode = ref(false)
const noteBodyDraft = ref('')
const noteSaving = ref(false)
const noteEditError = ref<string | null>(null)

/** Editor view mode (edit / split / preview) — shared with the new-note view via
 * the persisted preference. */
const { editorMode, showEditor, showPreview, modes } = useMarkdownEditorMode()

/** Sanitised HTML preview of the draft body so the preview reflects edits live
 * (the reader's markdownPageHtml is bound to the saved body). */
const noteDraftPreviewHtml = computed(() => markdownPageHtml(noteBodyDraft.value))

/** The title is the first line of the body, mirroring the new-note authoring view. */
const noteEditTitle = computed(() => deriveNoteTitle(noteBodyDraft.value))
const canSaveNote = computed(() => noteEditTitle.value !== '' && !noteSaving.value)

function openNoteEditor(): void {
  noteBodyDraft.value = noteBody.value
  noteEditError.value = null
  noteEditMode.value = true
}

function cancelNoteEdit(): void {
  noteEditMode.value = false
  noteEditError.value = null
}

async function saveNote(): Promise<void> {
  if (!doc.value || !canSaveNote.value) return
  noteSaving.value = true
  noteEditError.value = null
  try {
    const id = doc.value.id
    doc.value = await updateNote(id, {
      title: noteEditTitle.value,
      body_markdown: noteBodyDraft.value,
    })
    await loadMarkdown(id)
    noteEditMode.value = false
  } catch (error: unknown) {
    noteEditError.value =
      error instanceof ApiError && error.status !== 0
        ? error.detail
        : 'Could not save the note — check your connection and try again'
  } finally {
    noteSaving.value = false
  }
}

const noteVersions = ref<NoteVersion[]>([])
const noteVersionsOpen = ref(false)
const noteVersionsLoading = ref(false)
const noteVersionsError = ref<string | null>(null)
const restoringVersion = ref<number | null>(null)

async function toggleNoteVersions(): Promise<void> {
  noteVersionsOpen.value = !noteVersionsOpen.value
  if (!noteVersionsOpen.value || !doc.value) return
  await loadNoteVersions()
}

async function loadNoteVersions(): Promise<void> {
  if (!doc.value) return
  noteVersionsLoading.value = true
  noteVersionsError.value = null
  try {
    noteVersions.value = await listNoteVersions(doc.value.id)
  } catch {
    noteVersionsError.value = 'Could not load version history — try again later.'
  } finally {
    noteVersionsLoading.value = false
  }
}

async function restoreVersion(versionNo: number): Promise<void> {
  if (!doc.value || restoringVersion.value !== null) return
  restoringVersion.value = versionNo
  noteVersionsError.value = null
  try {
    const id = doc.value.id
    doc.value = await restoreNoteVersion(id, versionNo)
    await loadMarkdown(id)
    await loadNoteVersions()
  } catch {
    noteVersionsError.value = 'Could not restore that version — try again later.'
  } finally {
    restoringVersion.value = null
  }
}

/** Clear all note-specific state on navigation to another document. */
function resetNoteState(): void {
  noteEditMode.value = false
  noteEditError.value = null
  noteBodyDraft.value = ''
  noteVersions.value = []
  noteVersionsOpen.value = false
  noteVersionsError.value = null
}

// --- Load on navigation (registered last: the handler runs immediately and
// --- touches the edit/notice state declared above) ----------------------------

watch(
  () => route.params.id,
  async (id) => {
    if (route.name !== 'document-detail') return
    doc.value = null
    notFound.value = false
    loadError.value = false
    resetEditState()
    resetNoteState()
    notice.value = null
    actionError.value = null
    markdownData.value = null
    markdownLoading.value = false
    markdownError.value = false
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
      return
    }
    // Fetch the rendered text eagerly so the reader is ready without a reveal.
    await loadMarkdown(numericId)
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
    <AppBanner
      v-if="documentLevelFindings.length"
      data-testid="validation-findings"
      class="mb-6"
    >
      <ul class="list-disc list-inside space-y-1">
        <li v-for="finding in documentLevelFindings" :key="finding.rule">
          {{ finding.message }}
        </li>
      </ul>
    </AppBanner>

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
        v-if="heroStats.length"
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
          <DocumentPdfPreview
            v-else-if="preview === 'pdf'"
            :src="pdfPreviewUrl"
            :poster="doc.has_thumbnail ? thumbnailUrl(doc.id) : undefined"
            :open-href="previewOpenUrl"
            :download-href="previewDownloadUrl"
            :initial-page="pageParam"
            data-testid="preview-pdf"
          />
          <div
            v-else-if="!hasReadableText"
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

        <!-- Document text: a first-class long-form reader. The extracted text is
             the primary content for files with no PDF/image preview, so at lg+
             it is rendered directly (beside the metadata column). On small
             screens it stacks above the metadata, so it collapses by default
             behind a Show/Hide toggle to keep the metadata reachable. -->
        <div
          id="document-markdown-card"
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
        >
          <div class="flex items-center justify-between gap-3 mb-3">
            <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">Document text</h2>
            <!-- Mobile-only collapse toggle: at lg+ the text sits beside the
                 metadata (no scroll problem) so the body is always shown. -->
            <button
              type="button"
              class="lg:hidden btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300"
              data-testid="markdown-toggle"
              :aria-expanded="textExpanded"
              aria-controls="document-markdown-body"
              @click="textExpanded = !textExpanded"
            >
              {{ textExpanded ? 'Hide' : 'Show' }}
            </button>
          </div>
          <!-- v-show keeps the body in the DOM (so deep-links/anchors resolve)
               but hides it on mobile when collapsed; `lg:!block` overrides the
               inline display:none at lg+ so it is always visible there. -->
          <div id="document-markdown-body" v-show="textExpanded" class="lg:!block">
            <div v-if="markdownLoading" class="text-sm text-gray-500 dark:text-gray-400" data-testid="markdown-loading">
              Loading…
            </div>
            <div v-else-if="markdownError" class="text-sm text-red-600 dark:text-red-400" data-testid="markdown-error">
              Could not load markdown — try again later.
            </div>
            <div v-else-if="markdownData && markdownData.page_count === 0" class="text-sm text-gray-500 dark:text-gray-400" data-testid="markdown-empty">
              No markdown content is available for this document yet.
            </div>
            <template v-else-if="markdownData">
              <div
                v-for="page in markdownData.pages"
                :key="page.page_number"
                class="mt-3 first:mt-0"
                data-testid="markdown-page"
              >
                <p
                  v-if="markdownData.page_count > 1"
                  class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"
                >
                  Page {{ page.page_number }}
                </p>
                <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in markdownPageHtml -->
                <div
                  class="doc-markdown text-gray-800 dark:text-gray-100"
                  data-testid="markdown-content"
                  v-html="markdownPageHtml(page.markdown)"
                />
                <!-- eslint-enable vue/no-v-html -->
              </div>
            </template>
          </div>
        </div>

        <DocumentSeriesTrend v-if="doc" :document-id="doc.id" />
      </div>

      <!-- Metadata: left column on desktop (lg:order-1). min-w-0 (as above)
           lets long metadata values wrap rather than widen the page. -->
      <div id="document-metadata-column" class="min-w-0 space-y-6 lg:order-1">
        <!-- Note-only controls: in-place note editing + version history. Shown
             only for notes (source === 'note'); the generic metadata editor
             below stays available for notes too. -->
        <div
          v-if="isNote"
          id="document-note-card"
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
        >
          <div class="mb-4 flex items-center justify-between gap-3">
            <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">Note</h2>
            <button
              v-if="!noteEditMode"
              type="button"
              class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300"
              data-testid="note-edit-button"
              @click="openNoteEditor"
            >
              Edit note
            </button>
          </div>

          <template v-if="noteEditMode">
            <div class="mb-4 flex justify-end">
              <div
                class="inline-flex rounded-lg border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-800 p-0.5"
                role="group"
                aria-label="Editor view"
              >
                <button
                  v-for="m in modes"
                  :key="m.value"
                  type="button"
                  :id="`note-edit-mode-${m.value}`"
                  :data-testid="`note-edit-mode-${m.value}`"
                  :aria-pressed="editorMode === m.value"
                  :aria-label="`${m.label} view`"
                  class="px-3 py-1 text-sm font-medium rounded-md transition"
                  :class="[
                    editorMode === m.value
                      ? 'bg-violet-500 text-white'
                      : 'text-gray-600 dark:text-gray-300 hover:text-gray-800 dark:hover:text-gray-100',
                    m.wideOnly ? 'hidden lg:inline-flex' : 'inline-flex',
                  ]"
                  @click="editorMode = m.value"
                >
                  {{ m.label }}
                </button>
              </div>
            </div>
            <div
              class="grid grid-cols-1 gap-4"
              :class="{ 'lg:grid-cols-2': editorMode === 'split' }"
            >
              <div v-if="showEditor" data-testid="note-edit-editor-pane">
                <AppTextarea
                  id="note-edit-body"
                  v-model="noteBodyDraft"
                  label="Note"
                  hint="The first line becomes the title. Markdown is supported."
                  :rows="12"
                  :error-message="noteEditError ?? undefined"
                />
              </div>
              <div v-if="showPreview" data-testid="note-edit-preview-pane">
                <span class="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Preview</span>
                <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in noteDraftPreviewHtml -->
                <div
                  class="doc-markdown form-textarea w-full min-h-40 overflow-auto text-gray-800 dark:text-gray-100"
                  data-testid="note-edit-preview"
                  v-html="noteDraftPreviewHtml"
                />
                <!-- eslint-enable vue/no-v-html -->
              </div>
            </div>
            <div class="mt-4 flex flex-wrap gap-3">
              <AppButton
                type="button"
                :disabled="!canSaveNote"
                data-testid="note-edit-save"
                @click="saveNote"
              >
                {{ noteSaving ? 'Saving…' : 'Save note' }}
              </AppButton>
              <AppButton
                type="button"
                variant="secondary"
                :disabled="noteSaving"
                data-testid="note-edit-cancel"
                @click="cancelNoteEdit"
              >
                Cancel
              </AppButton>
            </div>
          </template>

          <!-- Version history disclosure. -->
          <div data-testid="note-versions" class="mt-4 border-t border-gray-200 dark:border-gray-700/60 pt-4">
            <button
              type="button"
              class="text-sm font-medium text-violet-500 hover:underline"
              data-testid="note-versions-toggle"
              :aria-expanded="noteVersionsOpen"
              @click="toggleNoteVersions"
            >
              {{ noteVersionsOpen ? 'Hide version history' : 'Show version history' }}
            </button>
            <div v-if="noteVersionsOpen" class="mt-3">
              <p
                v-if="noteVersionsLoading"
                class="text-sm text-gray-500 dark:text-gray-400"
                data-testid="note-versions-loading"
              >
                Loading…
              </p>
              <p
                v-else-if="noteVersionsError"
                class="text-sm text-red-600 dark:text-red-400"
                data-testid="note-versions-error"
              >
                {{ noteVersionsError }}
              </p>
              <p
                v-else-if="noteVersions.length === 0"
                class="text-sm text-gray-500 dark:text-gray-400"
                data-testid="note-versions-empty"
              >
                No earlier versions yet.
              </p>
              <ul v-else class="divide-y divide-gray-200 dark:divide-gray-700/60">
                <li
                  v-for="version in noteVersions"
                  :key="version.version_no"
                  class="flex items-center justify-between gap-3 py-2"
                  :data-testid="`note-version-${version.version_no}`"
                >
                  <span class="min-w-0 text-sm text-gray-800 dark:text-gray-100">
                    Version {{ version.version_no }}
                    <span class="block text-xs text-gray-400 dark:text-gray-500">
                      {{ formatDateTime(version.created_at) }}
                    </span>
                  </span>
                  <button
                    type="button"
                    class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300 whitespace-nowrap"
                    :disabled="restoringVersion !== null"
                    :data-testid="`note-restore-${version.version_no}`"
                    @click="restoreVersion(version.version_no)"
                  >
                    {{ restoringVersion === version.version_no ? 'Restoring…' : 'Restore' }}
                  </button>
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div
          id="document-details-card"
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
        >
          <div class="mb-4 flex items-center justify-between gap-3">
            <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">Details</h2>
            <button
              type="button"
              class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300 gap-1.5"
              :class="editMode ? 'bg-violet-50 text-violet-700 border-violet-200 dark:bg-violet-500/15 dark:text-violet-300' : ''"
              data-testid="edit-toggle"
              :aria-pressed="editMode"
              @click="toggleEditMode"
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
                  d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125"
                />
              </svg>
              {{ editMode ? 'Done' : 'Edit' }}
            </button>
          </div>

          <div id="document-details-list" class="space-y-3">
            <!-- One themed panel per metadata group: accent rail + tint + heading
                 make each kind of metadata distinguishable at a glance, and the
                 two-column grid uses the width instead of one tall column. -->
            <!-- v-for on a wrapper template keeps the per-group key off the same
                 element as the section content. Every group renders in both read
                 and edit modes (value-less fields show an em-dash) so a field
                 keeps its position when the Edit toggle flips. -->
            <template v-for="group in fieldGroups" :key="group.key">
            <section
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
                <template v-for="field in group.fields" :key="field">
                <div
                  :data-testid="`row-${field}`"
                  :class="WIDE_FIELDS.has(field) ? 'sm:col-span-2' : ''"
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
                  <!-- Read mode: value only. The page-wide Edit toggle replaces the
                       old per-row "Change" buttons. -->
                  <!-- Projects render as badges that link to the project-filtered
                       dashboard, rather than a plain comma-joined string. -->
                  <dd
                    v-if="!editMode && field === 'projects' && doc.projects.length"
                    class="mt-2 flex flex-wrap gap-2"
                    data-testid="row-value"
                  >
                    <RouterLink
                      v-for="project in doc.projects"
                      :key="project.slug"
                      :to="{ path: '/', query: { project: project.slug } }"
                      data-testid="project-badge"
                      class="rounded-full"
                    >
                      <AppBadge :colour="tagColour(project.name)">{{ project.name }}</AppBadge>
                    </RouterLink>
                  </dd>
                  <dd
                    v-else-if="!editMode"
                    class="mt-2 min-w-0 break-words leading-snug text-gray-800 dark:text-gray-100"
                    :class="field === 'amount' ? 'text-2xl font-semibold tracking-tight' : 'text-base'"
                    data-testid="row-value"
                    >{{ rowByField[field].display(doc) ?? EMPTY }}</dd
                  >
                  <!-- Edit mode: an inline editor that autosaves on commit. Each
                       editor owns its trigger: native `change` (fires on blur for
                       text, on selection for selects) for single-value fields; a
                       fieldset-level `focusout` for the three-part date inputs, so
                       a date saves once when focus leaves the group rather than on
                       every sub-field commit. -->
                  <dd v-else class="mt-2">
                    <AppInput
                      v-if="field === 'title'"
                      id="edit-title"
                      v-model="drafts.title"
                      label="Title"
                      hide-label
                      :error-message="fieldError.title ?? undefined"
                      @change="saveField('title')"
                      @keyup.enter="saveField('title')"
                    />
                    <AppTextarea
                      v-else-if="field === 'summary'"
                      id="edit-summary"
                      v-model="drafts.summary"
                      label="Summary"
                      hide-label
                      :rows="4"
                      :error-message="fieldError.summary ?? undefined"
                      @change="saveField('summary')"
                    />
                    <template v-else-if="field === 'kind'">
                      <!-- Kind is a controlled list (dropdown). "Add kind…"
                           reveals an inline input + confirm instead of a
                           blocking prompt; confirming POSTs /api/kinds (which
                           dedupes and rejects near-duplicates), then selects and
                           autosaves the new slug. -->
                      <AppSelect
                        v-if="!kindAdding"
                        id="edit-kind"
                        v-model="drafts.kind"
                        label="Kind"
                        hide-label
                        :items="kindItems"
                        :error-message="fieldError.kind ?? undefined"
                        @change="onKindChange"
                      />
                      <div v-else>
                        <AppInput
                          id="kind-add-input"
                          v-model="kindNewName"
                          label="New kind"
                          hint="Type a kind name, then confirm to add it"
                          :error-message="fieldError.kind ?? undefined"
                          @keyup.enter="confirmAddKind"
                        />
                        <div class="mt-2 flex gap-2">
                          <button
                            type="button"
                            class="btn-sm border-violet-200 bg-violet-50 text-violet-700 hover:border-violet-300 dark:border-violet-500/40 dark:bg-violet-500/15 dark:text-violet-300"
                            data-testid="kind-add-confirm"
                            @click="confirmAddKind"
                          >
                            Add kind
                          </button>
                          <button
                            type="button"
                            class="btn-sm border-gray-200 text-gray-700 hover:border-gray-300 dark:border-gray-700/60 dark:text-gray-300"
                            data-testid="kind-add-cancel"
                            @click="cancelAddKind"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    </template>
                    <template v-else-if="field === 'sender'">
                      <AppInput
                        id="edit-sender"
                        v-model="drafts.sender"
                        label="Sender"
                        hide-label
                        hint="Start typing to see known senders"
                        list="sender-options"
                        :error-message="fieldError.sender ?? undefined"
                        @change="saveField('sender')"
                        @keyup.enter="saveField('sender')"
                      />
                      <datalist id="sender-options">
                        <option v-for="sender in senders" :key="sender.id" :value="sender.name" />
                      </datalist>
                    </template>
                    <template v-else-if="field === 'recipient'">
                      <!-- Recipient is a controlled list (dropdown). "Add
                           recipient…" reveals an inline input + confirm instead
                           of a blocking prompt; confirming autosaves the name,
                           which the backend upserts. -->
                      <AppSelect
                        v-if="!recipientAdding"
                        id="edit-recipient"
                        v-model="drafts.recipient"
                        label="Recipient"
                        hide-label
                        :items="recipientItems"
                        :error-message="fieldError.recipient ?? undefined"
                        @change="onRecipientChange"
                      />
                      <div v-else>
                        <AppInput
                          id="recipient-add-input"
                          v-model="recipientNewName"
                          label="New recipient"
                          hint="Type a name, then confirm to add it"
                          :error-message="fieldError.recipient ?? undefined"
                          @keyup.enter="confirmAddRecipient"
                        />
                        <div class="mt-2 flex gap-2">
                          <button
                            type="button"
                            class="btn-sm border-violet-200 bg-violet-50 text-violet-700 hover:border-violet-300 dark:border-violet-500/40 dark:bg-violet-500/15 dark:text-violet-300"
                            data-testid="recipient-add-confirm"
                            @click="confirmAddRecipient"
                          >
                            Add recipient
                          </button>
                          <button
                            type="button"
                            class="btn-sm border-gray-200 text-gray-700 hover:border-gray-300 dark:border-gray-700/60 dark:text-gray-300"
                            data-testid="recipient-add-cancel"
                            @click="cancelAddRecipient"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    </template>
                    <AppSelect
                      v-else-if="field === 'language'"
                      id="edit-language"
                      v-model="drafts.language"
                      label="Language"
                      hide-label
                      :items="languageItems"
                      :error-message="fieldError.language ?? undefined"
                      @change="saveField('language')"
                    />
                    <AppInput
                      v-else-if="field === 'tags'"
                      id="edit-tags"
                      v-model="drafts.tags"
                      label="Tags"
                      hide-label
                      hint="Separate tags with commas"
                      :error-message="fieldError.tags ?? undefined"
                      @change="saveField('tags')"
                      @keyup.enter="saveField('tags')"
                    />
                    <AppMultiSelect
                      v-else-if="field === 'projects'"
                      id="edit-projects"
                      v-model="projectsDraft"
                      label="Projects"
                      :options="projectOptionNames"
                      placeholder="Select or add a project…"
                      :error-message="fieldError.projects ?? undefined"
                      @change="saveField('projects')"
                    />
                    <div
                      v-else-if="field === 'amount'"
                      class="flex flex-wrap gap-3"
                      @change="saveField('amount')"
                    >
                      <AppInput
                        id="edit-amount"
                        v-model="drafts.amount"
                        label="Amount"
                        hide-label
                        inputmode="decimal"
                        width-class="w-40"
                        :error-message="fieldError.amount ?? undefined"
                        @keyup.enter="saveField('amount')"
                      />
                      <AppInput
                        id="edit-currency"
                        v-model="currencyDraft"
                        label="Currency"
                        hint="3-letter code, like EUR"
                        width-class="w-24"
                        @keyup.enter="saveField('amount')"
                      />
                    </div>
                    <AppDateInput
                      v-else-if="field === 'document_date'"
                      id="edit-document-date"
                      v-model="dateDrafts.document_date"
                      :legend="rowByField[field].label"
                      hide-legend
                      :error-message="fieldError.document_date ?? undefined"
                      @focusout="onDateFocusOut('document_date', $event)"
                    />
                    <AppDateInput
                      v-else-if="field === 'due_date'"
                      id="edit-due-date"
                      v-model="dateDrafts.due_date"
                      :legend="rowByField[field].label"
                      hide-legend
                      :error-message="fieldError.due_date ?? undefined"
                      @focusout="onDateFocusOut('due_date', $event)"
                    />
                    <AppDateInput
                      v-else
                      id="edit-expiry-date"
                      v-model="dateDrafts.expiry_date"
                      :legend="rowByField[field].label"
                      hide-legend
                      :error-message="fieldError.expiry_date ?? undefined"
                      @focusout="onDateFocusOut('expiry_date', $event)"
                    />
                    <p
                      v-if="savedField[field]"
                      class="mt-1 flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400"
                      :data-testid="`saved-${field}`"
                    >
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        viewBox="0 0 20 20"
                        fill="currentColor"
                        class="w-3.5 h-3.5"
                        aria-hidden="true"
                      >
                        <path
                          fill-rule="evenodd"
                          d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
                          clip-rule="evenodd"
                        />
                      </svg>
                      Saved
                    </p>
                  </dd>
                </div>
                </template>
              </dl>
            </section>
            </template>

            <!-- Topics: auto-extracted subject phrases, shown read-only as badge
                 pills (no editor). Rendered in both read and edit modes so the
                 System panel below keeps its position; hidden only when none. -->
            <section
              v-if="doc.topics.length"
              class="rounded-lg border-l-4 px-4 py-3.5"
              :class="[ACCENT.violet.border, ACCENT.violet.bg]"
            >
              <div class="mb-3 flex items-center gap-2">
                <span class="h-3.5 w-1 rounded-full" :class="ACCENT.violet.bar"></span>
                <h3
                  class="text-xs font-semibold uppercase tracking-wider"
                  :class="ACCENT.violet.text"
                >
                  Topics
                </h3>
              </div>
              <dl class="grid grid-cols-1">
                <div data-testid="row-topics">
                  <dt class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    Topics
                  </dt>
                  <dd class="mt-1 flex flex-wrap gap-2" data-testid="row-value">
                    <AppBadge
                      v-for="topic in doc.topics"
                      :key="topic"
                      :colour="tagColour(topic)"
                      data-testid="topic-badge"
                      >{{ topic }}</AppBadge
                    >
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
                  <dd
                    class="mt-1 text-base capitalize text-gray-800 dark:text-gray-100"
                    data-testid="status-value"
                  >{{ doc.status }}</dd>
                </div>
                <div>
                  <dt class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    OCR confidence
                  </dt>
                  <!-- Null confidence has two provenances: a born-digital upload
                       (Library found a text layer and skipped OCR), or a Paperless
                       import (Library reused Paperless's own OCR text — so a scanned
                       letter is "imported", not "born-digital"). -->
                  <dd
                    v-if="doc.ocr_confidence === null && doc.source === 'import'"
                    class="mt-1 text-base text-gray-500 dark:text-gray-400"
                    data-testid="ocr-confidence"
                  >
                    Imported (Paperless)
                    <span class="block text-xs text-gray-400 dark:text-gray-500">
                      text layer reused from Paperless — no OCR re-run
                    </span>
                  </dd>
                  <dd
                    v-else-if="doc.ocr_confidence === null"
                    class="mt-1 text-base text-gray-500 dark:text-gray-400"
                    data-testid="ocr-confidence"
                  >
                    Not applicable
                    <span class="block text-xs text-gray-400 dark:text-gray-500">
                      born-digital text — no OCR run
                    </span>
                  </dd>
                  <dd
                    v-else
                    class="mt-1 text-base text-gray-800 dark:text-gray-100"
                    data-testid="ocr-confidence"
                  >
                    {{ Math.round(doc.ocr_confidence) }}%
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
          <p v-if="doc.has_searchable_pdf" class="text-sm mb-2">
            <a
              class="text-violet-600 hover:underline"
              :href="searchablePdfUrl(doc.id)"
              data-testid="download-searchable"
            >
              Download the searchable PDF
            </a>
          </p>
          <p class="text-sm mb-4">
            <RouterLink
              class="text-violet-600 hover:underline"
              :to="`/jobs?document_id=${doc.id}`"
              data-testid="view-job-history"
            >
              View job history
            </RouterLink>
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
            <!-- Opens the Ask view in a new tab with the composer pre-filled to
                 name this document. AppButton has no `target`, so this is a
                 plain RouterLink styled to match the secondary buttons. -->
            <RouterLink
              :to="{ name: 'ask', query: { q: askPrompt } }"
              target="_blank"
              rel="noopener"
              data-testid="ask-about-document"
              class="btn border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-800 dark:text-gray-300"
            >
              Ask about this document
            </RouterLink>
            <AppButton
              variant="warning"
              :to="`/documents/${doc.id}/delete`"
              data-testid="delete-link"
            >
              Delete this document
            </AppButton>
          </div>
        </div>

        <DocumentHistoryTimeline :events="doc.events" />
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

<style scoped>
/* Markdown rendered via v-html; restore readable prose spacing stripped by
   Tailwind preflight (mirrors .ask-answer in AskView.vue). */
.doc-markdown :deep(p) {
  margin-bottom: 0.75rem;
}
.doc-markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.doc-markdown :deep(strong) {
  font-weight: 600;
}
.doc-markdown :deep(em) {
  font-style: italic;
}
.doc-markdown :deep(ul),
.doc-markdown :deep(ol) {
  margin: 0.5rem 0 0.75rem;
  padding-left: 1.5rem;
}
.doc-markdown :deep(ul) {
  list-style: disc;
}
.doc-markdown :deep(ol) {
  list-style: decimal;
}
.doc-markdown :deep(li) {
  margin-bottom: 0.25rem;
}
.doc-markdown :deep(h1),
.doc-markdown :deep(h2),
.doc-markdown :deep(h3) {
  font-weight: 600;
  margin: 0.75rem 0 0.5rem;
}
.doc-markdown :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.875em;
  padding: 0.1em 0.3em;
  border-radius: 0.25rem;
  background: rgb(0 0 0 / 0.06);
}
.dark .doc-markdown :deep(code) {
  background: rgb(255 255 255 / 0.08);
}
/* Fenced code blocks scroll horizontally inside the block rather than
   overflowing the card and the viewport. */
.doc-markdown :deep(pre) {
  margin: 0.75rem 0;
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  background: rgb(0 0 0 / 0.06);
  overflow-x: auto;
}
.dark .doc-markdown :deep(pre) {
  background: rgb(255 255 255 / 0.08);
}
.doc-markdown :deep(pre code) {
  padding: 0;
  background: none;
  white-space: pre;
}
</style>
