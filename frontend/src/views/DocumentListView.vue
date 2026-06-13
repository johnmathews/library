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
import { useRoute, useRouter, type LocationQuery, type LocationQueryRaw } from 'vue-router'
import GovNotificationBanner from '@/components/govuk/GovNotificationBanner.vue'
import GovPagination from '@/components/govuk/GovPagination.vue'
import GovTag from '@/components/govuk/GovTag.vue'
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

const PAGE_SIZE = 25
const MAX_TAGS = 4

const route = useRoute()
const router = useRouter()

const auth = useAuthStore()

function shows(field: DashboardField): boolean {
  return auth.dashboardFields.includes(field)
}

// One-shot banner from an action that redirected here (e.g. delete).
const flashMessage = ref(useFlashStore().consume())

// --- Applied state (the URL is the source of truth) -----------------------

function queryString(query: LocationQuery, key: string): string {
  const value = query[key]
  return typeof value === 'string' ? value : ''
}

const applied = computed(() => ({
  q: queryString(route.query, 'q'),
  kind: queryString(route.query, 'kind'),
  senderId: queryString(route.query, 'sender_id'),
  tag: queryString(route.query, 'tag'),
  language: queryString(route.query, 'language'),
  dateFrom: queryString(route.query, 'date_from'),
  dateTo: queryString(route.query, 'date_to'),
  page: Math.max(1, Number.parseInt(queryString(route.query, 'page'), 10) || 1),
}))

const isFiltered = computed(() => {
  const a = applied.value
  return Boolean(a.q || a.kind || a.senderId || a.tag || a.language || a.dateFrom || a.dateTo)
})

function clearFilters(): void {
  void router.push({ query: {} })
}

function goToPage(page: number): void {
  void router.push({ query: buildQuery(page) })
}

/** Rebuild the URL query from the applied state; omit empties and page 1. */
function buildQuery(page: number): LocationQueryRaw {
  const a = applied.value
  const query: LocationQueryRaw = {}
  if (a.q) query.q = a.q
  if (a.kind) query.kind = a.kind
  if (a.senderId) query.sender_id = a.senderId
  if (a.tag) query.tag = a.tag
  if (a.language) query.language = a.language
  if (a.dateFrom) query.date_from = a.dateFrom
  if (a.dateTo) query.date_to = a.dateTo
  if (page > 1) query.page = String(page)
  return query
}

// --- Active-filter summary ---------------------------------------------------

// Resolve kind/sender/tag values to display names via the shared taxonomy
// cache (also used by the search modal); raw slug/id until loaded or on
// fetch failure. Loaded lazily — only when such a filter is active.
const { kinds, senders, tags, ensureLoaded } = useTaxonomyOptions()

