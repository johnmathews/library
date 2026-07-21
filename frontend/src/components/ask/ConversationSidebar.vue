<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { listThreads, deleteThread, renameThread, type ThreadSummary } from '@/api/ask'
import { AppButton } from '@/components/app'
import ThreadActionsMenu from './ThreadActionsMenu.vue'

// `newDisabled` greys out "New conversation" when the view is already an empty
// new conversation (no thread, no turns): starting a new one is redundant there,
// so the button is inert rather than a dead affordance that does nothing.
const props = defineProps<{ activeThreadId: number | null; newDisabled?: boolean }>()
const emit = defineEmits<{ select: [number]; new: []; 'threads-changed': [number] }>()

const threads = ref<ThreadSummary[]>([])
const query = ref('')
// Which thread is awaiting delete confirmation (inline two-step delete). Null
// when no row is in the confirming state.
const confirmingId = ref<number | null>(null)
// Which thread is being renamed inline, and the draft title. Null when no row
// is in the editing state.
const editingId = ref<number | null>(null)
const editTitle = ref('')

// Client-side title filter over the loaded threads (the list is small; no need
// for a server round-trip per keystroke).
const filteredThreads = computed<ThreadSummary[]>(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return threads.value
  return threads.value.filter((t) => (t.title ?? '').toLowerCase().includes(q))
})

async function refresh(): Promise<void> {
  confirmingId.value = null
  threads.value = await listThreads()
  // Let the parent (AskView) distinguish "no conversations exist" from
  // "conversations exist but none is selected" in its empty state.
  emit('threads-changed', threads.value.length)
}

// Inline two-step delete: the ⋯ menu's Delete arms the confirm/cancel
// affordance, the confirm click actually deletes. Prevents a single misclick
// from destroying a conversation with no undo.
function requestDelete(thread: ThreadSummary): void {
  editingId.value = null
  confirmingId.value = thread.id
}

function cancelDelete(): void {
  confirmingId.value = null
}

async function confirmDelete(thread: ThreadSummary): Promise<void> {
  confirmingId.value = null
  await deleteThread(thread.id)
  await refresh()
  if (thread.id === props.activeThreadId) {
    emit('new')
  }
}

// Inline rename: arm an edit input seeded with the current title, focused and
// pre-selected so the user can immediately overwrite it. Enter saves, Esc
// cancels (wired in the template).
function startRename(thread: ThreadSummary): void {
  confirmingId.value = null
  editingId.value = thread.id
  editTitle.value = thread.title
  void nextTick(() => {
    const input = document.querySelector<HTMLInputElement>('[data-testid="thread-rename-input"]')
    input?.focus()
    input?.select()
  })
}

function cancelRename(): void {
  editingId.value = null
  editTitle.value = ''
}

async function saveRename(thread: ThreadSummary): Promise<void> {
  const title = editTitle.value.trim()
  // A blank or unchanged title is a no-op — just close the editor rather than
  // sending a doomed request (the server 422s on blank).
  if (!title || title === thread.title) {
    cancelRename()
    return
  }
  await renameThread(thread.id, title)
  cancelRename()
  await refresh()
}

function formatDate(iso: string): string {
  if (!iso) return ''
  return iso.slice(0, 10)
}

onMounted(refresh)

defineExpose({ refresh })
</script>

