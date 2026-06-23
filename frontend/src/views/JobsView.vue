<script setup lang="ts">
/**
 * Jobs dashboard (route `/jobs`): one row per document being processed, showing
 * its current pipeline stage, extraction cost, and any error (the server
 * collapses a document's several jobs into its latest one). Split into Active
 * (queued or running) and Recent (finished) sections. Refetches whenever the
 * live jobs store reports a document finishing, so the history stays current
 * without a manual reload. Document-less system tasks (the email poll) are
 * hidden unless "Show system tasks" is on.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { AppBadge } from '@/components/app'
import { listJobs, type JobInfo } from '@/api/documents'
import { useJobsStore } from '@/stores/jobs'

const jobsStore = useJobsStore()

const jobs = ref<JobInfo[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
// Document-less system/periodic jobs (the email poll) are hidden by default so
// their constant successes don't bury document work; this toggles them back in.
const showSystem = ref(false)

const activeJobs = computed(() => jobs.value.filter((job) => job.active))
const historicalJobs = computed(() => jobs.value.filter((job) => !job.active))

async function load(): Promise<void> {
  error.value = null
  try {
    jobs.value = await listJobs(200, showSystem.value)
  } catch {
    error.value = 'Could not load jobs. Try refreshing the page.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
// A change in the live active count means a document started or finished —
// refresh so the table (and its historical section) reflects the new state.
watch(() => jobsStore.activeCount, () => void load())
// Re-fetch when the system-task filter flips (the server applies it).
watch(showSystem, () => void load())

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
</script>

<template>
  <div id="jobs-view">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-6">Jobs</h1>

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
      <!-- Active -->
      <section class="mb-8">
        <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">
          Active
          <span class="text-gray-400 dark:text-gray-500 font-normal">({{ activeJobs.length }})</span>
        </h2>
        <p
          v-if="activeJobs.length === 0"
          data-testid="jobs-active-empty"
          class="text-sm text-gray-500 dark:text-gray-400"
        >
          Nothing is processing right now.
        </p>
        <div v-else class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg shadow-xs">
          <table class="w-full text-sm">
            <thead
              class="text-xs uppercase text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700/60"
            >
              <tr>
                <th class="text-left font-semibold px-4 py-3">Document</th>
                <th class="text-left font-semibold px-4 py-3">Task</th>
                <th class="text-left font-semibold px-4 py-3">Status</th>
                <th class="text-left font-semibold px-4 py-3">Started</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700/60">
              <tr v-for="job in activeJobs" :key="job.id" data-testid="jobs-active-row">
                <td class="px-4 py-3">
                  <RouterLink
                    v-if="job.document_id !== null"
                    :to="`/documents/${job.document_id}`"
                    class="text-violet-500 hover:text-violet-600 dark:hover:text-violet-400 truncate"
                    >{{ documentLabel(job) }}</RouterLink
                  >
                  <span v-else class="text-gray-500 dark:text-gray-400">—</span>
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300">{{ taskLabel(job.task_name) }}</td>
                <td class="px-4 py-3">
                  <AppBadge :colour="statusColour(job)">{{ statusLabel(job) }}</AppBadge>
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300 whitespace-nowrap">
                  {{ formatDateTime(job.started_at ?? job.scheduled_at) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Recent / historical -->
      <section>
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">
            Recent
            <span class="text-gray-400 dark:text-gray-500 font-normal"
              >({{ historicalJobs.length }})</span
            >
          </h2>
          <label class="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 cursor-pointer">
            <input
              v-model="showSystem"
              type="checkbox"
              data-testid="jobs-show-system"
              class="rounded border-gray-300 dark:border-gray-600 text-violet-500 focus:ring-violet-500"
            />
            Show system tasks
          </label>
        </div>
        <p
          v-if="historicalJobs.length === 0"
          data-testid="jobs-historical-empty"
          class="text-sm text-gray-500 dark:text-gray-400"
        >
          No completed jobs yet.
        </p>
        <div v-else class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg shadow-xs">
          <table class="w-full text-sm">
            <thead
              class="text-xs uppercase text-gray-400 dark:text-gray-500 border-b border-gray-100 dark:border-gray-700/60"
            >
              <tr>
                <th class="text-left font-semibold px-4 py-3">Document</th>
                <th class="text-left font-semibold px-4 py-3">Task</th>
                <th class="text-left font-semibold px-4 py-3">Status</th>
                <th class="text-left font-semibold px-4 py-3">Finished</th>
                <th class="text-left font-semibold px-4 py-3">Duration</th>
                <th class="text-left font-semibold px-4 py-3">Cost</th>
                <th class="text-left font-semibold px-4 py-3">Error</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-gray-100 dark:divide-gray-700/60">
              <tr v-for="job in historicalJobs" :key="job.id" data-testid="jobs-historical-row">
                <td class="px-4 py-3">
                  <RouterLink
                    v-if="job.document_id !== null"
                    :to="`/documents/${job.document_id}`"
                    class="text-violet-500 hover:text-violet-600 dark:hover:text-violet-400 truncate"
                    >{{ documentLabel(job) }}</RouterLink
                  >
                  <span v-else class="text-gray-500 dark:text-gray-400">—</span>
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300">{{ taskLabel(job.task_name) }}</td>
                <td class="px-4 py-3">
                  <AppBadge :colour="statusColour(job)">{{ statusLabel(job) }}</AppBadge>
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300 whitespace-nowrap">
                  {{ formatDateTime(job.finished_at ?? job.scheduled_at) }}
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300 whitespace-nowrap">
                  {{ formatDuration(job.started_at, job.finished_at) }}
                </td>
                <td class="px-4 py-3 text-gray-600 dark:text-gray-300">{{ formatCost(job.cost_usd) }}</td>
                <td class="px-4 py-3 text-red-600 dark:text-red-400 max-w-xs truncate" :title="job.error ?? ''">
                  {{ job.error ?? '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>
