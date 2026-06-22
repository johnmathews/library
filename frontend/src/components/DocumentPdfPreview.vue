<script setup lang="ts">
import { ref, shallowRef, onMounted, onBeforeUnmount, watch } from 'vue'
import * as pdfjsLib from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'

// Module worker, bundled by Vite and loaded on demand (off the initial bundle).
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

const props = defineProps<{
  src: string
  poster?: string
  openHref: string
  downloadHref: string
  initialPage?: number | null
}>()

type Status = 'loading' | 'rendered' | 'error' | 'password'
const status = ref<Status>('loading')
const pageCount = ref(0)
const pdf = shallowRef<PDFDocumentProxy | null>(null)
let loadGeneration = 0

async function load(): Promise<void> {
  const generation = ++loadGeneration
  status.value = 'loading'
  pdf.value = null
  try {
    const doc = await pdfjsLib.getDocument({ url: props.src }).promise
    if (generation !== loadGeneration) {
      void doc.destroy()
      return
    }
    pdf.value = doc
    pageCount.value = doc.numPages
    status.value = 'rendered'
  } catch (err: unknown) {
    if (generation !== loadGeneration) return
    status.value =
      (err as { name?: string } | null)?.name === 'PasswordException' ? 'password' : 'error'
  }
}

onMounted(load)
watch(() => props.src, load)
onBeforeUnmount(() => {
  void pdf.value?.destroy()
})

defineExpose({ status, pageCount })
</script>

<template>
  <div data-testid="pdf-preview">
    <div v-if="status === 'loading'" data-testid="pdf-preview-loading" class="h-[70vh] bg-gray-100 dark:bg-gray-900/40" />
    <div v-else-if="status === 'rendered'" data-testid="pdf-preview-pages" class="h-[70vh] overflow-y-auto bg-gray-100 dark:bg-gray-900/40" />
    <div v-else-if="status === 'password'" data-testid="pdf-preview-password" class="h-[70vh]" />
    <div v-else data-testid="pdf-preview-error" class="h-[70vh]" />
  </div>
</template>
