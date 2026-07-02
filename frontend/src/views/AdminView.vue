<script setup lang="ts">
/**
 * Admin console (route `/admin`, admin-only via the router guard).
 *
 * Tabs (in display order), each backed by an /api/admin/* endpoint:
 *   - Users: list every user, toggle admin/active, create and delete users.
 *   - Metadata: senders / recipients / kinds management.
 *   - Architecture: the project's architecture docs, markdown → sanitised HTML
 *     (same marked + DOMPurify pipeline as the note authoring/reader views).
 *   - Coverage: the latest CI-generated coverage figures per test type.
 *   - System: version/build, deployment topology, runtime config, DB stats.
 * Tab selection is local state (no sub-routes).
 */
import { computed, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { AppBadge, AppButton, AppInput, AppSelect } from '@/components/app'
import type { SelectItem } from '@/components/app'
import { ApiError } from '@/api/client'
import {
  createRecipient,
  createSender,
  createUser,
  deleteRecipient,
  deleteSender,
  deleteKind,
  deleteUser,
  getArchitecture,
  getCoverage,
  getSystemInfo,
  listCurrencies,
  listRecipients,
  listUsers,
  normalizeCurrency,
  renameKind,
  renameRecipient,
  renameSender,
  updateUser,
  type AdminUser,
  type ArchitectureDoc,
  type CoverageInfo,
  type CoverageSide,
  type CurrencyConflictItem,
  type CurrencyInUse,
  type CurrencyNormalizeResult,
  type RecipientOption,
  type SystemInfo,
} from '@/api/admin'
import {
  createKind,
  listKinds,
  listSenders,
  type KindOption,
  type SenderOption,
} from '@/api/taxonomy'
import { refreshTaxonomyOptions } from '@/composables/taxonomyOptions'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

type Tab = 'users' | 'metadata' | 'architecture' | 'coverage' | 'system'
const tab = ref<Tab>('users')

const TABS: { id: Tab; label: string }[] = [
  { id: 'users', label: 'Users' },
  { id: 'metadata', label: 'Metadata' },
  { id: 'architecture', label: 'Architecture' },
  { id: 'coverage', label: 'Coverage' },
  { id: 'system', label: 'System' },
]

// --- System -----------------------------------------------------------------

const system = ref<SystemInfo | null>(null)
const systemLoading = ref(true)
const systemError = ref<string | null>(null)

/** Config values can be any JSON; render objects as compact JSON, rest as text. */
function formatConfigValue(value: unknown): string {
  if (value === null) return 'null'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

const usdFormat = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
function formatUsd(amount: number): string {
  return usdFormat.format(amount)
}

const configEntries = computed(() => Object.entries(system.value?.config ?? {}))
const statusEntries = computed(() =>
  Object.entries(system.value?.stats.documents_by_status ?? {}),
)

async function loadSystem(): Promise<void> {
  systemLoading.value = true
  systemError.value = null
  try {
    system.value = await getSystemInfo()
  } catch {
    systemError.value = 'Could not load system information. Try refreshing the page.'
  } finally {
    systemLoading.value = false
  }
}

// --- Architecture -----------------------------------------------------------

const archDocs = ref<ArchitectureDoc[]>([])
const archLoading = ref(true)
const archError = ref<string | null>(null)
const selectedDocName = ref<string | null>(null)

const selectedDoc = computed<ArchitectureDoc | null>(
  () => archDocs.value.find((doc) => doc.name === selectedDocName.value) ?? null,
)

/** Sanitised HTML for the selected doc — identical pipeline to the note reader. */
const archHtml = computed(() =>
  selectedDoc.value
    ? DOMPurify.sanitize(marked.parse(selectedDoc.value.markdown, { async: false }) as string)
    : '',
)

async function loadArchitecture(): Promise<void> {
  archLoading.value = true
  archError.value = null
  try {
    const result = await getArchitecture()
    archDocs.value = result.docs
    selectedDocName.value = result.docs[0]?.name ?? null
  } catch {
    archError.value = 'Could not load architecture docs. Try refreshing the page.'
  } finally {
    archLoading.value = false
  }
}

// --- Coverage ---------------------------------------------------------------

const coverage = ref<CoverageInfo | null>(null)
const coverageLoading = ref(true)
const coverageError = ref<string | null>(null)

/** Render a percentage as "95.2%" or a dash when unmeasured. */
function formatPct(pct: number | null): string {
  return pct === null ? '—' : `${pct}%`
}

interface CoverageTypeView {
  key: string
  label: string
  runner: string
  description: string
  /** Line-coverage detail for the unit suites; null for e2e / compose-smoke. */
  side: CoverageSide | null
  /** Whether the side meets its gate; null when there is no line coverage. */
  passes: boolean | null
}

/**
 * Every CI test type, in pipeline order, each joined to its line-coverage side
 * when it has one (backend/frontend) — so the tab reflects all four kinds of
 * test CI runs, not just the two that produce coverage. Falls back to the two
 * known sides if an older baked summary has no `test_types` list.
 */
const coverageTypes = computed<CoverageTypeView[]>(() => {
  const c = coverage.value
  if (!c) return []
  const sideFor = (key: string): CoverageSide | null =>
    key === 'backend' ? c.backend : key === 'frontend' ? c.frontend : null
  const passesFor = (side: CoverageSide | null): boolean | null =>
    side && side.pct !== null && side.threshold !== null ? side.pct >= side.threshold : null
  const types =
    c.test_types.length > 0
      ? c.test_types
      : [
          { key: 'backend', label: 'Backend', runner: 'pytest', has_coverage: true, description: '' },
          {
            key: 'frontend',
            label: 'Frontend',
            runner: 'Vitest',
            has_coverage: true,
            description: '',
          },
        ]
  return types.map((t) => {
    const side = t.has_coverage ? sideFor(t.key) : null
    return {
      key: t.key,
      label: t.label,
      runner: t.runner,
      description: t.description,
      side,
      passes: passesFor(side),
    }
  })
})

async function loadCoverage(): Promise<void> {
  coverageLoading.value = true
  coverageError.value = null
  try {
    coverage.value = await getCoverage()
  } catch {
    coverageError.value = 'Could not load coverage data. Try refreshing the page.'
  } finally {
    coverageLoading.value = false
  }
}

// --- Users ------------------------------------------------------------------

const users = ref<AdminUser[]>([])
const usersLoading = ref(true)
const usersError = ref<string | null>(null)
// Per-row action error (e.g. the last-admin 409 guard), keyed by user id.
const rowError = ref<Record<number, string>>({})
// Ids with an action in flight, to disable their buttons.
const pendingIds = ref<Set<number>>(new Set())

const dateFormat = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium' })
function formatDate(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormat.format(parsed)
}

function isCurrentUser(user: AdminUser): boolean {
  return auth.user?.id === user.id
}

async function loadUsers(): Promise<void> {
  usersLoading.value = true
  usersError.value = null
  try {
    users.value = await listUsers()
  } catch {
    usersError.value = 'Could not load users. Try refreshing the page.'
  } finally {
    usersLoading.value = false
  }
}

/** Apply a flag change to one user, surfacing a 409 (or other) error in-row. */
async function patchUser(
  user: AdminUser,
  body: { is_admin?: boolean; is_active?: boolean },
): Promise<void> {
  const next = new Set(pendingIds.value)
  next.add(user.id)
  pendingIds.value = next
  const nextErrors = { ...rowError.value }
  delete nextErrors[user.id]
  rowError.value = nextErrors
  try {
    const updated = await updateUser(user.id, body)
    users.value = users.value.map((u) => (u.id === updated.id ? updated : u))
  } catch (error) {
    rowError.value = {
      ...rowError.value,
      [user.id]:
        error instanceof ApiError ? error.detail : 'Could not update the user. Try again.',
    }
  } finally {
    const after = new Set(pendingIds.value)
    after.delete(user.id)
    pendingIds.value = after
  }
}

function toggleAdmin(user: AdminUser): void {
  void patchUser(user, { is_admin: !user.is_admin })
}

function toggleActive(user: AdminUser): void {
  void patchUser(user, { is_active: !user.is_active })
}

// Inline two-step delete: the id of the user whose Delete button was armed (so
// a second click confirms), rather than a native blocking confirm() dialog.
const confirmingDeleteId = ref<number | null>(null)

function requestDeleteUser(user: AdminUser): void {
  confirmingDeleteId.value = user.id
}

function cancelDeleteUser(): void {
  confirmingDeleteId.value = null
}

/** Delete a user, surfacing a guard error (400 self / 409 last-admin) in-row. */
async function confirmDeleteUser(user: AdminUser): Promise<void> {
  const next = new Set(pendingIds.value)
  next.add(user.id)
  pendingIds.value = next
  const nextErrors = { ...rowError.value }
  delete nextErrors[user.id]
  rowError.value = nextErrors
  try {
    await deleteUser(user.id)
    users.value = users.value.filter((u) => u.id !== user.id)
    confirmingDeleteId.value = null
  } catch (error) {
    rowError.value = {
      ...rowError.value,
      [user.id]:
        error instanceof ApiError ? error.detail : 'Could not delete the user. Try again.',
    }
  } finally {
    const after = new Set(pendingIds.value)
    after.delete(user.id)
    pendingIds.value = after
  }
}

// --- Create-user form -------------------------------------------------------

const newUsername = ref('')
const newPassword = ref('')
const newDisplayName = ref('')
const newIsAdmin = ref(false)
const creating = ref(false)
const createError = ref<string | null>(null)

const canCreate = computed(
  () => newUsername.value.trim() !== '' && newPassword.value !== '' && !creating.value,
)

async function onCreateUser(): Promise<void> {
  if (!canCreate.value) return
  creating.value = true
  createError.value = null
  try {
    await createUser({
      username: newUsername.value.trim(),
      password: newPassword.value,
      display_name: newDisplayName.value.trim() || undefined,
      is_admin: newIsAdmin.value,
    })
    newUsername.value = ''
    newPassword.value = ''
    newDisplayName.value = ''
    newIsAdmin.value = false
    await loadUsers()
  } catch (error) {
    createError.value =
      error instanceof ApiError ? error.detail : 'Could not create the user. Try again.'
  } finally {
    creating.value = false
  }
}

// --- Metadata: recipients ---------------------------------------------------

const recipients = ref<RecipientOption[]>([])
const recipientsLoading = ref(false)
const recipientsLoaded = ref(false)
const recipientsError = ref<string | null>(null)
// Per-row action state, keyed by recipient id (mirrors the users panel).
const recipientPendingIds = ref<Set<number>>(new Set())
const recipientRowError = ref<Record<number, string>>({})
// Inline rename state: at most one row in edit mode at a time.
const renameId = ref<number | null>(null)
const renameValue = ref('')
// When a rename hits a 409, the proposed merge target for the editing row.
const mergeTarget = ref<{
  target_id: number
  target_name: string
  target_document_count: number
} | null>(null)
// Inline delete state: at most one row in confirm mode at a time.
const deleteId = ref<number | null>(null)
// Selected reassign target: '' means "None (clear)", otherwise a recipient id.
const reassignValue = ref('')

function setRecipientPending(id: number, pending: boolean): void {
  const next = new Set(recipientPendingIds.value)
  if (pending) next.add(id)
  else next.delete(id)
  recipientPendingIds.value = next
}

function setRecipientError(id: number, message: string | null): void {
  const next = { ...recipientRowError.value }
  if (message) next[id] = message
  else delete next[id]
  recipientRowError.value = next
}

/** Reassign options for a row: every other recipient, plus "None (clear)". */
function reassignItems(row: RecipientOption): SelectItem[] {
  const others = recipients.value
    .filter((r) => r.id !== row.id)
    .map((r) => ({ value: String(r.id), text: `${r.name} (${r.document_count})` }))
  return [{ value: '', text: 'None (clear recipient)' }, ...others]
}

async function loadRecipients(): Promise<void> {
  recipientsLoading.value = true
  recipientsError.value = null
  try {
    recipients.value = await listRecipients()
    recipientsLoaded.value = true
  } catch {
    recipientsError.value = 'Could not load recipients. Try refreshing the page.'
  } finally {
    recipientsLoading.value = false
  }
}

/** After any successful mutation: reload the panel and the shared taxonomy cache
 * so document dropdowns/filters elsewhere reflect the change. */
async function afterRecipientMutation(): Promise<void> {
  await loadRecipients()
  void refreshTaxonomyOptions()
}

function startRename(row: RecipientOption): void {
  cancelDelete()
  renameId.value = row.id
  renameValue.value = row.name
  mergeTarget.value = null
  setRecipientError(row.id, null)
}

function cancelRename(): void {
  renameId.value = null
  renameValue.value = ''
  mergeTarget.value = null
}

/** Save a rename. On a 409 collision (without `merge`), reveal the merge prompt
 * instead of erroring; the merge-confirm button re-calls with `merge: true`. */
async function saveRename(row: RecipientOption, merge = false): Promise<void> {
  const name = renameValue.value.trim()
  if (!name) {
    setRecipientError(row.id, 'Enter a name.')
    return
  }
  setRecipientPending(row.id, true)
  setRecipientError(row.id, null)
  try {
    await renameRecipient(row.id, name, merge)
    cancelRename()
    await afterRecipientMutation()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && error.body && !merge) {
      mergeTarget.value = {
        target_id: Number(error.body.target_id),
        target_name: String(error.body.target_name),
        target_document_count: Number(error.body.target_document_count),
      }
    } else {
      setRecipientError(
        row.id,
        error instanceof ApiError ? error.detail : 'Could not rename the recipient. Try again.',
      )
    }
  } finally {
    setRecipientPending(row.id, false)
  }
}

function startDelete(row: RecipientOption): void {
  cancelRename()
  deleteId.value = row.id
  reassignValue.value = ''
  setRecipientError(row.id, null)
}

function cancelDelete(): void {
  deleteId.value = null
  reassignValue.value = ''
}

/** Confirm a delete. In-use recipients reassign (to the chosen id, or null to
 * clear); zero-document recipients delete outright. */
async function confirmDelete(row: RecipientOption): Promise<void> {
  setRecipientPending(row.id, true)
  setRecipientError(row.id, null)
  try {
    if (row.document_count > 0) {
      const chosen = reassignValue.value === '' ? null : Number(reassignValue.value)
      await deleteRecipient(row.id, chosen)
    } else {
      await deleteRecipient(row.id)
    }
    cancelDelete()
    await afterRecipientMutation()
  } catch (error) {
    setRecipientError(
      row.id,
      error instanceof ApiError ? error.detail : 'Could not delete the recipient. Try again.',
    )
  } finally {
    setRecipientPending(row.id, false)
  }
}

// Create-recipient control (above the list).
const recipientCreateValue = ref('')
const recipientCreating = ref(false)
const recipientCreateError = ref<string | null>(null)

/** Create a recipient (200 for an existing case-insensitive match, 201 for new
 * — both are success). Reload the panel and refresh the shared taxonomy cache. */
async function onCreateRecipient(): Promise<void> {
  const name = recipientCreateValue.value.trim()
  if (!name || recipientCreating.value) return
  recipientCreating.value = true
  recipientCreateError.value = null
  try {
    await createRecipient(name)
    recipientCreateValue.value = ''
    await afterRecipientMutation()
  } catch (error) {
    recipientCreateError.value =
      error instanceof ApiError ? error.detail : 'Could not create the recipient. Try again.'
  } finally {
    recipientCreating.value = false
  }
}

// --- Metadata: senders ------------------------------------------------------
// Same id-keyed rename/merge + delete-with-reassign contract as recipients.

const senders = ref<SenderOption[]>([])
const sendersLoading = ref(false)
const sendersLoaded = ref(false)
const sendersError = ref<string | null>(null)
const senderPendingIds = ref<Set<number>>(new Set())
const senderRowError = ref<Record<number, string>>({})
const senderRenameId = ref<number | null>(null)
const senderRenameValue = ref('')
const senderMergeTarget = ref<{
  target_id: number
  target_name: string
  target_document_count: number
} | null>(null)
const senderDeleteId = ref<number | null>(null)
const senderReassignValue = ref('')

function setSenderPending(id: number, pending: boolean): void {
  const next = new Set(senderPendingIds.value)
  if (pending) next.add(id)
  else next.delete(id)
  senderPendingIds.value = next
}

function setSenderError(id: number, message: string | null): void {
  const next = { ...senderRowError.value }
  if (message) next[id] = message
  else delete next[id]
  senderRowError.value = next
}

/** Reassign options for a row: every other sender, plus "None (clear)". */
function senderReassignItems(row: SenderOption): SelectItem[] {
  const others = senders.value
    .filter((s) => s.id !== row.id)
    .map((s) => ({ value: String(s.id), text: `${s.name} (${s.document_count})` }))
  return [{ value: '', text: 'None (clear sender)' }, ...others]
}

async function loadSenders(): Promise<void> {
  sendersLoading.value = true
  sendersError.value = null
  try {
    senders.value = await listSenders()
    sendersLoaded.value = true
  } catch {
    sendersError.value = 'Could not load senders. Try refreshing the page.'
  } finally {
    sendersLoading.value = false
  }
}

async function afterSenderMutation(): Promise<void> {
  await loadSenders()
  void refreshTaxonomyOptions()
}

function startSenderRename(row: SenderOption): void {
  cancelSenderDelete()
  senderRenameId.value = row.id
  senderRenameValue.value = row.name
  senderMergeTarget.value = null
  setSenderError(row.id, null)
}

function cancelSenderRename(): void {
  senderRenameId.value = null
  senderRenameValue.value = ''
  senderMergeTarget.value = null
}

/** Save a rename. On a 409 collision (without `merge`), reveal the merge prompt
 * instead of erroring; the merge-confirm button re-calls with `merge: true`. */
async function saveSenderRename(row: SenderOption, merge = false): Promise<void> {
  const name = senderRenameValue.value.trim()
  if (!name) {
    setSenderError(row.id, 'Enter a name.')
    return
  }
  setSenderPending(row.id, true)
  setSenderError(row.id, null)
  try {
    await renameSender(row.id, name, merge)
    cancelSenderRename()
    await afterSenderMutation()
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && error.body && !merge) {
      senderMergeTarget.value = {
        target_id: Number(error.body.target_id),
        target_name: String(error.body.target_name),
        target_document_count: Number(error.body.target_document_count),
      }
    } else {
      setSenderError(
        row.id,
        error instanceof ApiError ? error.detail : 'Could not rename the sender. Try again.',
      )
    }
  } finally {
    setSenderPending(row.id, false)
  }
}

