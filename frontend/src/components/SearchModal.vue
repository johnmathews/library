<script setup lang="ts">
/**
 * Search modal — query + filters in a dialog opened from the service
 * navigation (docs/frontend.md §1.2.7). GOV.UK has no modal component;
 * this app extension is built on the native `<dialog>` element via
 * `showModal()`, which supplies focus containment, ESC-to-close and
 * `::backdrop` for free, so no ARIA wiring is needed beyond the
 * accessible name (`aria-labelledby` on the dialog).
 *
 * Submit pushes the query to the documents route — the list view is
 * URL-synced and refetches — and closes. Opening pre-fills the draft
 * from the current route query so an active search can be edited.
 * Focus returns to the element that opened the modal on close.
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppButton, AppDateInput, AppInput, AppSelect } from '@/components/app'
import type { SelectItem } from '@/components/app'
import { DOCUMENT_LANGUAGES } from '@/api/documents'
import { useTaxonomyOptions } from '@/composables/taxonomyOptions'
import { parseDocumentQuery, buildDocumentQuery, type AppliedFilters } from '@/utils/documentQuery'

const route = useRoute()
const router = useRouter()

const dialog = ref<HTMLDialogElement | null>(null)

// The element that opened the modal; focus is handed back to it on close.
// Native dialogs restore focus in most browsers, but jsdom and some older
// engines do not — doing it explicitly keeps the contract deterministic.
let opener: HTMLElement | null = null

// Taxonomy options are fetched lazily on first open and cached app-wide.
const { kinds, senders, tags, ensureLoaded } = useTaxonomyOptions()

const draft = reactive({
  q: '',
  kind: '',
  senderId: '',
  tag: '',
  language: '',
  dateFrom: null as string | null,
  dateTo: null as string | null,
})

// Tracks what tags were present when the modal opened and what value the tag
// field was pre-filled with. Used by onSubmit to detect whether the user
// changed the tag field (so we know whether to keep the full multi-tag set or
// use the user's new single-tag choice).
let initialTags: string[] = []
let prefilledTag = ''

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

/** Open the dialog, pre-filled from the current route query. */
function open(): void {
  const applied: AppliedFilters = parseDocumentQuery(route.query)

  // Remember the original tags so onSubmit can preserve them when the user
  // hasn't touched the tag field.
  initialTags = applied.tags
  // Pre-fill the single-tag select only when there is exactly one tag; with
  // zero or multiple tags the field is left blank to avoid misrepresenting the
  // active filter set.
  prefilledTag = applied.tags.length === 1 ? (applied.tags[0] ?? '') : ''

  draft.q = applied.q
  draft.kind = applied.kind
  draft.senderId = applied.senderId
  draft.tag = prefilledTag
  draft.language = applied.language
  draft.dateFrom = applied.dateFrom || null
  draft.dateTo = applied.dateTo || null

  // Lazy taxonomy fetch on first open; the cached refs feed the computeds.
  void ensureLoaded()

  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  dialog.value?.showModal()
}

function close(): void {
  dialog.value?.close()
}

/** Native `close` event: fires for ESC, Cancel and submit alike. */
function onClose(): void {
  opener?.focus()
  opener = null
}

function onSubmit(): void {
  const current: AppliedFilters = parseDocumentQuery(route.query)

  // Determine the resulting tags without data loss:
  // - If the user did NOT change the tag field (it still matches the pre-filled
  //   value), keep initialTags unchanged (preserves multi-tag sets).
  // - If the user changed the field, use their explicit choice.
  const resolvedTags: string[] =
    draft.tag === prefilledTag ? initialTags : draft.tag ? [draft.tag] : []

  const next: AppliedFilters = {
    q: draft.q.trim(),
    kind: draft.kind,
    senderId: draft.senderId,
    tags: resolvedTags,
    language: draft.language,
    status: current.status, // preserved — modal doesn't manage it
    dateFrom: draft.dateFrom ?? '',
    dateTo: draft.dateTo ?? '',
    page: 1, // a new search resets paging
  }

  void router.push({ name: 'documents', query: buildDocumentQuery(next) })
  close()
}

function clearFields(): void {
  draft.q = ''
  draft.kind = ''
  draft.senderId = ''
  draft.tag = ''
  draft.language = ''
  draft.dateFrom = null
  draft.dateTo = null
}

// Keyboard nicety: `/` anywhere (outside form fields) opens the search.
function onKeydown(event: KeyboardEvent): void {
  if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return
  if (dialog.value?.open) return
  const target = event.target
  if (
    target instanceof HTMLElement &&
    (target.isContentEditable || /^(input|textarea|select)$/i.test(target.tagName))
  ) {
    return
  }
  event.preventDefault()
  open()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))

defineExpose({ open })
</script>

<template>
  <dialog
    ref="dialog"
    id="search-modal"
    class="app-search-modal bg-white dark:bg-gray-800 shadow-lg p-0 backdrop:bg-gray-900/30"
    aria-labelledby="search-modal-title"
    data-testid="search-modal"
    @close="onClose"
  >
    <div class="p-5">
      <h2
        id="search-modal-title"
        class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-4"
      >
        Search your documents
      </h2>

      <form id="search-form" novalidate role="search" class="space-y-4" @submit.prevent="onSubmit">
        <div class="relative">
          <svg
            class="absolute left-3 top-9 w-4 h-4 text-gray-400 pointer-events-none"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            stroke-width="1.5"
            stroke="currentColor"
            aria-hidden="true"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
            />
          </svg>
          <AppInput
            id="search"
            v-model="draft.q"
            label="Search"
            hint="For example, rekening or “energie contract”"
            type="search"
            inputmode="search"
            :spellcheck="false"
            class="[&_input]:pl-9"
          />
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <AppSelect id="filter-kind" v-model="draft.kind" label="Kind" :items="kindItems" />
          <AppSelect
            id="filter-sender"
            v-model="draft.senderId"
            label="Sender"
            :items="senderItems"
          />
          <AppSelect id="filter-tag" v-model="draft.tag" label="Tag" :items="tagItems" />
          <AppSelect
            id="filter-language"
            v-model="draft.language"
            label="Language"
            :items="languageItems"
          />
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <AppDateInput id="filter-date-from" v-model="draft.dateFrom" legend="Dated from" />
          <AppDateInput id="filter-date-to" v-model="draft.dateTo" legend="Dated to" />
        </div>

        <div class="flex items-center gap-3 pt-2">
          <AppButton type="submit">Search</AppButton>
          <AppButton
            variant="secondary"
            type="button"
            data-testid="modal-clear"
            @click="clearFields"
          >
            Clear
          </AppButton>
          <button
            type="button"
            class="text-sm text-gray-500 dark:text-gray-400 underline ml-auto"
            data-testid="modal-cancel"
            @click="close"
          >
            Cancel
          </button>
        </div>

        <p class="text-sm text-gray-500 dark:text-gray-400">
          Tip: press / anywhere to open this search.
        </p>
      </form>
    </div>
  </dialog>
</template>
