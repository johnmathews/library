<script setup lang="ts">
/**
 * Minimal document detail stub (route `/documents/:id`). W11 replaces
 * this with the full page: preview, metadata editing, downloads, delete.
 */
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import GovBackLink from '@/components/govuk/GovBackLink.vue'
import GovSummaryList from '@/components/govuk/GovSummaryList.vue'
import type { SummaryListRow } from '@/components/govuk'
import { getDocument, type DocumentDetail } from '@/api/documents'
import { ApiError } from '@/api/client'

const route = useRoute()

const doc = ref<DocumentDetail | null>(null)
const notFound = ref(false)
const loadError = ref(false)

const rows = ref<SummaryListRow[]>([])

watch(
  () => route.params.id,
  async (id) => {
    doc.value = null
    notFound.value = false
    loadError.value = false
    const numericId = Number(id)
    if (!Number.isInteger(numericId) || numericId < 1) {
      notFound.value = true
      return
    }
    try {
      const detail = await getDocument(numericId)
      doc.value = detail
      rows.value = [
        { key: 'Status', value: detail.status },
        { key: 'Kind', value: detail.kind?.name ?? '—' },
        { key: 'Sender', value: detail.sender?.name ?? '—' },
        { key: 'Document date', value: detail.document_date ?? '—' },
      ]
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 404) notFound.value = true
      else loadError.value = true
    }
  },
  { immediate: true },
)
</script>

<template>
  <GovBackLink to="/" text="Back to documents" />

  <div class="govuk-grid-row">
    <div class="govuk-grid-column-two-thirds">
      <template v-if="doc">
        <h1 class="govuk-heading-xl">{{ doc.title ?? 'Untitled document' }}</h1>
        <GovSummaryList :rows="rows" />
        <div class="govuk-inset-text">
          The full document page — preview, editing and downloads — arrives in the next release.
        </div>
      </template>
      <template v-else-if="notFound">
        <h1 class="govuk-heading-xl">Document not found</h1>
        <p class="govuk-body">It may have been deleted, or the link is wrong.</p>
      </template>
      <div v-else-if="loadError" class="govuk-inset-text">
        Sorry, the document could not be loaded. Try again later.
      </div>
    </div>
  </div>
</template>