function startSenderDelete(row: SenderOption): void {
  cancelSenderRename()
  senderDeleteId.value = row.id
  senderReassignValue.value = ''
  setSenderError(row.id, null)
}

function cancelSenderDelete(): void {
  senderDeleteId.value = null
  senderReassignValue.value = ''
}

/** Confirm a delete. In-use senders reassign (to the chosen id, or null to
 * clear); zero-document senders delete outright. */
async function confirmSenderDelete(row: SenderOption): Promise<void> {
  setSenderPending(row.id, true)
  setSenderError(row.id, null)
  try {
    if (row.document_count > 0) {
      const chosen = senderReassignValue.value === '' ? null : Number(senderReassignValue.value)
      await deleteSender(row.id, chosen)
    } else {
      await deleteSender(row.id)
    }
    cancelSenderDelete()
    await afterSenderMutation()
  } catch (error) {
    setSenderError(
      row.id,
      error instanceof ApiError ? error.detail : 'Could not delete the sender. Try again.',
    )
  } finally {
    setSenderPending(row.id, false)
  }
}

// Create-sender control (above the list).
const senderCreateValue = ref('')
const senderCreating = ref(false)
const senderCreateError = ref<string | null>(null)

async function onCreateSender(): Promise<void> {
  const name = senderCreateValue.value.trim()
  if (!name || senderCreating.value) return
  senderCreating.value = true
  senderCreateError.value = null
  try {
    await createSender(name)
    senderCreateValue.value = ''
    await afterSenderMutation()
  } catch (error) {
    senderCreateError.value =
      error instanceof ApiError ? error.detail : 'Could not create the sender. Try again.'
  } finally {
    senderCreating.value = false
  }
}

