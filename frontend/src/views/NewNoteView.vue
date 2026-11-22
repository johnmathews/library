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
      description="Write a markdown note. It becomes a document in your library, with full version history. The first line becomes the title; Markdown is supported and the preview updates as you type."
    >
      <template #actions>
        <div
          class="inline-flex rounded-sm border border-control-border bg-panel p-0.5"
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
                ? 'bg-brand-fill text-on-brand'
                : 'text-ink-secondary hover:text-ink',
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
      class="card p-5 mb-6"
      @submit.prevent="onSubmit"
    >
      <div class="space-y-4">
        <div
          class="grid grid-cols-1 gap-4"
          :class="{ 'lg:grid-cols-2': editorMode === 'split' }"
        >
          <div v-if="showEditor" data-testid="note-editor-pane">
            <AppTextarea id="note-body" v-model="body" label="Note" :rows="16" />
          </div>
          <div v-if="showPreview" data-testid="note-preview-pane">
            <span class="block text-sm font-medium mb-1 text-ink">Preview</span>
            <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in previewHtml -->
            <div
              class="doc-markdown form-textarea w-full min-h-40 overflow-auto text-ink"
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
