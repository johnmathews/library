<script setup lang="ts">
/**
 * Note-only controls for the document detail page: in-place note editing plus a
 * version-history disclosure with restore. Extracted from DocumentDetailView so
 * the note authoring flow lives beside its own state.
 *
 * The note body renders in the parent's preview-column reader (off
 * `markdownData`, not on `doc`), so saving a note needs two channels back to the
 * parent: `update:doc` (the fresh DocumentDetail) AND `reload-markdown` (re-fetch
 * the rendered body). `update:doc` alone would leave the reader stale.
 */
import { computed, ref } from 'vue'
import { AppButton, AppTextarea } from '@/components/app'
import type { DocumentDetail } from '@/api/documents'
import {
  listNoteVersions,
  restoreNoteVersion,
  updateNote,
  type NoteVersion,
} from '@/api/notes'
import { ApiError } from '@/api/client'
import { useMarkdownEditorMode } from '@/composables/useMarkdownEditorMode'
import { deriveNoteTitle } from '@/utils/noteTitle'
import { formatDateTime, markdownPageHtml } from '@/utils/documentFormat'

const props = defineProps<{
  /** The note document (always non-null; the parent gates this panel on `isNote`). */
  doc: DocumentDetail
  /** The note's saved markdown body, assembled by the parent from the reader's pages. */
  noteBody: string
}>()

const emit = defineEmits<{
  /** A fresh DocumentDetail after a save / restore (parent binds `v-model:doc`). */
  (e: 'update:doc', doc: DocumentDetail): void
  /** Ask the parent to re-fetch the rendered markdown (the reader lives there). */
  (e: 'reload-markdown'): void
}>()

const noteEditMode = ref(false)
const noteBodyDraft = ref('')
const noteSaving = ref(false)
const noteEditError = ref<string | null>(null)

/** Editor view mode (edit / split / preview) — shared with the new-note view via
 * the persisted preference. */
const { editorMode, showEditor, showPreview, modes } = useMarkdownEditorMode()

/** Sanitised HTML preview of the draft body so the preview reflects edits live
 * (the reader's markdownPageHtml is bound to the saved body). */
const noteDraftPreviewHtml = computed(() => markdownPageHtml(noteBodyDraft.value))

/** The title is the first line of the body, mirroring the new-note authoring view. */
const noteEditTitle = computed(() => deriveNoteTitle(noteBodyDraft.value))
const canSaveNote = computed(() => noteEditTitle.value !== '' && !noteSaving.value)

function openNoteEditor(): void {
  noteBodyDraft.value = props.noteBody
  noteEditError.value = null
  noteEditMode.value = true
}

function cancelNoteEdit(): void {
  noteEditMode.value = false
  noteEditError.value = null
}

async function saveNote(): Promise<void> {
  if (!canSaveNote.value) return
  noteSaving.value = true
  noteEditError.value = null
  try {
    const updated = await updateNote(props.doc.id, {
      title: noteEditTitle.value,
      body_markdown: noteBodyDraft.value,
    })
    emit('update:doc', updated)
    emit('reload-markdown')
    noteEditMode.value = false
  } catch (error: unknown) {
    noteEditError.value =
      error instanceof ApiError && error.status !== 0
        ? error.detail
        : 'Could not save the note — check your connection and try again'
  } finally {
    noteSaving.value = false
  }
}

const noteVersions = ref<NoteVersion[]>([])
const noteVersionsOpen = ref(false)
const noteVersionsLoading = ref(false)
const noteVersionsError = ref<string | null>(null)
const restoringVersion = ref<number | null>(null)

async function toggleNoteVersions(): Promise<void> {
  noteVersionsOpen.value = !noteVersionsOpen.value
  if (!noteVersionsOpen.value) return
  await loadNoteVersions()
}

async function loadNoteVersions(): Promise<void> {
  noteVersionsLoading.value = true
  noteVersionsError.value = null
  try {
    noteVersions.value = await listNoteVersions(props.doc.id)
  } catch {
    noteVersionsError.value = 'Could not load version history — try again later.'
  } finally {
    noteVersionsLoading.value = false
  }
}

async function restoreVersion(versionNo: number): Promise<void> {
  if (restoringVersion.value !== null) return
  restoringVersion.value = versionNo
  noteVersionsError.value = null
  try {
    const updated = await restoreNoteVersion(props.doc.id, versionNo)
    emit('update:doc', updated)
    emit('reload-markdown')
    await loadNoteVersions()
  } catch {
    noteVersionsError.value = 'Could not restore that version — try again later.'
  } finally {
    restoringVersion.value = null
  }
}
</script>

