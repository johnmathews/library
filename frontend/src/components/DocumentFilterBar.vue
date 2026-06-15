<script setup lang="ts">
/**
 * Dashboard filter bar (route `/`): a debounced search input, a row of filter
 * pills (Kind, Sender, Date, Tag multi-select, and a "More" pill for Language +
 * Status), and removable active-filter chips.
 *
 * Controlled by the URL: the parent passes the parsed `applied` state in, and
 * this component emits the next query out — `apply(query, { replace })` for the
 * parent to push (discrete filter change) or replace (debounced typing), and
 * `clear()` to drop every filter. Changing any filter resets to page 1, so the
 * emitted query never carries a page. Taxonomy names come from the shared cache.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { LocationQueryRaw } from 'vue-router'
import { AppCheckboxes, AppDateInput, FilterPill } from '@/components/app'
import type { ChoiceItem } from '@/components/app'
import {
  DOCUMENT_LANGUAGES,
  DOCUMENT_STATUSES,
  type DocumentLanguage,
  type DocumentStatus,
} from '@/api/documents'
import { useTaxonomyOptions } from '@/composables/taxonomyOptions'
import { buildDocumentQuery, type AppliedFilters } from '@/utils/documentQuery'

const SEARCH_DEBOUNCE_MS = 300

const props = defineProps<{ applied: AppliedFilters }>()
const emit = defineEmits<{
  apply: [LocationQueryRaw, { replace?: boolean }?]
  clear: []
}>()

const { kinds, senders, tags, ensureLoaded } = useTaxonomyOptions()
void ensureLoaded()

// Which pill popover is open (only one at a time); null = all closed.
const openPill = ref<string | null>(null)
function pillOpen(name: string): boolean {
  return openPill.value === name
}
function setPillOpen(name: string, open: boolean): void {
  openPill.value = open ? name : openPill.value === name ? null : openPill.value
}

// --- Search input (debounced) ---------------------------------------------

const searchText = ref(props.applied.q)
let debounceTimer: ReturnType<typeof setTimeout> | null = null

// Keep the field in sync if the query changes elsewhere (modal, chip removal).
watch(
  () => props.applied.q,
  (q) => {
    if (q !== searchText.value) searchText.value = q
  },
)

function emitWith(overrides: Partial<AppliedFilters>, replace = false): void {
  const next: AppliedFilters = { ...props.applied, ...overrides }
  emit('apply', buildDocumentQuery(next, 1), { replace })
}

function onSearchInput(): void {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => emitWith({ q: searchText.value.trim() }, true), SEARCH_DEBOUNCE_MS)
}

function onSearchEnter(): void {
  if (debounceTimer) clearTimeout(debounceTimer)
  emitWith({ q: searchText.value.trim() })
}

function clearSearch(): void {
  searchText.value = ''
  if (debounceTimer) clearTimeout(debounceTimer)
  emitWith({ q: '' })
}

onBeforeUnmount(() => {
  if (debounceTimer) clearTimeout(debounceTimer)
})

// --- Discrete filter changes ----------------------------------------------

function selectKind(slug: string): void {
  emitWith({ kind: slug })
  openPill.value = null
}
function selectSender(id: string): void {
  emitWith({ senderId: id })
  openPill.value = null
}
function selectLanguage(value: string): void {
  emitWith({ language: value as DocumentLanguage })
}
function selectStatus(value: string): void {
  emitWith({ status: value as DocumentStatus })
}

// Tag multi-select via AppCheckboxes. We keep a local ref so sequential
// checkbox changes accumulate correctly before the parent can update `applied`
// (important for tests and rapid interactions). Sync from parent when changed
// externally (e.g. chip removal).
const localTags = ref<string[]>([...props.applied.tags])
watch(
  () => props.applied.tags,
  (next) => {
    localTags.value = [...next]
  },
)
const tagModel = computed<string[]>({
  get: () => localTags.value,
  set: (next) => {
    localTags.value = next
    emitWith({ tags: next })
  },
})
const tagItems = computed<ChoiceItem[]>(() =>
  tags.value.map((t) => ({ value: t.slug, text: t.name })),
)

// Date range via two AppDateInputs.
const dateFromModel = computed<string | null>({
  get: () => props.applied.dateFrom || null,
  set: (value) => emitWith({ dateFrom: value ?? '' }),
})
const dateToModel = computed<string | null>({
  get: () => props.applied.dateTo || null,
  set: (value) => emitWith({ dateTo: value ?? '' }),
})

// --- Pill value labels -----------------------------------------------------

const kindLabel = computed(
  () => kinds.value.find((k) => k.slug === props.applied.kind)?.name ?? props.applied.kind,
)
const senderLabel = computed(
  () =>
    senders.value.find((s) => String(s.id) === props.applied.senderId)?.name ??
    props.applied.senderId,
)
const tagPillLabel = computed(() => {
  const n = props.applied.tags.length
  if (!n) return ''
  const first = tags.value.find((t) => t.slug === props.applied.tags[0])?.name ?? props.applied.tags[0]
  return n > 1 ? `${first} +${n - 1}` : first
})
const dateActive = computed(() => Boolean(props.applied.dateFrom || props.applied.dateTo))
const moreActive = computed(() => Boolean(props.applied.language || props.applied.status))
const languageName = (value: string): string =>
  DOCUMENT_LANGUAGES.find((l) => l.value === value)?.text ?? value
const statusName = (value: string): string =>
  DOCUMENT_STATUSES.find((s) => s.value === value)?.text ?? value

// --- Active-filter chips ---------------------------------------------------

interface Chip {
  key: string
  label: string
  remove: () => void
}

const chips = computed<Chip[]>(() => {
  const a = props.applied
  const out: Chip[] = []
  if (a.q) out.push({ key: 'q', label: `"${a.q}"`, remove: () => clearSearch() })
  if (a.kind)
    out.push({ key: 'kind', label: `Kind: ${kindLabel.value}`, remove: () => emitWith({ kind: '' }) })
  if (a.senderId)
    out.push({
      key: 'sender',
      label: `Sender: ${senderLabel.value}`,
      remove: () => emitWith({ senderId: '' }),
    })
  for (const slug of a.tags) {
    const name = tags.value.find((t) => t.slug === slug)?.name ?? slug
    out.push({
      key: `tag-${slug}`,
      label: `Tag: ${name}`,
      remove: () => emitWith({ tags: a.tags.filter((s) => s !== slug) }),
    })
  }
  if (a.language)
    out.push({
      key: 'language',
      label: `Language: ${languageName(a.language)}`,
      remove: () => emitWith({ language: '' as DocumentLanguage }),
    })
  if (a.status)
    out.push({
      key: 'status',
      label: `Status: ${statusName(a.status)}`,
      remove: () => emitWith({ status: '' as DocumentStatus }),
    })
  if (a.dateFrom)
    out.push({
      key: 'date-from',
      label: `From ${a.dateFrom}`,
      remove: () => emitWith({ dateFrom: '' }),
    })
  if (a.dateTo)
    out.push({ key: 'date-to', label: `To ${a.dateTo}`, remove: () => emitWith({ dateTo: '' }) })
  return out
})

const languageOptions = DOCUMENT_LANGUAGES
const statusOptions = DOCUMENT_STATUSES
</script>

<template>
  <div class="mb-5" data-testid="document-filter-bar">
    <!-- Search -->
    <div class="relative mb-3 max-w-xl">
      <svg
        class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
        viewBox="0 0 16 16"
        fill="currentColor"
        aria-hidden="true"
      >
        <path
          d="M7 14c-3.86 0-7-3.14-7-7s3.14-7 7-7 7 3.14 7 7-3.14 7-7 7ZM7 2C4.243 2 2 4.243 2 7s2.243 5 5 5 5-2.243 5-5-2.243-5-5-5Z"
        />
        <path
          d="M15.707 14.293 13.314 11.9a8.019 8.019 0 0 1-1.414 1.414l2.393 2.393a.997.997 0 0 0 1.414 0 .999.999 0 0 0 0-1.414Z"
        />
      </svg>
      <input
        v-model="searchText"
        data-testid="filter-search"
        type="search"
        inputmode="search"
        :spellcheck="false"
        placeholder="Search documents…"
        aria-label="Search documents"
        class="form-input w-full rounded-lg pl-9 pr-9"
        @input="onSearchInput"
        @keydown.enter.prevent="onSearchEnter"
      />
      <button
        v-if="searchText"
        type="button"
        data-testid="filter-search-clear"
        class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
        aria-label="Clear search"
        @click="clearSearch"
      >
        ✕
      </button>
    </div>

    <!-- Pills -->
    <div class="flex flex-wrap items-center gap-2">
      <FilterPill
        data-testid="pill-kind"
        label="Kind"
        :active="Boolean(applied.kind)"
        :value-label="kindLabel"
        :open="pillOpen('kind')"
        @update:open="setPillOpen('kind', $event)"
      >
        <ul class="max-h-64 overflow-auto text-sm">
          <li v-for="k in kinds" :key="k.slug">
            <button
              type="button"
              :data-testid="`kind-option-${k.slug}`"
              class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
              :class="{ 'font-semibold text-violet-600 dark:text-violet-300': applied.kind === k.slug }"
              @click="selectKind(k.slug)"
            >
              {{ k.name }}
            </button>
          </li>
        </ul>
      </FilterPill>

      <FilterPill
        data-testid="pill-sender"
        label="Sender"
        :active="Boolean(applied.senderId)"
        :value-label="senderLabel"
        :open="pillOpen('sender')"
        @update:open="setPillOpen('sender', $event)"
      >
        <ul class="max-h-64 overflow-auto text-sm">
          <li v-for="s in senders" :key="s.id">
            <button
              type="button"
              :data-testid="`sender-option-${s.id}`"
              class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
              :class="{ 'font-semibold text-violet-600 dark:text-violet-300': applied.senderId === String(s.id) }"
              @click="selectSender(String(s.id))"
            >
              {{ s.name }}
            </button>
          </li>
        </ul>
      </FilterPill>

      <FilterPill
        data-testid="pill-date"
        label="Date"
        :active="dateActive"
        :open="pillOpen('date')"
        @update:open="setPillOpen('date', $event)"
      >
        <div class="space-y-3">
          <AppDateInput id="filter-date-from" v-model="dateFromModel" legend="Dated from" />
          <AppDateInput id="filter-date-to" v-model="dateToModel" legend="Dated to" />
        </div>
      </FilterPill>

      <FilterPill
        data-testid="pill-tag"
        label="Tag"
        :active="Boolean(applied.tags.length)"
        :value-label="tagPillLabel"
        :open="pillOpen('tag')"
        @update:open="setPillOpen('tag', $event)"
      >
        <AppCheckboxes
          id="filter-tags"
          legend="Tags"
          legend-size="s"
          :items="tagItems"
          v-model="tagModel"
        />
      </FilterPill>

      <FilterPill
        data-testid="pill-more"
        label="More"
        :active="moreActive"
        :open="pillOpen('more')"
        @update:open="setPillOpen('more', $event)"
      >
        <div class="space-y-3 text-sm">
          <div>
            <p class="mb-1 font-semibold">Language</p>
            <ul>
              <li>
                <button
                  type="button"
                  data-testid="language-option-"
                  class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
                  @click="selectLanguage('')"
                >
                  Any language
                </button>
              </li>
              <li v-for="l in languageOptions" :key="l.value">
                <button
                  type="button"
                  :data-testid="`language-option-${l.value}`"
                  class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
                  :class="{ 'font-semibold text-violet-600 dark:text-violet-300': applied.language === l.value }"
                  @click="selectLanguage(l.value)"
                >
                  {{ l.text }}
                </button>
              </li>
            </ul>
          </div>
          <div>
            <p class="mb-1 font-semibold">Status</p>
            <ul>
              <li>
                <button
                  type="button"
                  data-testid="status-option-"
                  class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
                  @click="selectStatus('')"
                >
                  Any status
                </button>
              </li>
              <li v-for="s in statusOptions" :key="s.value">
                <button
                  type="button"
                  :data-testid="`status-option-${s.value}`"
                  class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
                  :class="{ 'font-semibold text-violet-600 dark:text-violet-300': applied.status === s.value }"
                  @click="selectStatus(s.value)"
                >
                  {{ s.text }}
                </button>
              </li>
            </ul>
          </div>
        </div>
      </FilterPill>
    </div>

    <!-- Active-filter chips -->
    <div v-if="chips.length" class="mt-3 flex flex-wrap items-center gap-2" data-testid="filter-chips">
      <span
        v-for="chip in chips"
        :key="chip.key"
        :data-testid="`chip-${chip.key}`"
        class="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2.5 py-1 text-xs text-violet-700 dark:bg-violet-500/20 dark:text-violet-200"
      >
        {{ chip.label }}
        <button
          type="button"
          :data-testid="`chip-remove-${chip.key}`"
          class="text-violet-500 hover:text-violet-800 dark:hover:text-violet-100"
          :aria-label="`Remove filter ${chip.label}`"
          @click="chip.remove()"
        >
          ✕
        </button>
      </span>
      <button
        type="button"
        data-testid="filter-clear-all"
        class="text-xs text-gray-500 underline hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
        @click="emit('clear')"
      >
        Clear all
      </button>
    </div>
  </div>
</template>