// --- Metadata: kinds --------------------------------------------------------
// Kinds are slug-keyed: rename edits the display name only (no merge — a name
// collision is a hard 409), and delete reassigns to another kind by slug.

const kinds = ref<KindOption[]>([])
const kindsLoading = ref(false)
const kindsLoaded = ref(false)
const kindsError = ref<string | null>(null)
const kindPendingSlugs = ref<Set<string>>(new Set())
const kindRowError = ref<Record<string, string>>({})
const kindRenameSlug = ref<string | null>(null)
const kindRenameValue = ref('')
const kindDeleteSlug = ref<string | null>(null)
const kindReassignValue = ref('')

function setKindPending(slug: string, pending: boolean): void {
  const next = new Set(kindPendingSlugs.value)
  if (pending) next.add(slug)
  else next.delete(slug)
  kindPendingSlugs.value = next
}

function setKindError(slug: string, message: string | null): void {
  const next = { ...kindRowError.value }
  if (message) next[slug] = message
  else delete next[slug]
  kindRowError.value = next
}

/** Reassign options for a row: every other kind (by slug), plus "None (clear)". */
function kindReassignItems(row: KindOption): SelectItem[] {
  const others = kinds.value
    .filter((k) => k.slug !== row.slug)
    .map((k) => ({ value: k.slug, text: `${k.name} (${k.document_count})` }))
  return [{ value: '', text: 'None (clear kind)' }, ...others]
}

async function loadKinds(): Promise<void> {
  kindsLoading.value = true
  kindsError.value = null
  try {
    kinds.value = await listKinds()
    kindsLoaded.value = true
  } catch {
    kindsError.value = 'Could not load kinds. Try refreshing the page.'
  } finally {
    kindsLoading.value = false
  }
}

async function afterKindMutation(): Promise<void> {
  await loadKinds()
  void refreshTaxonomyOptions()
}

function startKindRename(row: KindOption): void {
  cancelKindDelete()
  kindRenameSlug.value = row.slug
  kindRenameValue.value = row.name
  setKindError(row.slug, null)
}

function cancelKindRename(): void {
  kindRenameSlug.value = null
  kindRenameValue.value = ''
}

/** Save a name-only rename. A 409 name collision surfaces as a row error —
 * there is no kind merge. */
async function saveKindRename(row: KindOption): Promise<void> {
  const name = kindRenameValue.value.trim()
  if (!name) {
    setKindError(row.slug, 'Enter a name.')
    return
  }
  setKindPending(row.slug, true)
  setKindError(row.slug, null)
  try {
    await renameKind(row.slug, name)
    cancelKindRename()
    await afterKindMutation()
  } catch (error) {
    setKindError(
      row.slug,
      error instanceof ApiError ? error.detail : 'Could not rename the kind. Try again.',
    )
  } finally {
    setKindPending(row.slug, false)
  }
}

function startKindDelete(row: KindOption): void {
  cancelKindRename()
  kindDeleteSlug.value = row.slug
  kindReassignValue.value = ''
  setKindError(row.slug, null)
}

function cancelKindDelete(): void {
  kindDeleteSlug.value = null
  kindReassignValue.value = ''
}

/** Confirm a delete. In-use kinds reassign by slug (or null to clear);
 * zero-document kinds delete outright. */
async function confirmKindDelete(row: KindOption): Promise<void> {
  setKindPending(row.slug, true)
  setKindError(row.slug, null)
  try {
    if (row.document_count > 0) {
      const chosen = kindReassignValue.value === '' ? null : kindReassignValue.value
      await deleteKind(row.slug, chosen)
    } else {
      await deleteKind(row.slug)
    }
    cancelKindDelete()
    await afterKindMutation()
  } catch (error) {
    setKindError(
      row.slug,
      error instanceof ApiError ? error.detail : 'Could not delete the kind. Try again.',
    )
  } finally {
    setKindPending(row.slug, false)
  }
}

// Create-kind control (above the list). `createKind` 409s on a near-duplicate;
// surface the conflict detail.
const kindCreateValue = ref('')
const kindCreating = ref(false)
const kindCreateError = ref<string | null>(null)

async function onCreateKind(): Promise<void> {
  const name = kindCreateValue.value.trim()
  if (!name || kindCreating.value) return
  kindCreating.value = true
  kindCreateError.value = null
  try {
    await createKind(name)
    kindCreateValue.value = ''
    await afterKindMutation()
  } catch (error) {
    kindCreateError.value =
      error instanceof ApiError ? error.detail : 'Could not create the kind. Try again.'
  } finally {
    kindCreating.value = false
  }
}

