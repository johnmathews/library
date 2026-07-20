<script setup lang="ts">
/**
 * Recently Deleted holding area (route `/deleted`).
 *
 * Lists soft-deleted documents awaiting permanent purge. Each is kept for
 * `retention_days` days (from the API response), then removed automatically.
 * Restoring a document sends it back to the normal library and drops it from
 * this list. A clean mosaic grid of cards — no filters, no infinite scroll.
 */
import { computed, onMounted, ref } from 'vue'
import { AppBanner, AppButton, ConfirmDialog, PageHeader } from '@/components/app'
import {
  listDeletedDocuments,
  permanentlyDeleteDocument,
  restoreDocument,
  type DeletedDocumentItem,
} from '@/api/documents'
import { useFlashStore } from '@/stores/flash'
import { useAuthStore } from '@/stores/auth'

const flash = useFlashStore()
const auth = useAuthStore()

// Honours the account's phone_columns Appearance preference, matching the
// dashboard grid (DocumentListView) so the two views agree on phone-band
// column count.
const phoneGridStyle = computed<Record<string, string>>(() => ({
  '--doc-grid-cols-phone': String(auth.phoneColumns),
}))

const loading = ref(true)
const loadError = ref<string | null>(null)
const items = ref<DeletedDocumentItem[]>([])
const retentionDays = ref(0)
const restoringIds = ref<Set<number>>(new Set())

// The card awaiting permanent-delete confirmation (null = dialog closed), and
// the id currently being purged so the dialog can show a pending state.
const pendingDelete = ref<DeletedDocumentItem | null>(null)
const purgingId = ref<number | null>(null)
const confirmMessage = computed(() =>
  pendingDelete.value
    ? `“${pendingDelete.value.title ?? 'This document'}” will be permanently deleted. This cannot be undone.`
    : '',
)

// One-shot banner from a restore action (set below, consumed here on mount) so
// the confirmation survives the card being removed from the list.
const flashMessage = ref(flash.consume())

onMounted(async () => {
  try {
    const response = await listDeletedDocuments()
    items.value = response.items
    retentionDays.value = response.retention_days
  } catch {
    loadError.value = 'Sorry, the deleted documents could not be loaded. Try again later.'
  } finally {
    loading.value = false
  }
})

const dateFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})

function formatDeletedAt(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormat.format(parsed)
}

/** Countdown label for a card. Day 0 means it may be purged at any moment. */
function purgeLabel(days: number): string {
  if (days <= 0) return 'Purges soon'
  return `Purges in ${days} ${days === 1 ? 'day' : 'days'}`
}

async function restore(item: DeletedDocumentItem): Promise<void> {
  if (restoringIds.value.has(item.id)) return
  restoringIds.value.add(item.id)
  try {
    await restoreDocument(item.id)
    items.value = items.value.filter((doc) => doc.id !== item.id)
    flash.set(`${item.title ?? 'Document'} restored`)
    flashMessage.value = flash.consume()
  } catch {
    flash.set('Sorry, the document could not be restored. Try again later.')
    flashMessage.value = flash.consume()
  } finally {
    restoringIds.value.delete(item.id)
  }
}

/** Confirm the permanent (irreversible) deletion of the pending card. */
async function confirmPermanentDelete(): Promise<void> {
  const item = pendingDelete.value
  if (!item || purgingId.value !== null) return
  purgingId.value = item.id
  try {
    await permanentlyDeleteDocument(item.id)
    items.value = items.value.filter((doc) => doc.id !== item.id)
    flash.set(`${item.title ?? 'Document'} permanently deleted`)
    flashMessage.value = flash.consume()
    pendingDelete.value = null
  } catch {
    flash.set('Sorry, the document could not be deleted. Try again later.')
    flashMessage.value = flash.consume()
    pendingDelete.value = null
  } finally {
    purgingId.value = null
  }
}
</script>

<template>
  <AppBanner v-if="flashMessage" variant="success" data-testid="flash-banner" class="mb-6">
    {{ flashMessage }}
  </AppBanner>

  <PageHeader title="Recently Deleted" title-id="deleted-title" />

  <p class="text-sm text-gray-500 dark:text-gray-400 mb-6" data-testid="deleted-intro">
    Deleted documents are held here for {{ retentionDays }}
    {{ retentionDays === 1 ? 'day' : 'days' }} before they are permanently removed. Restore one to
    send it back to your library.
  </p>

  <div
    v-if="loadError"
    class="card p-4 text-gray-600 dark:text-gray-300"
    data-testid="load-error"
  >
    {{ loadError }}
  </div>

  <template v-else-if="!loading">
    <div
      v-if="!items.length"
      class="card p-8 text-center text-gray-500 dark:text-gray-400"
      data-testid="deleted-empty"
    >
      Nothing has been deleted recently. Documents you delete are kept here for
      {{ retentionDays }} {{ retentionDays === 1 ? 'day' : 'days' }}, then permanently removed.
    </div>

    <ul v-else class="app-doc-grid" :style="phoneGridStyle" data-testid="deleted-grid">
      <li
        v-for="item in items"
        :key="item.id"
        class="relative bg-white dark:bg-gray-800 overflow-hidden app-doc-card"
        data-testid="doc-card"
      >
        <div class="p-5 app-doc-card__body">
          <h2 class="app-doc-card__title mb-2">
            <RouterLink
              class="text-violet-600 font-semibold hover:underline"
              :to="{ name: 'document-detail', params: { id: item.id } }"
            >
              {{ item.title ?? 'Untitled document' }}
            </RouterLink>
          </h2>
          <p class="text-sm text-gray-500 dark:text-gray-400 app-doc-card__meta">
            <span v-if="item.kind">{{ item.kind.name }}</span>
            <span v-if="item.kind && item.sender"> · </span>
            <span v-if="item.sender">{{ item.sender.name }}</span>
          </p>
          <p class="text-sm text-gray-500 dark:text-gray-400 mt-2" data-testid="deleted-at">
            Deleted {{ formatDeletedAt(item.deleted_at) }}
          </p>
          <p
            class="text-sm font-medium text-violet-600 dark:text-violet-400 mt-1"
            data-testid="purge-countdown"
          >
            {{ purgeLabel(item.days_remaining) }}
          </p>
          <div class="mt-4 flex flex-wrap items-center gap-2">
            <AppButton
              type="button"
              variant="secondary"
              size="sm"
              :disabled="restoringIds.has(item.id)"
              :data-testid="`restore-${item.id}`"
              @click="restore(item)"
            >
              {{ restoringIds.has(item.id) ? 'Restoring…' : 'Restore' }}
            </AppButton>
            <AppButton
              type="button"
              variant="warning"
              size="sm"
              :data-testid="`purge-${item.id}`"
              @click="pendingDelete = item"
            >
              Delete permanently
            </AppButton>
          </div>
        </div>
      </li>
    </ul>
  </template>

  <ConfirmDialog
    :open="pendingDelete !== null"
    title="Delete permanently?"
    :message="confirmMessage"
    confirm-label="Delete permanently"
    :busy="purgingId !== null"
    @confirm="confirmPermanentDelete"
    @cancel="pendingDelete = null"
  />
</template>
