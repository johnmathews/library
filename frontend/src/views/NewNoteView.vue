<script setup lang="ts">
/**
 * New note page (route `/notes/new`).
 *
 * Authoring view for a markdown note: a title, a markdown body, and a live
 * preview of the rendered body (sanitised via DOMPurify, mirroring the reader
 * in DocumentDetailView). On submit the note is created and the user is taken
 * to its detail page. API failures land in a GOV.UK-style error summary.
 */
import { computed, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useStorage } from '@vueuse/core'
import { useRouter } from 'vue-router'
import { AppButton, AppErrorSummary, AppInput, AppTextarea, PageHeader } from '@/components/app'
import type { ErrorSummaryItem } from '@/components/app'
import { createNote } from '@/api/notes'
import { ApiError } from '@/api/client'

const router = useRouter()

const title = ref('')
const body = ref('')
const saving = ref(false)
const submitError = ref<string | null>(null)

/**
 * Editor view mode. Split (editor + preview side-by-side) is the default and the
 * best use of width on large screens; Edit / Preview are single-pane focus modes
 * that also serve narrow screens. Persisted per-machine — a display-size
 * preference (docs/frontend-view-principles.md §4).
 */
type EditorMode = 'edit' | 'split' | 'preview'
const editorMode = useStorage<EditorMode>('library:note-editor-mode', 'split')
const showEditor = computed(() => editorMode.value !== 'preview')
const showPreview = computed(() => editorMode.value !== 'edit')

const modes: { value: EditorMode; label: string; wideOnly: boolean }[] = [
  { value: 'edit', label: 'Edit', wideOnly: false },
  { value: 'split', label: 'Split', wideOnly: true },
  { value: 'preview', label: 'Preview', wideOnly: false },
]

/** Sanitised HTML for the live preview — identical pipeline to the reader. */
const previewHtml = computed(() =>
  DOMPurify.sanitize(marked.parse(body.value, { async: false }) as string),
)

const canSave = computed(() => title.value.trim() !== '' && body.value.trim() !== '' && !saving.value)

const errorItems = computed<ErrorSummaryItem[]>(() =>
  submitError.value ? [{ text: submitError.value }] : [],
)

async function onSubmit(): Promise<void> {
  if (!canSave.value) return
  saving.value = true
  submitError.value = null
  try {
    const created = await createNote({
      title: title.value.trim(),
      body_markdown: body.value,
    })
    await router.push({ name: 'document-detail', params: { id: created.id } })
  } catch (error: unknown) {
    submitError.value =
      error instanceof ApiError && error.status !== 0
        ? error.detail
        : 'Could not save the note — check your connection and try again'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div id="new-note-page">
    <PageHeader
      title="New note"
      description="Write a markdown note. It becomes a document in your library, with full version history."
    >
      <template #actions>
        <div
          class="inline-flex rounded-lg border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-800 p-0.5"
          role="group"
          aria-label="Editor view"
        >
          <button
            v-for="m in modes"
            :key="m.value"
            type="button"
            :data-testid="`mode-${m.value}`"
            :aria-pressed="editorMode === m.value"
            class="px-3 py-1 text-sm font-medium rounded-md transition"
            :class="[
              editorMode === m.value
                ? 'bg-violet-500 text-white'
                : 'text-gray-600 dark:text-gray-300 hover:text-gray-800 dark:hover:text-gray-100',
              m.wideOnly ? 'hidden lg:inline-flex' : 'inline-flex',
            ]"
            @click="editorMode = m.value"
          >
            {{ m.label }}
          </button>
        </div>
        <AppButton id="note-save" type="button" :disabled="!canSave" @click="onSubmit">
          {{ saving ? 'Saving…' : 'Save note' }}
        </AppButton>
      </template>
    </PageHeader>

    <AppErrorSummary v-if="errorItems.length" :errors="errorItems" data-testid="error-summary" class="mb-6" />

    <form
      id="new-note-form"
      novalidate
      class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5 mb-6"
      @submit.prevent="onSubmit"
    >
      <div class="space-y-4">
        <AppInput id="note-title" v-model="title" label="Title" />
        <div
          class="grid grid-cols-1 gap-4"
          :class="{ 'lg:grid-cols-2': editorMode === 'split' }"
        >
          <div v-if="showEditor" data-testid="note-editor-pane">
            <AppTextarea
              id="note-body"
              v-model="body"
              label="Body (markdown)"
              hint="Markdown is supported. The preview updates as you type."
              :rows="16"
            />
          </div>
          <div v-if="showPreview" data-testid="note-preview-pane">
            <span class="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Preview</span>
            <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in previewHtml -->
            <div
              class="doc-markdown form-textarea w-full min-h-40 overflow-auto text-gray-800 dark:text-gray-100"
              data-testid="note-preview"
              v-html="previewHtml"
            />
            <!-- eslint-enable vue/no-v-html -->
          </div>
        </div>
      </div>
    </form>
  </div>
</template>

<style scoped>
/* Markdown rendered via v-html; restore readable prose spacing stripped by
   Tailwind preflight (mirrors .doc-markdown in DocumentDetailView.vue). */
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
</style>
