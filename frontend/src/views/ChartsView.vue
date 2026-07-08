<script setup lang="ts">
/**
 * Charts dashboard (route `/charts`): one tile per series. Emergent series are
 * the recurring (sender, kind) groups detected automatically; authored series
 * (W14) are user-curated — created here via "Create a new series" (name it, pick
 * an optional currency, search & add documents) and rendered alongside the
 * emergent ones. The data comes from GET /api/charts; any change refetches.
 */
import { computed, onMounted, ref } from 'vue'
import {
  fetchCharts,
  seriesId,
  authoredSeriesId,
  createAuthoredSeries,
  listDocuments,
  type DocumentSeries,
  type DocumentListItem,
  type CandidateSeries,
} from '@/api/documents'
import SeriesChartTile from '@/components/SeriesChartTile.vue'
import CurrencySelect from '@/components/CurrencySelect.vue'
import ChartControls from '@/components/charts/ChartControls.vue'
import {
  AppBanner,
  AppButton,
  AppErrorSummary,
  AppInput,
  AppTextarea,
  PageHeader,
  type ErrorSummaryItem,
} from '@/components/app'
import { useChartsTimeframe } from '@/composables/useChartsTimeframe'
import { useChartsGrouping } from '@/composables/useChartsGrouping'

// Shared time-range window + grouping across every tile (W4/W5). Display-only.
const {
  timeframe,
  customFrom,
  customTo,
  options: timeframeOptions,
  bounds: axisBounds,
  selectTimeframe,
  setCustom,
} = useChartsTimeframe()
const { grouping, options: groupingOptions } = useChartsGrouping()

