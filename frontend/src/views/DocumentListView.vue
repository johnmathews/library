<script setup lang="ts">
/**
 * Documents dashboard: a responsive tile grid with infinite scroll (route `/`).
 *
 * The hero renders DocumentFilterBar (components/DocumentFilterBar.vue), which
 * owns the search input, the filter pills, and the removable active-filter
 * chips. All applied state still lives in the URL query (?q=…&kind=…&tag=…&
 * page=…) so back/forward and refresh keep the view. The bar emits the next
 * query and this view applies it via `router.push` (or `router.replace` for
 * debounced typing) and fetches. The navbar SearchModal
 * (components/SearchModal.vue) still exists and writes the same URL query, so
 * the two stay in sync. Snippets are rendered through `renderSnippet` — see
 * docs/api.md §1.3.3 for why they must never hit v-html unescaped.
 */
import { computed, reactive, ref, watch } from 'vue'
import { useIntersectionObserver, useStorage } from '@vueuse/core'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import { AppBadge, AppBanner, PageHeader } from '@/components/app'
import DocumentFilterBar from '@/components/DocumentFilterBar.vue'
import DashboardFieldsMenu from '@/components/DashboardFieldsMenu.vue'
import SaveViewMenu from '@/components/SaveViewMenu.vue'
import {
  DOCUMENT_LANGUAGES,
  listDocuments,
  thumbnailUrl,
  type DocumentFilters,
  type DocumentLanguage,
  type DocumentListItem,
} from '@/api/documents'
import { renderSnippet } from '@/utils/snippet'
import { resolveKindColor } from '@/utils/kindColor'
import { summarizeReviewReasons } from '@/utils/validationReason'
import { useFlashStore } from '@/stores/flash'
import { useAuthStore } from '@/stores/auth'
import { useReviewQueueStore } from '@/stores/reviewQueue'
import { useHeldEmailsStore } from '@/stores/heldEmails'
import { useJobsStore } from '@/stores/jobs'
import {
  parseDocumentQuery,
  hasActiveFilters,
  buildDocumentQuery,
  DEFAULT_SORT,
  DEFAULT_SORT_DIRECTION,
  type SortField,
  type SortDirection,
  type SortPreference,
} from '@/utils/documentQuery'

const PAGE_SIZE = 25
const MAX_TAGS = 4

const route = useRoute()
const router = useRouter()

const auth = useAuthStore()
const jobsStore = useJobsStore()

// Full-width mode fills the tile and crops the lower part of the page;
// whole-page mode letterboxes the entire first page. Box height is unchanged.
const thumbnailFitClass = computed<string>(() =>
  auth.tilePreview === 'whole_page' ? 'object-contain' : 'object-cover object-top',
)

// One-shot banner from an action that redirected here (e.g. delete).
const flashMessage = ref(useFlashStore().consume())

// --- Applied state (the URL is the source of truth) -----------------------
//
// Sort is the one exception: the URL still wins when it carries a sort param,
// but a bare `/` (no sort param) falls back to the user's remembered choice
// (`sortPref`) rather than the hard default. `setSort` writes both the URL and
// the preference, so a selection sticks across sessions and fresh navigations.
const sortPref = useStorage<SortPreference>('library:doc-sort-v1', {
  sort: DEFAULT_SORT,
  dir: DEFAULT_SORT_DIRECTION,
})

const applied = computed(() => parseDocumentQuery(route.query, sortPref.value))
const isFiltered = computed(() => hasActiveFilters(applied.value))

// The canonical URL query for the current applied state — what the "Save view"
// control persists so a saved view reproduces the exact filter/search/sort set.
const currentQuery = computed<LocationQueryRaw>(() => buildDocumentQuery(applied.value))

function clearFilters(): void {
  void router.push({ query: {} })
}

function applyFilterQuery(query: LocationQueryRaw, opts?: { replace?: boolean }): void {
  if (opts?.replace) void router.replace({ query })
  else void router.push({ query })
}

