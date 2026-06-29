<script setup lang="ts">
/**
 * Charts dashboard (route `/charts`): one tile per series. Emergent series are
 * the recurring (sender, kind) groups detected automatically; authored series
 * (W14) are user-curated — created here via "Create a new series" (name it, pick
 * an optional currency, search & add documents) and rendered alongside the
 * emergent ones. The data comes from GET /api/charts; any change refetches.
 */
import { onMounted, ref } from 'vue'
import {
  fetchCharts,
  seriesId,
  authoredSeriesId,
  createAuthoredSeries,
  listDocuments,
  type DocumentSeries,
  type DocumentListItem,
} from '@/api/documents'
import SeriesChartTile from '@/components/SeriesChartTile.vue'

const series = ref<DocumentSeries[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

// Stable per-tile key + deep-link id. Authored series have their own `a-{id}`
// scheme; emergent series use the shared `{sender}-{kind}-{currency}` id.
function tileKey(s: DocumentSeries): string {
  return s.authored_id != null ? authoredSeriesId(s.authored_id) : seriesId(s)
}

async function load(): Promise<void> {
  error.value = null
  try {
    const response = await fetchCharts()
    series.value = response.series
  } catch {
    error.value = 'Could not load charts. Try refreshing the page.'
  } finally {
    loading.value = false
  }
}

// --- Create-a-series flow (W14) --------------------------------------------

const showCreate = ref(false)
const newName = ref('')
const newCurrency = ref('')
const createQuery = ref('')
const createResults = ref<DocumentListItem[]>([])
const selectedDocs = ref<{ id: number; label: string }[]>([])
const creating = ref(false)
const createError = ref<string | null>(null)

function openCreate(): void {
  showCreate.value = true
}

function resetCreate(): void {
  showCreate.value = false
  newName.value = ''
  newCurrency.value = ''
  createQuery.value = ''
  createResults.value = []
  selectedDocs.value = []
  createError.value = null
}

function docLabel(doc: DocumentListItem): string {
  return doc.title?.trim() ? doc.title : `Document #${doc.id}`
}

async function onCreateSearch(): Promise<void> {
  const q = createQuery.value.trim()
  if (!q) {
    createResults.value = []
    return
  }
  const response = await listDocuments({ q, limit: 8 })
  createResults.value = response.items
}

function addSelected(doc: DocumentListItem): void {
  if (!selectedDocs.value.some((d) => d.id === doc.id)) {
    selectedDocs.value.push({ id: doc.id, label: docLabel(doc) })
  }
}

function removeSelected(id: number): void {
  selectedDocs.value = selectedDocs.value.filter((d) => d.id !== id)
}

async function submitCreate(): Promise<void> {
  const name = newName.value.trim()
  if (!name || creating.value) {
    if (!name) createError.value = 'Give the series a name.'
    return
  }
  creating.value = true
  createError.value = null
  try {
    await createAuthoredSeries({
      name,
      currency: newCurrency.value.trim().toUpperCase() || null,
      document_ids: selectedDocs.value.map((d) => d.id),
    })
    resetCreate()
    await load()
  } catch {
    createError.value = 'Could not create the series. Try again.'
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div id="charts-view">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold">Charts</h1>
      <button
        v-if="!showCreate"
        type="button"
        data-testid="charts-create-button"
        class="btn bg-violet-600 hover:bg-violet-700 text-white text-sm"
        @click="openCreate"
      >
        + Create a new series
      </button>
    </div>

    <!-- Create-a-series form (W14). -->
    <form
      v-if="showCreate"
      data-testid="charts-create-form"
      class="mb-6 bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5 space-y-3"
      @submit.prevent="submitCreate"
    >
      <h2 class="text-sm font-semibold text-gray-800 dark:text-gray-100">Create a new series</h2>
      <div class="grid grid-cols-1 sm:grid-cols-[1fr_8rem] gap-3">
        <input
          v-model="newName"
          type="text"
          data-testid="charts-create-name"
          placeholder="Series name"
          class="form-input w-full text-sm"
        />
        <input
          v-model="newCurrency"
          type="text"
          maxlength="3"
          data-testid="charts-create-currency"
          placeholder="Currency"
          class="form-input w-full text-sm uppercase"
        />
      </div>

      <input
        v-model="createQuery"
        type="search"
        data-testid="charts-create-search"
        placeholder="Search documents to add…"
        class="form-input w-full text-sm"
        @input="onCreateSearch"
      />
      <ul
        v-if="createResults.length"
        data-testid="charts-create-results"
        class="space-y-1"
      >
        <li v-for="doc in createResults" :key="doc.id">
          <button
            type="button"
            data-testid="charts-create-result"
            class="text-sm text-left text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 hover:underline"
            @click="addSelected(doc)"
          >
            + {{ docLabel(doc) }}
          </button>
        </li>
      </ul>

      <ul
        v-if="selectedDocs.length"
        data-testid="charts-create-selected"
        class="flex flex-wrap gap-2"
      >
        <li
          v-for="doc in selectedDocs"
          :key="doc.id"
          class="flex items-center gap-1 rounded-full bg-gray-100 dark:bg-gray-700/50 px-3 py-1 text-xs text-gray-700 dark:text-gray-200"
        >
          <span class="truncate max-w-[12rem]">{{ doc.label }}</span>
          <button
            type="button"
            data-testid="charts-create-remove"
            class="text-gray-400 hover:text-red-600 dark:hover:text-red-400"
            :aria-label="`Remove ${doc.label}`"
            @click="removeSelected(doc.id)"
          >
            ×
          </button>
        </li>
      </ul>

      <p v-if="createError" data-testid="charts-create-error" role="alert" class="text-sm text-red-600 dark:text-red-400">
        {{ createError }}
      </p>

      <div class="flex justify-end gap-2">
        <button
          type="button"
          data-testid="charts-create-cancel"
          class="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          :disabled="creating"
          @click="resetCreate"
        >
          Cancel
        </button>
        <button
          type="submit"
          data-testid="charts-create-submit"
          class="btn bg-violet-600 hover:bg-violet-700 text-white text-sm disabled:opacity-50"
          :disabled="creating"
        >
          Create series
        </button>
      </div>
    </form>

    <p v-if="loading" data-testid="charts-loading" class="text-gray-600 dark:text-gray-300">
      Loading charts…
    </p>

    <div
      v-else-if="error"
      data-testid="charts-error"
      role="alert"
      class="bg-white dark:bg-gray-800 border-l-4 border-red-500 rounded-lg px-4 py-3 shadow-xs text-gray-700 dark:text-gray-200"
    >
      {{ error }}
    </div>

    <p
      v-else-if="series.length === 0"
      data-testid="charts-empty"
      class="text-gray-600 dark:text-gray-300"
    >
      No recurring series yet. Once a sender has several documents of the same kind with amounts,
      its trend will appear here — or create one yourself with “Create a new series”.
    </p>

    <div
      v-else
      data-testid="charts-grid"
      class="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-6"
    >
      <SeriesChartTile
        v-for="s in series"
        :key="tileKey(s)"
        :series="s"
        editable
        detail-link
        @changed="load"
      />
    </div>
  </div>
</template>
