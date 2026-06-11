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
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'
import GovButton from '@/components/govuk/GovButton.vue'
import GovDateInput from '@/components/govuk/GovDateInput.vue'
import GovInput from '@/components/govuk/GovInput.vue'
import GovSelect from '@/components/govuk/GovSelect.vue'
import type { SelectItem } from '@/components/govuk'
import { DOCUMENT_LANGUAGES } from '@/api/documents'
import { useTaxonomyOptions } from '@/composables/taxonomyOptions'

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

function queryString(key: string): string {
  const value = route.query[key]
  return typeof value === 'string' ? value : ''
}

/** Open the dialog, pre-filled from the current route query. */
function open(): void {
  draft.q = queryString('q')
  draft.kind = queryString('kind')
  draft.senderId = queryString('sender_id')
  draft.tag = queryString('tag')
  draft.language = queryString('language')
  draft.dateFrom = queryString('date_from') || null
  draft.dateTo = queryString('date_to') || null

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
  const query: LocationQueryRaw = {}
  if (draft.q.trim()) query.q = draft.q.trim()
  if (draft.kind) query.kind = draft.kind
  if (draft.senderId) query.sender_id = draft.senderId
  if (draft.tag) query.tag = draft.tag
  if (draft.language) query.language = draft.language
  if (draft.dateFrom) query.date_from = draft.dateFrom
  if (draft.dateTo) query.date_to = draft.dateTo
  void router.push({ name: 'documents', query })
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
    class="app-modal"
    aria-labelledby="search-modal-title"
    data-testid="search-modal"
    @close="onClose"
  >
    <h2 id="search-modal-title" class="govuk-heading-m">Search your documents</h2>

    <form novalidate role="search" @submit.prevent="onSubmit">
      <GovInput
        id="search"
        v-model="draft.q"
        label="Search"
        hint="For example, rekening or “energie contract”"
        type="search"
        inputmode="search"
        :spellcheck="false"
      />

      <GovSelect id="filter-kind" v-model="draft.kind" label="Kind" :items="kindItems" />
      <GovSelect id="filter-sender" v-model="draft.senderId" label="Sender" :items="senderItems" />
      <GovSelect id="filter-tag" v-model="draft.tag" label="Tag" :items="tagItems" />
      <GovSelect
        id="filter-language"
        v-model="draft.language"
        label="Language"
        :items="languageItems"
      />
      <GovDateInput id="filter-date-from" v-model="draft.dateFrom" legend="Dated from" />
      <GovDateInput id="filter-date-to" v-model="draft.dateTo" legend="Dated to" />

      <div class="govuk-button-group">
        <GovButton type="submit">Search</GovButton>
        <GovButton variant="secondary" type="button" data-testid="modal-clear" @click="clearFields">
          Clear
        </GovButton>
        <button
          type="button"
          class="govuk-link app-link-button"
          data-testid="modal-cancel"
          @click="close"
        >
          Cancel
        </button>
      </div>

      <p class="govuk-hint app-modal__hint">Tip: press / anywhere to open this search.</p>
    </form>
  </dialog>
</template>
