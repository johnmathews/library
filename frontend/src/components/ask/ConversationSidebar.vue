<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { listThreads, deleteThread, type ThreadSummary } from '@/api/ask'

const props = defineProps<{ activeThreadId: number | null }>()
const emit = defineEmits<{ select: [number]; new: [] }>()

const threads = ref<ThreadSummary[]>([])

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
  <aside class="w-64 shrink-0 flex flex-col gap-2">
    <button
      data-testid="new-conversation"
      type="button"
      class="w-full text-left px-4 py-2 rounded-lg bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium transition"
      @click="emit('new')"
    >
      New conversation
    </button>

    <ul
      class="divide-y divide-gray-200 dark:divide-gray-700/60 border border-gray-200 dark:border-gray-700/60 rounded-lg overflow-hidden"
    >
      <li
        v-for="thread in threads"
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
