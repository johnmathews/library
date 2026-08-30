<script setup lang="ts">
/**
 * Senders tab: chart split colour for a sender, and nothing else.
 *
 * A sender's name is derived from ingested documents, not owner-entered, and
 * renaming, merging or deleting one is an admin taxonomy operation with its
 * own panel elsewhere (and its own merge/reassignment semantics that do not
 * apply to a colour). This panel offers only the swatch.
 *
 * `GET /api/senders` is unpaginated and returns every sender ever ingested,
 * so the list is sorted busiest-first (the senders a chart would actually
 * split by) and a name filter narrows it — see charts-view design §2.5 for
 * why a sender's split slot derives from its id.
 *
 * Loads lazily exactly as `AdminMetadataPanel`/`FacetsPanel` do:
 * `watch(() => props.active, ..., { immediate: true })` with a `loaded` flag,
 * fetching on the first moment `active` is true.
 */
import { computed, ref, watch } from 'vue'
import { listSenders, setSenderColour, type SenderOption } from '@/api/taxonomy'
import { ApiError } from '@/api/client'
import SplitColourPicker from '@/components/vocabulary/SplitColourPicker.vue'

defineOptions({ name: 'SendersPanel' })
const props = defineProps<{ active: boolean }>()

const senders = ref<SenderOption[]>([])
const loading = ref(false)
const loaded = ref(false)
const loadError = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    senders.value = await listSenders()
    loaded.value = true
  } catch {
    loadError.value = 'Could not load the senders. Try refreshing the page.'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.active,
  (isActive) => {
    if (!isActive) return
    if (!loaded.value && !loading.value) void load()
  },
  { immediate: true },
)

const filter = ref('')

const sortedSenders = computed(() =>
  [...senders.value].sort((a, b) => b.document_count - a.document_count || a.name.localeCompare(b.name)),
)

const filteredSenders = computed(() => {
  const needle = filter.value.trim().toLowerCase()
  if (!needle) return sortedSenders.value
  return sortedSenders.value.filter((sender) => sender.name.toLowerCase().includes(needle))
})

const pendingIds = ref<Set<number>>(new Set())
const rowError = ref<Record<number, string>>({})

function setPending(id: number, pending: boolean): void {
  const next = new Set(pendingIds.value)
  if (pending) next.add(id)
  else next.delete(id)
  pendingIds.value = next
}

function isPending(id: number): boolean {
  return pendingIds.value.has(id)
}

function setRowError(id: number, message: string | null): void {
  const next = { ...rowError.value }
  if (message) next[id] = message
  else delete next[id]
  rowError.value = next
}

async function onColourChange(id: number, colour: string | null): Promise<void> {
  setPending(id, true)
  setRowError(id, null)
  try {
    const updated = await setSenderColour(id, colour)
    const index = senders.value.findIndex((sender) => sender.id === id)
    if (index !== -1) senders.value[index] = updated
  } catch (err) {
    setRowError(id, err instanceof ApiError ? err.detail : 'Could not set the colour. Try again.')
  } finally {
    setPending(id, false)
  }
}
</script>

<template>
  <div class="space-y-4">
    <div class="card p-6 @container">
      <div class="mb-4 flex flex-wrap items-end gap-3">
        <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">Senders</h2>
        <div class="ml-auto">
          <label for="sender-filter" class="filter-label">Filter by name</label>
          <input
            id="sender-filter"
            v-model="filter"
            type="text"
            autocomplete="off"
            class="form-input"
            data-testid="sender-filter"
          />
        </div>
      </div>

      <p v-if="loading" class="text-sm text-gray-500 dark:text-gray-400" data-testid="senders-loading">
        Loading the senders…
      </p>
      <div
        v-else-if="loadError"
        role="alert"
        class="border-l-4 border-red-500 bg-red-50 dark:bg-red-500/10 rounded-lg px-3 py-2 text-sm text-red-700 dark:text-red-300"
        data-testid="senders-error"
      >
        {{ loadError }}
      </div>
      <p
        v-else-if="senders.length === 0"
        class="text-sm text-gray-500 dark:text-gray-400"
        data-testid="senders-empty"
      >
        No senders yet. Senders appear here once a document names one.
      </p>
      <p
        v-else-if="filteredSenders.length === 0"
        class="text-sm text-gray-500 dark:text-gray-400"
        data-testid="senders-filter-empty"
      >
        No senders match that filter.
      </p>

      <ul v-else class="divide-y divide-gray-100 dark:divide-gray-700/60">
        <li
          v-for="sender in filteredSenders"
          :key="sender.id"
          class="py-3"
          :class="{ 'opacity-60': isPending(sender.id) }"
          :data-testid="`sender-row-${sender.id}`"
        >
          <div class="flex flex-col gap-2 @md:flex-row @md:items-center @md:justify-between">
            <div class="min-w-0">
              <span class="font-medium text-gray-800 dark:text-gray-100">{{ sender.name }}</span>
            </div>
            <div
              class="shrink-0 text-sm text-gray-600 dark:text-gray-300"
              :data-testid="`sender-${sender.id}-count`"
            >
              {{ sender.document_count }} documents
            </div>
          </div>

          <div class="mt-2">
            <SplitColourPicker
              :model-value="sender.colour"
              :slot-key="String(sender.id)"
              :testid="`sender-${sender.id}-colour`"
              @update:model-value="(colour) => onColourChange(sender.id, colour)"
            />
          </div>

          <p
            v-if="rowError[sender.id]"
            role="alert"
            class="mt-1 text-xs text-red-600 dark:text-red-400"
            :data-testid="`sender-${sender.id}-error`"
          >
            {{ rowError[sender.id] }}
          </p>
        </li>
      </ul>
    </div>
  </div>
</template>
