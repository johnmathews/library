<script setup lang="ts">
/**
 * Upload page (route `/upload`).
 *
 * Multi-file upload: each selected file is sent independently with XHR
 * progress, then its document is polled (GET /api/documents/{id}) until
 * the pipeline reaches `indexed` or `failed`. Duplicates (200 with
 * `duplicate: true`) get a notification banner pointing at the existing
 * document. 413/415/network failures land in a GOV.UK error summary.
 *
 * The file input deliberately has `accept="image/*,application/pdf"` and
 * NO `capture` attribute: on iOS/Android the picker then offers both the
 * camera and the photo library (a `capture` attribute would force the
 * camera and hide the library).
 */
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import GovButton from '@/components/govuk/GovButton.vue'
import GovErrorSummary from '@/components/govuk/GovErrorSummary.vue'
import GovFileUpload from '@/components/govuk/GovFileUpload.vue'
import GovNotificationBanner from '@/components/govuk/GovNotificationBanner.vue'
import GovTag from '@/components/govuk/GovTag.vue'
import type { ErrorSummaryItem } from '@/components/govuk'
import AppProgressBar from '@/components/AppProgressBar.vue'
import { getDocument, uploadDocument } from '@/api/documents'
import { ApiError } from '@/api/client'

const props = withDefaults(
  defineProps<{
    /** Delay between status polls; tests pass 0. */
    pollIntervalMs?: number
    /** Give up polling (leave the entry as "processing") after this long. */
    pollTimeoutMs?: number
  }>(),
  { pollIntervalMs: 2000, pollTimeoutMs: 180_000 },
)

type Phase = 'uploading' | 'processing' | 'indexed' | 'failed' | 'duplicate' | 'error'

interface UploadEntry {
  key: number
  name: string
  phase: Phase
  /** Upload transfer progress, 0–100. */
  progress: number
  documentId: number | null
  /** Human-readable failure reason. */
  message: string | null
}

const selected = ref<File[] | null>(null)
const fileError = ref<string | null>(null)
const entries = ref<UploadEntry[]>([])
let nextKey = 1
let unmounted = false
onBeforeUnmount(() => {
  unmounted = true
})

const duplicates = computed(() => entries.value.filter((entry) => entry.phase === 'duplicate'))
const successes = computed(() => entries.value.filter((entry) => entry.phase === 'indexed'))
const failures = computed<ErrorSummaryItem[]>(() =>
  entries.value
    .filter((entry) => entry.phase === 'error' || entry.phase === 'failed')
    .map((entry) => ({ text: `${entry.name}: ${entry.message ?? 'upload failed'}` })),
)

function onSubmit(): void {
  if (!selected.value?.length) {
    fileError.value = 'Select at least one file to upload'
    return
  }
  fileError.value = null
  const files = selected.value
  selected.value = null
  for (const file of files) {
    const entry = reactive<UploadEntry>({
      key: nextKey++,
      name: file.name,
      phase: 'uploading',
      progress: 0,
      documentId: null,
      message: null,
    })
    entries.value.push(entry)
    void processFile(entry, file)
  }
}

async function processFile(entry: UploadEntry, file: File): Promise<void> {
  try {
    const result = await uploadDocument(file, (fraction) => {
      entry.progress = Math.round(fraction * 100)
    })
    entry.documentId = result.id
    if (result.duplicate) {
      entry.phase = 'duplicate'
      return
    }
    if (result.status === 'indexed') {
      entry.phase = 'indexed'
      return
    }
    if (result.status === 'failed') {
      entry.phase = 'failed'
      entry.message = 'processing failed'
      return
    }
    entry.phase = 'processing'
    await pollStatus(entry)
  } catch (error: unknown) {
    entry.phase = 'error'
    entry.message = friendlyError(error)
  }
}

