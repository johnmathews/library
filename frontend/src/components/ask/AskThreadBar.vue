<script setup lang="ts">
/**
 * The slim bar at the top of Ask — the only chrome between the app header and
 * the conversation.
 *
 * Left: the conversation's title with a ▾. Clicking it opens the switcher —
 * a search box and every conversation, newest first — which is how you move
 * between conversations: there is no persistent list beside the transcript,
 * so the reading column gets the width. Right: the one-click actions — search
 * conversations (opens the same switcher with the search focused), the
 * conversation's ⋯ menu (rename, delete), the document-view toggle at lg+, and
 * New conversation.
 *
 * Rename edits the title in place; Delete arms a Confirm / Cancel pair first,
 * because a conversation cannot be restored.
 */
import { computed, nextTick, ref } from 'vue'
import { listThreads, type ThreadSummary } from '@/api/ask'
import AppPopover from '@/components/app/AppPopover.vue'
import ThreadActionsMenu from './ThreadActionsMenu.vue'
import type { AskViewMode } from '@/composables/useAskViewMode'

const props = defineProps<{
  title: string
  threadId: number | null
  /** False when this is already an empty new conversation. */
  canStartNew: boolean
  isLargeScreen: boolean
  modes: { value: AskViewMode; label: string }[]
}>()
const emit = defineEmits<{
  select: [id: number]
  new: []
  rename: [title: string]
  delete: []
}>()
const viewMode = defineModel<AskViewMode>('viewMode', { required: true })

// ── Switcher ────────────────────────────────────────────────────────────────
const switcherOpen = ref(false)
const threads = ref<ThreadSummary[] | null>(null)
const query = ref('')
const searchEl = ref<HTMLInputElement | null>(null)

const filtered = computed<ThreadSummary[]>(() => {
  const q = query.value.trim().toLowerCase()
  const all = threads.value ?? []
  return q ? all.filter((t) => (t.title ?? '').toLowerCase().includes(q)) : all
})

async function loadThreads(): Promise<void> {
  try {
    threads.value = await listThreads()
  } catch {
    threads.value = threads.value ?? []
  }
}

/** Open or close the switcher. Every open refetches, so a conversation
 * created or renamed elsewhere is never missing from it. */
function setSwitcher(open: boolean, focusSearch = false): void {
  switcherOpen.value = open
  if (!open) return
  query.value = ''
  void loadThreads()
  if (focusSearch) void nextTick(() => searchEl.value?.focus())
}

function choose(id: number): void {
  switcherOpen.value = false
  if (id !== props.threadId) emit('select', id)
}

function formatDate(iso: string): string {
  return iso ? iso.slice(0, 10) : ''
}

// ── Rename / delete ─────────────────────────────────────────────────────────
const renaming = ref(false)
const draft = ref('')
const confirmingDelete = ref(false)

function startRename(): void {
  confirmingDelete.value = false
  draft.value = props.title
  renaming.value = true
  void nextTick(() => {
    const el = document.getElementById('ask-title-rename-input') as HTMLInputElement | null
    el?.focus()
    el?.select()
  })
}
function cancelRename(): void {
  renaming.value = false
}
function saveRename(): void {
  const title = draft.value.trim()
  renaming.value = false
  if (title && title !== props.title) emit('rename', title)
}
function confirmDelete(): void {
  confirmingDelete.value = false
  emit('delete')
}

const iconButton =
  'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-gray-500 transition hover:bg-gray-100 hover:text-gray-700 disabled:pointer-events-none disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-700/60 dark:hover:text-gray-200'

defineExpose({ refresh: loadThreads })
</script>

