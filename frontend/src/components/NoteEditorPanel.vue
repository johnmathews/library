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
      <h2 class="text-base font-[650] text-ink">Note</h2>
      <button
        v-if="!noteEditMode"
        type="button"
        class="btn-sm border-control-border bg-panel text-ink hover:bg-tile"
        data-testid="note-edit-button"
        @click="openNoteEditor"
      >
        Edit note
      </button>
    </div>

    <template v-if="noteEditMode">
      <div class="mb-4 flex justify-end">
        <div
          class="inline-flex rounded-sm border border-control-border bg-panel p-0.5"
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
                ? 'bg-brand-fill text-on-brand'
                : 'text-ink-secondary hover:bg-tile',
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
          <span class="block text-sm font-medium mb-1 text-ink">Preview</span>
          <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in noteDraftPreviewHtml -->
          <div
            class="doc-markdown form-textarea w-full min-h-40 overflow-auto text-ink"
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
    <div data-testid="note-versions" class="mt-4 border-t border-line pt-4">
      <button
        type="button"
        class="text-sm font-medium text-brand hover:underline"
        data-testid="note-versions-toggle"
        :aria-expanded="noteVersionsOpen"
        @click="toggleNoteVersions"
      >
        {{ noteVersionsOpen ? 'Hide version history' : 'Show version history' }}
      </button>
      <div v-if="noteVersionsOpen" class="mt-3">
        <p
          v-if="noteVersionsLoading"
          class="text-sm text-ink-secondary"
          data-testid="note-versions-loading"
        >
          Loading…
        </p>
        <p
          v-else-if="noteVersionsError"
          class="text-sm text-error-ink"
          data-testid="note-versions-error"
        >
          {{ noteVersionsError }}
        </p>
        <p
          v-else-if="noteVersions.length === 0"
          class="text-sm text-ink-secondary"
          data-testid="note-versions-empty"
        >
          No earlier versions yet.
        </p>
        <ul v-else class="divide-y divide-line">
          <li
            v-for="version in noteVersions"
            :key="version.version_no"
            class="flex items-center justify-between gap-3 py-2"
            :data-testid="`note-version-${version.version_no}`"
          >
            <span class="min-w-0 text-sm text-ink">
              Version {{ version.version_no }}
              <span class="block text-xs text-ink-secondary">
                {{ formatDateTime(version.created_at) }}
              </span>
            </span>
            <button
              type="button"
              class="btn-sm border-control-border bg-panel text-ink hover:bg-tile whitespace-nowrap"
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