/** Poll the document until the pipeline finishes (indexed/failed). */
async function pollStatus(entry: UploadEntry): Promise<void> {
  const deadline = Date.now() + props.pollTimeoutMs
  while (!unmounted && Date.now() <= deadline) {
    await sleep(props.pollIntervalMs)
    if (unmounted || entry.documentId === null) return
    try {
      const doc = await getDocument(entry.documentId)
      if (doc.status === 'indexed') {
        entry.phase = 'indexed'
        return
      }
      if (doc.status === 'failed') {
        entry.phase = 'failed'
        entry.message = 'processing failed — see the document page for details'
        return
      }
    } catch {
      // transient poll error: keep trying until the deadline
    }
  }
  // Still processing at the deadline; the document page will catch up.
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function friendlyError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 413) return 'the file is too large'
    if (error.status === 415) return 'this file type is not supported — upload a PDF or an image'
    if (error.status === 409) return 'this file matches a deleted document'
    if (error.status === 0) return 'network problem — check your connection and try again'
    return error.detail
  }
  return 'something went wrong — try again'
}

function phaseLabel(phase: Phase): { text: string; colour: 'green' | 'yellow' | 'red' | 'grey' } {
  switch (phase) {
    case 'indexed':
      return { text: 'Indexed', colour: 'green' }
    case 'processing':
      return { text: 'Processing', colour: 'yellow' }
    case 'duplicate':
      return { text: 'Already in your library', colour: 'grey' }
    case 'uploading':
      return { text: 'Uploading', colour: 'grey' }
    default:
      return { text: 'Failed', colour: 'red' }
  }
}
</script>

<template>
  <div class="govuk-grid-row">
    <div class="govuk-grid-column-two-thirds">
      <GovErrorSummary v-if="failures.length" :errors="failures" />

      <GovNotificationBanner v-if="successes.length" variant="success" data-testid="success-banner">
        <h3 class="govuk-notification-banner__heading">
          {{ successes.length === 1 ? 'Document uploaded and indexed' : 'Documents uploaded and indexed' }}
        </h3>
        <p v-for="entry in successes" :key="entry.key" class="govuk-body">
          <RouterLink
            class="govuk-notification-banner__link"
            :to="{ name: 'document-detail', params: { id: entry.documentId! } }"
          >
            {{ entry.name }}
          </RouterLink>
        </p>
      </GovNotificationBanner>

      <GovNotificationBanner v-if="duplicates.length" data-testid="duplicate-banner">
        <p v-for="entry in duplicates" :key="entry.key" class="govuk-body">
          {{ entry.name }} is already in your library.
          <RouterLink
            class="govuk-notification-banner__link"
            :to="{ name: 'document-detail', params: { id: entry.documentId! } }"
          >
            View the document</RouterLink
          >.
        </p>
      </GovNotificationBanner>

      <h1 class="govuk-heading-xl">Upload documents</h1>

      <form novalidate @submit.prevent="onSubmit">
        <GovFileUpload
          id="file-upload"
          v-model="selected"
          label="Choose files"
          hint="PDFs and photos, up to 100MB each. On a phone you can take a photo or pick one from your library."
          multiple
          accept="image/*,application/pdf"
          :error-message="fileError ?? undefined"
        />
        <GovButton type="submit">Upload</GovButton>
      </form>

      <ul v-if="entries.length" class="govuk-list app-upload-list" data-testid="upload-list">
        <li v-for="entry in entries" :key="entry.key" class="app-upload-list__item">
          <span class="app-upload-list__name">{{ entry.name }}</span>
          <AppProgressBar
            v-if="entry.phase === 'uploading'"
            :label="`Uploading ${entry.name}`"
            :value="entry.progress"
          />
          <GovTag v-else :colour="phaseLabel(entry.phase).colour">
            {{ phaseLabel(entry.phase).text }}
          </GovTag>
          <RouterLink
            v-if="entry.phase === 'indexed' && entry.documentId !== null"
            class="govuk-link app-upload-list__link"
            :to="{ name: 'document-detail', params: { id: entry.documentId } }"
          >
            View<span class="govuk-visually-hidden"> {{ entry.name }}</span>
          </RouterLink>
        </li>
      </ul>
    </div>
  </div>
</template>
