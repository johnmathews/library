<script setup lang="ts">
/**
 * The document "Details" card: grouped, themed metadata with a single page-wide
 * Edit toggle that reveals a per-field inline editor. Each field autosaves
 * independently on commit (native `change` on blur/selection, a fieldset-level
 * `focusout` for the three-part dates) via a per-field PATCH.
 *
 * Extracted from DocumentDetailView. The parent owns `doc` as the single source
 * of truth and binds `v-model:doc`; every save PATCHes one field and this editor
 * emits the server's fresh DocumentDetail back up so the hero/preview never
 * freeze on a pre-save snapshot. Drafts are re-hydrated ONLY when the shared
 * edit-mode flag flips on (via a `watch`, so this fires no matter which button —
 * this card's own toggle or the detail view's floating Action dock — flipped it) —
 * never on a prop change — so an external SSE refresh mid-edit never clobbers
 * in-progress drafts.
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  AppBadge,
  AppDateInput,
  AppDetails,
  AppInput,
  AppMultiSelect,
  AppSelect,
  AppTextarea,
} from '@/components/app'
import type { SelectItem } from '@/components/app'
import {
  DOCUMENT_LANGUAGES,
  updateDocument,
  type DocumentDetail,
  type DocumentLanguage,
  type DocumentUpdate,
  type ValidationFinding,
} from '@/api/documents'
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
import { useMetadataEditMode } from '@/composables/useMetadataEditMode'
import { ApiError } from '@/api/client'
import { formatDate, tagColour, formatDateTime } from '@/utils/documentFormat'

/** Which metadata section this tile renders. What used to be a single "Details"
 * card is now one instance per section (the detail view mounts five of them);
 * `system` is the read-only provenance tile and has no editable field group. */
type MetadataSection = 'content' | 'classification' | 'parties' | 'financial' | 'system'

const props = defineProps<{
  /** The document being edited (always non-null; the parent gates on `doc`). */
  doc: DocumentDetail
  /** The section this instance renders as its own standalone tile. */
  section: MetadataSection
}>()

const emit = defineEmits<{
  /** The server's fresh DocumentDetail after a per-field PATCH (parent binds `v-model:doc`). */
  (e: 'update:doc', doc: DocumentDetail): void
}>()

// --- Taxonomy options (kind select, sender autocomplete) ----------------------

const kinds = ref<KindOption[]>([])
const senders = ref<SenderOption[]>([])
const recipients = ref<RecipientOption[]>([])

// Existing projects feed the projects multiselect (picking an existing name or
// typing a new one, which the backend upserts on save).
const { projects: projectOptions, ensureLoaded: ensureProjectsLoaded } = useTaxonomyOptions()
// Only the Content tile renders the projects multiselect; the composable caches
// its fetch, so this is at most one request across the whole page regardless.
if (props.section === 'content') void ensureProjectsLoaded()
const projectOptionNames = computed(() => projectOptions.value.map((project) => project.name))

