<script setup lang="ts">
/**
 * Jobs dashboard (route `/jobs`): one row per document being processed, showing
 * its current pipeline stage, extraction cost, and any error (the server
 * collapses a document's several jobs into its latest one). A single table —
 * in-progress jobs sort to the top and carry a spinner; finished jobs follow.
 * Refetches whenever the live jobs store reports a document finishing, so the
 * table stays current without a manual reload. Document-less system tasks (the
 * email poll) are hidden — active and finished alike — unless "Show system
 * tasks" is on.
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useStorage } from '@vueuse/core'
import { useRoute, useRouter } from 'vue-router'
import { AppBadge, AppInput, AppPopover, AppSelect, PageHeader } from '@/components/app'
import {
  getDocument,
  listDocuments,
  listJobs,
  listJobTaskNames,
  type JobInfo,
} from '@/api/documents'
import { useJobsStore } from '@/stores/jobs'

const jobsStore = useJobsStore()
const route = useRoute()
const router = useRouter()

const jobs = ref<JobInfo[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
// Document-less system/periodic jobs (the email poll) are hidden by default so
// their constant successes don't bury document work; this toggles them back in.
const showSystem = ref(false)

// A single ordered list: active (queued/running) jobs first — that's the work a
// user most wants to see — then finished jobs in the server's order. Active
// system rows are already excluded server-side unless "Show system tasks" is on,
// so merging them in needs no extra gating here.
const orderedJobs = computed(() => {
  const active = jobs.value.filter((job) => job.active)
  const rest = jobs.value.filter((job) => !job.active)
  return [...active, ...rest]
})

// --- Filters: document + task type ------------------------------------------
//
// Both live in the URL query (?document_id=&task=) so the view survives a
// reload and a document's history can be deep-linked — e.g. the document detail
// page links to /jobs?document_id=<id>. A document filter switches the server to
// uncollapsed "history mode": every job for that one document, newest first.

/** Currently-filtered document id, or null when not filtering by document. */
const documentFilterId = computed<number | null>(() => {
  const raw = route.query.document_id
  const n = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isInteger(n) && n > 0 ? n : null
})

/** Selected task name (the select's v-model); '' means "All tasks". */
const taskFilter = computed<string>({
  get() {
    const raw = route.query.task
    return typeof raw === 'string' ? raw : ''
  },
  set(value: string) {
    void router.push({ query: withQuery({ task: value || undefined }) })
  },
})

const inHistoryMode = computed(() => documentFilterId.value !== null)

/** Merge overrides into the current query; keys set to undefined are removed. */
function withQuery(overrides: Record<string, string | undefined>): Record<string, string> {
  const next: Record<string, string> = {}
  for (const [key, value] of Object.entries(route.query)) {
    if (typeof value === 'string') next[key] = value
  }
  for (const [key, value] of Object.entries(overrides)) {
    if (value === undefined) delete next[key]
    else next[key] = value
  }
  return next
}

// Task-type dropdown options, loaded once from the queue's distinct task names.
const taskNames = ref<string[]>([])
const taskOptions = computed(() => [
  { value: '', text: 'All tasks' },
  ...taskNames.value.map((name) => ({ value: name, text: taskLabel(name) })),
])

// Document typeahead: search by title, map the chosen title back to its id.
const documentInput = ref('')
const documentSuggestions = ref<{ id: number; title: string }[]>([])
const documentChipTitle = ref('')
let suggestAbort: AbortController | null = null

async function searchDocuments(): Promise<void> {
  const q = documentInput.value.trim()
  suggestAbort?.abort()
  if (!q) {
    documentSuggestions.value = []
    return
  }
  suggestAbort = new AbortController()
  try {
    const res = await listDocuments({ q, limit: 10 }, suggestAbort.signal)
    documentSuggestions.value = res.items.map((item) => ({
      id: item.id,
      title: item.title ?? `Document #${item.id}`,
    }))
  } catch {
    // Aborted or failed — keep whatever suggestions we already had.
  }
}

/** Apply the filter when the typed text matches a suggested document title. */
function applyTypedDocument(): void {
  const match = documentSuggestions.value.find((d) => d.title === documentInput.value.trim())
  if (match) setDocumentFilter(match.id)
}

function setDocumentFilter(id: number | null): void {
  void router.push({ query: withQuery({ document_id: id === null ? undefined : String(id) }) })
}

function clearDocumentFilter(): void {
  documentInput.value = ''
  setDocumentFilter(null)
}