<template>
  <!-- Conversation rail / list. On mobile it is the full-screen list screen
       (fills its parent and scrolls); at lg+ it is a fixed-width docked rail
       beside the thread. It owns no card chrome of its own — just an internal
       right divider on desktop. -->
  <aside
    class="flex flex-col min-h-0 flex-1 lg:flex-none lg:w-72 lg:shrink-0 lg:border-r lg:border-gray-200 dark:lg:border-gray-700/60"
    data-testid="conversation-sidebar"
  >
    <div class="flex flex-col gap-2 p-3 border-b border-gray-200 dark:border-gray-700/60">
      <!-- On mobile the list screen's top bar owns "New" (a ＋ icon), so the
           full-width button is desktop-only here to avoid two "new" affordances. -->
      <AppButton
        variant="primary"
        size="sm"
        type="button"
        data-testid="new-conversation"
        class="w-full max-lg:hidden"
        :disabled="newDisabled"
        @click="!newDisabled && emit('new')"
      >
        <svg class="w-4 h-4 shrink-0 fill-current opacity-80 mr-1" viewBox="0 0 16 16">
          <path d="M15 7H9V1c0-.6-.4-1-1-1S7 .4 7 1v6H1c-.6 0-1 .4-1 1s.4 1 1 1h6v6c0 .6.4 1 1 1s1-.4 1-1V9h6c.6 0 1-.4 1-1s-.4-1-1-1z" />
        </svg>
        New conversation
      </AppButton>

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
    </div>

    <ul class="flex-1 min-h-0 overflow-y-auto thin-scrollbar p-2 space-y-1">
      <li
        v-for="thread in filteredThreads"
        :key="thread.id"
        data-testid="thread-item"
        class="flex items-center justify-between gap-2 px-3 py-2.5 rounded-lg ring-1 cursor-pointer transition"
        :class="
          thread.id === activeThreadId
            ? 'bg-violet-50 dark:bg-violet-900/20 ring-violet-500'
            : 'ring-transparent hover:bg-gray-50 dark:hover:bg-gray-700/40'
        "
        @click="emit('select', thread.id)"
      >
        <span class="min-w-0 flex-1">
          <!-- Inline rename: an editable title input replaces the label while
               this row is being renamed. Clicks inside it must not select the
               row; Enter saves, Esc cancels. -->
          <input
            v-if="editingId === thread.id"
            v-model="editTitle"
            data-testid="thread-rename-input"
            type="text"
            maxlength="120"
            aria-label="Conversation title"
            class="form-input w-full text-sm"
            @click.stop
            @keydown.enter.prevent.stop="saveRename(thread)"
            @keydown.esc.prevent.stop="cancelRename"
          />
          <template v-else>
            <span
              class="block truncate text-sm"
              :class="
                thread.id === activeThreadId
                  ? 'text-violet-700 dark:text-violet-300 font-medium'
                  : 'text-gray-800 dark:text-gray-100'
              "
              >{{ thread.title }}</span
            >
            <span class="block text-xs text-gray-500 dark:text-gray-400">{{
              formatDate(thread.updated_at)
            }}</span>
          </template>
        </span>
        <div class="shrink-0 flex items-center gap-2">
          <template v-if="editingId === thread.id">
            <button
              data-testid="thread-rename-save"
              type="button"
              class="text-xs font-medium text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 transition"
              @click.stop="saveRename(thread)"
            >
              Save
            </button>
            <button
              data-testid="thread-rename-cancel"
              type="button"
              class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
              @click.stop="cancelRename"
            >
              Cancel
            </button>
          </template>
          <template v-else-if="confirmingId === thread.id">
            <button
              data-testid="thread-delete-confirm"
              type="button"
              class="text-xs font-medium text-red-500 hover:text-red-600 dark:hover:text-red-400 transition"
              @click.stop="confirmDelete(thread)"
            >
              Confirm
            </button>
            <button
              data-testid="thread-delete-cancel"
              type="button"
              class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
              @click.stop="cancelDelete"
            >
              Cancel
            </button>
          </template>
          <ThreadActionsMenu
            v-else
            testid="thread-actions-menu"
            :label="`Actions for ${thread.title}`"
            @rename="startRename(thread)"
            @delete="requestDelete(thread)"
          />
        </div>
      </li>

      <li
        v-if="!threads.length"
        class="px-4 py-6 text-center text-xs text-gray-500 dark:text-gray-400"
      >
        No conversations yet.
      </li>
    </ul>
  </aside>
</template>
