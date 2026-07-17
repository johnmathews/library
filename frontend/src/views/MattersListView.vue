<script setup lang="ts">
/**
 * Matters index (route `/matters`): lists every business matter with its
 * document count, links each to the matter-filtered dashboard, and — for
 * admins — supports create, rename/hint edit, archive toggle, and delete via
 * the matters REST API. Non-admins get a read-only list.
 *
 * Mirrors ProjectsListView. The one substantive difference: a matter carries a
 * `hint` (not a `description`) — the free text the LLM classifier reads when
 * deciding whether a document belongs to the matter — so the create/edit forms
 * surface `hint` and a helper caption explains it feeds auto-classification.
 */
import { computed, onMounted, ref } from 'vue'

import {
  createMatter,
  deleteMatter,
  listMatters,
  updateMatter,
  type MatterOption,
} from '@/api/matters'
import { refreshTaxonomyOptions } from '@/composables/taxonomyOptions'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/api/client'
import {
  AppButton,
  AppErrorSummary,
  AppInput,
  PageHeader,
  type ErrorSummaryItem,
} from '@/components/app'

const auth = useAuthStore()
const isAdmin = computed(() => auth.isAdmin)

const matters = ref<MatterOption[]>([])
const loading = ref(true)
const loadError = ref(false)
const includeArchived = ref(false)

/** Helper caption shown under every hint input, so admins know the field is not
 * cosmetic — it feeds the auto-classifier. */
const HINT_CAPTION = 'The hint feeds auto-classification: the LLM reads it to decide which documents belong to this matter.'

async function load(): Promise<void> {
  loading.value = true
  loadError.value = false
  try {
    matters.value = await listMatters(includeArchived.value)
  } catch {
    loadError.value = true
  } finally {
    loading.value = false
  }
}

onMounted(load)

function errorText(error: unknown): string {
  return error instanceof ApiError && error.status !== 0
    ? error.detail
    : 'Something went wrong — check your connection and try again.'
}

// --- Create ------------------------------------------------------------------

const showCreate = ref(false)
const newName = ref('')
const newHint = ref('')
const createError = ref<string | null>(null)
const creating = ref(false)

// Stable-identity list for AppErrorSummary (see ChartsView for the rationale).
const createErrorItems = computed<ErrorSummaryItem[]>(() =>
  createError.value ? [{ text: createError.value }] : [],
)

function openCreate(): void {
  showCreate.value = true
  newName.value = ''
  newHint.value = ''
  createError.value = null
}

async function submitCreate(): Promise<void> {
  if (!newName.value.trim() || creating.value) return
  creating.value = true
  createError.value = null
  try {
    await createMatter(newName.value.trim(), newHint.value.trim() || undefined)
    showCreate.value = false
    await Promise.all([load(), refreshTaxonomyOptions()])
  } catch (error) {
    createError.value = errorText(error)
  } finally {
    creating.value = false
  }
}

// --- Inline edit (rename + hint) ---------------------------------------------

const editingSlug = ref<string | null>(null)
const editName = ref('')
const editHint = ref('')
const rowError = ref<{ slug: string; message: string } | null>(null)
const busySlug = ref<string | null>(null)

function startEdit(matter: MatterOption): void {
  editingSlug.value = matter.slug
  editName.value = matter.name
  editHint.value = matter.hint ?? ''
  rowError.value = null
}

function cancelEdit(): void {
  editingSlug.value = null
}

async function saveEdit(slug: string): Promise<void> {
  if (!editName.value.trim() || busySlug.value) return
  busySlug.value = slug
  rowError.value = null
  try {
    await updateMatter(slug, {
      name: editName.value.trim(),
      hint: editHint.value.trim() || null,
    })
    editingSlug.value = null
    await Promise.all([load(), refreshTaxonomyOptions()])
  } catch (error) {
    rowError.value = { slug, message: errorText(error) }
  } finally {
    busySlug.value = null
  }
}