// Resolve a readable title for the active-document chip. Prefer a title already
// in the suggestion list (free); otherwise fetch it (covers deep-links).
watch(
  documentFilterId,
  async (id) => {
    if (id === null) {
      documentChipTitle.value = ''
      return
    }
    const known = documentSuggestions.value.find((d) => d.id === id)
    if (known) {
      documentChipTitle.value = known.title
      return
    }
    try {
      const doc = await getDocument(id)
      documentChipTitle.value = doc.title ?? `Document #${id}`
    } catch {
      documentChipTitle.value = `Document #${id}`
    }
  },
  { immediate: true },
)

async function load(): Promise<void> {
  error.value = null
  try {
    jobs.value = await listJobs({
      limit: 200,
      includeSystem: showSystem.value,
      documentId: documentFilterId.value ?? undefined,
      taskName: taskFilter.value || undefined,
    })
  } catch {
    error.value = 'Could not load jobs. Try refreshing the page.'
  } finally {
    loading.value = false
  }
}

// System/periodic rows (the email poll, series insight) don't emit SSE events,
// so a live document stream can never surface them. While they're shown — and
// we're not pinned to one document's history — poll so new ones appear without a
// manual reload.
const SYSTEM_POLL_MS = 10000
let systemPollTimer: ReturnType<typeof setInterval> | null = null

function stopSystemPoll(): void {
  if (systemPollTimer !== null) {
    clearInterval(systemPollTimer)
    systemPollTimer = null
  }
}

function syncSystemPoll(): void {
  const shouldPoll = showSystem.value && !inHistoryMode.value
  if (shouldPoll && systemPollTimer === null) {
    systemPollTimer = setInterval(() => void load(), SYSTEM_POLL_MS)
  } else if (!shouldPoll) {
    stopSystemPoll()
  }
}

onMounted(load)
onMounted(async () => {
  try {
    taskNames.value = await listJobTaskNames()
  } catch {
    // Best-effort: the dropdown just offers "All tasks".
  }
})
// Any document event — including an intra-pipeline stage change that leaves the
// active count unchanged (ocr → extract → …) — means the table may be stale, so
// refetch. (activeCount alone misses stage-to-stage transitions.)
watch(() => jobsStore.lastEvent, () => void load())
// Re-fetch when the system-task filter flips (the server applies it), and start
// or stop the system-task poll to match.
watch(showSystem, () => {
  void load()
  syncSystemPoll()
})
// Re-fetch when a document or task-type filter changes; entering/leaving history
// mode also flips whether the system poll should run.
watch([documentFilterId, () => taskFilter.value], () => void load())
watch(inHistoryMode, syncSystemPoll)
onUnmounted(stopSystemPoll)

type BadgeColour = 'grey' | 'blue' | 'yellow' | 'green' | 'red'

// Display label + colour for each value the status badge can show: a document's
// pipeline stage, or — for document-less system rows — a bare Procrastinate
// status.
const STATUS_LABELS: Record<string, string> = {
  received: 'Received',
  ocr: 'OCR',
  extract: 'Extracting',
  markdown: 'Markdown',
  embed: 'Embedding',
  indexed: 'Indexed',
  failed: 'Failed',
  todo: 'Queued',
  doing: 'Running',
  succeeded: 'Succeeded',
}

const STATUS_COLOURS: Record<string, BadgeColour> = {
  received: 'blue',
  ocr: 'yellow',
  extract: 'yellow',
  markdown: 'yellow',
  embed: 'yellow',
  indexed: 'green',
  failed: 'red',
  todo: 'blue',
  doing: 'yellow',
  succeeded: 'green',
}

// One row = one document (its latest job). Prefer the document's pipeline stage;
// fall back to the raw job status for document-less system rows.
function rowStatus(job: JobInfo): string {
  return job.document_status ?? job.status
}

function statusLabel(job: JobInfo): string {
  const status = rowStatus(job)
  return STATUS_LABELS[status] ?? status
}

function statusColour(job: JobInfo): BadgeColour {
  return STATUS_COLOURS[rowStatus(job)] ?? 'grey'
}

function documentLabel(job: JobInfo): string {
  return job.document_title || (job.document_id !== null ? `Document #${job.document_id}` : '—')
}

function formatCost(cost: number | null): string {
  return cost === null ? '—' : `$${cost.toFixed(4)}`
}

