<script setup lang="ts">
/**
 * Comments card for the document detail page: a lightweight discussion thread
 * per document. Comments themselves arrive via the `comments` prop (part of
 * `DocumentDetail`, loaded by the parent) — this panel only performs
 * mutations (add/edit/delete) via the API client and emits `changed` so the
 * parent re-fetches the document, mirroring the write-back pattern used by
 * NoteEditorPanel / DocumentMetadataEditor. At most one comment is being
 * edited at a time, so the inline editor's Save/Cancel controls use singleton
 * testids (like NoteEditorPanel's `note-edit-save`), while the per-comment
 * "Edit"/"Delete" triggers are keyed by comment id so a specific comment among
 * several can be targeted.
 */
import { computed, reactive, ref } from 'vue'
import { AppButton, AppTextarea } from '@/components/app'
import { createComment, deleteComment, updateComment, type DocumentComment } from '@/api/documents'
import { ApiError } from '@/api/client'
import { formatDateTime } from '@/utils/documentFormat'

const props = defineProps<{
  /** The document this comment thread belongs to. */
  documentId: number
  /** Current comments, as loaded by the parent (not mutated locally). */
  comments: DocumentComment[]
}>()

const emit = defineEmits<{
  /** A mutation succeeded; the parent should re-fetch the document. */
  (e: 'changed'): void
}>()

/** Newest-first, matching a discussion-thread reading order. */
const sortedComments = computed(() =>
  [...props.comments].sort((a, b) => b.created_at.localeCompare(a.created_at)),
)

function apiErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError && error.status !== 0 ? error.detail : fallback
}

// --- Add ------------------------------------------------------------------

const newBody = ref('')
const adding = ref(false)
const addError = ref<string | null>(null)
const canAdd = computed(() => newBody.value.trim() !== '' && !adding.value)

async function addComment(): Promise<void> {
  if (!canAdd.value) return
  addError.value = null
  adding.value = true
  try {
    await createComment(props.documentId, newBody.value.trim())
    newBody.value = ''
    emit('changed')
  } catch (error: unknown) {
    addError.value = apiErrorMessage(
      error,
      'Could not add the comment — check your connection and try again',
    )
  } finally {
    adding.value = false
  }
}

// --- Edit (at most one open at a time) ---------------------------------------

const editingId = ref<number | null>(null)
const editBody = ref('')
const editSaving = ref(false)
const editError = ref<string | null>(null)

function startEdit(comment: DocumentComment): void {
  editingId.value = comment.id
  editBody.value = comment.body
  editError.value = null
}

function cancelEdit(): void {
  editingId.value = null
  editBody.value = ''
  editError.value = null
}

async function saveEdit(id: number): Promise<void> {
  if (editSaving.value) return
  const body = editBody.value.trim()
  if (!body) return
  editError.value = null
  editSaving.value = true
  try {
    await updateComment(props.documentId, id, body)
    editingId.value = null
    emit('changed')
  } catch (error: unknown) {
    editError.value = apiErrorMessage(
      error,
      'Could not save the change — check your connection and try again',
    )
  } finally {
    editSaving.value = false
  }
}

// --- Delete -------------------------------------------------------------------

const deletingId = ref<number | null>(null)
// Keyed by comment id so a failed delete surfaces beside the comment that
// failed, without disturbing the others.
const deleteErrors = reactive<Record<number, string>>({})

async function removeComment(id: number): Promise<void> {
  if (deletingId.value !== null) return
  delete deleteErrors[id]
  deletingId.value = id
  try {
    await deleteComment(props.documentId, id)
    emit('changed')
  } catch (error: unknown) {
    deleteErrors[id] = apiErrorMessage(error, 'Could not delete the comment — try again later')
  } finally {
    deletingId.value = null
  }
}
</script>

<template>
  <div id="document-comments-card" class="card p-5" data-testid="document-comments">
    <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4">Comments</h2>

    <div class="mb-4">
      <AppTextarea
        id="comment-add-body"
        v-model="newBody"
        label="Add a comment"
        hide-label
        :rows="3"
        testid="comment-add-body"
        :error-message="addError ?? undefined"
      />
      <div class="mt-2 flex justify-end">
        <AppButton
          type="button"
          size="sm"
          :disabled="!canAdd"
          data-testid="comment-add-submit"
          @click="addComment"
        >
          {{ adding ? 'Adding…' : 'Add' }}
        </AppButton>
      </div>
    </div>

    <p
      v-if="sortedComments.length === 0"
      class="text-sm text-gray-500 dark:text-gray-400"
      data-testid="comments-empty"
    >
      No comments yet.
    </p>
    <ul v-else class="divide-y divide-gray-200 dark:divide-gray-700/60">
      <li
        v-for="comment in sortedComments"
        :key="comment.id"
        class="py-3 first:pt-0 last:pb-0"
        :data-testid="`comment-item-${comment.id}`"
      >
        <template v-if="editingId === comment.id">
          <AppTextarea
            id="comment-edit-body"
            v-model="editBody"
            label="Edit comment"
            hide-label
            :rows="3"
            testid="comment-edit-body"
            :error-message="editError ?? undefined"
          />
          <div class="mt-2 flex gap-2">
            <button
              type="button"
              class="btn-sm border-violet-200 bg-violet-50 text-violet-700 hover:border-violet-300 dark:border-violet-500/40 dark:bg-violet-500/15 dark:text-violet-300"
              :disabled="editSaving"
              data-testid="comment-edit-save"
              @click="saveEdit(comment.id)"
            >
              {{ editSaving ? 'Saving…' : 'Save' }}
            </button>
            <button
              type="button"
              class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300"
              :disabled="editSaving"
              data-testid="comment-edit-cancel"
              @click="cancelEdit"
            >
              Cancel
            </button>
          </div>
        </template>
        <template v-else>
          <p class="whitespace-pre-wrap break-words text-sm text-gray-800 dark:text-gray-100">{{
            comment.body
          }}</p>
          <div class="mt-1 flex items-center justify-between gap-3">
            <span class="text-xs text-gray-400 dark:text-gray-500">{{
              formatDateTime(comment.created_at)
            }}</span>
            <div class="flex gap-2">
              <button
                type="button"
                class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300"
                :data-testid="`comment-edit-${comment.id}`"
                @click="startEdit(comment)"
              >
                Edit
              </button>
              <button
                type="button"
                class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-red-600 dark:text-red-400"
                :disabled="deletingId === comment.id"
                :data-testid="`comment-delete-${comment.id}`"
                @click="removeComment(comment.id)"
              >
                {{ deletingId === comment.id ? 'Deleting…' : 'Delete' }}
              </button>
            </div>
          </div>
          <p
            v-if="deleteErrors[comment.id]"
            class="mt-1 text-xs text-red-600 dark:text-red-400"
          >
            {{ deleteErrors[comment.id] }}
          </p>
        </template>
      </li>
    </ul>
  </div>
</template>