// --- Metadata: currencies ---------------------------------------------------
// Currency is free-text (no reference table) but part of series identity, so
// "normalise" is a whole-store rewrite, not a per-row edit (see docs/api.md
// §1.18.6). There is a confirm step because the series-insight cache merge drops
// rows (they regenerate) and the rename spans every document.
const currencies = ref<CurrencyInUse[]>([])
const currenciesLoading = ref(false)
const currenciesLoaded = ref(false)
const currenciesError = ref<string | null>(null)

const normalizeFrom = ref('')
const normalizeTo = ref('')
const normalizeConfirming = ref(false)
const normalizePending = ref(false)
const normalizeError = ref<string | null>(null)
const normalizeConflicts = ref<CurrencyConflictItem[]>([])
const normalizeResult = ref<CurrencyNormalizeResult | null>(null)

const currencyItems = computed<SelectItem[]>(() => [
  { value: '', text: 'Select a code…' },
  ...currencies.value.map((c) => ({ value: c.code, text: `${c.code} (${c.document_count})` })),
])

async function loadCurrencies(): Promise<void> {
  currenciesLoading.value = true
  currenciesError.value = null
  try {
    currencies.value = await listCurrencies()
    currenciesLoaded.value = true
  } catch {
    currenciesError.value = 'Could not load currencies. Try refreshing the page.'
  } finally {
    currenciesLoading.value = false
  }
}

function startNormalize(): void {
  normalizeError.value = null
  normalizeConflicts.value = []
  normalizeResult.value = null
  if (!normalizeFrom.value || !normalizeTo.value.trim()) {
    normalizeError.value = 'Choose a source code and enter a 3-letter target code.'
    return
  }
  normalizeConfirming.value = true
}

function cancelNormalize(): void {
  normalizeConfirming.value = false
}

async function confirmNormalize(): Promise<void> {
  normalizePending.value = true
  normalizeError.value = null
  normalizeConflicts.value = []
  try {
    const result = await normalizeCurrency(normalizeFrom.value, normalizeTo.value.trim())
    normalizeResult.value = result
    normalizeConfirming.value = false
    normalizeFrom.value = ''
    normalizeTo.value = ''
    await loadCurrencies()
  } catch (error) {
    normalizeConfirming.value = false
    if (error instanceof ApiError && error.status === 409 && error.body) {
      normalizeConflicts.value = (error.body.conflicts as CurrencyConflictItem[]) ?? []
      normalizeError.value = error.detail
    } else {
      normalizeError.value =
        error instanceof ApiError ? error.detail : 'Could not normalise the currency. Try again.'
    }
  } finally {
    normalizePending.value = false
  }
}

// Lazily load the metadata lists the first time the Metadata tab is opened.
watch(tab, (current) => {
  if (current !== 'metadata') return
  if (!recipientsLoaded.value && !recipientsLoading.value) void loadRecipients()
  if (!sendersLoaded.value && !sendersLoading.value) void loadSenders()
  if (!kindsLoaded.value && !kindsLoading.value) void loadKinds()
  if (!currenciesLoaded.value && !currenciesLoading.value) void loadCurrencies()
})

onMounted(() => {
  void loadSystem()
  void loadArchitecture()
  void loadCoverage()
  void loadUsers()
})

const cardClass =
  'bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-6'
const tabClass = (active: boolean): string =>
  [
    'px-4 py-2 -mb-px text-sm font-medium border-b-2 transition cursor-pointer',
    active
      ? 'border-violet-500 text-violet-600'
      : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200',
  ].join(' ')
</script>

