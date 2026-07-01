<script setup lang="ts">
import { computed } from 'vue'

import AppDetails from '@/components/app/AppDetails.vue'
import type { IngestionEvent } from '@/api/documents'

const props = defineProps<{ events: IngestionEvent[] }>()

/** Human labels for the milestone events shown in the curated timeline.
 * Anything not listed here falls back to a humanized version of its raw name;
 * genuinely noisy events (per-stage status_changed, *_skipped) are hidden by
 * default and only appear under "Show all". */
const MILESTONE_LABELS: Record<string, string> = {
  received: 'Ingested',
  paperless_imported: 'Imported from Paperless',
  duplicate_upload: 'Duplicate upload detected',
  ocr_completed: 'OCR complete',
  ocr_failed: 'OCR failed',
  extraction_completed: 'Description & metadata added',
  extraction_failed: 'Extraction failed',
  markdown_completed: 'Page markdown generated',
  markdown_failed: 'Markdown failed',
  embedding_failed: 'Embedding failed',
  user_edited: 'Edited',
  project_changed: 'Projects changed',
  note_edited: 'Note edited',
  note_restored: 'Note restored',
  mcp_source_note: 'Source note added',
  review_verified: 'Verified',
  deleted: 'Deleted',
}

function humanize(name: string): string {
  const spaced = name.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** True for events that are noise in the at-a-glance view: the per-stage
 * pipeline transitions (except the final "indexed") and skipped stages. */
function isNoise(event: IngestionEvent): boolean {
  if (event.event === 'status_changed') return event.detail?.to !== 'indexed'
  return event.event.endsWith('_skipped')
}

function label(event: IngestionEvent): string {
  if (event.event === 'status_changed' && event.detail?.to === 'indexed') {
    return 'Indexed for search'
  }
  return MILESTONE_LABELS[event.event] ?? humanize(event.event)
}

/** A short secondary line for events that carry meaningful detail. */
function secondary(event: IngestionEvent): string | null {
  const detail = event.detail ?? {}
  if (event.event === 'user_edited' && Array.isArray(detail.fields) && detail.fields.length) {
    return `Fields: ${(detail.fields as string[]).join(', ')}`
  }
  if (event.event === 'project_changed' && Array.isArray(detail.projects)) {
    const projects = detail.projects as string[]
    return projects.length ? `Projects: ${projects.join(', ')}` : 'Projects cleared'
  }
  return null
}

const dateTimeFormat = new Intl.DateTimeFormat('en-GB', { dateStyle: 'long', timeStyle: 'short' })

function formatDateTime(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateTimeFormat.format(parsed)
}

/** Oldest-first; the API returns them ordered but we sort defensively. */
const ordered = computed(() =>
  [...props.events].sort((a, b) => a.created_at.localeCompare(b.created_at)),
)

const milestones = computed(() => ordered.value.filter((event) => !isNoise(event)))
</script>

<template>
  <section
    id="document-history"
    data-testid="document-history"
    class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
  >
    <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">History</h2>

    <p
      v-if="milestones.length === 0"
      class="text-sm text-gray-500 dark:text-gray-400"
      data-testid="history-empty"
    >
      No events recorded yet.
    </p>

    <ol v-else class="relative border-l border-gray-200 dark:border-gray-700/60 ml-1.5">
      <li
        v-for="(event, index) in milestones"
        :key="index"
        class="ml-4 pb-4 last:pb-0"
        data-testid="history-item"
      >
        <span
          class="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full bg-violet-400 dark:bg-violet-500 ring-4 ring-white dark:ring-gray-800"
          aria-hidden="true"
        />
        <div class="text-sm font-medium text-gray-800 dark:text-gray-100">{{ label(event) }}</div>
        <div class="text-xs text-gray-400 dark:text-gray-500">
          {{ formatDateTime(event.created_at) }}
        </div>
        <div
          v-if="secondary(event)"
          class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 break-words"
        >
          {{ secondary(event) }}
        </div>
      </li>
    </ol>

    <div
      v-if="ordered.length"
      class="mt-4 border-t border-gray-200 dark:border-gray-700/60 pt-3"
    >
      <AppDetails summary="Show all events" data-testid="history-show-all">
        <ul class="divide-y divide-gray-200 dark:divide-gray-700/60" data-testid="history-raw-list">
          <li
            v-for="(event, index) in ordered"
            :key="index"
            class="py-2"
            data-testid="history-raw-item"
          >
            <div class="flex items-baseline justify-between gap-3">
              <span class="font-mono text-xs text-gray-700 dark:text-gray-200 break-all">
                {{ event.event }}
              </span>
              <span class="text-xs text-gray-400 dark:text-gray-500 whitespace-nowrap">
                {{ formatDateTime(event.created_at) }}
              </span>
            </div>
            <pre
              v-if="Object.keys(event.detail ?? {}).length"
              class="mt-1 text-xs text-gray-500 dark:text-gray-400 whitespace-pre-wrap break-words"
            >{{ JSON.stringify(event.detail) }}</pre>
          </li>
        </ul>
      </AppDetails>
    </div>
  </section>
</template>