// Procrastinate task names are fully-qualified ("library.jobs.poll_email_inbox").
// Show just the final segment, humanised, so the column is readable — especially
// for document-less system rows whose DOCUMENT cell is a bare "—".
function taskLabel(name: string): string {
  const leaf = name.split('.').pop() ?? name
  const words = leaf.replace(/_/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

const dateTimeFormat = new Intl.DateTimeFormat('en-GB', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateTimeFormat.format(parsed)
}

// Wall-clock run time between a job's start and finish events. "—" while a job
// is still running (no finish yet) or when timing events weren't recorded.
function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt || !finishedAt) return '—'
  const start = new Date(startedAt).getTime()
  const end = new Date(finishedAt).getTime()
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—'
  const seconds = (end - start) / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const mins = Math.floor(seconds / 60)
  const rem = Math.round(seconds % 60)
  return `${mins}m ${rem}s`
}

// --- Recent-table column configuration ------------------------------------
// Each column is user-toggleable; the Document column is special (it renders a
// link, so it has its own cell template) but is still listed here so the menu
// can offer it. `style` drives per-column sizing under `table-fixed`: the
// Document column clamps between ~10rem and ~22rem and truncates so it stops
// dominating; narrow value columns get a fixed compact width.
interface ColumnDef {
  key: string
  label: string
  defaultVisible: boolean
  // Inline width style applied to the matching <col> in the <colgroup>.
  style: string
}

const COLUMNS: ColumnDef[] = [
  { key: 'document', label: 'Document', defaultVisible: true, style: 'width: clamp(10rem, 22vw, 22rem)' },
  { key: 'task', label: 'Task', defaultVisible: true, style: 'width: 9rem' },
  { key: 'status', label: 'Status', defaultVisible: true, style: 'width: 7rem' },
  { key: 'finished', label: 'Finished', defaultVisible: true, style: 'width: 11rem' },
  { key: 'duration', label: 'Duration', defaultVisible: true, style: 'width: 6rem' },
  { key: 'cost', label: 'Cost', defaultVisible: true, style: 'width: 6rem' },
  { key: 'error', label: 'Error', defaultVisible: true, style: 'width: clamp(8rem, 20vw, 20rem)' },
]

const COLUMNS_STORAGE_KEY = 'library:jobs-columns'

const DEFAULT_VISIBILITY: Record<string, boolean> = Object.fromEntries(
  COLUMNS.map((c) => [c.key, c.defaultVisible]),
)

// Persisted per-machine. `mergeDefaults` merges the stored map over the current
// defaults so a newly-added column keeps its default visibility instead of
// vanishing for users with an older saved preference.
const columnVisibility = useStorage<Record<string, boolean>>(
  COLUMNS_STORAGE_KEY,
  { ...DEFAULT_VISIBILITY },
  undefined,
  { mergeDefaults: true },
)
const showColumnMenu = ref(false)

function isVisible(key: string): boolean {
  return columnVisibility.value[key] ?? true
}

// Visible columns, in definition order, used to drive both <thead>/<tbody>.
const visibleColumns = computed(() => COLUMNS.filter((c) => isVisible(c.key)))

// Mobile-card meta grid: every visible column except the two that form the
// card headline (Document + Status), so the card respects the user's prefs.
const cardMetaColumns = computed(() =>
  visibleColumns.value.filter((c) => c.key !== 'document' && c.key !== 'status'),
)

function toggleColumn(key: string): void {
  // useStorage persists the mutation automatically.
  columnVisibility.value = {
    ...columnVisibility.value,
    [key]: !isVisible(key),
  }
}

// String value for a column's cell, used by both the Recent table (non-document
// columns) and the mobile meta grid. The Document column renders a link, so it
// is handled directly in the template rather than here.
function cellValue(key: string, job: JobInfo): string {
  switch (key) {
    case 'task':
      return taskLabel(job.task_name)
    case 'finished':
      return formatDateTime(job.finished_at ?? job.scheduled_at)
    case 'duration':
      return formatDuration(job.started_at, job.finished_at)
    case 'cost':
      return formatCost(job.cost_usd)
    case 'error':
      return job.error ?? '—'
    default:
      return ''
  }
}

</script>

