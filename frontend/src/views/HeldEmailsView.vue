<script setup lang="ts">
/**
 * Held emails (route `/held-emails`): the hold-for-review queue.
 *
 * Emails the ingest pipeline judged not library-worthy sit here (their
 * messages safe in the IMAP Held folder) until a human resolves them. Each row
 * shows who/what/when/why (verdict chip + reason line) and expands into the
 * structured per-item decision trace (loaded lazily via GET detail). Actions
 * on a held row: "Ingest anyway" (queues the override task; the row shows a
 * queued state while the store polls it to resolution) and "Dismiss" (DB-only,
 * immediate). Resolved rows — visible under the ingested/dismissed/all filters
 * — show the outcome, links to any created documents, and the last error.
 *
 * The trace rendering mirrors the "Email triage" breakdown in
 * DocumentHistoryTimeline.vue (one line per item: filename ?? '<body>' →
 * stage → verdict (reason), plus From/Subject chips) — parallel markup with
 * the same classes; the two read different event shapes so they stay separate.
 */
import { onMounted, ref } from 'vue'
import { AppBadge, AppBanner, PageHeader } from '@/components/app'
import { getHeldEmail, type HeldEmailItem, type HeldEmailListStatus } from '@/api/heldEmails'
import { useHeldEmailsStore } from '@/stores/heldEmails'

const store = useHeldEmailsStore()

const STATUS_OPTIONS: { value: HeldEmailListStatus; label: string }[] = [
  { value: 'held', label: 'Held' },
  { value: 'ingested', label: 'Ingested' },
  { value: 'dismissed', label: 'Dismissed' },
  { value: 'all', label: 'All' },
]

const statusFilter = ref<HeldEmailListStatus>('held')

// This view is behind the async auth guard (it is not the persistent layout),
// so a mount-time load is safe here — the session is already resolved.
onMounted(() => {
  void store.load(statusFilter.value)
})

function onStatusChange(event: Event): void {
  statusFilter.value = (event.target as HTMLSelectElement).value as HeldEmailListStatus
  void store.load(statusFilter.value)
}

// --- Presentation helpers ---------------------------------------------------

const VERDICT_LABELS: Record<string, string> = {
  llm_hold: 'LLM hold',
  below_substance: 'Below substance',
  nothing_ingested: 'Nothing ingested',
  sender_unknown: 'Sender unknown',
}

