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
  <p v-if="archLoading" data-testid="arch-loading" class="text-ink-secondary">
    Loading architecture docs…
  </p>
  <AppBanner v-else-if="archError" variant="error" data-testid="arch-error">
    {{ archError }}
  </AppBanner>
  <p
    v-else-if="archDocs.length === 0"
    data-testid="arch-empty"
    class="text-sm text-ink-secondary"
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
          'rounded-full border px-3 py-1.5 text-sm font-medium transition-colors cursor-pointer',
          selectedDocName === doc.name
            ? 'border-brand bg-brand-tint text-brand'
            : 'border-control-border bg-panel text-ink hover:bg-tile',
        ]"
        @click="selectedDocName = doc.name"
      >
        {{ doc.title }}
      </button>
    </div>

    <div v-if="selectedDoc" :class="cardClass">
      <h2 class="text-base font-[650] text-ink mb-4">
        {{ selectedDoc.title }}
      </h2>
      <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in archHtml -->
      <div
        class="doc-markdown text-ink"
        data-testid="arch-content"
        v-html="archHtml"
      />
    </div>
  </div>
</template>