// --- Fetching (infinite scroll) ---------------------------------------------
//
// The list accumulates: each batch is APPENDED to `items`. `total` (from the
// response) tells us when there is nothing left to load. A deep-linked
// `?page=N` loads the first N pages' worth in one batch so the link round-
// trips; thereafter scrolling (or "Load more") appends one PAGE_SIZE batch at
// a time from `offset = items.length`.
//
// `generation` guards against a late response from a superseded filter: the
// `applied` watch bumps it and clears `items`; any in-flight fetch whose
// generation no longer matches is discarded (belt-and-braces with abort).

const loading = ref(true) // initial load for the current filter set
const loadingMore = ref(false) // a "load more" batch is in flight
const loadError = ref<string | null>(null)
const items = ref<DocumentListItem[]>([])
const total = ref(0)
const hasMore = computed(() => items.value.length < total.value)

// Global count of documents needing review, independent of the current filter,
// for the "Needs review" affordance. Refreshed on each list load (a cheap
// total-only query); the button hides entirely when the count is zero.
const reviewCount = ref(0)
async function refreshReviewCount(): Promise<void> {
  try {
    const response = await listDocuments({ review_status: 'needs_review', limit: 1, offset: 0 })
    reviewCount.value = response.total
  } catch {
    // Non-critical: keep the last known count if the count query fails.
  }
}

const reviewQueue = useReviewQueueStore()
const startingQueue = ref(false)
/** Enter the step-through review queue: load the needs-review set and open the
 *  first document in queue mode (`?queue=1`). No-op if nothing needs review. */
async function startReviewQueue(): Promise<void> {
  if (startingQueue.value) return
  startingQueue.value = true
  try {
    const firstId = await reviewQueue.start()
    if (firstId !== null) {
      void router.push({ name: 'document-detail', params: { id: firstId }, query: { queue: '1' } })
    }
  } finally {
    startingQueue.value = false
  }
}

const isReviewFilterActive = computed(() => applied.value.review === 'needs_review')
// Show the button when something needs review, or when the filter is active (so
// the user can always toggle it back off), otherwise hide it.
const showReviewButton = computed(() => reviewCount.value > 0 || isReviewFilterActive.value)
const reviewButtonLabel = computed(() => {
  const n = reviewCount.value
  return `${n} ${n === 1 ? 'document needs' : 'documents need'} review`
})

// Held-emails affordance, cloning the needs-review pattern: a cheap total-only
// probe refreshed from the list-load path (NOT onMounted — auth-guard timing),
// a button that hides entirely at zero, and the same responsive classes.
const heldEmails = useHeldEmailsStore()
const heldEmailsLabel = computed(() => {
  const n = heldEmails.count
  return `${n} ${n === 1 ? 'email' : 'emails'} held`
})
// The attention row hosts both affordances; render it when either shows.
const showAttentionRow = computed(() => showReviewButton.value || heldEmails.count > 0)

let abortController: AbortController | null = null
let generation = 0

/** Build the API filters for the current applied state at a given window. */
function buildFilters(
  state: typeof applied.value,
  limit: number,
  offset: number,
): DocumentFilters {
  const senderId = Number.parseInt(state.senderId, 10)
  const recipientId = Number.parseInt(state.recipientId, 10)
  return {
    q: state.q || undefined,
    kind: state.kind || undefined,
    sender_id: Number.isInteger(senderId) ? senderId : undefined,
    recipient_id: Number.isInteger(recipientId) ? recipientId : undefined,
    project: state.projects.length ? state.projects : undefined,
    matter: state.matters.length ? state.matters : undefined,
    tag: state.tags.length ? state.tags : undefined,
    language: (state.language || undefined) as DocumentLanguage | undefined,
    status: (state.status || undefined) as DocumentListItem['status'] | undefined,
    review_status: (state.review || undefined) as DocumentListItem['review_status'] | undefined,
    date_from: state.dateFrom || undefined,
    date_to: state.dateTo || undefined,
    // Always send sort + direction explicitly: the frontend's default
    // (added_date) differs from the API's default (document_date), so omitting
    // them at the frontend default would silently order by the wrong field.
    sort: state.sort,
    direction: state.dir,
    limit,
    offset,
  }
}