watch(
  () => Boolean(applied.value.kind || applied.value.senderId || applied.value.tag),
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
  if (a.tag) parts.push(`tag ${tags.value.find((t) => t.slug === a.tag)?.name ?? a.tag}`)
  if (a.language) parts.push(`language ${languageName(a.language as DocumentLanguage)}`)
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
      tag: state.tag ? [state.tag] : undefined,
      language: (state.language || undefined) as DocumentLanguage | undefined,
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
  <GovNotificationBanner v-if="flashMessage" variant="success" data-testid="flash-banner">
    <p class="govuk-notification-banner__heading">{{ flashMessage }}</p>
  </GovNotificationBanner>

  <h1 class="govuk-heading-xl">Documents</h1>

  <div v-if="loadError" class="govuk-inset-text" data-testid="load-error">
    {{ loadError }}
  </div>

  <template v-else-if="!loading">
    <p v-if="isFiltered" class="govuk-body-s app-filter-summary" data-testid="filter-summary">
      Filtered by {{ filterSummary.join(', ') }} ·
      <a
        href="#"
        class="govuk-link app-standalone-link"
        data-testid="clear-filters"
        @click.prevent="clearFilters"
        >Clear filters</a
      >
    </p>

    <p v-if="items.length" class="govuk-body app-doc-grid__count" data-testid="result-count">
      {{ total }} {{ total === 1 ? 'document' : 'documents' }}
    </p>

    <div v-if="!items.length && !isFiltered" class="govuk-inset-text" data-testid="empty-library">
      There are no documents in your library yet.
      <RouterLink class="govuk-link" :to="{ name: 'upload' }">
        Upload your first document</RouterLink
      >.
    </div>
    <div v-else-if="!items.length" class="govuk-inset-text" data-testid="empty-results">
      No documents match your search. Try different words, check the filters, or
      <a href="#" class="govuk-link" @click.prevent="clearFilters">clear the filters</a>.
    </div>

    <ul v-if="items.length" class="govuk-list app-doc-grid">
      <li v-for="item in items" :key="item.id" class="app-doc-card">
        <div class="app-doc-card__thumbnail">
          <img
            v-if="item.has_thumbnail && !brokenThumbnails.has(item.id)"
            :src="thumbnailUrl(item.id)"
            alt=""
            loading="lazy"
            @error="brokenThumbnails.add(item.id)"
          />
          <span v-else class="app-doc-card__thumbnail-fallback" aria-hidden="true">
            {{ fileTypeLabel(item) }}
          </span>
        </div>
        <div class="app-doc-card__body">
          <h2 class="govuk-heading-s app-doc-card__title">
            <!--
              The tile's ONE anchor: stretched over the whole card via a CSS
              ::after overlay (app-doc-card__title in main.scss), so the full
              tile is clickable without nested/duplicate links; focusing it
              outlines the whole tile (GOV.UK yellow/black, :focus-within).
            -->
            <RouterLink
              class="govuk-link"
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
          <p class="govuk-body-s app-doc-card__meta">
            <GovTag v-if="shows('kind') && item.kind" colour="blue">{{ item.kind.name }}</GovTag>
            <GovTag v-if="shows('language') && item.language !== 'unknown'" colour="grey">
              {{ languageName(item.language) }}
            </GovTag>
            <template v-if="shows('status')">
              <GovTag v-if="item.status === 'failed'" colour="red">Failed</GovTag>
              <GovTag v-else-if="item.status !== 'indexed'" colour="yellow">Processing</GovTag>
            </template>
            <GovTag v-if="shows('file_type')" colour="grey">{{ fileTypeLabel(item) }}</GovTag>
            <span v-if="shows('sender') && item.sender" class="app-doc-card__sender">
              {{ item.sender.name }}
            </span>
            <span v-if="shows('date') && item.document_date" class="app-doc-card__date">
              {{ formatDate(item.document_date) }}
            </span>
            <span v-if="shows('amount') && amountLabels.get(item.id)" class="app-doc-card__amount">
              {{ amountLabels.get(item.id) }}
            </span>
          </p>
          <p
            v-if="shows('tags') && item.tags.length"
            class="govuk-body-s app-doc-card__tags"
            data-testid="doc-tags"
          >
            <GovTag v-for="tag in item.tags.slice(0, MAX_TAGS)" :key="tag.slug" colour="grey">
              {{ tag.name }}
            </GovTag>
            <span v-if="item.tags.length > MAX_TAGS" class="app-doc-card__tags-more">
              +{{ item.tags.length - MAX_TAGS }}
            </span>
          </p>
          <!-- eslint-disable-next-line vue/no-v-html -- renderSnippet escapes everything except the ts_headline <b> markers (docs/api.md §1.3.3) -->
          <p
            v-if="applied.q && item.snippet"
            class="govuk-body-s app-doc-card__snippet"
            v-html="renderSnippet(item.snippet)"
          ></p>
        </div>
      </li>
    </ul>

    <GovPagination :page="applied.page" :total-pages="totalPages" @change="goToPage" />
  </template>
</template>
