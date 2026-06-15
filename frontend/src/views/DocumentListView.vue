<script setup lang="ts">
/**
 * Documents dashboard: a responsive tile grid with pagination (route `/`).
 *
 * All applied state lives in the URL query (?q=…&kind=…&page=…) so back/
 * forward and refresh keep the view. The search form itself moved into the
 * navbar's SearchModal (components/SearchModal.vue), which pushes the same
 * URL query; this view only *reads* the query, fetches, and shows an
 * active-filter summary with a "Clear filters" escape hatch. Snippets are
 * rendered through `renderSnippet` — see docs/api.md §1.3.3 for why they
 * must never hit v-html unescaped.
 */
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppBadge, AppBanner, AppPagination } from '@/components/app'
import {
  DOCUMENT_LANGUAGES,
  listDocuments,
  thumbnailUrl,
  type DocumentFilters,
  type DocumentLanguage,
  type DocumentListItem,
} from '@/api/documents'
import { useTaxonomyOptions } from '@/composables/taxonomyOptions'
import { renderSnippet } from '@/utils/snippet'
import { useFlashStore } from '@/stores/flash'
import { useAuthStore } from '@/stores/auth'
import type { DashboardField } from '@/api/settings'
import {
  parseDocumentQuery,
  buildDocumentQuery,
  hasActiveFilters,
} from '@/utils/documentQuery'

const PAGE_SIZE = 25
const MAX_TAGS = 4

const route = useRoute()
const router = useRouter()

const auth = useAuthStore()

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

function goToPage(page: number): void {
  void router.push({ query: buildDocumentQuery(applied.value, page) })
}

// --- Active-filter summary ---------------------------------------------------

// Resolve kind/sender/tag values to display names via the shared taxonomy
// cache (also used by the search modal); raw slug/id until loaded or on
// fetch failure. Loaded lazily — only when such a filter is active.
const { kinds, senders, tags, ensureLoaded } = useTaxonomyOptions()

watch(
  () => Boolean(applied.value.kind || applied.value.senderId || applied.value.tags.length),
  (needsNames) => {
    if (needsNames) void ensureLoaded()
  },
  { immediate: true },
)

const filterSummary = computed<string[]>(() => {
  const a = applied.value
  const parts: string[] = []
  if (a.q) parts.push(`search “${a.q}”`)
  if (a.kind) parts.push(`kind ${kinds.value.find((k) => k.slug === a.kind)?.name ?? a.kind}`)
  if (a.senderId) {
    const name = senders.value.find((s) => String(s.id) === a.senderId)?.name
    parts.push(`sender ${name ?? `#${a.senderId}`}`)
  }
  for (const slug of a.tags) {
    parts.push(`tag ${tags.value.find((t) => t.slug === slug)?.name ?? slug}`)
  }
  if (a.language) parts.push(`language ${languageName(a.language as DocumentLanguage)}`)
  if (a.status) parts.push(`status ${a.status}`)
  if (a.dateFrom) parts.push(`dated from ${formatDate(a.dateFrom)}`)
  if (a.dateTo) parts.push(`dated to ${formatDate(a.dateTo)}`)
  return parts
})

// --- Fetching ---------------------------------------------------------------

const loading = ref(true)
const loadError = ref<string | null>(null)
const items = ref<DocumentListItem[]>([])
const total = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)))

let abortController: AbortController | null = null

watch(
  applied,
  async (state) => {
    abortController?.abort()
    abortController = new AbortController()
    loading.value = true
    loadError.value = null
    const senderId = Number.parseInt(state.senderId, 10)
    const filters: DocumentFilters = {
      q: state.q || undefined,
      kind: state.kind || undefined,
      sender_id: Number.isInteger(senderId) ? senderId : undefined,
      tag: state.tags.length ? state.tags : undefined,
      language: (state.language || undefined) as DocumentLanguage | undefined,
      status: (state.status || undefined) as DocumentListItem['status'] | undefined,
      date_from: state.dateFrom || undefined,
      date_to: state.dateTo || undefined,
      limit: PAGE_SIZE,
      offset: (state.page - 1) * PAGE_SIZE,
    }
    try {
      const response = await listDocuments(filters, abortController.signal)
      items.value = response.items
      total.value = response.total
      loading.value = false
    } catch (error: unknown) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      items.value = []
      total.value = 0
      loadError.value = 'Sorry, the document list could not be loaded. Try again later.'
      loading.value = false
    }
  },
  { immediate: true },
)

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
</script>

<template>
  <AppBanner v-if="flashMessage" variant="success" data-testid="flash-banner" class="mb-6">
    {{ flashMessage }}
  </AppBanner>

  <h1 id="dashboard-title" class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-2">Documents</h1>

  <div
    v-if="loadError"
    class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-4 text-gray-600 dark:text-gray-300"
    data-testid="load-error"
  >
    {{ loadError }}
  </div>

  <template v-else-if="!loading">
    <p
      v-if="isFiltered"
      class="text-sm text-gray-500 dark:text-gray-400 mb-4"
      data-testid="filter-summary"
    >
      Filtered by {{ filterSummary.join(', ') }} ·
      <a
        href="#"
        class="text-violet-600 hover:underline"
        data-testid="clear-filters"
        @click.prevent="clearFilters"
        >Clear filters</a
      >
    </p>

    <p
      v-if="items.length"
      class="text-sm text-gray-500 dark:text-gray-400 mb-4"
      data-testid="result-count"
    >
      {{ total }} {{ total === 1 ? 'document' : 'documents' }}
    </p>

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
              :to="{
                name: 'document-detail',
                params: { id: item.id },
                // Carry the search into the detail page so the OCR text
                // view can highlight the matches.
                query: applied.q ? { highlight: applied.q } : {},
              }"
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
          <!-- eslint-disable-next-line vue/no-v-html -- renderSnippet escapes everything except the ts_headline <b> markers (docs/api.md §1.3.3) -->
          <p
            v-if="applied.q && item.snippet"
            class="text-sm text-gray-500 dark:text-gray-400 mt-2 app-doc-card__snippet"
            v-html="renderSnippet(item.snippet)"
          ></p>
        </div>
      </li>
    </ul>

    <div class="mt-6">
      <AppPagination :page="applied.page" :total-pages="totalPages" @change="goToPage" />
    </div>
  </template>
</template>