/** Append the next PAGE_SIZE batch from the current offset (items.length). */
async function loadMore(): Promise<void> {
  if (loading.value || loadingMore.value || !hasMore.value) return
  if (!abortController) return
  const gen = generation
  const signal = abortController.signal
  loadingMore.value = true
  try {
    const response = await listDocuments(
      buildFilters(applied.value, PAGE_SIZE, items.value.length),
      signal,
    )
    if (gen !== generation) return // a newer filter superseded this fetch
    items.value = [...items.value, ...response.items]
    total.value = response.total
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    if (gen !== generation) return
    loadError.value = 'Sorry, the document list could not be loaded. Try again later.'
  } finally {
    if (gen === generation) loadingMore.value = false
  }
}

watch(
  applied,
  async (state) => {
    abortController?.abort()
    abortController = new AbortController()
    const gen = ++generation
    const signal = abortController.signal
    loading.value = true
    loadingMore.value = false
    loadError.value = null
    items.value = []
    total.value = 0
    // Deep-link: ?page=N shows the first N pages in one batch, then appends.
    const initialLimit = state.page * PAGE_SIZE
    try {
      const response = await listDocuments(buildFilters(state, initialLimit, 0), signal)
      if (gen !== generation) return
      items.value = response.items
      total.value = response.total
      loading.value = false
      void refreshReviewCount()
      void heldEmails.refreshCount()
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      if (gen !== generation) return
      items.value = []
      total.value = 0
      loadError.value = 'Sorry, the document list could not be loaded. Try again later.'
      loading.value = false
    }
  },
  { immediate: true },
)

// Live status: when the jobs store reports a document advancing or finishing,
// patch that tile's status in place so its badge (Processing / Failed) updates
// without a refetch — preserving scroll position and the accumulated infinite-
// scroll pages. A document not currently in the list is ignored; it appears on
// the next navigation/fetch.
watch(
  () => jobsStore.lastEvent,
  (event) => {
    if (!event) return
    const item = items.value.find((doc) => doc.id === event.document_id)
    if (item) item.status = event.status as DocumentListItem['status']
  },
)

// IntersectionObserver on a foot sentinel auto-loads as it scrolls into view.
// (jsdom has no real IntersectionObserver, so the visible "Load more" button
// below is the test/a11y fallback.)
const sentinel = ref<HTMLElement | null>(null)
useIntersectionObserver(sentinel, ([entry]) => {
  if (entry?.isIntersecting) void loadMore()
})

// --- Presentation helpers ---------------------------------------------------

const brokenThumbnails = reactive(new Set<number>())

const dateFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})

function formatDate(iso: string | null): string {
  if (!iso) return ''
  const parsed = new Date(`${iso}T00:00:00Z`)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormat.format(parsed)
}

function languageName(language: DocumentLanguage): string {
  return DOCUMENT_LANGUAGES.find((item) => item.value === language)?.text ?? language
}

function fileTypeLabel(item: DocumentListItem): string {
  if (item.mime_type === 'application/pdf') return 'PDF'
  if (item.mime_type.startsWith('image/')) return 'Image'
  if (item.mime_type.startsWith('text/')) return 'Text'
  return 'File'
}

// A PDF with no thumbnail couldn't be rendered — almost always because it is
// password-protected. The tile shows a padlock placeholder instead of a bare
// "PDF" label (mirrors the detail-view padlock).
function isLockedPdf(item: DocumentListItem): boolean {
  return item.mime_type === 'application/pdf' && !item.has_thumbnail
}

// Text documents / email notes (text/*, e.g. text/plain and text/markdown) have
// no visual to thumbnail. Instead of a bare "Text" label we render a small
// metadata "facsimile" in the preview box — a stand-in that echoes the header
// of a real document (sender, addressee,
// date). Empty fields are dropped so the box never shows dangling labels. The
// title is rendered separately as the heading line (see the template).
function previewMetadata(item: DocumentListItem): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = []
  if (item.kind) rows.push({ label: 'Kind', value: item.kind.name })
  if (item.sender) rows.push({ label: 'From', value: item.sender.name })
  if (item.recipient) rows.push({ label: 'To', value: item.recipient.name })
  if (item.document_date) rows.push({ label: 'Date', value: formatDate(item.document_date) })
  return rows
}

