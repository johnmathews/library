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
import { PageHeader } from '@/components/app'

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
        <button
          v-if="isAdmin && !showCreate"
          type="button"
          data-testid="project-new-button"
          class="btn bg-violet-600 hover:bg-violet-700 text-white text-sm"
          @click="openCreate"
        >
          + New project
        </button>
      </template>
    </PageHeader>

    <!-- Create form (admins only). -->
    <form
      v-if="showCreate"
      data-testid="project-create-form"
      class="mb-6 card p-5 space-y-3"
      @submit.prevent="submitCreate"
    >
      <div>
        <label class="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300" for="project-create-name">
          Name
        </label>
        <input
          id="project-create-name"
          v-model="newName"
          data-testid="project-create-name"
          class="form-input w-full"
          type="text"
          autocomplete="off"
        />
      </div>
      <div>
        <label
          class="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300"
          for="project-create-description"
        >
          Description <span class="text-gray-400">(optional)</span>
        </label>
        <input
          id="project-create-description"
          v-model="newDescription"
          data-testid="project-create-description"
          class="form-input w-full"
          type="text"
          autocomplete="off"
        />
      </div>
      <p v-if="createError" data-testid="project-create-error" class="text-sm text-red-600 dark:text-red-400">
        {{ createError }}
      </p>
      <div class="flex gap-3">
        <button
          type="submit"
          data-testid="project-create-submit"
          class="btn bg-violet-600 hover:bg-violet-700 text-white text-sm"
          :disabled="!newName.trim() || creating"
        >
          {{ creating ? 'Creating…' : 'Create project' }}
        </button>
        <button
          type="button"
          data-testid="project-create-cancel"
          class="btn-sm border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
          @click="showCreate = false"
        >
          Cancel
        </button>
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
          <input
            v-model="editName"
            :data-testid="`project-edit-name-${project.slug}`"
            class="form-input w-full"
            type="text"
          />
          <input
            v-model="editDescription"
            :data-testid="`project-edit-description-${project.slug}`"
            class="form-input w-full"
            type="text"
            placeholder="Description (optional)"
          />
          <div class="flex gap-2">
            <button
              type="button"
              :data-testid="`project-edit-save-${project.slug}`"
              class="btn-sm bg-violet-600 hover:bg-violet-700 text-white"
              :disabled="!editName.trim() || busySlug === project.slug"
              @click="saveEdit(project.slug)"
            >
              Save
            </button>
            <button
              type="button"
              :data-testid="`project-edit-cancel-${project.slug}`"
              class="btn-sm border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
              @click="cancelEdit"
            >
              Cancel
            </button>
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
            <button
              type="button"
              :data-testid="`project-edit-${project.slug}`"
              class="btn-sm border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
              @click="startEdit(project)"
            >
              Edit
            </button>
            <button
              type="button"
              :data-testid="`project-archive-${project.slug}`"
              class="btn-sm border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
              :disabled="busySlug === project.slug"
              @click="toggleArchive(project)"
            >
              {{ project.archived ? 'Unarchive' : 'Archive' }}
            </button>
            <template v-if="confirmingSlug === project.slug">
              <button
                type="button"
                :data-testid="`project-delete-confirm-${project.slug}`"
                class="btn-sm bg-red-600 hover:bg-red-700 text-white"
                :disabled="busySlug === project.slug"
                @click="confirmDelete(project.slug)"
              >
                Confirm delete
              </button>
              <button
                type="button"
                :data-testid="`project-delete-cancel-${project.slug}`"
                class="btn-sm border-gray-200 dark:border-gray-700/60 text-gray-700 dark:text-gray-300"
                @click="cancelDelete"
              >
                Cancel
              </button>
            </template>
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