<template>
  <div id="admin-page" class="max-w-4xl">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-4">Admin</h1>

    <div
      role="tablist"
      class="flex gap-1 border-b border-gray-200 dark:border-gray-700/60 mb-6"
    >
      <button
        v-for="t in TABS"
        :key="t.id"
        role="tab"
        type="button"
        :aria-selected="tab === t.id"
        :tabindex="tab === t.id ? 0 : -1"
        :class="tabClass(tab === t.id)"
        :data-testid="`admin-tab-${t.id}-btn`"
        @click="tab = t.id"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- System tab -->
    <section v-show="tab === 'system'" role="tabpanel" data-testid="admin-tab-system">
      <p v-if="systemLoading" data-testid="system-loading" class="text-gray-600 dark:text-gray-300">
        Loading system information…
      </p>
      <div
        v-else-if="systemError"
        data-testid="system-error"
        role="alert"
        class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        {{ systemError }}
      </div>
      <div v-else-if="system" class="space-y-6">
        <!-- Build + deployment -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Build</h2>
          <dl class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Version</dt>
              <dd class="text-gray-800 dark:text-gray-100 font-medium" data-testid="system-version">
                {{ system.version }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Git SHA</dt>
              <dd
                class="text-gray-800 dark:text-gray-100 font-mono break-all"
                data-testid="system-git-sha"
              >
                {{ system.git_sha ?? '—' }}
              </dd>
            </div>
          </dl>

          <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-200 mt-5 mb-2">
            Deployment
          </h3>
          <ul class="divide-y divide-gray-100 dark:divide-gray-700/60" data-testid="system-deployment">
            <li
              v-for="svc in system.deployment"
              :key="svc.name"
              class="flex items-center justify-between py-2 text-sm"
              data-testid="system-deployment-row"
            >
              <span class="text-gray-800 dark:text-gray-100 font-medium">{{ svc.name }}</span>
              <AppBadge colour="blue">{{ svc.role }}</AppBadge>
            </li>
          </ul>
        </div>

        <!-- Stats -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Statistics</h2>
          <dl class="grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Documents</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-documents-total">
                {{ system.stats.documents_total }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Deleted</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-documents-deleted">
                {{ system.stats.documents_deleted }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Users (active)</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-users">
                {{ system.stats.users_active }} / {{ system.stats.users_total }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Jobs (active)</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-jobs">
                {{ system.stats.jobs_active }} / {{ system.stats.jobs_total }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Extraction cost</dt>
              <dd class="text-2xl font-bold text-gray-800 dark:text-gray-100" data-testid="stat-cost">
                {{ formatUsd(system.stats.extraction_cost_usd_total) }}
              </dd>
            </div>
          </dl>

          <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-200 mt-5 mb-2">
            Documents by status
          </h3>
          <ul class="flex flex-wrap gap-2" data-testid="stat-by-status">
            <li
              v-for="[status, count] in statusEntries"
              :key="status"
              class="inline-flex items-center gap-1.5 rounded-full bg-gray-100 dark:bg-gray-700/40 px-3 py-1 text-sm"
            >
              <span class="text-gray-600 dark:text-gray-300">{{ status }}</span>
              <span class="font-semibold text-gray-800 dark:text-gray-100">{{ count }}</span>
            </li>
          </ul>
        </div>

        <!-- Config -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Configuration</h2>
          <!-- Key above value (not a 2-col table): long keys and values both get
               the full width and wrap cleanly instead of cramping on a phone. -->
          <dl class="divide-y divide-gray-100 dark:divide-gray-700/60" data-testid="system-config">
            <div v-for="[key, value] in configEntries" :key="key" data-testid="system-config-row" class="py-2.5">
              <dt class="text-xs font-medium text-gray-500 dark:text-gray-400">{{ key }}</dt>
              <dd class="mt-0.5 text-sm text-gray-800 dark:text-gray-100 font-mono break-words">
                {{ formatConfigValue(value) }}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </section>

    <!-- Architecture tab -->
    <section v-show="tab === 'architecture'" role="tabpanel" data-testid="admin-tab-architecture">
      <p v-if="archLoading" data-testid="arch-loading" class="text-gray-600 dark:text-gray-300">
        Loading architecture docs…
      </p>
      <div
        v-else-if="archError"
        data-testid="arch-error"
        role="alert"
        class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        {{ archError }}
      </div>
      <p
        v-else-if="archDocs.length === 0"
        data-testid="arch-empty"
        class="text-sm text-gray-500 dark:text-gray-400"
      >
        No architecture documents are available.
      </p>
      <div v-else class="space-y-4">
        <div class="flex flex-wrap gap-2" data-testid="arch-doc-list">
          <button
            v-for="doc in archDocs"
            :key="doc.name"
            type="button"
            :data-testid="`arch-doc-${doc.name}`"
            :class="[
              'rounded-lg border px-3 py-1.5 text-sm font-medium transition cursor-pointer',
              selectedDocName === doc.name
                ? 'border-violet-500 ring-2 ring-violet-500/30 text-violet-600 dark:text-violet-300'
                : 'border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-200 hover:border-gray-300 dark:hover:border-gray-600',
            ]"
            @click="selectedDocName = doc.name"
          >
            {{ doc.title }}
          </button>
        </div>

        <div v-if="selectedDoc" :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">
            {{ selectedDoc.title }}
          </h2>
          <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in archHtml -->
          <div
            class="doc-markdown text-gray-800 dark:text-gray-100"
            data-testid="arch-content"
            v-html="archHtml"
          />
        </div>
      </div>
    </section>

    <!-- Coverage tab -->
    <section v-show="tab === 'coverage'" role="tabpanel" data-testid="admin-tab-coverage">
      <p v-if="coverageLoading" data-testid="coverage-loading" class="text-gray-600 dark:text-gray-300">
        Loading coverage data…
      </p>
      <div
        v-else-if="coverageError"
        data-testid="coverage-error"
        role="alert"
        class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        {{ coverageError }}
      </div>
      <div
        v-else-if="coverage && !coverage.available"
        data-testid="coverage-unavailable"
        class="bg-white dark:bg-gray-800 border-l-4 border-yellow-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        Coverage data unavailable (generated by CI).
      </div>
      <div v-else-if="coverage" class="space-y-6">
        <!-- One card per CI test type (.github/workflows/ci.yml), in pipeline
             order. The two unit suites show headline %, gate pass/fail, file
             counts and the lowest-covered files; e2e / compose-smoke have no
             line coverage and show what they exercise + that they gate in CI. -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <div
            v-for="t in coverageTypes"
            :key="t.key"
            :class="cardClass"
            :data-testid="`coverage-card-${t.key}`"
          >
            <div class="flex items-center justify-between gap-3 mb-1">
              <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">{{ t.label }}</h2>
              <AppBadge v-if="t.passes !== null" :colour="t.passes ? 'green' : 'red'">
                {{ t.passes ? 'Pass' : 'Below gate' }}
              </AppBadge>
              <AppBadge v-else colour="grey">CI gate</AppBadge>
            </div>
            <p class="text-xs text-gray-500 dark:text-gray-400 mb-3">{{ t.runner }}</p>

            <template v-if="t.side">
              <p
                class="text-3xl font-bold text-gray-800 dark:text-gray-100"
                :data-testid="`coverage-${t.key}`"
              >
                {{ formatPct(t.side.pct) }}
              </p>
              <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
                <span v-if="t.side.threshold != null">Gate {{ t.side.threshold }}%</span>
                <span v-if="t.side.files_total != null"> · {{ t.side.files_total }} files</span>
                <span v-if="t.side.files_below_gate != null">
                  · {{ t.side.files_below_gate }} below gate
                </span>
              </p>

              <div v-if="t.side.worst_files.length" class="mt-4">
                <h3 class="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
                  Lowest-covered files
                </h3>
                <ul class="divide-y divide-gray-100 dark:divide-gray-700/60">
                  <li
                    v-for="f in t.side.worst_files"
                    :key="f.path"
                    class="flex items-center justify-between gap-3 py-1.5 text-sm"
                  >
                    <span class="min-w-0 truncate font-mono text-xs text-gray-600 dark:text-gray-300">
                      {{ f.path }}
                    </span>
                    <span
                      class="shrink-0 font-semibold"
                      :class="
                        t.side.threshold != null && f.pct < t.side.threshold
                          ? 'text-red-600 dark:text-red-400'
                          : 'text-gray-800 dark:text-gray-100'
                      "
                    >
                      {{ formatPct(f.pct) }}
                    </span>
                  </li>
                </ul>
              </div>
            </template>

            <!-- e2e / compose-smoke: no line coverage, just what they cover. -->
            <p
              v-else
              class="text-sm text-gray-600 dark:text-gray-300"
              :data-testid="`coverage-note-${t.key}`"
            >
              {{ t.description }}
            </p>
          </div>
        </div>

        <!-- When + which build produced these numbers. -->
        <div :class="cardClass">
          <dl class="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Generated</dt>
              <dd class="text-gray-800 dark:text-gray-100" data-testid="coverage-generated">
                {{ coverage.generated_at ?? '—' }}
              </dd>
            </div>
            <div>
              <dt class="text-gray-500 dark:text-gray-400">Git SHA</dt>
              <dd
                class="text-gray-800 dark:text-gray-100 font-mono break-all"
                data-testid="coverage-git-sha"
              >
                {{ coverage.git_sha ?? '—' }}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </section>

    <!-- Users tab -->
    <section v-show="tab === 'users'" role="tabpanel" data-testid="admin-tab-users">
      <p v-if="usersLoading" data-testid="users-loading" class="text-gray-600 dark:text-gray-300">
        Loading users…
      </p>
      <div
        v-else-if="usersError"
        data-testid="users-error"
        role="alert"
        class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
      >
        {{ usersError }}
      </div>
      <div v-else class="space-y-6">
        <div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg shadow-xs">
          <table class="w-full text-sm">
            <thead
              class="text-xs uppercase text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700/60"
            >
              <tr>
                <th class="text-left font-semibold px-4 py-3">Username</th>
                <th class="text-left font-semibold px-4 py-3">Display name</th>
                <th class="text-left font-semibold px-4 py-3">Role</th>
                <th class="text-left font-semibold px-4 py-3">Status</th>
                <th class="text-left font-semibold px-4 py-3">Created</th>
                <th class="text-left font-semibold px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700/60">
              <tr
                v-for="user in users"
                :key="user.id"
                :data-testid="`user-row-${user.id}`"
              >
                <td class="px-4 py-3 text-gray-800 dark:text-gray-100 font-medium">
                  {{ user.username }}
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300">{{ user.display_name }}</td>
                <td class="px-4 py-3">
                  <AppBadge :colour="user.is_admin ? 'purple' : 'grey'">
                    {{ user.is_admin ? 'Admin' : 'User' }}
                  </AppBadge>
                </td>
                <td class="px-4 py-3">
                  <AppBadge :colour="user.is_active ? 'green' : 'red'">
                    {{ user.is_active ? 'Active' : 'Inactive' }}
                  </AppBadge>
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300 whitespace-nowrap">
                  {{ formatDate(user.created_at) }}
                </td>
                <td class="px-4 py-3">
                  <div v-if="isCurrentUser(user)" class="text-xs text-gray-400 dark:text-gray-500">
                    You
                  </div>
                  <div v-else class="flex flex-wrap gap-2">
                    <button
                      type="button"
                      :data-testid="`user-toggle-admin-${user.id}`"
                      :disabled="pendingIds.has(user.id)"
                      class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
                      @click="toggleAdmin(user)"
                    >
                      {{ user.is_admin ? 'Demote' : 'Promote' }}
                    </button>
                    <button
                      type="button"
                      :data-testid="`user-toggle-active-${user.id}`"
                      :disabled="pendingIds.has(user.id)"
                      class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
                      @click="toggleActive(user)"
                    >
                      {{ user.is_active ? 'Deactivate' : 'Activate' }}
                    </button>
                    <template v-if="confirmingDeleteId === user.id">
                      <button
                        type="button"
                        :data-testid="`user-delete-confirm-${user.id}`"
                        :disabled="pendingIds.has(user.id)"
                        class="rounded-md border border-red-500 bg-red-500 px-2.5 py-1 text-xs font-medium text-white hover:bg-red-600 disabled:opacity-50 cursor-pointer"
                        @click="confirmDeleteUser(user)"
                      >
                        Confirm delete
                      </button>
                      <button
                        type="button"
                        :data-testid="`user-delete-cancel-${user.id}`"
                        :disabled="pendingIds.has(user.id)"
                        class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
                        @click="cancelDeleteUser"
                      >
                        Cancel
                      </button>
                    </template>
                    <button
                      v-else
                      type="button"
                      :data-testid="`user-delete-${user.id}`"
                      :disabled="pendingIds.has(user.id)"
                      class="rounded-md border border-red-200 dark:border-red-500/40 px-2.5 py-1 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-50 cursor-pointer"
                      @click="requestDeleteUser(user)"
                    >
                      Delete
                    </button>
                  </div>
                  <p
                    v-if="rowError[user.id]"
                    :data-testid="`user-error-${user.id}`"
                    class="mt-1 text-xs text-red-600 dark:text-red-400"
                  >
                    {{ rowError[user.id] }}
                  </p>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Create user -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Create user</h2>
          <div
            v-if="createError"
            data-testid="create-user-error"
            role="alert"
            class="mb-4 border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-4 py-3 text-sm text-red-700 dark:text-red-300"
          >
            {{ createError }}
          </div>
          <form class="space-y-4" data-testid="create-user-form" @submit.prevent="onCreateUser">
            <AppInput id="new-username" v-model="newUsername" label="Username" autocomplete="off" />
            <AppInput
              id="new-password"
              v-model="newPassword"
              type="password"
              label="Password"
              autocomplete="new-password"
            />
            <AppInput
              id="new-display-name"
              v-model="newDisplayName"
              label="Display name (optional)"
              autocomplete="off"
            />
            <label class="flex items-center gap-2">
              <input
                v-model="newIsAdmin"
                type="checkbox"
                data-testid="new-is-admin"
                class="rounded border-gray-300 dark:border-gray-600 text-violet-500 focus:ring-violet-500"
              />
              <span class="text-sm font-medium text-gray-700 dark:text-gray-300">Administrator</span>
            </label>
            <AppButton type="submit" data-testid="create-user-submit" :disabled="!canCreate">
              {{ creating ? 'Creating…' : 'Create user' }}
            </AppButton>
          </form>
        </div>
      </div>
    </section>

    <!-- Metadata tab (senders / recipients / kinds management) -->
    <section v-show="tab === 'metadata'" role="tabpanel" data-testid="admin-tab-metadata">
      <div class="space-y-6">
        <!-- Senders -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Senders</h2>

          <!-- Create sender -->
          <div class="mb-4">
            <label
              for="sender-create-input"
              class="block text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"
            >
              Add sender
            </label>
            <div class="flex items-end gap-2">
              <input
                id="sender-create-input"
                v-model="senderCreateValue"
                type="text"
                autocomplete="off"
                class="form-input flex-1"
                data-testid="sender-create-input"
                @keyup.enter="onCreateSender()"
              />
              <AppButton
                type="button"
                data-testid="sender-create-button"
                :disabled="senderCreating"
                @click="onCreateSender()"
              >
                {{ senderCreating ? 'Adding…' : 'Add' }}
              </AppButton>
            </div>
            <p
              v-if="senderCreateError"
              data-testid="sender-create-error"
              class="mt-1 text-xs text-red-600 dark:text-red-400"
            >
              {{ senderCreateError }}
            </p>
          </div>

          <p v-if="sendersLoading" data-testid="senders-loading" class="text-sm text-gray-500 dark:text-gray-400">
            Loading senders…
          </p>
          <div
            v-else-if="sendersError"
            data-testid="senders-error"
            role="alert"
            class="border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300"
          >
            {{ sendersError }}
          </div>
          <p
            v-else-if="senders.length === 0"
            data-testid="senders-empty"
            class="text-sm text-gray-500 dark:text-gray-400"
          >
            No senders yet.
          </p>
          <ul
            v-else
            class="divide-y divide-gray-100 dark:divide-gray-700/60"
            data-testid="sender-list"
          >
            <li
              v-for="s in senders"
              :key="s.id"
              :data-testid="`sender-row-${s.id}`"
              class="py-3"
            >
              <!-- Display row -->
              <div v-if="senderRenameId !== s.id" class="flex items-center justify-between gap-3">
                <span class="min-w-0 truncate font-medium text-gray-800 dark:text-gray-100">
                  {{ s.name }}
                </span>
                <div class="flex shrink-0 items-center gap-2">
                  <AppBadge colour="grey">{{ s.document_count }} docs</AppBadge>
                  <button
                    type="button"
                    data-testid="sender-rename"
                    :disabled="senderPendingIds.has(s.id)"
                    class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
                    @click="startSenderRename(s)"
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    data-testid="sender-delete"
                    :disabled="senderPendingIds.has(s.id)"
                    class="rounded-md border border-red-200 dark:border-red-500/40 px-2.5 py-1 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-50 cursor-pointer"
                    @click="startSenderDelete(s)"
                  >
                    Delete
                  </button>
                </div>
              </div>

              <!-- Rename editor -->
              <div v-else class="space-y-2">
                <div class="flex items-end gap-2">
                  <div class="flex-1">
                    <AppInput
                      :id="`sender-rename-input-${s.id}`"
                      v-model="senderRenameValue"
                      label="Sender name"
                      autocomplete="off"
                      data-testid="sender-rename-input"
                      @keyup.enter="saveSenderRename(s)"
                    />
                  </div>
                  <AppButton
                    type="button"
                    data-testid="sender-rename-save"
                    :disabled="senderPendingIds.has(s.id)"
                    @click="saveSenderRename(s)"
                  >
                    Save
                  </AppButton>
                  <AppButton
                    type="button"
                    variant="secondary"
                    data-testid="sender-rename-cancel"
                    @click="cancelSenderRename()"
                  >
                    Cancel
                  </AppButton>
                </div>

                <!-- Merge prompt (shown when the rename collides, 409) -->
                <div
                  v-if="senderMergeTarget"
                  data-testid="sender-merge-warning"
                  role="alert"
                  class="border-l-4 border-yellow-500 bg-yellow-50 dark:bg-yellow-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
                >
                  <p>
                    '{{ s.name }}' will be merged into '{{ senderMergeTarget.target_name }}'
                    ({{ senderMergeTarget.target_document_count }} documents) and removed.
                  </p>
                  <AppButton
                    type="button"
                    class="mt-2"
                    data-testid="sender-merge-confirm"
                    :disabled="senderPendingIds.has(s.id)"
                    @click="saveSenderRename(s, true)"
                  >
                    Merge and remove
                  </AppButton>
                </div>
              </div>

              <!-- Delete confirm / reassign -->
              <div
                v-if="senderDeleteId === s.id"
                data-testid="sender-delete-confirm-box"
                class="mt-2 border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
              >
                <template v-if="s.document_count > 0">
                  <p class="mb-2">
                    '{{ s.name }}' still has {{ s.document_count }} documents. Choose where to move
                    them (or clear the sender) before deleting.
                  </p>
                  <AppSelect
                    :id="`sender-reassign-${s.id}`"
                    v-model="senderReassignValue"
                    label="Reassign documents to"
                    :items="senderReassignItems(s)"
                    data-testid="sender-reassign-select"
                  />
                </template>
                <p v-else class="mb-2">Delete '{{ s.name }}'? This cannot be undone.</p>
                <div class="mt-2 flex gap-2">
                  <AppButton
                    type="button"
                    data-testid="sender-delete-confirm"
                    :disabled="senderPendingIds.has(s.id)"
                    @click="confirmSenderDelete(s)"
                  >
                    Delete
                  </AppButton>
                  <AppButton
                    type="button"
                    variant="secondary"
                    data-testid="sender-delete-cancel"
                    @click="cancelSenderDelete()"
                  >
                    Cancel
                  </AppButton>
                </div>
              </div>

              <p
                v-if="senderRowError[s.id]"
                :data-testid="`sender-error-${s.id}`"
                class="mt-1 text-xs text-red-600 dark:text-red-400"
              >
                {{ senderRowError[s.id] }}
              </p>
            </li>
          </ul>
        </div>

        <!-- Recipients -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Recipients</h2>

          <!-- Create recipient -->
          <div class="mb-4">
            <label
              for="recipient-create-input"
              class="block text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"
            >
              Add recipient
            </label>
            <div class="flex items-end gap-2">
              <input
                id="recipient-create-input"
                v-model="recipientCreateValue"
                type="text"
                autocomplete="off"
                class="form-input flex-1"
                data-testid="recipient-create-input"
                @keyup.enter="onCreateRecipient()"
              />
              <AppButton
                type="button"
                data-testid="recipient-create-button"
                :disabled="recipientCreating"
                @click="onCreateRecipient()"
              >
                {{ recipientCreating ? 'Adding…' : 'Add' }}
              </AppButton>
            </div>
            <p
              v-if="recipientCreateError"
              data-testid="recipient-create-error"
              class="mt-1 text-xs text-red-600 dark:text-red-400"
            >
              {{ recipientCreateError }}
            </p>
          </div>

          <p
            v-if="recipientsLoading"
            data-testid="recipients-loading"
            class="text-sm text-gray-500 dark:text-gray-400"
          >
            Loading recipients…
          </p>
          <div
            v-else-if="recipientsError"
            data-testid="recipients-error"
            role="alert"
            class="border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300"
          >
            {{ recipientsError }}
          </div>
          <p
            v-else-if="recipients.length === 0"
            data-testid="recipients-empty"
            class="text-sm text-gray-500 dark:text-gray-400"
          >
            No recipients yet.
          </p>
          <ul
            v-else
            class="divide-y divide-gray-100 dark:divide-gray-700/60"
            data-testid="recipient-list"
          >
            <li
              v-for="r in recipients"
              :key="r.id"
              :data-testid="`recipient-row-${r.id}`"
              class="py-3"
            >
              <!-- Display row -->
              <div v-if="renameId !== r.id" class="flex items-center justify-between gap-3">
                <span class="min-w-0 truncate font-medium text-gray-800 dark:text-gray-100">
                  {{ r.name }}
                </span>
                <div class="flex shrink-0 items-center gap-2">
                  <AppBadge colour="grey">{{ r.document_count }} docs</AppBadge>
                  <button
                    type="button"
                    data-testid="recipient-rename"
                    :disabled="recipientPendingIds.has(r.id)"
                    class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
                    @click="startRename(r)"
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    data-testid="recipient-delete"
                    :disabled="recipientPendingIds.has(r.id)"
                    class="rounded-md border border-red-200 dark:border-red-500/40 px-2.5 py-1 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-50 cursor-pointer"
                    @click="startDelete(r)"
                  >
                    Delete
                  </button>
                </div>
              </div>

              <!-- Rename editor -->
              <div v-else class="space-y-2">
                <div class="flex items-end gap-2">
                  <div class="flex-1">
                    <AppInput
                      :id="`recipient-rename-input-${r.id}`"
                      v-model="renameValue"
                      label="Recipient name"
                      autocomplete="off"
                      data-testid="recipient-rename-input"
                      @keyup.enter="saveRename(r)"
                    />
                  </div>
                  <AppButton
                    type="button"
                    data-testid="recipient-rename-save"
                    :disabled="recipientPendingIds.has(r.id)"
                    @click="saveRename(r)"
                  >
                    Save
                  </AppButton>
                  <AppButton
                    type="button"
                    variant="secondary"
                    data-testid="recipient-rename-cancel"
                    @click="cancelRename()"
                  >
                    Cancel
                  </AppButton>
                </div>

                <!-- Merge prompt (shown when the rename collides, 409) -->
                <div
                  v-if="mergeTarget"
                  data-testid="recipient-merge-warning"
                  role="alert"
                  class="border-l-4 border-yellow-500 bg-yellow-50 dark:bg-yellow-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
                >
                  <p>
                    '{{ r.name }}' will be merged into '{{ mergeTarget.target_name }}'
                    ({{ mergeTarget.target_document_count }} documents) and removed.
                  </p>
                  <AppButton
                    type="button"
                    class="mt-2"
                    data-testid="recipient-merge-confirm"
                    :disabled="recipientPendingIds.has(r.id)"
                    @click="saveRename(r, true)"
                  >
                    Merge and remove
                  </AppButton>
                </div>
              </div>

              <!-- Delete confirm / reassign -->
              <div
                v-if="deleteId === r.id"
                data-testid="recipient-delete-confirm-box"
                class="mt-2 border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
              >
                <template v-if="r.document_count > 0">
                  <p class="mb-2">
                    '{{ r.name }}' still has {{ r.document_count }} documents. Choose where to move
                    them (or clear the recipient) before deleting.
                  </p>
                  <AppSelect
                    :id="`recipient-reassign-${r.id}`"
                    v-model="reassignValue"
                    label="Reassign documents to"
                    :items="reassignItems(r)"
                    data-testid="recipient-reassign-select"
                  />
                </template>
                <p v-else class="mb-2">Delete '{{ r.name }}'? This cannot be undone.</p>
                <div class="mt-2 flex gap-2">
                  <AppButton
                    type="button"
                    data-testid="recipient-delete-confirm"
                    :disabled="recipientPendingIds.has(r.id)"
                    @click="confirmDelete(r)"
                  >
                    Delete
                  </AppButton>
                  <AppButton
                    type="button"
                    variant="secondary"
                    data-testid="recipient-delete-cancel"
                    @click="cancelDelete()"
                  >
                    Cancel
                  </AppButton>
                </div>
              </div>

              <p
                v-if="recipientRowError[r.id]"
                :data-testid="`recipient-error-${r.id}`"
                class="mt-1 text-xs text-red-600 dark:text-red-400"
              >
                {{ recipientRowError[r.id] }}
              </p>
            </li>
          </ul>
        </div>

        <!-- Kinds -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Kinds</h2>

          <!-- Create kind -->
          <div class="mb-4">
            <label
              for="kind-create-input"
              class="block text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"
            >
              Add kind
            </label>
            <div class="flex items-end gap-2">
              <input
                id="kind-create-input"
                v-model="kindCreateValue"
                type="text"
                autocomplete="off"
                class="form-input flex-1"
                data-testid="kind-create-input"
                @keyup.enter="onCreateKind()"
              />
              <AppButton
                type="button"
                data-testid="kind-create-button"
                :disabled="kindCreating"
                @click="onCreateKind()"
              >
                {{ kindCreating ? 'Adding…' : 'Add' }}
              </AppButton>
            </div>
            <p
              v-if="kindCreateError"
              data-testid="kind-create-error"
              class="mt-1 text-xs text-red-600 dark:text-red-400"
            >
              {{ kindCreateError }}
            </p>
          </div>

          <p v-if="kindsLoading" data-testid="kinds-loading" class="text-sm text-gray-500 dark:text-gray-400">
            Loading kinds…
          </p>
          <div
            v-else-if="kindsError"
            data-testid="kinds-error"
            role="alert"
            class="border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300"
          >
            {{ kindsError }}
          </div>
          <p
            v-else-if="kinds.length === 0"
            data-testid="kinds-empty"
            class="text-sm text-gray-500 dark:text-gray-400"
          >
            No kinds yet.
          </p>
          <ul
            v-else
            class="divide-y divide-gray-100 dark:divide-gray-700/60"
            data-testid="kind-list"
          >
            <li
              v-for="k in kinds"
              :key="k.slug"
              :data-testid="`kind-row-${k.slug}`"
              class="py-3"
            >
              <!-- Display row -->
              <div v-if="kindRenameSlug !== k.slug" class="flex items-center justify-between gap-3">
                <span class="min-w-0 truncate font-medium text-gray-800 dark:text-gray-100">
                  {{ k.name }}
                </span>
                <div class="flex shrink-0 items-center gap-2">
                  <AppBadge colour="grey">{{ k.document_count }} docs</AppBadge>
                  <button
                    type="button"
                    data-testid="kind-rename"
                    :disabled="kindPendingSlugs.has(k.slug)"
                    class="rounded-md border border-gray-200 dark:border-gray-700/60 px-2.5 py-1 text-xs font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 cursor-pointer"
                    @click="startKindRename(k)"
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    data-testid="kind-delete"
                    :disabled="kindPendingSlugs.has(k.slug)"
                    class="rounded-md border border-red-200 dark:border-red-500/40 px-2.5 py-1 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 disabled:opacity-50 cursor-pointer"
                    @click="startKindDelete(k)"
                  >
                    Delete
                  </button>
                </div>
              </div>

              <!-- Rename editor (name-only; no merge) -->
              <div v-else class="space-y-2">
                <div class="flex items-end gap-2">
                  <div class="flex-1">
                    <AppInput
                      :id="`kind-rename-input-${k.slug}`"
                      v-model="kindRenameValue"
                      label="Kind name"
                      autocomplete="off"
                      data-testid="kind-rename-input"
                      @keyup.enter="saveKindRename(k)"
                    />
                  </div>
                  <AppButton
                    type="button"
                    data-testid="kind-rename-save"
                    :disabled="kindPendingSlugs.has(k.slug)"
                    @click="saveKindRename(k)"
                  >
                    Save
                  </AppButton>
                  <AppButton
                    type="button"
                    variant="secondary"
                    data-testid="kind-rename-cancel"
                    @click="cancelKindRename()"
                  >
                    Cancel
                  </AppButton>
                </div>
              </div>

              <!-- Delete confirm / reassign (by slug) -->
              <div
                v-if="kindDeleteSlug === k.slug"
                data-testid="kind-delete-confirm-box"
                class="mt-2 border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
              >
                <template v-if="k.document_count > 0">
                  <p class="mb-2">
                    '{{ k.name }}' still has {{ k.document_count }} documents. Choose where to move
                    them (or clear the kind) before deleting.
                  </p>
                  <AppSelect
                    :id="`kind-reassign-${k.slug}`"
                    v-model="kindReassignValue"
                    label="Reassign documents to"
                    :items="kindReassignItems(k)"
                    data-testid="kind-reassign-select"
                  />
                </template>
                <p v-else class="mb-2">Delete '{{ k.name }}'? This cannot be undone.</p>
                <div class="mt-2 flex gap-2">
                  <AppButton
                    type="button"
                    data-testid="kind-delete-confirm"
                    :disabled="kindPendingSlugs.has(k.slug)"
                    @click="confirmKindDelete(k)"
                  >
                    Delete
                  </AppButton>
                  <AppButton
                    type="button"
                    variant="secondary"
                    data-testid="kind-delete-cancel"
                    @click="cancelKindDelete()"
                  >
                    Cancel
                  </AppButton>
                </div>
              </div>

              <p
                v-if="kindRowError[k.slug]"
                :data-testid="`kind-error-${k.slug}`"
                class="mt-1 text-xs text-red-600 dark:text-red-400"
              >
                {{ kindRowError[k.slug] }}
              </p>
            </li>
          </ul>
        </div>

        <!-- Currencies -->
        <div :class="cardClass">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-1">Currencies</h2>
          <p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
            Currency codes aren't a reference table, but they're part of series
            identity. Normalising rewrites a code across every document and series
            — merging duplicate cached insights (they regenerate) and refusing if
            it would collide with your series overrides.
          </p>

          <p
            v-if="currenciesLoading"
            data-testid="currencies-loading"
            class="text-sm text-gray-500 dark:text-gray-400"
          >
            Loading…
          </p>
          <p
            v-else-if="currenciesError"
            data-testid="currencies-error"
            class="text-sm text-red-600 dark:text-red-400"
          >
            {{ currenciesError }}
          </p>

          <template v-else>
            <!-- Normalise form -->
            <div class="mb-4 flex flex-wrap items-end gap-2">
              <AppSelect
                id="currency-normalize-from"
                v-model="normalizeFrom"
                label="From"
                :items="currencyItems"
                data-testid="currency-normalize-from"
              />
              <div>
                <label
                  for="currency-normalize-to"
                  class="block text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"
                >
                  To
                </label>
                <input
                  id="currency-normalize-to"
                  v-model="normalizeTo"
                  type="text"
                  maxlength="3"
                  autocomplete="off"
                  placeholder="EUR"
                  class="form-input w-24 uppercase"
                  data-testid="currency-normalize-to"
                />
              </div>
              <AppButton
                type="button"
                data-testid="currency-normalize-button"
                :disabled="normalizePending"
                @click="startNormalize()"
              >
                Normalise
              </AppButton>
            </div>

            <!-- Confirm step -->
            <div
              v-if="normalizeConfirming"
              data-testid="currency-normalize-confirm-box"
              class="mb-4 border-l-4 border-amber-500 bg-amber-50 dark:bg-amber-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
            >
              <p class="mb-2">
                Rename <strong>{{ normalizeFrom }}</strong> to
                <strong>{{ normalizeTo.toUpperCase() }}</strong> across all documents and series?
                Duplicate cached insights are merged; this isn't a per-document undo.
              </p>
              <div class="flex gap-2">
                <AppButton
                  type="button"
                  data-testid="currency-normalize-confirm"
                  :disabled="normalizePending"
                  @click="confirmNormalize()"
                >
                  {{ normalizePending ? 'Normalising…' : 'Confirm' }}
                </AppButton>
                <AppButton
                  type="button"
                  variant="secondary"
                  data-testid="currency-normalize-cancel"
                  @click="cancelNormalize()"
                >
                  Cancel
                </AppButton>
              </div>
            </div>

            <!-- Result summary -->
            <div
              v-if="normalizeResult"
              data-testid="currency-normalize-result"
              class="mb-4 rounded-lg border border-green-200 bg-green-50 dark:border-green-500/30 dark:bg-green-500/10 px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
            >
              <p>
                Renamed <strong>{{ normalizeResult.from_code }}</strong> →
                <strong>{{ normalizeResult.to_code }}</strong> ·
                {{ normalizeResult.counts.documents ?? 0 }} document(s).
              </p>
              <p
                v-if="normalizeResult.fx_rate_missing"
                data-testid="currency-fx-warning"
                class="mt-1 text-amber-700 dark:text-amber-400"
              >
                No FX rate exists for {{ normalizeResult.to_code }} — FX conversion for it is
                unavailable until a rate is seeded.
              </p>
            </div>

            <!-- Override-collision refusal -->
            <div
              v-if="normalizeConflicts.length"
              data-testid="currency-conflict"
              class="mb-4 rounded-lg border border-red-200 bg-red-50 dark:border-red-500/30 dark:bg-red-500/10 px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
            >
              <p class="mb-1">
                Refused: this would collide with {{ normalizeConflicts.length }} series
                override(s). Resolve them first (nothing was changed).
              </p>
              <ul class="list-disc pl-5">
                <li v-for="(c, i) in normalizeConflicts" :key="i">
                  {{ c.table }} · sender {{ c.sender_id ?? '—' }} · kind {{ c.kind_id ?? '—' }}
                </li>
              </ul>
            </div>

            <p
              v-else-if="normalizeError"
              data-testid="currency-normalize-error"
              class="mb-4 text-sm text-red-600 dark:text-red-400"
            >
              {{ normalizeError }}
            </p>

            <!-- Codes in use -->
            <ul v-if="currencies.length" class="divide-y divide-gray-100 dark:divide-gray-700/60">
              <li
                v-for="c in currencies"
                :key="c.code"
                :data-testid="`currency-row-${c.code}`"
                class="flex items-center justify-between py-2 text-sm"
              >
                <span class="font-medium text-gray-800 dark:text-gray-100">{{ c.code }}</span>
                <AppBadge colour="grey">{{ c.document_count }}</AppBadge>
              </li>
            </ul>
            <p v-else class="text-sm text-gray-500 dark:text-gray-400">
              No currencies are set on any document yet.
            </p>
          </template>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* Markdown rendered via v-html; restore prose spacing stripped by Tailwind
   preflight (mirrors .doc-markdown in NewNoteView.vue / DocumentDetailView). */
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
/* Fenced code blocks (incl. wide ASCII diagrams) scroll horizontally inside the
   block instead of overflowing the card and the viewport on a phone. */
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
/* GFM tables: marked emits real <table> markup, but Tailwind preflight strips
   borders/spacing so they collapse to unstyled text. Restore borders, padding,
   a header tint, and horizontal scroll for wide tables on a phone. */
.doc-markdown :deep(table) {
  display: block;
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
  margin: 0.75rem 0;
  border-collapse: collapse;
  font-size: 0.9375em;
}
.doc-markdown :deep(th),
.doc-markdown :deep(td) {
  border: 1px solid rgb(0 0 0 / 0.12);
  padding: 0.375rem 0.625rem;
  text-align: left;
  vertical-align: top;
}
.dark .doc-markdown :deep(th),
.dark .doc-markdown :deep(td) {
  border-color: rgb(255 255 255 / 0.15);
}
.doc-markdown :deep(th) {
  font-weight: 600;
  background: rgb(0 0 0 / 0.04);
}
.dark .doc-markdown :deep(th) {
  background: rgb(255 255 255 / 0.06);
}
</style>
