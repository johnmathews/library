<script setup lang="ts">
/**
 * Architecture tab: the project's architecture docs, markdown → sanitised HTML
 * (same marked + DOMPurify pipeline as the note authoring/reader views).
 * Loads eagerly on mount (parent keeps every panel mounted via v-show).
 */
import { computed, onMounted, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { AppBanner } from '@/components/app'
import { getArchitecture, type ArchitectureDoc } from '@/api/admin'

const cardClass = 'card p-6'

const archDocs = ref<ArchitectureDoc[]>([])
const archLoading = ref(true)
const archError = ref<string | null>(null)
const selectedDocName = ref<string | null>(null)

const selectedDoc = computed<ArchitectureDoc | null>(
  () => archDocs.value.find((doc) => doc.name === selectedDocName.value) ?? null,
)

/** Sanitised HTML for the selected doc — identical pipeline to the note reader. */
const archHtml = computed(() =>
  selectedDoc.value
    ? DOMPurify.sanitize(marked.parse(selectedDoc.value.markdown, { async: false }) as string)
    : '',
)

async function loadArchitecture(): Promise<void> {
  archLoading.value = true
  archError.value = null
  try {
    const result = await getArchitecture()
    archDocs.value = result.docs
    selectedDocName.value = result.docs[0]?.name ?? null
  } catch {
    archError.value = 'Could not load architecture docs. Try refreshing the page.'
  } finally {
    archLoading.value = false
  }
}

onMounted(() => {
  void loadArchitecture()
})
</script>

<template>
  <p v-if="archLoading" data-testid="arch-loading" class="text-gray-600 dark:text-gray-300">
    Loading architecture docs…
  </p>
  <AppBanner v-else-if="archError" variant="error" data-testid="arch-error">
    {{ archError }}
  </AppBanner>
  <p
    v-else-if="archDocs.length === 0"
    data-testid="arch-empty"
    class="text-sm text-gray-500 dark:text-gray-400"
  >
    No architecture documents are available.
  </p>
  <div v-else class="space-y-4">
    <div class="flex flex-wrap gap-2" data-testid="arch-doc-list">
      <button
        v-for="doc in archDocs"
        :key="doc.name"
        type="button"
        :data-testid="`arch-doc-${doc.name}`"
        :class="[
          'rounded-lg border px-3 py-1.5 text-sm font-medium transition cursor-pointer',
          selectedDocName === doc.name
            ? 'border-violet-500 ring-2 ring-violet-500/30 text-violet-600 dark:text-violet-300'
            : 'border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-200 hover:border-gray-300 dark:hover:border-gray-600',
        ]"
        @click="selectedDocName = doc.name"
      >
        {{ doc.title }}
      </button>
    </div>

    <div v-if="selectedDoc" :class="cardClass">
      <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">
        {{ selectedDoc.title }}
      </h2>
      <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in archHtml -->
      <div
        class="doc-markdown text-gray-800 dark:text-gray-100"
        data-testid="arch-content"
        v-html="archHtml"
      />
    </div>
  </div>
</template>

<style scoped>
/* Markdown rendered via v-html; restore prose spacing stripped by Tailwind
   preflight (mirrors .doc-markdown in NewNoteView.vue / DocumentDetailView). */
.doc-markdown :deep(p) {
  margin-bottom: 0.75rem;
}
.doc-markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.doc-markdown :deep(strong) {
  font-weight: 600;
}
.doc-markdown :deep(em) {
  font-style: italic;
}
.doc-markdown :deep(ul),
.doc-markdown :deep(ol) {
  margin: 0.5rem 0 0.75rem;
  padding-left: 1.5rem;
}
.doc-markdown :deep(ul) {
  list-style: disc;
}
.doc-markdown :deep(ol) {
  list-style: decimal;
}
.doc-markdown :deep(li) {
  margin-bottom: 0.25rem;
}
.doc-markdown :deep(h1),
.doc-markdown :deep(h2),
.doc-markdown :deep(h3) {
  font-weight: 600;
  margin: 0.75rem 0 0.5rem;
}
.doc-markdown :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.875em;
  padding: 0.1em 0.3em;
  border-radius: 0.25rem;
  background: rgb(0 0 0 / 0.06);
}
.dark .doc-markdown :deep(code) {
  background: rgb(255 255 255 / 0.08);
}
/* Fenced code blocks (incl. wide ASCII diagrams) scroll horizontally inside the
   block instead of overflowing the card and the viewport on a phone. */
.doc-markdown :deep(pre) {
  margin: 0.75rem 0;
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  background: rgb(0 0 0 / 0.06);
  overflow-x: auto;
}
.dark .doc-markdown :deep(pre) {
  background: rgb(255 255 255 / 0.08);
}
.doc-markdown :deep(pre code) {
  padding: 0;
  background: none;
  white-space: pre;
}
/* GFM tables: marked emits real <table> markup, but Tailwind preflight strips
   borders/spacing so they collapse to unstyled text. Restore borders, padding,
   a header tint, and horizontal scroll for wide tables on a phone. */
.doc-markdown :deep(table) {
  display: block;
  width: max-content;
  max-width: 100%;
  overflow-x: auto;
  margin: 0.75rem 0;
  border-collapse: collapse;
  font-size: 0.9375em;
}
.doc-markdown :deep(th),
.doc-markdown :deep(td) {
  border: 1px solid rgb(0 0 0 / 0.12);
  padding: 0.375rem 0.625rem;
  text-align: left;
  vertical-align: top;
}
.dark .doc-markdown :deep(th),
.dark .doc-markdown :deep(td) {
  border-color: rgb(255 255 255 / 0.15);
}
.doc-markdown :deep(th) {
  font-weight: 600;
  background: rgb(0 0 0 / 0.04);
}
.dark .doc-markdown :deep(th) {
  background: rgb(255 255 255 / 0.06);
}
</style>
