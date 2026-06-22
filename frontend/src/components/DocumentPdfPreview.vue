<script setup lang="ts">
import { ref, shallowRef, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
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

const container = ref<HTMLElement | null>(null)
const canvasRefs = ref<Array<HTMLCanvasElement | null>>([])
const renderedPages = new Set<number>()
let observer: IntersectionObserver | null = null

function setCanvasRef(el: Element | null, index: number): void {
  canvasRefs.value[index] = el as HTMLCanvasElement | null
}

async function renderPage(n: number): Promise<void> {
  if (renderedPages.has(n) || !pdf.value) return
  const canvas = canvasRefs.value[n - 1]
  const ctx = canvas?.getContext('2d')
  if (!canvas || !ctx) return
  renderedPages.add(n)
  const page = await pdf.value.getPage(n)
  const width = container.value?.clientWidth || canvas.clientWidth || 800
  const unscaled = page.getViewport({ scale: 1 })
  const scale = (width / unscaled.width) * (window.devicePixelRatio || 1)
  const viewport = page.getViewport({ scale })
  canvas.width = viewport.width
  canvas.height = viewport.height
  await page.render({ canvasContext: ctx, viewport }).promise
}

function observePages(): void {
  if (typeof IntersectionObserver === 'undefined') {
    for (let n = 1; n <= pageCount.value; n++) void renderPage(n)
    return
  }
  observer?.disconnect()
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          const n = Number((entry.target as HTMLElement).dataset.page)
          void renderPage(n)
        }
      }
    },
    { root: container.value, rootMargin: '300px' },
  )
  container.value?.querySelectorAll('[data-page]').forEach((el) => observer?.observe(el))
}

function scrollToPage(n: number): void {
  container.value?.querySelector(`[data-page="${n}"]`)?.scrollIntoView()
}

async function load(): Promise<void> {
  const generation = ++loadGeneration
  status.value = 'loading'
  pdf.value = null
  let loaded = false
  try {
    const doc = await pdfjsLib.getDocument({ url: props.src }).promise
    if (generation !== loadGeneration) {
      void doc.destroy()
      return
    }
    pdf.value = doc
    pageCount.value = doc.numPages
    renderedPages.clear()
    status.value = 'rendered'
    loaded = true
  } catch (err: unknown) {
    if (generation !== loadGeneration) return
    status.value =
      (err as { name?: string } | null)?.name === 'PasswordException' ? 'password' : 'error'
  }
  if (loaded) {
    await nextTick()
    observePages()
    if (props.initialPage) scrollToPage(props.initialPage)
  }
}

onMounted(load)
watch(() => props.src, load)
onBeforeUnmount(() => {
  observer?.disconnect()
  void pdf.value?.destroy()
})

defineExpose({ status, pageCount })
</script>

<template>
  <div data-testid="pdf-preview">
    <div
      v-if="status === 'loading'"
      data-testid="pdf-preview-loading"
      class="relative flex h-[70vh] items-center justify-center bg-gray-100 dark:bg-gray-900/40"
    >
      <img
        v-if="poster"
        :src="poster"
        alt=""
        class="absolute inset-0 h-full w-full object-contain opacity-40"
      />
      <span class="relative text-sm text-gray-500 dark:text-gray-400">Loading preview…</span>
    </div>
    <div
      v-else-if="status === 'rendered'"
      ref="container"
      data-testid="pdf-preview-pages"
      class="h-[70vh] overflow-y-auto bg-gray-100 dark:bg-gray-900/40"
    >
      <canvas
        v-for="n in pageCount"
        :key="n"
        :ref="(el) => setCanvasRef(el as Element | null, n - 1)"
        :data-page="n"
        class="mx-auto mb-2 block w-full max-w-3xl shadow-sm"
      />
    </div>
    <div
      v-else-if="status === 'password'"
      data-testid="pdf-preview-password"
      class="flex h-[70vh] flex-col items-center justify-center gap-3 bg-gray-100 p-6 text-center text-gray-500 dark:bg-gray-900/40 dark:text-gray-400"
    >
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="h-12 w-12" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
      </svg>
      <span class="text-sm font-medium">Protected PDF — open to unlock</span>
      <a :href="openHref" target="_blank" rel="noopener" data-testid="pdf-preview-open" class="text-violet-600 hover:underline">Open</a>
    </div>
    <div
      v-else
      data-testid="pdf-preview-error"
      class="flex h-[70vh] flex-col items-center justify-center gap-2 bg-gray-100 p-6 text-center text-sm text-gray-500 dark:bg-gray-900/40 dark:text-gray-400"
    >
      <span>This preview couldn't be displayed.</span>
      <span class="flex gap-3">
        <a :href="openHref" target="_blank" rel="noopener" data-testid="pdf-preview-open" class="text-violet-600 hover:underline">Open</a>
        <a :href="downloadHref" data-testid="pdf-preview-download" class="text-violet-600 hover:underline">Download</a>
      </span>
    </div>
  </div>
</template>
