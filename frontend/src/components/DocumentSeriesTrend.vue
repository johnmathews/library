<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchDocumentSeries, type DocumentSeries } from '@/api/documents'
import SeriesChartTile from './SeriesChartTile.vue'

const props = defineProps<{ documentId: number }>()
const series = ref<DocumentSeries | null>(null)

onMounted(async () => {
  try {
    const data = await fetchDocumentSeries(props.documentId)
    series.value = data.status === 'ok' ? data : null
  } catch {
    series.value = null
  }
})
</script>

<template>
  <SeriesChartTile
    v-if="series"
    class="mt-4"
    :series="series"
    :highlight-document-id="props.documentId"
  />
</template>
