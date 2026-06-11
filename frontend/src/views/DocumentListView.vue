<script setup lang="ts">
/**
 * Documents list: search, filters, pagination (route `/`).
 *
 * All applied state lives in the URL query (?q=…&kind=…&page=…) so back/
 * forward and refresh keep the view; the form holds a draft that is only
 * applied on submit. Snippets are rendered through `renderSnippet` — see
 * docs/api.md §1.3.3 for why they must never hit v-html unescaped.
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQuery, type LocationQueryRaw } from 'vue-router'
import GovButton from '@/components/govuk/GovButton.vue'
import GovDateInput from '@/components/govuk/GovDateInput.vue'
import GovDetails from '@/components/govuk/GovDetails.vue'
import GovInput from '@/components/govuk/GovInput.vue'
import GovNotificationBanner from '@/components/govuk/GovNotificationBanner.vue'
import GovPagination from '@/components/govuk/GovPagination.vue'
import GovSelect from '@/components/govuk/GovSelect.vue'
import GovTag from '@/components/govuk/GovTag.vue'
import type { SelectItem } from '@/components/govuk'
import {
  DOCUMENT_LANGUAGES,
  listDocuments,
  thumbnailUrl,
  type DocumentFilters,
  type DocumentLanguage,
  type DocumentListItem,
} from '@/api/documents'
import {
  listKinds,
  listSenders,
  listTags,
  type KindOption,
  type SenderOption,
  type TagOption,
} from '@/api/taxonomy'
import { renderSnippet } from '@/utils/snippet'
import { useFlashStore } from '@/stores/flash'

const PAGE_SIZE = 25

const route = useRoute()
const router = useRouter()

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

const hasFilterApplied = computed(() => {
  const a = applied.value
  return Boolean(a.kind || a.senderId || a.tag || a.language || a.dateFrom || a.dateTo)
})

// --- Draft form state ------------------------------------------------------

const draft = reactive({
  q: '',
  kind: '',
  senderId: '',
  tag: '',
  language: '',
  dateFrom: null as string | null,
  dateTo: null as string | null,
})

// Filter options come from the taxonomy endpoints (docs/api.md §1.8.2);
// fetched once, best-effort (without them the selects just offer "All …").
const kinds = ref<KindOption[]>([])
const senders = ref<SenderOption[]>([])
const tags = ref<TagOption[]>([])

onMounted(async () => {
  const [kindResult, senderResult, tagResult] = await Promise.allSettled([
    listKinds(),
    listSenders(),
    listTags(),
  ])
  if (kindResult.status === 'fulfilled') kinds.value = kindResult.value
  if (senderResult.status === 'fulfilled') senders.value = senderResult.value
  if (tagResult.status === 'fulfilled') tags.value = tagResult.value
})

const kindItems = computed<SelectItem[]>(() => [
  { value: '', text: 'All kinds' },
  ...kinds.value.map((kind) => ({ value: kind.slug, text: kind.name })),
])
const senderItems = computed<SelectItem[]>(() => [
  { value: '', text: 'All senders' },
  ...senders.value.map((sender) => ({ value: String(sender.id), text: sender.name })),
])
const tagItems = computed<SelectItem[]>(() => [
  { value: '', text: 'All tags' },
  ...tags.value.map((tag) => ({ value: tag.slug, text: tag.name })),
])
const languageItems: SelectItem[] = [
  { value: '', text: 'All languages' },
  ...DOCUMENT_LANGUAGES.map((language) => ({ value: language.value, text: language.text })),
]

function applyFilters(): void {
  void router.push({ query: buildQuery({ page: 1 }) })
}

function clearFilters(): void {
  draft.q = ''
  draft.kind = ''
  draft.senderId = ''
  draft.tag = ''
  draft.language = ''
  draft.dateFrom = null
  draft.dateTo = null
  void router.push({ query: {} })
}

function goToPage(page: number): void {
  void router.push({ query: buildQuery({ page }) })
}

/** Build the URL query from the draft form; omit empties and page 1. */
function buildQuery(overrides: { page: number }): LocationQueryRaw {
  const query: LocationQueryRaw = {}
  if (draft.q.trim()) query.q = draft.q.trim()
  if (draft.kind) query.kind = draft.kind
  if (draft.senderId) query.sender_id = draft.senderId
  if (draft.tag) query.tag = draft.tag
  if (draft.language) query.language = draft.language
  if (draft.dateFrom) query.date_from = draft.dateFrom
  if (draft.dateTo) query.date_to = draft.dateTo
  if (overrides.page > 1) query.page = String(overrides.page)
  return query
}

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
    // Re-sync the draft so back/forward/refresh restore the form.
    draft.q = state.q
    draft.kind = state.kind
    draft.senderId = state.senderId
    draft.tag = state.tag
    draft.language = state.language
    draft.dateFrom = state.dateFrom || null
    draft.dateTo = state.dateTo || null

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
</script>

