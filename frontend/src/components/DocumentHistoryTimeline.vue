<script setup lang="ts">
import { computed } from 'vue'

import AppDetails from '@/components/app/AppDetails.vue'
import type { IngestionEvent } from '@/api/documents'

const props = defineProps<{ events: IngestionEvent[] }>()

/** Human labels for the milestone events shown in the curated timeline.
 * Anything not listed here falls back to a humanized version of its raw name;
 * genuinely noisy events (per-stage status_changed, low-signal *_skipped) are
 * hidden by default and only appear under "Show all". Extraction skips and all
 * *_failed events are processing-relevant and always surface. */
const MILESTONE_LABELS: Record<string, string> = {
  received: 'Ingested',
  paperless_imported: 'Imported from Paperless',
  duplicate_upload: 'Duplicate upload detected',
  ocr_completed: 'OCR complete',
  ocr_failed: 'OCR failed',
  extraction_completed: 'Description & metadata added',
  extraction_skipped: 'Extraction skipped',
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
  email_selection: 'Email triage',
}

function humanize(name: string): string {
  const spaced = name.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** True for events that are noise in the at-a-glance view: the per-stage
 * pipeline transitions (except the final "indexed") and the low-signal skips.
 * `extraction_skipped` is NOT noise — extraction is the headline step, so a
 * skip there (disabled / no key / budget / unusable input) is worth showing;
 * failures (`*_failed`) are never skips and always surface. */
function isNoise(event: IngestionEvent): boolean {
  if (event.event === 'status_changed') return event.detail?.to !== 'indexed'
  if (event.event === 'extraction_skipped') return false
  // Pure billing/telemetry for the per-email LLM label pass; the outcome it
  // produced is already narrated by the email_selection triage breakdown.
  if (event.event === 'email_label_completed') return true
  return event.event.endsWith('_skipped')
}

function label(event: IngestionEvent): string {
  if (event.event === 'status_changed' && event.detail?.to === 'indexed') {
    return 'Indexed for search'
  }
  return MILESTONE_LABELS[event.event] ?? humanize(event.event)
}

// --- Extraction breakdown -------------------------------------------------
// `input_mode` = what was sent on the FINAL attempt; `escalated` = whether the
// low-confidence retry ran. The four combinations tell distinct stories — most
// importantly the VISION fallback, where a low-confidence retry re-read the
// ORIGINAL FILE (thin-OCR image-PDF recovery). See docs/ingestion.md.
type ExtractionBreakdown = {
  method: string
  isVisionFallback: boolean
  chips: { label: string; value: string }[]
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function extractionBreakdown(event: IngestionEvent): ExtractionBreakdown | null {
  if (event.event !== 'extraction_completed') return null
  const detail = event.detail ?? {}
  const escalated = detail.escalated === true
  const fromFile = detail.input_mode === 'document' || detail.input_mode === 'image'
  const isVisionFallback = escalated && fromFile

  let method: string
  if (isVisionFallback) {
    method = 'Low confidence — re-read the original file (vision fallback)'
  } else if (escalated) {
    method = 'Low confidence — retried with a stronger model'
  } else if (fromFile) {
    method = 'Read the original file directly (OCR text was unusable)'
  } else {
    method = 'Read the OCR text'
  }

  const chips: { label: string; value: string }[] = []
  if (typeof detail.model === 'string' && detail.model) {
    chips.push({ label: 'Model', value: detail.model })
  }
  if (typeof detail.confidence === 'string' && detail.confidence) {
    chips.push({ label: 'Confidence', value: titleCase(detail.confidence) })
  }
  if (typeof detail.cost_usd === 'number' && detail.cost_usd > 0) {
    chips.push({ label: 'Cost', value: `$${detail.cost_usd.toFixed(4)}` })
  }

  return { method, isVisionFallback, chips }
}

// --- Email triage breakdown -------------------------------------------------
// `email_selection` carries the decision trace written by
// `_selection_event_detail` (src/library/email_ingest.py): sender/subject
// provenance plus one entry per email item (body + attachments) with the
// verdict the selection pipeline reached and why. Rendered as one line per
// item so the raw JSON stays in "Show all".
type EmailSelectionItem = {
  /** Display name: the attachment filename, or `<body>` for the email body. */
  name: string
  verdict: string
  reason: string | null
  /** The labeller thought this might not be a real document — worth an accent. */
  isAmbiguous: boolean
}

type EmailSelectionBreakdown = {
  items: EmailSelectionItem[]
  chips: { label: string; value: string }[]
}

function emailSelectionBreakdown(event: IngestionEvent): EmailSelectionBreakdown | null {
  if (event.event !== 'email_selection') return null
  const detail = event.detail ?? {}
  // Tolerate missing/malformed detail: no items array → no breakdown at all.
  if (!Array.isArray(detail.items)) return null

  const items: EmailSelectionItem[] = []
  for (const raw of detail.items as unknown[]) {
    if (typeof raw !== 'object' || raw === null) continue
    const item = raw as Record<string, unknown>
    const verdict = typeof item.verdict === 'string' && item.verdict ? item.verdict : 'unknown'
    items.push({
      name: typeof item.filename === 'string' && item.filename ? item.filename : '<body>',
      verdict: humanize(verdict),
      reason: typeof item.reason === 'string' && item.reason ? item.reason : null,
      isAmbiguous: verdict === 'flagged_ambiguous',
    })
  }

  const chips: { label: string; value: string }[] = []
  if (typeof detail.email_from === 'string' && detail.email_from) {
    chips.push({ label: 'From', value: detail.email_from })
  }
  if (typeof detail.email_subject === 'string' && detail.email_subject) {
    chips.push({ label: 'Subject', value: detail.email_subject })
  }

  return { items, chips }
}

// --- Skips and failures ---------------------------------------------------
const SKIP_REASONS: Record<string, string> = {
  disabled: 'Extraction is disabled',
  missing_api_key: 'No API key configured',
  input_unusable: 'Input unusable',
  file_too_large: 'File too large',
}

function skipReason(detail: Record<string, unknown>): string {
  const reason = typeof detail.reason === 'string' ? detail.reason : ''
  if (reason === 'budget') {
    const spent = typeof detail.spent_usd === 'number' ? `$${detail.spent_usd.toFixed(2)}` : '?'
    const budget = typeof detail.budget_usd === 'number' ? `$${detail.budget_usd.toFixed(2)}` : '?'
    return `Daily budget reached — ${spent} of ${budget} spent`
  }
  // Unusable/oversized input carries a human "detail" string worth showing verbatim.
  if (typeof detail.detail === 'string' && detail.detail) {
    const reasonLabel = SKIP_REASONS[reason] ?? (reason ? humanize(reason) : 'Skipped')
    return `${reasonLabel} — ${detail.detail}`
  }
  return SKIP_REASONS[reason] ?? (reason ? humanize(reason) : 'Skipped')
}

/** The error/detail string carried by a failure event, if any. */
function failureDetail(detail: Record<string, unknown>): string | null {
  if (typeof detail.error === 'string' && detail.error) return detail.error
  if (typeof detail.detail === 'string' && detail.detail) return detail.detail
  return null
}

/** A short secondary line for events that carry meaningful detail. Extraction
 * successes render their own breakdown block instead (see the template). */
function secondary(event: IngestionEvent): string | null {
  const detail = event.detail ?? {}
  if (event.event === 'user_edited' && Array.isArray(detail.fields) && detail.fields.length) {
    return `Fields: ${(detail.fields as string[]).join(', ')}`
  }
  if (event.event === 'project_changed' && Array.isArray(detail.projects)) {
    const projects = detail.projects as string[]
    return projects.length ? `Projects: ${projects.join(', ')}` : 'Projects cleared'
  }
  if (event.event === 'extraction_skipped') {
    return skipReason(detail)
  }
  if (event.event.endsWith('_failed')) {
    return failureDetail(detail)
  }
  return null
}

const dateTimeFormat = new Intl.DateTimeFormat('en-GB', { dateStyle: 'long', timeStyle: 'short' })

function formatDateTime(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateTimeFormat.format(parsed)
}

/** Newest-first; the API returns them ordered but we sort defensively.
 * Array.prototype.sort is stable, so events sharing a timestamp keep their
 * incoming relative order. Feeds both the milestone timeline and "Show all". */
const ordered = computed(() =>
  [...props.events].sort((a, b) => b.created_at.localeCompare(a.created_at)),
)

const milestones = computed(() => ordered.value.filter((event) => !isNoise(event)))
</script>

<template>
  <section
    id="document-history"
    data-testid="document-history"
    class="card p-5"
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

        <!-- Extraction success: a compact breakdown of how it was processed. -->
        <template v-if="extractionBreakdown(event)">
          <div
            data-testid="history-extraction-method"
            class="text-xs mt-0.5 break-words"
            :class="
              extractionBreakdown(event)!.isVisionFallback
                ? 'text-violet-600 dark:text-violet-300 font-medium'
                : 'text-gray-500 dark:text-gray-400'
            "
          >
            {{ extractionBreakdown(event)!.method }}
          </div>
          <div
            v-if="extractionBreakdown(event)!.chips.length"
            class="flex flex-wrap gap-1.5 mt-1.5"
          >
            <span
              v-for="chip in extractionBreakdown(event)!.chips"
              :key="chip.label"
              data-testid="history-extraction-chip"
              class="inline-flex items-center gap-1 rounded-full bg-gray-100 dark:bg-gray-700/50 px-2 py-0.5 text-xs text-gray-600 dark:text-gray-300"
            >
              <span class="uppercase tracking-wide text-[10px] text-gray-400 dark:text-gray-500">
                {{ chip.label }}
              </span>
              <span class="font-medium break-all">{{ chip.value }}</span>
            </span>
          </div>
        </template>

        <!-- Email triage: what happened to each item of the source email. -->
        <template v-else-if="emailSelectionBreakdown(event)">
          <ul
            v-if="emailSelectionBreakdown(event)!.items.length"
            data-testid="history-email-items"
            class="mt-0.5 space-y-0.5"
          >
            <li
              v-for="(item, itemIndex) in emailSelectionBreakdown(event)!.items"
              :key="itemIndex"
              data-testid="history-email-item"
              class="text-xs break-words"
              :class="
                item.isAmbiguous
                  ? 'text-violet-600 dark:text-violet-300 font-medium'
                  : 'text-gray-500 dark:text-gray-400'
              "
            >
              <span class="font-medium">{{ item.name }}</span>
              <span> — {{ item.verdict }}</span>
              <span v-if="item.reason"> ({{ item.reason }})</span>
            </li>
          </ul>
          <div
            v-if="emailSelectionBreakdown(event)!.chips.length"
            class="flex flex-wrap gap-1.5 mt-1.5"
          >
            <span
              v-for="chip in emailSelectionBreakdown(event)!.chips"
              :key="chip.label"
              data-testid="history-email-chip"
              class="inline-flex items-center gap-1 rounded-full bg-gray-100 dark:bg-gray-700/50 px-2 py-0.5 text-xs text-gray-600 dark:text-gray-300"
            >
              <span class="uppercase tracking-wide text-[10px] text-gray-400 dark:text-gray-500">
                {{ chip.label }}
              </span>
              <span class="font-medium break-all">{{ chip.value }}</span>
            </span>
          </div>
        </template>

        <!-- Everything else with a meaningful detail line (skips, failures, edits). -->
        <div
          v-else-if="secondary(event)"
          data-testid="history-secondary"
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
