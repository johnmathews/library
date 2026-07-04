<script setup lang="ts">
/**
 * Projects index (route `/projects`): lists every project with its document
 * count, links each to the project-filtered dashboard, and — for admins —
 * supports create, rename/description edit, archive toggle, and delete via the
 * projects REST API. Non-admins get a read-only list.
 */
import { computed, onMounted, ref } from 'vue'

import {
  createProject,
  deleteProject,
  listProjects,
  updateProject,
  type ProjectOption,
} from '@/api/projects'
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

const projects = ref<ProjectOption[]>([])
const loading = ref(true)
const loadError = ref(false)
const includeArchived = ref(false)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = false
  try {
    projects.value = await listProjects(includeArchived.value)
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
const newDescription = ref('')
const createError = ref<string | null>(null)
const creating = ref(false)

// Stable-identity list for AppErrorSummary (see ChartsView for the rationale).
const createErrorItems = computed<ErrorSummaryItem[]>(() =>
  createError.value ? [{ text: createError.value }] : [],
)

function openCreate(): void {
  showCreate.value = true
  newName.value = ''
  newDescription.value = ''
  createError.value = null
}

async function submitCreate(): Promise<void> {
  if (!newName.value.trim() || creating.value) return
  creating.value = true
  createError.value = null
  try {
    await createProject(newName.value.trim(), newDescription.value.trim() || undefined)
    showCreate.value = false
    await Promise.all([load(), refreshTaxonomyOptions()])
  } catch (error) {
    createError.value = errorText(error)
  } finally {
    creating.value = false
  }
}

// --- Inline edit (rename + description) --------------------------------------

const editingSlug = ref<string | null>(null)
const editName = ref('')
const editDescription = ref('')
const rowError = ref<{ slug: string; message: string } | null>(null)
const busySlug = ref<string | null>(null)

function startEdit(project: ProjectOption): void {
  editingSlug.value = project.slug
  editName.value = project.name
  editDescription.value = project.description ?? ''
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
    await updateProject(slug, {
      name: editName.value.trim(),
      description: editDescription.value.trim() || null,
    })
    editingSlug.value = null
    await Promise.all([load(), refreshTaxonomyOptions()])
  } catch (error) {
    rowError.value = { slug, message: errorText(error) }
  } finally {
    busySlug.value = null
  }
}