<template>
  <GovNotificationBanner v-if="flashMessage" variant="success" data-testid="flash-banner">
    <p class="govuk-notification-banner__heading">{{ flashMessage }}</p>
  </GovNotificationBanner>

  <h1 class="govuk-heading-xl">Documents</h1>

  <div class="govuk-grid-row">
    <div class="govuk-grid-column-one-third">
      <form novalidate role="search" @submit.prevent="applyFilters">
        <GovInput
          id="search"
          v-model="draft.q"
          label="Search your documents"
          hint="For example, rekening or “energie contract”"
          type="search"
          inputmode="search"
          :spellcheck="false"
        />
        <GovButton type="submit">Search</GovButton>

        <GovDetails summary="Filter results" :open="hasFilterApplied">
          <GovSelect id="filter-kind" v-model="draft.kind" label="Kind" :items="kindItems" />
          <GovSelect
            id="filter-sender"
            v-model="draft.senderId"
            label="Sender"
            :items="senderItems"
          />
          <GovSelect id="filter-tag" v-model="draft.tag" label="Tag" :items="tagItems" />
          <GovSelect
            id="filter-language"
            v-model="draft.language"
            label="Language"
            :items="languageItems"
          />
          <GovDateInput id="filter-date-from" v-model="draft.dateFrom" legend="Dated from" />
          <GovDateInput id="filter-date-to" v-model="draft.dateTo" legend="Dated to" />
          <GovButton type="submit">Apply filters</GovButton>
          <p class="govuk-body">
            <a href="#" class="govuk-link app-standalone-link" @click.prevent="clearFilters"
              >Clear filters</a
            >
          </p>
        </GovDetails>
      </form>
    </div>

    <div class="govuk-grid-column-two-thirds">
      <div v-if="loadError" class="govuk-inset-text" data-testid="load-error">
        {{ loadError }}
      </div>

      <template v-else-if="!loading">
        <p v-if="items.length" class="govuk-body app-doc-list__count" data-testid="result-count">
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

        <ul v-if="items.length" class="govuk-list app-doc-list">
          <li v-for="item in items" :key="item.id" class="app-doc-list__item">
            <div class="app-doc-list__thumbnail">
              <img
                v-if="item.has_thumbnail && !brokenThumbnails.has(item.id)"
                :src="thumbnailUrl(item.id)"
                alt=""
                loading="lazy"
                width="60"
                @error="brokenThumbnails.add(item.id)"
              />
              <span v-else class="app-doc-list__thumbnail-fallback" aria-hidden="true">
                {{ fileTypeLabel(item) }}
              </span>
            </div>
            <div class="app-doc-list__body">
              <h2 class="govuk-heading-s app-doc-list__title">
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
              <p class="govuk-body-s app-doc-list__meta">
                <GovTag v-if="item.kind" colour="blue">{{ item.kind.name }}</GovTag>
                <GovTag v-if="item.language !== 'unknown'" colour="grey">
                  {{ languageName(item.language) }}
                </GovTag>
                <GovTag v-if="item.status === 'failed'" colour="red">Failed</GovTag>
                <GovTag v-else-if="item.status !== 'indexed'" colour="yellow">Processing</GovTag>
                <span v-if="item.sender" class="app-doc-list__sender">{{ item.sender.name }}</span>
                <span v-if="item.document_date" class="app-doc-list__date">
                  {{ formatDate(item.document_date) }}
                </span>
              </p>
              <!-- eslint-disable-next-line vue/no-v-html -- renderSnippet escapes everything except the ts_headline <b> markers (docs/api.md §1.3.3) -->
              <p
                v-if="applied.q && item.snippet"
                class="govuk-body-s app-doc-list__snippet"
                v-html="renderSnippet(item.snippet)"
              ></p>
            </div>
          </li>
        </ul>

        <GovPagination :page="applied.page" :total-pages="totalPages" @change="goToPage" />
      </template>
    </div>
  </div>
</template>