// Show the metadata facsimile only when there is something worth showing.
function hasPreviewMetadata(item: DocumentListItem): boolean {
  return Boolean(item.title) || previewMetadata(item).length > 0
}

function formatAmount(item: DocumentListItem): string | null {
  if (item.amount_total === null) return null
  if (item.currency) {
    try {
      return new Intl.NumberFormat('en-GB', {
        style: 'currency',
        currency: item.currency,
        // amount_total is a decimal STRING (backend preserves precision).
        // Number() is safe for the bill/invoice magnitudes this app stores;
        // it would lose precision only beyond ~15 significant digits.
      }).format(Number(item.amount_total))
    } catch {
      return `${item.currency} ${item.amount_total}`
    }
  }
  return item.amount_total
}

// Format each amount once per items change (not twice per render per tile).
const amountLabels = computed<Map<number, string | null>>(() => {
  const labels = new Map<number, string | null>()
  for (const item of items.value) labels.set(item.id, formatAmount(item))
  return labels
})

// Per-tile border accent by document kind (user override → default palette →
// null). Precomputed once per item; a null means "no accent", so the tile keeps
// its neutral default border and violet hover. Applied as the --card-accent CSS
// var; utility-patterns.css turns that into the light/dark/hover border colour.
const tileAccents = computed<Map<number, string | null>>(() => {
  const overrides = auth.kindColors
  const accents = new Map<number, string | null>()
  for (const item of items.value) accents.set(item.id, resolveKindColor(item.kind?.slug, overrides))
  return accents
})

function tileAccentClass(id: number): string {
  return tileAccents.value.get(id) ? 'app-doc-card--accented' : ''
}

function tileAccentStyle(id: number): { '--card-accent': string } | undefined {
  const accent = tileAccents.value.get(id)
  return accent ? { '--card-accent': accent } : undefined
}

// Tiles-per-row preference. 'auto' keeps the responsive breakpoints (the W16
// contract default); a number pins the desktop column count. Per-machine, since
// it's a display-size choice (docs/frontend-view-principles.md §4): persisted in
// localStorage and surfaced to .app-doc-grid as the --doc-grid-cols CSS var.
const GRID_COLS_OPTIONS = ['auto', '3', '4', '5', '6'] as const
const gridCols = useStorage<string>('library:doc-grid-cols', 'auto')
const gridColsStyle = computed<Record<string, string>>(() => ({
  '--doc-grid-cols-phone': String(auth.phoneColumns),
  ...(gridCols.value === 'auto' ? {} : { '--doc-grid-cols': gridCols.value }),
}))

// Dashboard tile date fields beyond the plain document date. Each configurable
// date carries a short muted prefix so several dates on one tile stay
// unambiguous (the document date keeps its bare rendering as the primary date).
// `created_at`/`updated_at` are datetimes, so `tileDate` slices to the date
// portion before `formatDate` (which expects `YYYY-MM-DD`).
const TILE_DATE_FIELDS: Record<
  string,
  { label: string; value: (item: DocumentListItem) => string | null }
> = {
  due_date: { label: 'Due', value: (i) => i.due_date },
  expiry_date: { label: 'Expires', value: (i) => i.expiry_date },
  added_date: { label: 'Added', value: (i) => i.created_at },
  last_edited: { label: 'Edited', value: (i) => i.updated_at },
}

function tileDate(iso: string | null): string | null {
  return iso ? formatDate(iso.slice(0, 10)) : null
}

// --- Sort control ----------------------------------------------------------
// Sort round-trips through the URL like the filters, but is not a "filter"
// (excluded from hasActiveFilters). It has no effect while a search query is
// present — the backend orders by relevance rank — so the control is disabled
// then. Changing sort preserves the other filters and resets to page 1.
const SORT_OPTIONS: { value: SortField; label: string }[] = [
  { value: 'document_date', label: 'Document date' },
  { value: 'added_date', label: 'Added date' },
]
const sortDisabled = computed(() => Boolean(applied.value.q))

