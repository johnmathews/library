<script setup lang="ts">
/**
 * A trailing "⋯" overflow menu for a conversation, replacing the pair of
 * always-visible Rename/Delete text links. It owns only the open/closed state
 * of the popover and emits the chosen intent — the host decides what "rename"
 * and "delete" actually do (inline edit in a list row, inline edit in the chat
 * title bar, two-step delete confirm, …). One menu, two hosts (the sidebar row
 * and the chat title bar), so the affordance is identical across breakpoints.
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    /** Accessible label for the trigger; distinguishes multiple menus on a page. */
    label?: string
    /** Forwarded onto the trigger button so tests/hosts can target it. */
    testid?: string
  }>(),
  { label: 'Conversation actions', testid: undefined },
)

const emit = defineEmits<{ rename: []; delete: [] }>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)

function toggle(): void {
  open.value = !open.value
}

function close(): void {
  open.value = false
}

function choose(action: 'rename' | 'delete'): void {
  close()
  if (action === 'rename') emit('rename')
  else emit('delete')
}

// Close on any click outside the menu, and on Escape. Registered in the CAPTURE
// phase so it runs before another menu's trigger button, whose `@click.stop`
// would otherwise stop the event from ever reaching a bubble-phase document
// listener — leaving this menu open while the other one opens too.
function onDocPointer(event: MouseEvent): void {
  const target = event.target as Node | null
  if (!open.value || !root.value || !target) return
  if (!root.value.contains(target)) close()
}
function onKeydown(event: KeyboardEvent): void {
  if (open.value && event.key === 'Escape') close()
}

onMounted(() => {
  document.addEventListener('click', onDocPointer, true)
  document.addEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocPointer, true)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div ref="root" class="relative shrink-0">
    <button
      type="button"
      :data-testid="props.testid"
      :aria-label="props.label"
      :aria-expanded="open"
      aria-haspopup="menu"
      class="flex h-8 w-8 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700/60 dark:hover:text-gray-200 transition"
      @click.stop="toggle"
    >
      <svg class="h-4.5 w-4.5 fill-current" viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="3" cy="8" r="1.5" />
        <circle cx="8" cy="8" r="1.5" />
        <circle cx="13" cy="8" r="1.5" />
      </svg>
    </button>

    <div
      v-if="open"
      role="menu"
      class="absolute right-0 top-9 z-20 min-w-[8rem] rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-1 shadow-lg"
      @click.stop
    >
      <button
        type="button"
        role="menuitem"
        data-testid="thread-rename"
        class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-700/60 transition"
        @click.stop="choose('rename')"
      >
        <svg class="h-3.5 w-3.5 fill-none stroke-current" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 20h16M4 20l1-4 9-9 3 3-9 9-4 1z" />
        </svg>
        Rename
      </button>
      <button
        type="button"
        role="menuitem"
        data-testid="thread-delete"
        class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-red-500 hover:bg-red-50 dark:hover:bg-red-500/10 transition"
        @click.stop="choose('delete')"
      >
        <svg class="h-3.5 w-3.5 fill-none stroke-current" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 7h14M9 7V5h6v2M7 7l1 12h8l1-12" />
        </svg>
        Delete
      </button>
    </div>
  </div>
</template>