<template>
  <div id="jobs-view">
    <PageHeader title="Jobs">
      <template #actions>
        <label class="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 cursor-pointer">
          <input
            v-model="showSystem"
            type="checkbox"
            data-testid="jobs-show-system"
            class="rounded border-gray-300 dark:border-gray-600 text-violet-500 focus:ring-violet-500"
          />
          Show system tasks
        </label>

        <!-- Column-visibility menu (governs the table) -->
        <AppPopover
          :open="showColumnMenu"
          align="right"
          :panel-attrs="{ 'data-testid': 'jobs-columns-menu' }"
          panel-class="absolute mt-1 w-52 py-1"
          @update:open="showColumnMenu = $event"
        >
          <template #trigger="{ open, toggle, triggerRef }">
            <button
              :ref="triggerRef"
              type="button"
              data-testid="jobs-columns-button"
              class="flex items-center gap-1.5 rounded-lg border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-800 px-3 py-1.5 text-sm font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
              :aria-expanded="open"
              @click="toggle"
            >
              <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M9 4h6m-6 4h6m-6 4h6m-6 4h6M4 4v16m16-16v16"
                />
              </svg>
              Columns
            </button>
          </template>

          <label
            v-for="col in COLUMNS"
            :key="col.key"
            class="flex items-center gap-2 px-3 py-1.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer"
          >
            <input
              type="checkbox"
              :checked="isVisible(col.key)"
              :data-testid="`jobs-col-toggle-${col.key}`"
              class="rounded border-gray-300 dark:border-gray-600 text-violet-500 focus:ring-violet-500"
              @change="toggleColumn(col.key)"
            />
            {{ col.label }}
          </label>
        </AppPopover>
      </template>
    </PageHeader>

    <p v-if="loading" data-testid="jobs-loading" class="text-gray-600 dark:text-gray-300">
      Loading jobs…
    </p>

    <div
      v-else-if="error"
      data-testid="jobs-error"
      role="alert"
      class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
    >
      {{ error }}
    </div>

    <template v-else>
      <!-- Filter bar: pick a task type, or a document to trace its full history.
           Both are reflected in the URL query for deep-linking. The document
           field grows to fit its hint on one line. -->
      <div class="mb-4 flex flex-wrap items-end gap-4" data-testid="jobs-filter-bar">
        <div class="w-full sm:w-48">
          <AppSelect
            id="jobs-task-filter"
            v-model="taskFilter"
            label="Task type"
            :items="taskOptions"
          />
        </div>
        <div class="w-full sm:flex-1 sm:min-w-[20rem] sm:max-w-xl">
          <AppInput
            id="jobs-document-filter"
            v-model="documentInput"
            label="Document"
            hint="Type to find a document, then pick it to trace its history"
            list="jobs-document-options"
            @input="searchDocuments"
            @change="applyTypedDocument"
          />
          <datalist id="jobs-document-options">
            <option v-for="d in documentSuggestions" :key="d.id" :value="d.title" />
          </datalist>
        </div>
        <div v-if="inHistoryMode" class="pb-1.5" data-testid="jobs-document-chip">
          <span
            class="inline-flex items-center gap-1.5 rounded-full bg-violet-100 dark:bg-violet-500/20 px-3 py-1 text-sm font-medium text-violet-700 dark:text-violet-300"
          >
            Document: {{ documentChipTitle }}
            <button
              type="button"
              data-testid="jobs-document-chip-clear"
              class="text-violet-500 hover:text-violet-700 dark:hover:text-violet-200"
              aria-label="Clear document filter"
              @click="clearDocumentFilter"
            >
              ✕
            </button>
          </span>
        </div>
      </div>

      <p
        v-if="orderedJobs.length === 0"
        data-testid="jobs-empty"
        class="text-sm text-gray-500 dark:text-gray-400"
      >
        {{ inHistoryMode ? 'No jobs for this document yet.' : 'No jobs yet.' }}
      </p>
      <template v-else>
        <!-- Jobs table (tablet & desktop) — table-fixed with per-column <col>
             widths so the Document column clamps and stops dominating. Active
             rows sort first and carry a spinner in the Status cell. -->
        <div class="hidden sm:block overflow-x-auto bg-white dark:bg-gray-800 rounded-lg shadow-xs">
          <table class="table-fixed w-full text-sm">
            <colgroup>
              <col v-for="col in visibleColumns" :key="col.key" :style="col.style" />
            </colgroup>
            <thead
              class="text-xs uppercase text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700/60"
            >
              <tr>
                <th
                  v-for="col in visibleColumns"
                  :key="col.key"
                  :data-testid="`jobs-col-header-${col.key}`"
                  class="text-left font-semibold px-4 py-3 whitespace-nowrap"
                >
                  {{ col.label }}
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700/60">
              <tr
                v-for="job in orderedJobs"
                :key="job.id"
                data-testid="jobs-row"
                :data-active="job.active ? 'true' : undefined"
                :class="job.active ? 'bg-violet-50/40 dark:bg-violet-500/5' : undefined"
              >
                <td
                  v-for="col in visibleColumns"
                  :key="col.key"
                  :data-testid="`jobs-col-cell-${col.key}`"
                  class="px-4 py-3"
                  :class="{
                    'text-gray-600 dark:text-gray-300': col.key !== 'error',
                    'text-red-600 dark:text-red-400 truncate': col.key === 'error',
                    'whitespace-nowrap': col.key === 'finished' || col.key === 'duration' || col.key === 'cost',
                  }"
                  :title="col.key === 'error' ? (job.error ?? '') : undefined"
                >
                  <RouterLink
                    v-if="col.key === 'document' && job.document_id !== null"
                    :to="`/documents/${job.document_id}`"
                    :title="documentLabel(job)"
                    class="text-violet-500 hover:text-violet-600 dark:hover:text-violet-400 block truncate"
                    >{{ documentLabel(job) }}</RouterLink
                  >
                  <span
                    v-else-if="col.key === 'document'"
                    class="inline-flex items-center gap-1.5 min-w-0 text-gray-500 dark:text-gray-400"
                    data-testid="jobs-system-label"
                  >
                    <AppBadge colour="grey">System</AppBadge>
                    <span class="truncate">{{ taskLabel(job.task_name) }}</span>
                  </span>
                  <span v-else-if="col.key === 'status'" class="inline-flex items-center gap-1.5">
                    <svg
                      v-if="job.active"
                      data-testid="jobs-active-indicator"
                      class="w-3.5 h-3.5 animate-spin text-violet-500 shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      aria-label="In progress"
                      role="img"
                    >
                      <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                      <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
                    </svg>
                    <AppBadge :colour="statusColour(job)">{{ statusLabel(job) }}</AppBadge>
                  </span>
                  <template v-else>{{ cellValue(col.key, job) }}</template>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- Jobs cards (mobile) — headline Document + Status, then the remaining
             visible columns as a meta grid (respects column prefs). -->
        <ul
          class="sm:hidden bg-white dark:bg-gray-800 rounded-lg shadow-xs divide-y divide-gray-100 dark:divide-gray-700/60"
          data-testid="jobs-cards"
        >
          <li
            v-for="job in orderedJobs"
            :key="job.id"
            class="p-4"
            data-testid="jobs-card"
            :class="job.active ? 'bg-violet-50/40 dark:bg-violet-500/5' : undefined"
          >
            <div class="flex items-center justify-between gap-2">
              <RouterLink
                v-if="isVisible('document') && job.document_id !== null"
                :to="`/documents/${job.document_id}`"
                class="font-medium text-violet-500 hover:text-violet-600 dark:hover:text-violet-400 truncate"
                >{{ documentLabel(job) }}</RouterLink
              >
              <span
                v-else-if="isVisible('document')"
                class="inline-flex items-center gap-1.5 min-w-0 font-medium text-gray-500 dark:text-gray-400"
              >
                <AppBadge colour="grey">System</AppBadge>
                <span class="truncate">{{ taskLabel(job.task_name) }}</span>
              </span>
              <span v-else class="font-medium text-gray-800 dark:text-gray-100 truncate"
                >Job #{{ job.id }}</span
              >
              <span v-if="isVisible('status')" class="inline-flex items-center gap-1.5 shrink-0">
                <svg
                  v-if="job.active"
                  class="w-3.5 h-3.5 animate-spin text-violet-500 shrink-0"
                  fill="none"
                  viewBox="0 0 24 24"
                  aria-label="In progress"
                  role="img"
                >
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
                </svg>
                <AppBadge :colour="statusColour(job)">{{ statusLabel(job) }}</AppBadge>
              </span>
            </div>

            <div v-if="cardMetaColumns.length" class="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <div v-for="col in cardMetaColumns" :key="col.key">
                <span class="text-gray-500 dark:text-gray-400">{{ col.label }}:</span>
                <span
                  class="ml-1 break-words"
                  :class="col.key === 'error' ? 'text-red-600 dark:text-red-400' : 'text-gray-700 dark:text-gray-300'"
                  >{{ cellValue(col.key, job) }}</span
                >
              </div>
            </div>
          </li>
        </ul>
      </template>
    </template>
  </div>
</template>