<template>
  <div
    data-testid="ask-thread-bar"
    class="flex min-h-[3.25rem] shrink-0 items-center gap-1 px-2 sm:px-4 lg:px-6"
  >
    <!-- Rename in place -->
    <template v-if="renaming">
      <input
        id="ask-title-rename-input"
        v-model="draft"
        data-testid="ask-title-rename-input"
        type="text"
        maxlength="120"
        aria-label="Conversation title"
        class="form-input min-w-0 flex-1 text-sm"
        @keydown.enter.prevent="saveRename"
        @keydown.esc.prevent="cancelRename"
      />
      <button
        type="button"
        data-testid="ask-title-rename-save"
        class="px-2 text-sm font-medium text-violet-600 hover:text-violet-700 dark:text-violet-400"
        @click="saveRename"
      >
        Save
      </button>
      <button
        type="button"
        class="px-2 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
        @click="cancelRename"
      >
        Cancel
      </button>
    </template>

    <template v-else>
      <!-- Title + switcher -->
      <AppPopover
        :open="switcherOpen"
        align="left"
        class="min-w-0"
        :panel-attrs="{ 'data-testid': 'ask-switcher', role: 'dialog', 'aria-label': 'Conversations' }"
        panel-class="absolute top-full mt-1 w-80 max-w-[calc(100vw-2rem)] p-2"
        @update:open="setSwitcher($event)"
      >
        <template #trigger="{ open, triggerRef }">
          <button
            :ref="triggerRef"
            type="button"
            data-testid="ask-title"
            aria-haspopup="dialog"
            :aria-expanded="open"
            class="flex min-w-0 items-center gap-1 rounded-lg px-2 py-1.5 text-left transition hover:bg-gray-100 dark:hover:bg-gray-700/60"
            @click.stop="setSwitcher(!open)"
          >
            <span class="truncate text-sm font-medium text-gray-800 dark:text-gray-100">{{
              title || 'New conversation'
            }}</span>
            <svg class="h-4 w-4 shrink-0 fill-none stroke-current text-gray-400" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
        </template>

        <input
          ref="searchEl"
          v-model="query"
          type="search"
          data-testid="thread-search"
          placeholder="Search conversations…"
          aria-label="Search conversations"
          class="form-input mb-2 w-full text-sm"
        />
        <ul class="thin-scrollbar max-h-80 space-y-0.5 overflow-y-auto" data-testid="thread-list">
          <li v-for="thread in filtered" :key="thread.id">
            <button
              type="button"
              data-testid="thread-item"
              class="flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left transition"
              :class="
                thread.id === threadId
                  ? 'bg-violet-50 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300'
                  : 'text-gray-800 hover:bg-gray-100 dark:text-gray-100 dark:hover:bg-gray-700/60'
              "
              :aria-current="thread.id === threadId ? 'true' : undefined"
              @click="choose(thread.id)"
            >
              <span class="min-w-0 truncate text-sm">{{ thread.title }}</span>
              <span class="shrink-0 text-xs text-gray-400 dark:text-gray-500">{{
                formatDate(thread.updated_at)
              }}</span>
            </button>
          </li>
        </ul>
        <p
          v-if="threads !== null && threads.length && !filtered.length"
          data-testid="thread-search-empty"
          class="px-2.5 py-2 text-xs text-gray-500 dark:text-gray-400"
        >
          No conversations match “{{ query }}”.
        </p>
        <p
          v-else-if="threads !== null && !threads.length"
          data-testid="thread-list-empty"
          class="px-2.5 py-2 text-xs text-gray-500 dark:text-gray-400"
        >
          No conversations yet.
        </p>
      </AppPopover>

      <div class="ml-auto flex shrink-0 items-center gap-0.5">
        <template v-if="threadId !== null">
          <template v-if="confirmingDelete">
            <span class="mr-1 text-xs text-gray-500 dark:text-gray-400">Delete this conversation?</span>
            <button
              type="button"
              data-testid="ask-title-delete-confirm"
              class="px-2 text-sm font-medium text-red-500 hover:text-red-600 dark:hover:text-red-400"
              @click="confirmDelete"
            >
              Delete
            </button>
            <button
              type="button"
              data-testid="ask-title-delete-cancel"
              class="px-2 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
              @click="confirmingDelete = false"
            >
              Cancel
            </button>
          </template>
          <ThreadActionsMenu
            v-else
            testid="ask-title-actions"
            label="Conversation actions"
            @rename="startRename"
            @delete="confirmingDelete = true"
          />
        </template>

        <!-- Transcript layout. `v-if`, not a CSS-hidden `lg:flex`: a hidden
             button would still sit in a phone's tab order. -->
        <div
          v-if="isLargeScreen"
          data-testid="ask-view-mode"
          role="group"
          aria-label="Transcript layout"
          class="mx-1 inline-flex shrink-0 rounded-lg border border-gray-200 bg-gray-50 p-0.5 dark:border-gray-700/60 dark:bg-gray-900/40"
        >
          <button
            v-for="m in modes"
            :key="m.value"
            type="button"
            :data-testid="`ask-view-mode-${m.value}`"
            :aria-pressed="viewMode === m.value"
            :aria-label="`${m.label} layout`"
            :title="`${m.label} layout`"
            class="inline-flex h-7 w-7 items-center justify-center rounded-md transition"
            :class="
              viewMode === m.value
                ? 'bg-white text-violet-600 shadow-xs dark:bg-gray-700 dark:text-violet-300'
                : 'text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300'
            "
            @click="viewMode = m.value"
          >
            <svg v-if="m.value === 'conversation'" class="h-4 w-4 fill-none stroke-current" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z" />
            </svg>
            <svg v-else class="h-4 w-4 fill-none stroke-current" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8Z" />
              <path d="M14 3v5h5M9 13h6M9 17h6" />
            </svg>
          </button>
        </div>

        <button
          type="button"
          data-testid="ask-search-conversations"
          aria-label="Search conversations"
          title="Search conversations"
          :class="iconButton"
          @mousedown.stop
          @click.stop="setSwitcher(true, true)"
        >
          <svg class="h-4 w-4 fill-none stroke-current" stroke-width="2" stroke-linecap="round" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
        </button>
        <button
          type="button"
          data-testid="new-conversation"
          aria-label="New conversation"
          title="New conversation"
          :class="iconButton"
          :disabled="!canStartNew"
          @click="emit('new')"
        >
          <!-- Heavier than its neighbours on purpose: a 2px plus has so little
               ink it read as disabled beside the search glyph. -->
          <svg class="h-[1.125rem] w-[1.125rem] fill-none stroke-current" stroke-width="2.5" stroke-linecap="round" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </div>
    </template>
  </div>
</template>
