<script setup lang="ts">
/**
 * Delete confirmation page (route `/documents/:id/delete`).
 *
 * Destructive actions get a real page with its own URL — warning text, an
 * explicit confirm button, and a cancel back-link — not a JS modal. Confirming
 * sends DELETE (apiFetch adds the CSRF header) and redirects to the list with a
 * success banner via the flash store.
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppBackLink, AppButton, AppErrorSummary } from '@/components/app'
import { deleteDocument, getDocument, type DocumentDetail } from '@/api/documents'
import { ApiError } from '@/api/client'
import { useFlashStore } from '@/stores/flash'

const route = useRoute()
const router = useRouter()
const flash = useFlashStore()

const doc = ref<DocumentDetail | null>(null)
const notFound = ref(false)
const loadError = ref(false)
const deleting = ref(false)
const deleteError = ref<string | null>(null)

const documentId = computed(() => Number(route.params.id))
// A path string, not {name, params}: after the post-delete redirect this
// computed re-evaluates without an :id param, which a named route would
// fail to resolve.
const detailPath = computed(() => `/documents/${String(route.params.id ?? '')}`)

watch(
  () => route.params.id,
  async (id) => {
    if (route.name !== 'document-delete') return
    doc.value = null
    notFound.value = false
    loadError.value = false
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
    }
  },
  { immediate: true },
)

async function confirmDelete(): Promise<void> {
  if (deleting.value) return
  deleting.value = true
  deleteError.value = null
  try {
    await deleteDocument(documentId.value)
    flash.set(`${doc.value?.title ?? 'The document'} has been deleted.`)
    await router.push({ name: 'documents' })
  } catch (error: unknown) {
    deleteError.value =
      error instanceof ApiError && error.status !== 0
        ? `Could not delete the document: ${error.detail}`
        : 'Could not delete the document — check your connection and try again'
    deleting.value = false
  }
}
</script>

<template>
  <div class="max-w-xl mx-auto">
    <div class="mb-4">
      <AppBackLink :to="detailPath" text="Back to the document" />
    </div>

    <template v-if="doc">
      <div
        class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-red-200 dark:border-red-500/30 p-6"
      >
        <AppErrorSummary v-if="deleteError" :errors="[{ text: deleteError }]" />

        <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-4">
          Are you sure you want to delete “{{ doc.title ?? 'Untitled document' }}”?
        </h1>

        <p class="text-red-600 dark:text-red-400 mb-6" data-testid="delete-warning">
          “{{ doc.title ?? 'Untitled document' }}” will disappear from your library, search and the
          API. This cannot be undone from the web app.
        </p>

        <div class="flex flex-wrap items-center gap-3">
          <AppButton
            type="button"
            variant="warning"
            :disabled="deleting"
            data-testid="confirm-delete"
            @click="confirmDelete"
          >
            Yes, delete this document
          </AppButton>
          <AppButton
            type="button"
            variant="secondary"
            :to="detailPath"
            data-testid="cancel-delete"
          >
            Cancel
          </AppButton>
        </div>
      </div>
    </template>

    <template v-else-if="notFound">
      <div
        class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-6"
      >
        <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-2">
          Document not found
        </h1>
        <p class="text-gray-500 dark:text-gray-400">
          It may already have been deleted, or the link is wrong.
        </p>
      </div>
    </template>
    <div
      v-else-if="loadError"
      class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-6 text-gray-500 dark:text-gray-400"
    >
      Sorry, the document could not be loaded. Try again later.
    </div>
  </div>
</template>