function setSort(sort: SortField, dir: SortDirection): void {
  // Remember the choice so a later bare `/` reproduces it, then apply it via
  // the URL like any other filter (resetting to page 1).
  sortPref.value = { sort, dir }
  applyFilterQuery(buildDocumentQuery({ ...applied.value, sort, dir }, 1))
}

function onSortFieldChange(event: Event): void {
  setSort((event.target as HTMLSelectElement).value as SortField, applied.value.dir)
}

function toggleSortDirection(): void {
  setSort(applied.value.sort, applied.value.dir === 'asc' ? 'desc' : 'asc')
}
</script>

<template>
  <AppBanner v-if="flashMessage" variant="success" data-testid="flash-banner" class="mb-6">
    {{ flashMessage }}
  </AppBanner>

  <PageHeader title="Documents" title-id="dashboard-title" />

  <DocumentFilterBar :applied="applied" @apply="applyFilterQuery" @clear="clearFilters" />

  <div v-if="showAttentionRow" class="flex flex-col sm:flex-row sm:items-center gap-2 mb-4">
    <button
      v-if="showReviewButton"
      type="button"
      :class="[
        'flex w-full sm:w-auto items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium transition-colors',
        isReviewFilterActive
          ? 'border-red-500 bg-red-100 text-red-800 ring-1 ring-red-400 dark:border-red-500/60 dark:bg-red-500/20 dark:text-red-200'
          : 'border-red-300 bg-red-50 text-red-700 hover:bg-red-100 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300 dark:hover:bg-red-500/20',
      ]"
      :aria-pressed="isReviewFilterActive"
      data-testid="needs-review-filter"
      @click="applyFilterQuery(isReviewFilterActive ? {} : { review: 'needs_review' })"
    >
      <svg class="h-4 w-4 shrink-0 fill-current" viewBox="0 0 16 16" aria-hidden="true">
        <path
          d="M8 1a1 1 0 0 1 .87.5l6 10.5A1 1 0 0 1 14 13.5H2a1 1 0 0 1-.87-1.5l6-10.5A1 1 0 0 1 8 1Zm0 4a1 1 0 0 0-1 1v2a1 1 0 1 0 2 0V6a1 1 0 0 0-1-1Zm0 6a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z"
        />
      </svg>
      <span class="flex-1 text-left">{{ reviewButtonLabel }}</span>
      <span v-if="isReviewFilterActive" class="text-xs font-normal opacity-80">Showing · clear</span>
    </button>
    <button
      v-if="reviewCount > 0"
      type="button"
      class="flex w-full sm:w-auto items-center justify-center gap-1.5 rounded-md border border-violet-300 bg-violet-50 px-3 py-2 text-sm font-medium text-violet-700 transition-colors hover:bg-violet-100 disabled:opacity-60 dark:border-violet-500/40 dark:bg-violet-500/10 dark:text-violet-300 dark:hover:bg-violet-500/20"
      :disabled="startingQueue"
      data-testid="start-review-queue"
      @click="startReviewQueue"
    >
      {{ startingQueue ? 'Starting…' : 'Review these one by one →' }}
    </button>
    <RouterLink
      v-if="heldEmails.count > 0"
      to="/held-emails"
      class="flex w-full sm:w-auto items-center justify-center gap-1.5 rounded-md border border-violet-300 bg-violet-50 px-3 py-2 text-sm font-medium text-violet-700 transition-colors hover:bg-violet-100 dark:border-violet-500/40 dark:bg-violet-500/10 dark:text-violet-300 dark:hover:bg-violet-500/20"
      data-testid="held-emails-button"
    >
      <svg class="h-4 w-4 shrink-0 fill-current" viewBox="0 0 16 16" aria-hidden="true">
        <path
          d="M2 3a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V4a1 1 0 0 0-1-1H2Zm1.4 2h9.2L8 8.25 3.4 5ZM3 6.7l4.7 3.3a.5.5 0 0 0 .6 0L13 6.7V11H3V6.7Z"
        />
      </svg>
      <span>{{ heldEmailsLabel }} →</span>
    </RouterLink>
  </div>

  <div
    v-if="loadError"
    class="card p-4 text-gray-600 dark:text-gray-300"
    data-testid="load-error"
  >
    {{ loadError }}
  </div>

  <template v-else-if="!loading">
    <div
      v-if="items.length"
      class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-4"
    >
      <p class="text-sm text-gray-500 dark:text-gray-400" data-testid="result-count">
        {{ total }} {{ total === 1 ? 'document' : 'documents' }}
      </p>
      <div class="flex flex-wrap items-end gap-3">
        <div
          class="flex flex-col gap-1"
          :title="sortDisabled ? 'Sorted by relevance while searching' : undefined"
        >
          <span class="text-xs font-medium uppercase tracking-wide text-gray-400">Sort</span>
          <div class="flex items-center gap-1.5">
            <select
              :value="applied.sort"
              :disabled="sortDisabled"
              data-testid="sort-field-select"
              aria-label="Sort field"
              class="form-select py-1 text-sm disabled:opacity-50"
              @change="onSortFieldChange"
            >
              <option v-for="opt in SORT_OPTIONS" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <button
              type="button"
              :disabled="sortDisabled"
              data-testid="sort-dir-toggle"
              :aria-label="applied.dir === 'asc' ? 'Ascending, click for descending' : 'Descending, click for ascending'"
              :aria-pressed="applied.dir === 'asc'"
              class="inline-flex items-center rounded-md border border-gray-300 dark:border-gray-600 px-2 py-1 text-sm text-violet-600 dark:text-violet-400 hover:bg-violet-50 dark:hover:bg-violet-400/10 disabled:opacity-50 disabled:hover:bg-transparent"
              @click="toggleSortDirection"
            >
              {{ applied.dir === 'asc' ? '↑' : '↓' }}
            </button>
          </div>
        </div>
        <!-- Tiles-per-row only affects the multi-column desktop grid; below `lg`
             the grid is fixed (1 col on phones, 2 on tablets), so hide the control. -->
        <label class="hidden lg:flex flex-col gap-1 text-sm text-gray-500 dark:text-gray-400">
          <span class="text-xs font-medium uppercase tracking-wide text-gray-400">Tiles per row</span>
          <select
            v-model="gridCols"
            data-testid="grid-cols-select"
            aria-label="Tiles per row"
            class="form-select py-1 text-sm"
          >
            <option v-for="opt in GRID_COLS_OPTIONS" :key="opt" :value="opt">
              {{ opt === 'auto' ? 'Auto' : opt }}
            </option>
          </select>
        </label>
        <RouterLink
          to="/saved-views"
          data-testid="manage-saved-views-link"
          class="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1 text-sm text-gray-700 transition-colors hover:bg-violet-50 hover:text-violet-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-violet-400/10"
        >
          <svg class="h-4 w-4 fill-current opacity-70" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M2 2a1 1 0 0 0-1 1v10a1 1 0 0 0 1.55.83L8 11.2l5.45 2.63A1 1 0 0 0 15 13V3a1 1 0 0 0-1-1H2Zm0 2h12v7.4l-4.45-2.15a1 1 0 0 0-.87 0L4 11.4V4Z"
            />
          </svg>
          Saved views
        </RouterLink>
        <SaveViewMenu :filter-state="currentQuery" />
        <DashboardFieldsMenu />
      </div>
    </div>

    <div
      v-if="!items.length && !isFiltered"
      class="card p-8 text-center text-gray-500 dark:text-gray-400"
      data-testid="empty-library"
    >
      There are no documents in your library yet.
      <RouterLink class="text-violet-600 hover:underline" :to="{ name: 'upload' }">
        Upload your first document</RouterLink
      >.
    </div>
    <div
      v-else-if="!items.length"
      class="card p-8 text-center text-gray-500 dark:text-gray-400"
      data-testid="empty-results"
    >
      No documents match your search. Try different words, check the filters, or
      <a href="#" class="text-violet-600 hover:underline" @click.prevent="clearFilters"
        >clear the filters</a
      >.
    </div>

    <ul
      v-if="items.length"
      id="dashboard-grid"
      class="app-doc-grid"
      :style="gridColsStyle"
    >
      <li
        v-for="item in items"
        :key="item.id"
        :id="`doc-card-${item.id}`"
        class="relative bg-white dark:bg-gray-800 overflow-hidden app-doc-card"
        :class="tileAccentClass(item.id)"
        :style="tileAccentStyle(item.id)"
        :data-kind="item.kind?.slug"
        data-testid="doc-card"
      >
        <div class="app-doc-card__thumbnail relative aspect-[4/3] bg-gray-100 dark:bg-gray-900/40 border-b border-gray-200 dark:border-gray-700/60 w-full flex items-center justify-center">
          <img
            v-if="item.has_thumbnail && !brokenThumbnails.has(item.id)"
            :class="['aspect-[4/3] w-full', thumbnailFitClass]"
            :src="thumbnailUrl(item.id)"
            alt=""
            loading="lazy"
            @error="brokenThumbnails.add(item.id)"
          />
          <span
            v-else
            class="app-doc-card__thumbnail-fallback flex flex-col items-center gap-2 text-sm font-medium text-gray-400 dark:text-gray-500"
            aria-hidden="true"
          >
            <template v-if="isLockedPdf(item)">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                stroke-width="1.5"
                stroke="currentColor"
                class="w-9 h-9"
                data-testid="thumbnail-locked"
              >
                <path
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z"
                />
              </svg>
              <span class="text-xs">Protected PDF</span>
            </template>
            <template v-else-if="item.mime_type.startsWith('text/') && hasPreviewMetadata(item)">
              <!-- Metadata facsimile: a stand-in "document header" for text
                   documents / email notes, which have no page image to
                   thumbnail. One field per line; title as the heading, the
                   rest as label/value rows. -->
              <span
                class="block w-full px-4 py-3 text-left font-normal not-italic text-gray-600 dark:text-gray-300"
                data-testid="markdown-preview"
              >
                <span
                  v-if="item.title"
                  class="block text-base font-semibold text-gray-700 dark:text-gray-200 line-clamp-2"
                  >{{ item.title }}</span
                >
                <span
                  v-for="row in previewMetadata(item)"
                  :key="row.label"
                  class="mt-1 block truncate text-sm leading-snug"
                >
                  <span class="text-gray-400 dark:text-gray-500">{{ row.label }}</span>
                  {{ row.value }}
                </span>
              </span>
            </template>
            <template v-else>{{ fileTypeLabel(item) }}</template>
          </span>
          <!-- Soften the hard cut where a full-width crop meets the white body:
               fade the bottom of the preview into the card body colour. Only in
               full-width mode (the image bleeds to the edge) and over a real
               thumbnail (the letterboxed/fallback states sit on the gray box). -->
          <div
            v-if="auth.tilePreview === 'full_width' && item.has_thumbnail && !brokenThumbnails.has(item.id)"
            class="app-doc-card__thumbnail-fade pointer-events-none absolute inset-x-0 bottom-0 h-4 bg-gradient-to-b from-transparent to-white dark:to-gray-800"
            aria-hidden="true"
            data-testid="thumbnail-fade"
          ></div>
        </div>
        <div class="p-5 app-doc-card__body">
          <h2 class="app-doc-card__title mb-2">
            <!-- Stretched link: the `after:absolute after:inset-0` pseudo-element
                 makes the whole `relative` card a click target for this single
                 anchor (better on touch), without nesting extra links. -->
            <RouterLink
              class="text-violet-600 font-semibold hover:underline after:absolute after:inset-0 after:content-['']"
              :to="{ name: 'document-detail', params: { id: item.id } }"
            >
              {{ item.title ?? 'Untitled document' }}
            </RouterLink>
          </h2>
          <!-- Card metadata rendered in the user's chosen field order (stored per
               user in dashboard_fields). The "Needs review" badge is pinned first
               and is NOT part of the toggleable/orderable field set. -->
          <p class="flex flex-wrap items-center gap-2 app-doc-card__meta">
            <template v-if="item.review_status === 'needs_review'">
              <AppBadge colour="yellow" data-testid="review-badge">Needs review</AppBadge>
              <span
                v-if="item.review_findings.length"
                class="text-xs text-amber-700 dark:text-amber-400"
                data-testid="review-reason"
              >{{ summarizeReviewReasons(item.review_findings) }}</span>
            </template>
            <template v-for="field in auth.dashboardFields" :key="field">
              <AppBadge v-if="field === 'kind' && item.kind" colour="blue">{{ item.kind.name }}</AppBadge>
              <AppBadge
                v-else-if="field === 'language' && item.language !== 'unknown'"
                colour="grey"
              >
                {{ languageName(item.language) }}
              </AppBadge>
              <template v-else-if="field === 'status'">
                <AppBadge v-if="item.status === 'failed'" colour="red">Failed</AppBadge>
                <AppBadge v-else-if="item.status !== 'indexed'" colour="yellow">Processing</AppBadge>
              </template>
              <AppBadge v-else-if="field === 'file_type'" colour="grey">{{ fileTypeLabel(item) }}</AppBadge>
              <span
                v-else-if="field === 'sender' && item.sender"
                class="app-doc-card__sender text-sm text-gray-500 dark:text-gray-400"
              >
                {{ item.sender.name }}
              </span>
              <span
                v-else-if="field === 'date' && item.document_date"
                class="app-doc-card__date text-sm text-gray-500 dark:text-gray-400"
                data-testid="doc-date"
              >
                <span class="text-gray-400 dark:text-gray-500">Date</span>
                {{ formatDate(item.document_date) }}
              </span>
              <span
                v-else-if="TILE_DATE_FIELDS[field] && tileDate(TILE_DATE_FIELDS[field].value(item))"
                :class="`app-doc-card__${field.replace('_', '-')} text-sm text-gray-500 dark:text-gray-400`"
                :data-testid="`doc-${field.replace('_', '-')}`"
              >
                <span class="text-gray-400 dark:text-gray-500">{{ TILE_DATE_FIELDS[field].label }}</span>
                {{ tileDate(TILE_DATE_FIELDS[field].value(item)) }}
              </span>
              <span
                v-else-if="field === 'amount' && amountLabels.get(item.id)"
                class="app-doc-card__amount text-sm text-gray-500 dark:text-gray-400"
              >
                {{ amountLabels.get(item.id) }}
              </span>
              <span
                v-else-if="field === 'tags' && item.tags.length"
                class="inline-flex flex-wrap items-center gap-2 app-doc-card__tags"
                data-testid="doc-tags"
              >
                <AppBadge v-for="tag in item.tags.slice(0, MAX_TAGS)" :key="tag.slug" colour="grey">
                  {{ tag.name }}
                </AppBadge>
                <span
                  v-if="item.tags.length > MAX_TAGS"
                  class="app-doc-card__tags-more text-sm text-gray-500 dark:text-gray-400"
                >
                  +{{ item.tags.length - MAX_TAGS }}
                </span>
              </span>
            </template>
          </p>
          <p
            v-if="item.summary && !(applied.q && item.snippet)"
            class="text-sm text-gray-600 dark:text-gray-400 mt-2 app-doc-card__summary line-clamp-3"
            data-testid="doc-summary"
          >{{ item.summary }}</p>
          <!-- eslint-disable-next-line vue/no-v-html -- renderSnippet escapes everything except the ts_headline <b> markers (docs/api.md §1.3.3) -->
          <p
            v-if="applied.q && item.snippet"
            class="text-sm text-gray-500 dark:text-gray-400 mt-2 app-doc-card__snippet"
            v-html="renderSnippet(item.snippet)"
          ></p>
        </div>
      </li>
    </ul>

    <!-- Infinite scroll: the sentinel triggers loadMore() as it enters view;
         the button is the visible a11y / no-IntersectionObserver fallback. -->
    <div v-if="items.length" class="mt-6 flex flex-col items-center gap-3">
      <p
        v-if="loadingMore"
        class="text-sm text-gray-500 dark:text-gray-400"
        data-testid="loading-more"
      >
        Loading more…
      </p>
      <button
        v-if="hasMore"
        type="button"
        class="inline-flex items-center rounded-lg border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-800 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
        data-testid="load-more"
        :disabled="loadingMore"
        @click="loadMore"
      >
        Load more
      </button>
      <div ref="sentinel" aria-hidden="true" class="h-px w-full"></div>
    </div>
  </template>
</template>