const series = ref<DocumentSeries[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

// --- Candidate ("almost there") series -------------------------------------
// Emergent buckets one document short of charting. Hidden behind an opt-in
// toggle; a candidate can be promoted into an authored series (which charts
// immediately) so tracking starts before the next matching document lands.
// Once promoted, the backend stops offering the bucket (it matches an authored
// series' signature), so the reload after a promote simply drops the row.
const candidates = ref<CandidateSeries[]>([])
const showCandidates = ref(false)
const promotingKey = ref<string | null>(null)
const candidateError = ref<string | null>(null)

function candidateKey(c: CandidateSeries): string {
  return `${c.sender_id}-${c.kind_id}-${c.currency ?? 'none'}`
}

async function promoteCandidate(c: CandidateSeries): Promise<void> {
  if (promotingKey.value) return
  promotingKey.value = candidateKey(c)
  candidateError.value = null
  try {
    await createAuthoredSeries({
      name: `${c.sender} · ${c.kind}`,
      currency: c.currency,
      document_ids: c.document_ids,
    })
    await load()
  } catch {
    candidateError.value = 'Could not create the chart. Try again.'
  } finally {
    promotingKey.value = null
  }
}

// Stable per-tile key + deep-link id. Authored series have their own `a-{id}`
// scheme; emergent series use the shared `{sender}-{kind}-{currency}` id.
function tileKey(s: DocumentSeries): string {
  return s.authored_id != null ? authoredSeriesId(s.authored_id) : seriesId(s)
}

// Drop a deleted tile from the grid without a full refetch (W4).
function onTileDeleted(s: DocumentSeries): void {
  series.value = series.value.filter((x) => tileKey(x) !== tileKey(s))
}

async function load(): Promise<void> {
  error.value = null
  try {
    const response = await fetchCharts()
    series.value = response.series
    candidates.value = response.candidates ?? []
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
const newDescription = ref('')
const createQuery = ref('')
const createResults = ref<DocumentListItem[]>([])
const selectedDocs = ref<{ id: number; label: string; currency: string | null }[]>([])
const creating = ref(false)
const createError = ref<string | null>(null)

// Stable-identity list for AppErrorSummary: only re-derived when the message
// changes, so editing other form fields while the error shows doesn't re-fire
// the summary's focus-on-change behaviour.
const createErrorItems = computed<ErrorSummaryItem[]>(() =>
  createError.value ? [{ text: createError.value }] : [],
)

// Mechanical currency-consistency check: warn when the chosen chart currency
// disagrees with the currencies of the selected documents. Documents with no
// currency don't count as a conflict. Purely advisory — creation is not blocked.
const currencyMismatch = computed<{ currencies: string[]; count: number } | null>(() => {
  const chosen = newCurrency.value.trim().toUpperCase()
  if (!chosen) return null
  const conflicting = selectedDocs.value.filter(
    (d) => d.currency && d.currency.toUpperCase() !== chosen,
  )
  if (conflicting.length === 0) return null
  const currencies = [...new Set(conflicting.map((d) => d.currency!.toUpperCase()))].sort()
  return { currencies, count: conflicting.length }
})

function openCreate(): void {
  showCreate.value = true
}

function resetCreate(): void {
  showCreate.value = false
  newName.value = ''
  newCurrency.value = ''
  newDescription.value = ''
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
    selectedDocs.value.push({ id: doc.id, label: docLabel(doc), currency: doc.currency })
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
      description: newDescription.value.trim() || null,
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
    <PageHeader title="Charts">
      <template #actions>
        <AppButton
          v-if="candidates.length > 0"
          variant="secondary"
          type="button"
          data-testid="charts-candidates-toggle"
          @click="showCandidates = !showCandidates"
        >
          {{ showCandidates ? 'Hide' : 'Show' }} candidates ({{ candidates.length }})
        </AppButton>
        <AppButton
          v-if="!showCreate"
          variant="primary"
          type="button"
          data-testid="charts-create-button"
          @click="openCreate"
        >
          + Create a new series
        </AppButton>
      </template>
    </PageHeader>

    <!-- Shared time-range + grouping applied to every tile (W5). -->
    <ChartControls
      class="mb-6"
      :timeframe="timeframe"
      :timeframe-options="timeframeOptions"
      :custom-from="customFrom"
      :custom-to="customTo"
      :grouping="grouping"
      :grouping-options="groupingOptions"
      @select-timeframe="selectTimeframe"
      @set-custom="setCustom"
      @update:grouping="grouping = $event"
    />

    <!-- Create-a-series form (W14). -->
    <form
      v-if="showCreate"
      data-testid="charts-create-form"
      class="mb-6 card p-5 space-y-3"
      @submit.prevent="submitCreate"
    >
      <h2 class="text-sm font-semibold text-gray-800 dark:text-gray-100">Create a new series</h2>
      <div class="grid grid-cols-1 sm:grid-cols-[1fr_10rem] gap-3 items-end">
        <AppInput
          id="charts-create-name"
          v-model="newName"
          testid="charts-create-name"
          label="Series name"
          hide-label
          placeholder="Series name"
        />
        <CurrencySelect v-model="newCurrency" data-testid="charts-create-currency" />
      </div>

      <AppTextarea
        id="charts-create-description"
        v-model="newDescription"
        testid="charts-create-description"
        label="Subtitle or context"
        hide-label
        :rows="2"
        placeholder="Subtitle or context (optional) — e.g. what this series tracks and why"
      />

      <AppInput
        id="charts-create-search"
        v-model="createQuery"
        testid="charts-create-search"
        label="Search documents to add"
        hide-label
        type="search"
        placeholder="Search documents to add…"
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

      <p
        v-if="currencyMismatch"
        data-testid="charts-create-currency-warning"
        role="alert"
        class="flex items-start gap-2 rounded-md bg-amber-50 dark:bg-amber-500/10 border border-amber-300 dark:border-amber-500/30 px-3 py-2 text-sm text-amber-800 dark:text-amber-300"
      >
        <svg class="mt-0.5 h-4 w-4 shrink-0 fill-current" viewBox="0 0 16 16" aria-hidden="true">
          <path
            d="M8 1a1 1 0 0 1 .87.5l6 10.5A1 1 0 0 1 14 13.5H2a1 1 0 0 1-.87-1.5l6-10.5A1 1 0 0 1 8 1Zm0 4a1 1 0 0 0-1 1v2a1 1 0 1 0 2 0V6a1 1 0 0 0-1-1Zm0 6a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z"
          />
        </svg>
        <span>
          {{ currencyMismatch.count }}
          {{ currencyMismatch.count === 1 ? 'selected document is' : 'selected documents are' }}
          in {{ currencyMismatch.currencies.join(', ') }}, but this series is set to
          {{ newCurrency.trim().toUpperCase() }}. You can still create it — amounts in another
          currency are handled separately.
        </span>
      </p>

      <AppErrorSummary
        v-if="createError"
        data-testid="charts-create-error"
        :errors="createErrorItems"
      />

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
        <AppButton
          variant="primary"
          type="submit"
          data-testid="charts-create-submit"
          :disabled="creating"
        >
          Create series
        </AppButton>
      </div>
    </form>

    <!-- "Almost there" candidates: emergent buckets one document short of
         charting. Hidden until the header toggle reveals them (W-candidates). -->
    <section
      v-if="showCandidates && candidates.length"
      data-testid="charts-candidates"
      class="mb-6 card p-5 space-y-3"
    >
      <div>
        <h2 class="text-sm font-semibold text-gray-800 dark:text-gray-100">Almost there</h2>
        <p class="text-xs text-gray-500 dark:text-gray-400">
          These groups are a document short of charting automatically. Create a chart now to start
          tracking, or just wait — the next matching document promotes them on its own.
        </p>
      </div>

      <AppBanner v-if="candidateError" variant="error" data-testid="charts-candidates-error">
        {{ candidateError }}
      </AppBanner>

      <ul class="space-y-2">
        <li
          v-for="c in candidates"
          :key="candidateKey(c)"
          data-testid="charts-candidate"
          class="flex items-center justify-between gap-3 rounded-md border border-gray-200 dark:border-gray-700 px-3 py-2"
        >
          <div class="min-w-0">
            <p class="truncate text-sm text-gray-800 dark:text-gray-100">
              {{ c.sender }} · {{ c.kind }}
              <span v-if="c.currency" class="text-gray-400 dark:text-gray-500">{{ c.currency }}</span>
            </p>
            <p class="text-xs uppercase tracking-wide text-gray-400 dark:text-gray-500">
              {{ c.count }} of {{ c.needed }} documents
            </p>
          </div>
          <AppButton
            variant="primary"
            type="button"
            data-testid="charts-candidate-promote"
            :disabled="promotingKey === candidateKey(c)"
            @click="promoteCandidate(c)"
          >
            Create chart
          </AppButton>
        </li>
      </ul>
    </section>

    <p v-if="loading" data-testid="charts-loading" class="text-gray-600 dark:text-gray-300">
      Loading charts…
    </p>

    <AppBanner v-else-if="error" variant="error" data-testid="charts-error">
      {{ error }}
    </AppBanner>

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
        :axis-min="axisBounds.min"
        :axis-max="axisBounds.max"
        :grouping="grouping"
        @changed="load"
        @deleted="onTileDeleted(s)"
      />
    </div>
  </div>
</template>