async function toggleArchive(project: ProjectOption): Promise<void> {
  if (busySlug.value) return
  busySlug.value = project.slug
  rowError.value = null
  try {
    await updateProject(project.slug, { archived: !project.archived })
    await Promise.all([load(), refreshTaxonomyOptions()])
  } catch (error) {
    rowError.value = { slug: project.slug, message: errorText(error) }
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
    await deleteProject(slug)
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
  <div id="projects-view">
    <PageHeader title="Projects">
      <template #actions>
        <AppButton
          v-if="isAdmin && !showCreate"
          variant="primary"
          type="button"
          data-testid="project-new-button"
          @click="openCreate"
        >
          + New project
        </AppButton>
      </template>
    </PageHeader>

    <!-- Create form (admins only). -->
    <form
      v-if="showCreate"
      data-testid="project-create-form"
      class="mb-6 card p-5 space-y-3"
      @submit.prevent="submitCreate"
    >
      <AppInput
        id="project-create-name"
        v-model="newName"
        testid="project-create-name"
        label="Name"
        autocomplete="off"
      />
      <AppInput
        id="project-create-description"
        v-model="newDescription"
        testid="project-create-description"
        label="Description (optional)"
        autocomplete="off"
      />
      <AppErrorSummary
        v-if="createError"
        data-testid="project-create-error"
        :errors="createErrorItems"
      />
      <div class="flex gap-3">
        <AppButton
          variant="primary"
          type="submit"
          data-testid="project-create-submit"
          :disabled="!newName.trim() || creating"
        >
          {{ creating ? 'Creating…' : 'Create project' }}
        </AppButton>
        <AppButton
          variant="secondary"
          size="sm"
          type="button"
          data-testid="project-create-cancel"
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
        data-testid="project-archived-toggle"
        @change="load"
      />
      Show archived
    </label>

    <p v-if="loading" data-testid="projects-loading" class="text-gray-500 dark:text-gray-400">
      Loading…
    </p>
    <p
      v-else-if="loadError"
      data-testid="projects-error"
      class="text-red-600 dark:text-red-400"
    >
      Could not load projects. Try again later.
    </p>
    <p
      v-else-if="projects.length === 0"
      data-testid="projects-empty"
      class="text-gray-500 dark:text-gray-400"
    >
      No projects yet. Assign one to a document, or create one here.
    </p>

    <ul v-else class="space-y-2" data-testid="projects-list">
      <li
        v-for="project in projects"
        :key="project.slug"
        class="card p-4"
        :data-testid="`project-row-${project.slug}`"
      >
        <!-- Inline edit form. -->
        <div v-if="editingSlug === project.slug" class="space-y-2">
          <AppInput
            :id="`project-edit-name-${project.slug}`"
            v-model="editName"
            :testid="`project-edit-name-${project.slug}`"
            label="Name"
            hide-label
          />
          <AppInput
            :id="`project-edit-description-${project.slug}`"
            v-model="editDescription"
            :testid="`project-edit-description-${project.slug}`"
            label="Description"
            hide-label
            placeholder="Description (optional)"
          />
          <div class="flex gap-2">
            <AppButton
              variant="primary"
              size="sm"
              type="button"
              :data-testid="`project-edit-save-${project.slug}`"
              :disabled="!editName.trim() || busySlug === project.slug"
              @click="saveEdit(project.slug)"
            >
              Save
            </AppButton>
            <AppButton
              variant="secondary"
              size="sm"
              type="button"
              :data-testid="`project-edit-cancel-${project.slug}`"
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
                :to="`/?project=${project.slug}`"
                :data-testid="`project-link-${project.slug}`"
                class="font-medium text-violet-600 dark:text-violet-300 hover:underline break-words"
              >
                {{ project.name }}
              </RouterLink>
              <span
                v-if="project.archived"
                :data-testid="`project-archived-badge-${project.slug}`"
                class="text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-300 px-1.5 py-0.5"
              >
                Archived
              </span>
            </div>
            <p
              v-if="project.description"
              class="text-sm text-gray-500 dark:text-gray-400 mt-0.5 break-words"
            >
              {{ project.description }}
            </p>
            <p
              class="text-xs text-gray-400 dark:text-gray-500 mt-0.5"
              :data-testid="`project-count-${project.slug}`"
            >
              {{ project.document_count }}
              {{ project.document_count === 1 ? 'document' : 'documents' }}
            </p>
          </div>

          <div v-if="isAdmin" class="flex flex-wrap items-center gap-2 shrink-0">
            <AppButton
              variant="secondary"
              size="sm"
              type="button"
              :data-testid="`project-edit-${project.slug}`"
              @click="startEdit(project)"
            >
              Edit
            </AppButton>
            <AppButton
              variant="secondary"
              size="sm"
              type="button"
              :data-testid="`project-archive-${project.slug}`"
              :disabled="busySlug === project.slug"
              @click="toggleArchive(project)"
            >
              {{ project.archived ? 'Unarchive' : 'Archive' }}
            </AppButton>
            <template v-if="confirmingSlug === project.slug">
              <AppButton
                variant="warning"
                size="sm"
                type="button"
                :data-testid="`project-delete-confirm-${project.slug}`"
                :disabled="busySlug === project.slug"
                @click="confirmDelete(project.slug)"
              >
                Confirm delete
              </AppButton>
              <AppButton
                variant="secondary"
                size="sm"
                type="button"
                :data-testid="`project-delete-cancel-${project.slug}`"
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
              :data-testid="`project-delete-${project.slug}`"
              class="btn-sm border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400"
              @click="askDelete(project.slug)"
            >
              Delete
            </button>
          </div>
        </div>

        <p
          v-if="rowError && rowError.slug === project.slug"
          :data-testid="`project-error-${project.slug}`"
          class="text-sm text-red-600 dark:text-red-400 mt-2"
        >
          {{ rowError.message }}
        </p>
      </li>
    </ul>
  </div>
</template>