function humanize(name: string): string {
  const spaced = name.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

function verdictLabel(verdict: string): string {
  return VERDICT_LABELS[verdict] ?? humanize(verdict)
}

const dateTimeFormat = new Intl.DateTimeFormat('en-GB', { dateStyle: 'long', timeStyle: 'short' })

function formatDateTime(iso: string | null): string {
  if (!iso) return ''
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateTimeFormat.format(parsed)
}

// --- Per-row trace (lazy, cached per id) -------------------------------------

type TraceState =
  | { state: 'loading' }
  | { state: 'error' }
  | { state: 'ready'; trace: Record<string, unknown> }

const expandedIds = ref<Set<number>>(new Set())
const traces = ref<Record<number, TraceState>>({})

async function toggleTrace(id: number): Promise<void> {
  if (expandedIds.value.has(id)) {
    expandedIds.value.delete(id)
    return
  }
  expandedIds.value.add(id)
  if (traces.value[id]?.state === 'ready') return
  traces.value[id] = { state: 'loading' }
  try {
    const detail = await getHeldEmail(id)
    traces.value[id] = { state: 'ready', trace: detail.trace ?? {} }
  } catch {
    traces.value[id] = { state: 'error' }
  }
}

/** One rendered line of the decision trace (mirrors the timeline's
 *  EmailSelectionItem shape, plus the pipeline stage the verdict came from). */
type TraceLine = {
  name: string
  stage: string
  verdict: string
  reason: string | null
  isAmbiguous: boolean
}

function traceLines(trace: Record<string, unknown>): TraceLine[] {
  // Tolerate missing/malformed detail, like emailSelectionBreakdown does.
  if (!Array.isArray(trace.items)) return []
  const lines: TraceLine[] = []
  for (const raw of trace.items as unknown[]) {
    if (typeof raw !== 'object' || raw === null) continue
    const item = raw as Record<string, unknown>
    const verdict = typeof item.verdict === 'string' && item.verdict ? item.verdict : 'unknown'
    lines.push({
      name: typeof item.filename === 'string' && item.filename ? item.filename : '<body>',
      stage: typeof item.stage === 'string' && item.stage ? item.stage : 'unknown',
      verdict: humanize(verdict),
      reason: typeof item.reason === 'string' && item.reason ? item.reason : null,
      isAmbiguous: verdict === 'flagged_ambiguous',
    })
  }
  return lines
}

function traceChips(trace: Record<string, unknown>): { label: string; value: string }[] {
  const chips: { label: string; value: string }[] = []
  if (typeof trace.email_from === 'string' && trace.email_from) {
    chips.push({ label: 'From', value: trace.email_from })
  }
  if (typeof trace.email_subject === 'string' && trace.email_subject) {
    chips.push({ label: 'Subject', value: trace.email_subject })
  }
  return chips
}

function isQueued(row: HeldEmailItem): boolean {
  return store.queuedIds.has(row.id)
}
</script>

<template>
  <div data-testid="held-emails-view">
    <PageHeader
      title="Held emails"
      title-id="held-emails-title"
      description="Emails the ingest pipeline held for review instead of filing. Ingest one anyway, or dismiss it — the original message always stays recoverable."
    />

    <!-- Control row (mosaic conventions: uppercase-xs label, native select). -->
    <div class="flex flex-wrap items-end gap-3 mb-4">
      <label class="flex flex-col gap-1">
        <span class="text-xs font-medium uppercase tracking-wide text-gray-400">Status</span>
        <select
          :value="statusFilter"
          class="form-select py-1 text-sm"
          aria-label="Status filter"
          data-testid="held-emails-status-filter"
          @change="onStatusChange"
        >
          <option v-for="opt in STATUS_OPTIONS" :key="opt.value" :value="opt.value">
            {{ opt.label }}
          </option>
        </select>
      </label>
      <p
        v-if="!store.loading && store.items.length"
        class="text-sm text-gray-500 dark:text-gray-400 py-1"
        data-testid="held-emails-count"
      >
        {{ store.total }} {{ store.total === 1 ? 'email' : 'emails' }}
      </p>
    </div>

    <AppBanner v-if="store.actionError" variant="error" data-testid="held-emails-action-error" class="mb-4">
      {{ store.actionError }}
    </AppBanner>

    <div
      v-if="store.loadError"
      class="card p-4 text-gray-600 dark:text-gray-300"
      data-testid="load-error"
    >
      {{ store.loadError }}
    </div>

    <template v-else-if="!store.loading">
      <div
        v-if="!store.items.length"
        class="card p-8 text-center text-gray-500 dark:text-gray-400"
        data-testid="held-emails-empty"
      >
        <template v-if="statusFilter === 'held'">
          No held emails — everything filed itself.
        </template>
        <template v-else>No emails under this filter.</template>
      </div>

      <ul v-else class="space-y-3">
        <li
          v-for="row in store.items"
          :key="row.id"
          class="card p-4"
          data-testid="held-email-row"
        >
          <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span
                  class="font-medium text-gray-800 dark:text-gray-100 break-all"
                  data-testid="held-email-sender"
                >
                  {{ row.sender ?? 'Unknown sender' }}
                </span>
                <AppBadge
                  :colour="row.verdict === 'llm_hold' ? 'purple' : 'grey'"
                  data-testid="held-email-verdict"
                >
                  {{ verdictLabel(row.verdict) }}
                </AppBadge>
                <AppBadge v-if="row.status === 'ingested'" colour="green" data-testid="held-email-status">
                  Ingested
                </AppBadge>
                <AppBadge v-else-if="row.status === 'dismissed'" colour="grey" data-testid="held-email-status">
                  Dismissed
                </AppBadge>
              </div>
              <p
                class="text-sm text-gray-600 dark:text-gray-300 mt-0.5 break-words"
                data-testid="held-email-subject"
              >
                {{ row.subject ?? '(no subject)' }}
              </p>
              <p class="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                Held {{ formatDateTime(row.created_at) }}
                <template v-if="row.owner"> · {{ row.owner }}</template>
                <template v-if="row.resolved_at"> · resolved {{ formatDateTime(row.resolved_at) }}</template>
              </p>
              <p
                v-if="row.reason"
                class="text-xs text-gray-500 dark:text-gray-400 mt-1 break-words"
                data-testid="held-email-reason"
              >
                {{ row.reason }}
              </p>
              <p
                v-if="row.document_ids.length"
                class="text-xs text-gray-500 dark:text-gray-400 mt-1"
                data-testid="held-email-documents"
              >
                Created:
                <RouterLink
                  v-for="documentId in row.document_ids"
                  :key="documentId"
                  :to="{ name: 'document-detail', params: { id: documentId } }"
                  class="text-violet-600 dark:text-violet-400 hover:underline mr-1.5"
                  data-testid="held-email-document-link"
                >
                  #{{ documentId }}
                </RouterLink>
              </p>
              <p
                v-if="row.last_error"
                class="text-xs text-amber-700 dark:text-amber-400 mt-1 break-words"
                data-testid="held-email-error"
              >
                {{ row.last_error }}
              </p>
            </div>

            <div class="flex flex-wrap items-center gap-2 sm:shrink-0">
              <button
                type="button"
                class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300"
                :aria-expanded="expandedIds.has(row.id)"
                data-testid="held-email-trace-toggle"
                @click="toggleTrace(row.id)"
              >
                {{ expandedIds.has(row.id) ? 'Hide trace' : 'Trace' }}
              </button>
              <template v-if="row.status === 'held'">
                <span
                  v-if="isQueued(row)"
                  class="text-sm font-medium text-violet-600 dark:text-violet-400"
                  data-testid="held-email-queued"
                >
                  Queued — ingesting…
                </span>
                <template v-else>
                  <button
                    type="button"
                    class="btn-sm border-violet-300 bg-violet-50 text-violet-700 hover:bg-violet-100 disabled:opacity-60 dark:border-violet-500/40 dark:bg-violet-500/10 dark:text-violet-300 dark:hover:bg-violet-500/20"
                    :disabled="Boolean(store.acting[row.id])"
                    data-testid="held-email-ingest"
                    @click="store.ingest(row.id)"
                  >
                    {{ store.acting[row.id] === 'ingest' ? 'Queueing…' : 'Ingest anyway' }}
                  </button>
                  <button
                    type="button"
                    class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300 disabled:opacity-60"
                    :disabled="Boolean(store.acting[row.id])"
                    data-testid="held-email-dismiss"
                    @click="store.dismiss(row.id)"
                  >
                    {{ store.acting[row.id] === 'dismiss' ? 'Dismissing…' : 'Dismiss' }}
                  </button>
                </template>
              </template>
            </div>
          </div>

          <!-- Expanded decision trace (same classes as the timeline's Email
               triage breakdown; loaded lazily on first expand). -->
          <div
            v-if="expandedIds.has(row.id)"
            class="mt-3 border-t border-gray-200 dark:border-gray-700/60 pt-3"
            data-testid="held-email-trace"
          >
            <p
              v-if="traces[row.id]?.state === 'loading'"
              class="text-xs text-gray-400 dark:text-gray-500"
            >
              Loading trace…
            </p>
            <p
              v-else-if="traces[row.id]?.state === 'error'"
              class="text-xs text-amber-700 dark:text-amber-400"
            >
              Sorry, the trace could not be loaded. Try again later.
            </p>
            <template v-else-if="traces[row.id]?.state === 'ready'">
              <ul
                v-if="traceLines((traces[row.id] as { trace: Record<string, unknown> }).trace).length"
                class="space-y-0.5"
              >
                <li
                  v-for="(line, lineIndex) in traceLines((traces[row.id] as { trace: Record<string, unknown> }).trace)"
                  :key="lineIndex"
                  class="text-xs break-words"
                  :class="
                    line.isAmbiguous
                      ? 'text-violet-600 dark:text-violet-300 font-medium'
                      : 'text-gray-500 dark:text-gray-400'
                  "
                  data-testid="held-email-trace-item"
                >
                  <span class="font-medium">{{ line.name }}</span>
                  <span> — {{ line.stage }}</span>
                  <span> → {{ line.verdict }}</span>
                  <span v-if="line.reason"> ({{ line.reason }})</span>
                </li>
              </ul>
              <p v-else class="text-xs text-gray-400 dark:text-gray-500">
                No per-item trace was recorded for this email.
              </p>
              <div
                v-if="traceChips((traces[row.id] as { trace: Record<string, unknown> }).trace).length"
                class="flex flex-wrap gap-1.5 mt-1.5"
              >
                <span
                  v-for="chip in traceChips((traces[row.id] as { trace: Record<string, unknown> }).trace)"
                  :key="chip.label"
                  class="inline-flex items-center gap-1 rounded-full bg-gray-100 dark:bg-gray-700/50 px-2 py-0.5 text-xs text-gray-600 dark:text-gray-300"
                  data-testid="held-email-trace-chip"
                >
                  <span class="uppercase tracking-wide text-[10px] text-gray-400 dark:text-gray-500">
                    {{ chip.label }}
                  </span>
                  <span class="font-medium break-all">{{ chip.value }}</span>
                </span>
              </div>
            </template>
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>