async function toggleArchive(matter: MatterOption): Promise<void> {
  if (busySlug.value) return
  busySlug.value = matter.slug
  rowError.value = null
  try {
    await updateMatter(matter.slug, { archived: !matter.archived })
    await Promise.all([load(), refreshTaxonomyOptions()])
  } catch (error) {
    rowError.value = { slug: matter.slug, message: errorText(error) }
  } finally {
    busySlug.value = null
  }
}

// --- Delete (two-step inline confirm, no blocking dialog) --------------------

const confirmingSlug = ref<string | null>(null)

function askDelete(slug: string): void {
  confirmingSlug.value = slug
  rowError.value = null
}

function cancelDelete(): void {
  confirmingSlug.value = null
}

async function confirmDelete(slug: string): Promise<void> {
  if (busySlug.value) return
  busySlug.value = slug
  rowError.value = null
  try {
    await deleteMatter(slug)
    confirmingSlug.value = null
    await Promise.all([load(), refreshTaxonomyOptions()])
  } catch (error) {
    rowError.value = { slug, message: errorText(error) }
  } finally {
    busySlug.value = null
  }
}
</script>

<template>
  <div id="matters-view">
    <PageHeader title="Matters">
      <template #actions>
        <AppButton
          v-if="isAdmin && !showCreate"
          variant="primary"
          type="button"
          data-testid="matter-new-button"
          @click="openCreate"
        >
          + New matter
        </AppButton>
      </template>
    </PageHeader>

    <!-- Create form (admins only). -->
    <form
      v-if="showCreate"
      data-testid="matter-create-form"
      class="mb-6 card p-5 space-y-3"
      @submit.prevent="submitCreate"
    >
      <AppInput
        id="matter-create-name"
        v-model="newName"
        testid="matter-create-name"
        label="Name"
        autocomplete="off"
      />
      <AppInput
        id="matter-create-hint"
        v-model="newHint"
        testid="matter-create-hint"
        label="Hint (optional)"
        :hint="HINT_CAPTION"
        autocomplete="off"
      />
      <AppErrorSummary
        v-if="createError"
        data-testid="matter-create-error"
        :errors="createErrorItems"
      />
      <div class="flex gap-3">
        <AppButton
          variant="primary"
          type="submit"
          data-testid="matter-create-submit"
          :disabled="!newName.trim() || creating"
        >
          {{ creating ? 'Creating…' : 'Create matter' }}
        </AppButton>
        <AppButton
          variant="secondary"
          size="sm"
          type="button"
          data-testid="matter-create-cancel"
          @click="showCreate = false"
        >
          Cancel
        </AppButton>
      </div>
    </form>

    <label class="inline-flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300 mb-4">
      <input
        v-model="includeArchived"
        type="checkbox"
        class="form-checkbox"
        data-testid="matter-archived-toggle"
        @change="load"
      />
      Show archived
    </label>

    <p v-if="loading" data-testid="matters-loading" class="text-gray-500 dark:text-gray-400">
      Loading…
    </p>
    <p
      v-else-if="loadError"
      data-testid="matters-error"
      class="text-red-600 dark:text-red-400"
    >
      Could not load matters. Try again later.
    </p>
    <p
      v-else-if="matters.length === 0"
      data-testid="matters-empty"
      class="text-gray-500 dark:text-gray-400"
    >
      No matters yet. Assign one to a document, or create one here.
    </p>

    <ul v-else class="space-y-2" data-testid="matters-list">
      <li
        v-for="matter in matters"
        :key="matter.slug"
        class="card p-4"
        :data-testid="`matter-row-${matter.slug}`"
      >
        <!-- Inline edit form. -->
        <div v-if="editingSlug === matter.slug" class="space-y-2">
          <AppInput
            :id="`matter-edit-name-${matter.slug}`"
            v-model="editName"
            :testid="`matter-edit-name-${matter.slug}`"
            label="Name"
            hide-label
          />
          <AppInput
            :id="`matter-edit-hint-${matter.slug}`"
            v-model="editHint"
            :testid="`matter-edit-hint-${matter.slug}`"
            label="Hint"
            hide-label
            :hint="HINT_CAPTION"
            placeholder="Hint (optional)"
          />
          <div class="flex gap-2">
            <AppButton
              variant="primary"
              size="sm"
              type="button"
              :data-testid="`matter-edit-save-${matter.slug}`"
              :disabled="!editName.trim() || busySlug === matter.slug"
              @click="saveEdit(matter.slug)"
            >
              Save
            </AppButton>
            <AppButton
              variant="secondary"
              size="sm"
              type="button"
              :data-testid="`matter-edit-cancel-${matter.slug}`"
              @click="cancelEdit"
            >
              Cancel
            </AppButton>
          </div>
        </div>

        <!-- Read row. -->
        <div v-else class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <RouterLink
                :to="`/?matter=${matter.slug}`"
                :data-testid="`matter-link-${matter.slug}`"
                class="font-medium text-violet-600 dark:text-violet-300 hover:underline break-words"
              >
                {{ matter.name }}
              </RouterLink>
              <span
                v-if="matter.archived"
                :data-testid="`matter-archived-badge-${matter.slug}`"
                class="text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-300 px-1.5 py-0.5"
              >
                Archived
              </span>
            </div>
            <p
              v-if="matter.hint"
              class="text-sm text-gray-500 dark:text-gray-400 mt-0.5 break-words"
              :data-testid="`matter-hint-${matter.slug}`"
            >
              {{ matter.hint }}
            </p>
            <p
              class="text-xs text-gray-400 dark:text-gray-500 mt-0.5"
              :data-testid="`matter-count-${matter.slug}`"
            >
              {{ matter.document_count }}
              {{ matter.document_count === 1 ? 'document' : 'documents' }}
            </p>
          </div>

          <div v-if="isAdmin" class="flex flex-wrap items-center gap-2 shrink-0">
            <AppButton
              variant="secondary"
              size="sm"
              type="button"
              :data-testid="`matter-edit-${matter.slug}`"
              @click="startEdit(matter)"
            >
              Edit
            </AppButton>
            <AppButton
              variant="secondary"
              size="sm"
              type="button"
              :data-testid="`matter-archive-${matter.slug}`"
              :disabled="busySlug === matter.slug"
              @click="toggleArchive(matter)"
            >
              {{ matter.archived ? 'Unarchive' : 'Archive' }}
            </AppButton>
            <template v-if="confirmingSlug === matter.slug">
              <AppButton
                variant="warning"
                size="sm"
                type="button"
                :data-testid="`matter-delete-confirm-${matter.slug}`"
                :disabled="busySlug === matter.slug"
                @click="confirmDelete(matter.slug)"
              >
                Confirm delete
              </AppButton>
              <AppButton
                variant="secondary"
                size="sm"
                type="button"
                :data-testid="`matter-delete-cancel-${matter.slug}`"
                @click="cancelDelete"
              >
                Cancel
              </AppButton>
            </template>
            <!-- Two-step delete arming affordance: an outline-red button that is
                 deliberately quieter than the solid-red "Confirm delete" it
                 reveals. AppButton has no outline-destructive variant, so this
                 stays hand-rolled to preserve the escalation. -->
            <button
              v-else
              type="button"
              :data-testid="`matter-delete-${matter.slug}`"
              class="btn-sm border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400"
              @click="askDelete(matter.slug)"
            >
              Delete
            </button>
          </div>
        </div>

        <p
          v-if="rowError && rowError.slug === matter.slug"
          :data-testid="`matter-error-${matter.slug}`"
          class="text-sm text-red-600 dark:text-red-400 mt-2"
        >
          {{ rowError.message }}
        </p>
      </li>
    </ul>
  </div>
</template>
