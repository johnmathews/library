<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { listThreads, deleteThread, type ThreadSummary } from '@/api/ask'

const props = defineProps<{ activeThreadId: number | null }>()
const emit = defineEmits<{ select: [number]; new: [] }>()

const threads = ref<ThreadSummary[]>([])
const query = ref('')

// Client-side title filter over the loaded threads (the list is small; no need
// for a server round-trip per keystroke).
const filteredThreads = computed<ThreadSummary[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return threads.value
  return threads.value.filter((t) => (t.title ?? '').toLowerCase().includes(q))
})

async function refresh(): Promise<void> {
  threads.value = await listThreads()
}

async function onDelete(thread: ThreadSummary): Promise<void> {
  await deleteThread(thread.id)
  await refresh()
  if (thread.id === props.activeThreadId) {
    emit('new')
  }
}

function formatDate(iso: string): string {
  if (!iso) return ''
  return iso.slice(0, 10)
}

onMounted(refresh)

defineExpose({ refresh })
</script>

<template>
  <aside
    class="w-64 shrink-0 flex flex-col gap-2 max-lg:sticky max-lg:top-4 max-lg:self-start max-lg:max-h-[calc(100vh-2rem)] lg:min-h-0"
    data-testid="conversation-sidebar"
  >
    <button
      data-testid="new-conversation"
      type="button"
      class="w-full text-left px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium transition"
      @click="emit('new')"
    >
      New conversation
    </button>

    <input
      v-model="query"
      type="search"
      data-testid="thread-search"
      placeholder="Search conversations…"
      class="form-input w-full text-sm"
      aria-label="Search conversations"
    />

    <p
      v-if="threads.length && !filteredThreads.length"
      data-testid="thread-search-empty"
      class="px-1 text-xs text-gray-500 dark:text-gray-400"
    >
      No conversations match “{{ query }}”.
    </p>

    <ul
      class="lg:flex-1 lg:min-h-0 divide-y divide-gray-200 dark:divide-gray-700/60 border border-gray-200 dark:border-gray-700/60 rounded-lg overflow-y-auto"
    >
      <li
        v-for="thread in filteredThreads"
        :key="thread.id"
        data-testid="thread-item"
        class="flex items-center justify-between gap-2 px-4 py-3 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/40 transition"
        :class="{
          'bg-violet-50 dark:bg-violet-900/20': thread.id === activeThreadId,
        }"
        @click="emit('select', thread.id)"
      >
        <span class="min-w-0 flex-1">
          <span class="block truncate text-sm text-gray-800 dark:text-gray-100">{{
            thread.title
          }}</span>
          <span class="block text-xs text-gray-500 dark:text-gray-400">{{
            formatDate(thread.updated_at)
          }}</span>
        </span>
        <button
          data-testid="thread-delete"
          type="button"
          class="shrink-0 text-xs text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition"
          @click.stop="onDelete(thread)"
        >
          Delete
        </button>
      </li>
    </ul>
  </aside>
</template>
