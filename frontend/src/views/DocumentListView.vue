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
import { AppBadge, AppBanner } from '@/components/app'
import DocumentFilterBar from '@/components/DocumentFilterBar.vue'
import {
  DOCUMENT_LANGUAGES,
  listDocuments,
  thumbnailUrl,
  type DocumentFilters,
  type DocumentLanguage,
  type DocumentListItem,
} from '@/api/documents'
import { renderSnippet } from '@/utils/snippet'
import { useFlashStore } from '@/stores/flash'
import { useAuthStore } from '@/stores/auth'
import { useJobsStore } from '@/stores/jobs'
import type { DashboardField } from '@/api/settings'
import { parseDocumentQuery, hasActiveFilters } from '@/utils/documentQuery'

const PAGE_SIZE = 25
const MAX_TAGS = 4

const route = useRoute()
const router = useRouter()

const auth = useAuthStore()
const jobsStore = useJobsStore()

function shows(field: DashboardField): boolean {
  return auth.dashboardFields.includes(field)
}

// Full-width mode fills the tile and crops the lower part of the page;
// whole-page mode letterboxes the entire first page. Box height is unchanged.
const thumbnailFitClass = computed<string>(() =>
  auth.tilePreview === 'whole_page' ? 'object-contain' : 'object-cover object-top',
)

// One-shot banner from an action that redirected here (e.g. delete).
const flashMessage = ref(useFlashStore().consume())

// --- Applied state (the URL is the source of truth) -----------------------

const applied = computed(() => parseDocumentQuery(route.query))
const isFiltered = computed(() => hasActiveFilters(applied.value))

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
    tag: state.tags.length ? state.tags : undefined,
    language: (state.language || undefined) as DocumentLanguage | undefined,
    status: (state.status || undefined) as DocumentListItem['status'] | undefined,
    review_status: (state.review || undefined) as DocumentListItem['review_status'] | undefined,
    date_from: state.dateFrom || undefined,
    date_to: state.dateTo || undefined,
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

// Tiles-per-row preference. 'auto' keeps the responsive breakpoints (the W16
// contract default); a number pins the desktop column count. Per-machine, since
// it's a display-size choice (docs/frontend-view-principles.md §4): persisted in
// localStorage and surfaced to .app-doc-grid as the --doc-grid-cols CSS var.
const GRID_COLS_OPTIONS = ['auto', '3', '4', '5', '6'] as const
const gridCols = useStorage<string>('library:doc-grid-cols', 'auto')
const gridColsStyle = computed(() =>
  gridCols.value === 'auto' ? {} : { '--doc-grid-cols': gridCols.value },
)
</script>

<template>
  <AppBanner v-if="flashMessage" variant="success" data-testid="flash-banner" class="mb-6">
    {{ flashMessage }}
  </AppBanner>

  <h1 id="dashboard-title" class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-2">Documents</h1>

  <DocumentFilterBar :applied="applied" @apply="applyFilterQuery" @clear="clearFilters" />

  <div class="flex flex-wrap gap-2 mb-4">
    <button
      type="button"
      :class="[
        'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium transition-colors',
        applied.review === 'needs_review'
          ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-400/30 dark:text-yellow-400 ring-1 ring-yellow-400/50'
          : 'bg-gray-100 text-gray-600 hover:bg-yellow-50 hover:text-yellow-700 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-yellow-400/20 dark:hover:text-yellow-400',
      ]"
      data-testid="needs-review-filter"
      @click="applyFilterQuery(applied.review === 'needs_review' ? {} : { review: 'needs_review' })"
    >
      Needs review
    </button>
  </div>

  <div
    v-if="loadError"
    class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-4 text-gray-600 dark:text-gray-300"
    data-testid="load-error"
  >
    {{ loadError }}
  </div>

  <template v-else-if="!loading">
    <div v-if="items.length" class="flex items-center justify-between gap-3 mb-4">
      <p class="text-sm text-gray-500 dark:text-gray-400" data-testid="result-count">
        {{ total }} {{ total === 1 ? 'document' : 'documents' }}
      </p>
      <label class="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
        <span class="hidden sm:inline">Tiles per row</span>
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
    </div>

    <div
      v-if="!items.length && !isFiltered"
      class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-8 text-center text-gray-500 dark:text-gray-400"
      data-testid="empty-library"
    >
      There are no documents in your library yet.
      <RouterLink class="text-violet-600 hover:underline" :to="{ name: 'upload' }">
        Upload your first document</RouterLink
      >.
    </div>
    <div
      v-else-if="!items.length"
      class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-8 text-center text-gray-500 dark:text-gray-400"
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
        class="relative bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700/60 overflow-hidden app-doc-card"
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
          <p class="flex flex-wrap items-center gap-2 app-doc-card__meta">
            <AppBadge v-if="shows('kind') && item.kind" colour="blue">{{ item.kind.name }}</AppBadge>
            <AppBadge v-if="shows('language') && item.language !== 'unknown'" colour="grey">
              {{ languageName(item.language) }}
            </AppBadge>
            <template v-if="shows('status')">
              <AppBadge v-if="item.status === 'failed'" colour="red">Failed</AppBadge>
              <AppBadge v-else-if="item.status !== 'indexed'" colour="yellow">Processing</AppBadge>
            </template>
            <AppBadge v-if="item.review_status === 'needs_review'" colour="yellow" data-testid="review-badge">Needs review</AppBadge>
            <AppBadge v-if="shows('file_type')" colour="grey">{{ fileTypeLabel(item) }}</AppBadge>
            <span
              v-if="shows('sender') && item.sender"
              class="app-doc-card__sender text-sm text-gray-500 dark:text-gray-400"
            >
              {{ item.sender.name }}
            </span>
            <span
              v-if="shows('date') && item.document_date"
              class="app-doc-card__date text-sm text-gray-500 dark:text-gray-400"
            >
              {{ formatDate(item.document_date) }}
            </span>
            <span
              v-if="shows('amount') && amountLabels.get(item.id)"
              class="app-doc-card__amount text-sm text-gray-500 dark:text-gray-400"
            >
              {{ amountLabels.get(item.id) }}
            </span>
          </p>
          <p
            v-if="shows('tags') && item.tags.length"
            class="flex flex-wrap items-center gap-2 mt-2 app-doc-card__tags"
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
