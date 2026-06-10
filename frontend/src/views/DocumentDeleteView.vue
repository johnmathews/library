<script setup lang="ts">
/**
 * Delete confirmation page (route `/documents/:id/delete`).
 *
 * GOV.UK pattern: destructive actions get a real page with its own URL —
 * warning text, an explicit confirm button, and a cancel back-link — not
 * a JS modal. Confirming sends DELETE (apiFetch adds the CSRF header) and
 * redirects to the list with a success banner via the flash store.
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GovBackLink from '@/components/govuk/GovBackLink.vue'
import GovButton from '@/components/govuk/GovButton.vue'
import GovErrorSummary from '@/components/govuk/GovErrorSummary.vue'
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
  <GovBackLink :to="detailPath" text="Back to the document" />

  <div class="govuk-grid-row">
    <div class="govuk-grid-column-two-thirds">
      <template v-if="doc">
        <GovErrorSummary v-if="deleteError" :errors="[{ text: deleteError }]" />

        <h1 class="govuk-heading-xl">
          Are you sure you want to delete this document?
        </h1>

        <div class="govuk-warning-text">
          <span class="govuk-warning-text__icon" aria-hidden="true">!</span>
          <strong class="govuk-warning-text__text">
            <span class="govuk-visually-hidden">Warning</span>
            “{{ doc.title ?? 'Untitled document' }}” will disappear from your library, search and
            the API. This cannot be undone from the web app.
          </strong>
        </div>

        <div class="govuk-button-group">
          <GovButton
            type="button"
            variant="warning"
            :disabled="deleting"
            data-testid="confirm-delete"
            @click="confirmDelete"
          >
            Yes, delete this document
          </GovButton>
          <RouterLink class="govuk-link" :to="detailPath" data-testid="cancel-delete">
            Cancel
          </RouterLink>
        </div>
      </template>

      <template v-else-if="notFound">
        <h1 class="govuk-heading-xl">Document not found</h1>
        <p class="govuk-body">It may already have been deleted, or the link is wrong.</p>
      </template>
      <div v-else-if="loadError" class="govuk-inset-text">
        Sorry, the document could not be loaded. Try again later.
      </div>
    </div>
  </div>
</template>