onMounted(async () => {
  // Only the tile that renders a given controlled-list editor fetches its
  // options: the page mounts one instance per section, so loading kinds in the
  // Classification tile and senders/recipients in the Sender-&-dates tile keeps
  // this to one request each instead of five. Best-effort — without options the
  // kind/recipient selects still offer the current value and "Not set", and the
  // sender input just loses its suggestions.
  if (props.section === 'classification') {
    const [kindResult] = await Promise.allSettled([listKinds()])
    if (kindResult.status === 'fulfilled') kinds.value = kindResult.value
  }
  if (props.section === 'parties') {
    const [senderResult, recipientResult] = await Promise.allSettled([
      listSenders(),
      listRecipients(),
    ])
    if (senderResult.status === 'fulfilled') senders.value = senderResult.value
    if (recipientResult.status === 'fulfilled') recipients.value = recipientResult.value
  }
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
  const current = props.doc.kind
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
  const current = props.doc.recipient
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

/** The field group this tile renders, wrapped as a 0-or-1 array so the template
 * can drive its per-field body with `v-for="group in activeGroups"` — the same
 * body the old single "Details" card used, now emitted for exactly one group.
 * Empty for the `system` tile (read-only provenance, rendered separately). */
const activeGroups = computed<FieldGroup[]>(() =>
  props.section === 'system' ? [] : fieldGroups.filter((group) => group.key === props.section),
)

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

function languageName(language: DocumentLanguage): string {
  return DOCUMENT_LANGUAGES.find((item) => item.value === language)?.text ?? language
}

function sourceLabel(source: string): string {
  return source.charAt(0).toUpperCase() + source.slice(1)
}

// --- Page-wide edit mode (per-field autosave) ---------------------------------
//
// A single page-wide "Edit" toggle (now in the detail view's hero, not on any
// one tile) reveals an inline editor for every field at once, replacing the old
// one-row-at-a-time "Change" reveal. Each field autosaves independently when
// committed — native `change` fires on blur for text inputs and on selection for
// selects/dates, and bubbles up to the field wrapper — via the same per-field
// PATCH the backend already expects. There is no global Save/Cancel; "Done"
// just leaves edit mode.

// `useMetadataEditMode` is a module singleton (mirroring `useDocumentLayout`'s
// `editMode`), so the hero toggle, every section tile, and the floating Action
// dock all read and flip ONE flag — flipping it must open these very editors,
// not an independent second mode. Ephemeral: the detail view resets it to false
// on unmount.
//
// Hydration/reset is driven by a `watch` below (not by whichever button flips
// the flag): the flag can flip from the hero toggle OR the Action dock, and
// with the Details card split across several tiles every mounted instance must
// hydrate fresh drafts before its editors render — otherwise a tile would open
// with stale/empty drafts and risk autosaving them.
const { editMode } = useMetadataEditMode()

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

/** Load every draft from the current document (called when edit mode opens). */
function hydrateDrafts(): void {
  const d = props.doc
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
 * canonicalised value (e.g. slugified tags) is reflected back in the editor.
 * Takes the fresh document explicitly: after emitting `update:doc` the parent's
 * prop hasn't propagated back down yet, so reading `props.doc` here would be stale. */
function hydrateField(field: EditableField, d: DocumentDetail): void {
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

/** Leave edit mode and clear the transient per-field error / "Saved" state. */
function resetEditState(): void {
  recipientAdding.value = false
  recipientNewName.value = ''
  kindAdding.value = false
  kindNewName.value = ''
  for (const key of Object.keys(fieldError)) fieldError[key] = null
  for (const key of Object.keys(savedField)) savedField[key] = false
}

// Hydrate on entry / reset on exit whenever the SHARED flag changes — fired
// for both this card's own toggle and the detail view's floating Action dock,
// since both flip the same singleton. Moving this out of `toggleEditMode`
// (which only the card's own button called) is what makes the Action dock's
// toggle hydrate fresh drafts too, instead of opening the editors with
// whatever stale/empty drafts happened to be left over.
watch(editMode, (on) => {
  if (on) hydrateDrafts()
  else resetEditState()
})

// A value-less section tile (e.g. Financial on a non-financial doc) is hidden in
// read mode and first mounts only once edit mode is already on — so the `watch`
// above never fires for it. Hydrate on mount in that case so its editors open
// from the document, not from empty initial drafts.
onMounted(() => {
  if (editMode.value) hydrateDrafts()
})

/** Whether a field's draft differs from the stored value — guards autosave so a
 * plain focus-through (no real edit) never fires a needless PATCH. */
function fieldDirty(field: EditableField): boolean {
  const d = props.doc
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
  if (savingField[field]) return
  fieldError[field] = null
  if (!fieldDirty(field)) return
  const patch = buildPatch(field)
  if (!patch) return
  savingField[field] = true
  try {
    const updated = await updateDocument(props.doc.id, patch)
    emit('update:doc', updated)
    hydrateField(field, updated)
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
    drafts.recipient = props.doc.recipient?.name ?? ''
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
    drafts.kind = props.doc.kind?.slug ?? ''
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
  const findings = props.doc.validation?.findings
  if (!findings?.length) return {}
  const result: Record<string, ValidationFinding[]> = {}
  for (const finding of findings) {
    if (finding.field === null || !(finding.field in STORAGE_TO_UI_FIELD)) continue
    const uiField = STORAGE_TO_UI_FIELD[finding.field] ?? finding.field
    ;(result[uiField] ??= []).push(finding)
  }
  return result
})

const latestExtractionEvent = computed(() => {
  return (
    props.doc.events.filter((event) => event.event.startsWith('extraction')).at(-1) ?? null
  )
})
</script>

<template>
  <!-- Each metadata section is its own reorderable tile (card). The detail view
       renders one instance per section via the `section` prop, so what used to
       be a single "Details" card is now a Content / Classification / Sender-&-
       dates / Financial / System tile that can be dragged and reordered
       independently. There is no per-tile Edit toggle: the single page-wide
       toggle lives in the hero and flips the shared `useMetadataEditMode` flag
       every instance reads. `activeGroups` holds this tile's one field group
       (empty for the read-only System tile, rendered separately below). -->
  <div
    v-for="group in activeGroups"
    :key="group.key"
    :id="`document-details-${group.key}`"
    class="card p-5"
    :data-testid="`metadata-section-${group.key}`"
  >
    <div class="min-w-0">
      <section>
        <div class="mb-4 flex items-center gap-2">
          <span class="h-4 w-1.5 rounded-full" :class="ACCENT[group.accent].bar"></span>
          <h2
            class="text-sm font-semibold uppercase tracking-wider"
            :class="ACCENT[group.accent].text"
          >
            {{ group.label }}
          </h2>
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
                  hide-label
                  placeholder="EUR"
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
        <!-- Topics: auto-extracted subject phrases, read-only badge pills. They
             describe what the document is about, so they live in the Content
             tile; hidden entirely when the document has none. -->
        <dl
          v-if="group.key === 'content' && doc.topics.length"
          class="mt-5 grid grid-cols-1 border-t border-gray-100 pt-4 dark:border-gray-700/60"
        >
          <div data-testid="row-topics">
            <dt class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
              Topics
            </dt>
            <dd class="mt-2 flex flex-wrap gap-2" data-testid="row-value">
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
    </div>
  </div>

  <!-- System tile: read-only provenance (status, OCR, source, extraction), a
       neutral accent, its own card. Always present, so it never collapses to an
       empty tile. -->
  <div
    v-if="section === 'system'"
    id="document-details-system"
    class="card p-5"
    data-testid="metadata-section-system"
  >
    <div class="min-w-0">
      <section>
        <div class="mb-4 flex items-center gap-2">
          <span class="h-4 w-1.5 rounded-full" :class="ACCENT.gray.bar"></span>
          <h2
            class="text-sm font-semibold uppercase tracking-wider"
            :class="ACCENT.gray.text"
          >
            System
          </h2>
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
</template>