<template>
  <div
    id="document-note-card"
    class="card p-5"
  >
    <div class="mb-4 flex items-center justify-between gap-3">
      <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">Note</h2>
      <button
        v-if="!noteEditMode"
        type="button"
        class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300"
        data-testid="note-edit-button"
        @click="openNoteEditor"
      >
        Edit note
      </button>
    </div>

    <template v-if="noteEditMode">
      <div class="mb-4 flex justify-end">
        <div
          class="inline-flex rounded-lg border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-800 p-0.5"
          role="group"
          aria-label="Editor view"
        >
          <button
            v-for="m in modes"
            :key="m.value"
            type="button"
            :id="`note-edit-mode-${m.value}`"
            :data-testid="`note-edit-mode-${m.value}`"
            :aria-pressed="editorMode === m.value"
            :aria-label="`${m.label} view`"
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
      </div>
      <div
        class="grid grid-cols-1 gap-4"
        :class="{ 'lg:grid-cols-2': editorMode === 'split' }"
      >
        <div v-if="showEditor" data-testid="note-edit-editor-pane">
          <AppTextarea
            id="note-edit-body"
            v-model="noteBodyDraft"
            label="Note"
            hint="The first line becomes the title. Markdown is supported."
            :rows="12"
            :error-message="noteEditError ?? undefined"
          />
        </div>
        <div v-if="showPreview" data-testid="note-edit-preview-pane">
          <span class="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">Preview</span>
          <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in noteDraftPreviewHtml -->
          <div
            class="doc-markdown form-textarea w-full min-h-40 overflow-auto text-gray-800 dark:text-gray-100"
            data-testid="note-edit-preview"
            v-html="noteDraftPreviewHtml"
          />
          <!-- eslint-enable vue/no-v-html -->
        </div>
      </div>
      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton
          type="button"
          :disabled="!canSaveNote"
          data-testid="note-edit-save"
          @click="saveNote"
        >
          {{ noteSaving ? 'Saving…' : 'Save note' }}
        </AppButton>
        <AppButton
          type="button"
          variant="secondary"
          :disabled="noteSaving"
          data-testid="note-edit-cancel"
          @click="cancelNoteEdit"
        >
          Cancel
        </AppButton>
      </div>
    </template>

    <!-- Version history disclosure. -->
    <div data-testid="note-versions" class="mt-4 border-t border-gray-200 dark:border-gray-700/60 pt-4">
      <button
        type="button"
        class="text-sm font-medium text-violet-500 hover:underline"
        data-testid="note-versions-toggle"
        :aria-expanded="noteVersionsOpen"
        @click="toggleNoteVersions"
      >
        {{ noteVersionsOpen ? 'Hide version history' : 'Show version history' }}
      </button>
      <div v-if="noteVersionsOpen" class="mt-3">
        <p
          v-if="noteVersionsLoading"
          class="text-sm text-gray-500 dark:text-gray-400"
          data-testid="note-versions-loading"
        >
          Loading…
        </p>
        <p
          v-else-if="noteVersionsError"
          class="text-sm text-red-600 dark:text-red-400"
          data-testid="note-versions-error"
        >
          {{ noteVersionsError }}
        </p>
        <p
          v-else-if="noteVersions.length === 0"
          class="text-sm text-gray-500 dark:text-gray-400"
          data-testid="note-versions-empty"
        >
          No earlier versions yet.
        </p>
        <ul v-else class="divide-y divide-gray-200 dark:divide-gray-700/60">
          <li
            v-for="version in noteVersions"
            :key="version.version_no"
            class="flex items-center justify-between gap-3 py-2"
            :data-testid="`note-version-${version.version_no}`"
          >
            <span class="min-w-0 text-sm text-gray-800 dark:text-gray-100">
              Version {{ version.version_no }}
              <span class="block text-xs text-gray-400 dark:text-gray-500">
                {{ formatDateTime(version.created_at) }}
              </span>
            </span>
            <button
              type="button"
              class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300 whitespace-nowrap"
              :disabled="restoringVersion !== null"
              :data-testid="`note-restore-${version.version_no}`"
              @click="restoreVersion(version.version_no)"
            >
              {{ restoringVersion === version.version_no ? 'Restoring…' : 'Restore' }}
            </button>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Markdown rendered via v-html in the note preview pane; restore readable prose
   spacing stripped by Tailwind preflight. Mirrors the reader styles in
   DocumentDetailView.vue (and .ask-answer in AskView.vue). */
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
