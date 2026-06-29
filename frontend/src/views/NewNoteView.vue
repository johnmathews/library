<script setup lang="ts">
/**
 * New note page (route `/notes/new`).
 *
 * Authoring view for a markdown note: a markdown body and a live preview of the
 * rendered body (sanitised via DOMPurify, mirroring the reader in
 * DocumentDetailView). There is no separate title field — the note's title is
 * the first line of the body (see deriveNoteTitle). On submit the note is
 * created and the user is taken to its detail page. API failures land in a
 * GOV.UK-style error summary.
 */
import { computed, ref } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useRouter } from 'vue-router'
import { AppButton, AppErrorSummary, AppTextarea, PageHeader } from '@/components/app'
import type { ErrorSummaryItem } from '@/components/app'
import { createNote } from '@/api/notes'
import { ApiError } from '@/api/client'
import { deriveNoteTitle } from '@/utils/noteTitle'
import { useMarkdownEditorMode } from '@/composables/useMarkdownEditorMode'

const router = useRouter()

const body = ref('')
/** The note title is the first line of the body, not a separate field. */
const title = computed(() => deriveNoteTitle(body.value))
const saving = ref(false)
const submitError = ref<string | null>(null)

/** Editor view mode (edit / split / preview) — shared with the in-place note
 * editor in DocumentDetailView via the persisted preference. */
const { editorMode, showEditor, showPreview, modes } = useMarkdownEditorMode()

/** Sanitised HTML for the live preview — identical pipeline to the reader. */
const previewHtml = computed(() =>
  DOMPurify.sanitize(marked.parse(body.value, { async: false }) as string),
)

const canSave = computed(() => title.value !== '' && !saving.value)

const errorItems = computed<ErrorSummaryItem[]>(() =>
  submitError.value ? [{ text: submitError.value }] : [],
)

async function onSubmit(): Promise<void> {
  if (!canSave.value) return
  saving.value = true
  submitError.value = null
  try {
    const created = await createNote({
      title: title.value,
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
        <div
          class="grid grid-cols-1 gap-4"
          :class="{ 'lg:grid-cols-2': editorMode === 'split' }"
        >
          <div v-if="showEditor" data-testid="note-editor-pane">
            <AppTextarea
              id="note-body"
              v-model="body"
              label="Note"
              hint="The first line becomes the title. Markdown is supported; the preview updates as you type."
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
/* Fenced code blocks scroll horizontally inside the block rather than
   overflowing the card and the viewport. */
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
</style>
