<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { useNotificationsStore, type ToastVariant } from '@/stores/notifications'

// Fixed, top-right toast stack. Lives once in DefaultLayout; reads the global
// notifications queue. Errors carry role="alert"/aria-live="assertive" so they
// are announced immediately; info/success are polite.
const notifications = useNotificationsStore()
const { toasts } = storeToRefs(notifications)

const accentClass: Record<ToastVariant, string> = {
  info: 'border-sky-500',
  success: 'border-green-500',
  error: 'border-red-500',
}
</script>

<template>
  <div
    id="toast-container"
    class="fixed top-20 right-4 z-50 flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)] pointer-events-none"
  >
    <TransitionGroup
      enter-active-class="transition ease-out duration-200"
      enter-from-class="opacity-0 translate-x-4"
      enter-to-class="opacity-100 translate-x-0"
      leave-active-class="transition ease-in duration-150 absolute"
      leave-from-class="opacity-100 translate-x-0"
      leave-to-class="opacity-0 translate-x-4"
    >
      <div
        v-for="toast in toasts"
        :key="toast.id"
        :data-testid="`toast-${toast.id}`"
        :data-variant="toast.variant"
        :role="toast.variant === 'error' ? 'alert' : 'status'"
        :aria-live="toast.variant === 'error' ? 'assertive' : 'polite'"
        class="pointer-events-auto bg-white dark:bg-gray-800 border-l-4 rounded-lg px-4 py-3 shadow-lg flex items-start gap-3"
        :class="accentClass[toast.variant]"
      >
        <!-- Status icon -->
        <span class="shrink-0 mt-0.5" aria-hidden="true">
          <svg
            v-if="toast.variant === 'success'"
            class="w-5 h-5 text-green-500 fill-current"
            viewBox="0 0 20 20"
          >
            <path
              d="M10 0a10 10 0 100 20 10 10 0 000-20Zm5 7.6-5.7 5.7a1 1 0 01-1.4 0L5 10.4 6.4 9l2.2 2.2L13.6 6 15 7.6Z"
            />
          </svg>
          <svg
            v-else-if="toast.variant === 'error'"
            class="w-5 h-5 text-red-500 fill-current"
            viewBox="0 0 20 20"
          >
            <path
              d="M10 0a10 10 0 100 20 10 10 0 000-20Zm1 15H9v-2h2v2Zm0-4H9V5h2v6Z"
            />
          </svg>
          <svg v-else class="w-5 h-5 text-sky-500 fill-current" viewBox="0 0 20 20">
            <path
              d="M10 0a10 10 0 100 20 10 10 0 000-20Zm1 5H9v2h2V5Zm0 4H9v6h2V9Z"
            />
          </svg>
        </span>

        <div class="grow min-w-0">
          <p class="font-semibold text-sm text-gray-800 dark:text-gray-100">{{ toast.title }}</p>
          <p v-if="toast.message" class="text-sm text-gray-600 dark:text-gray-300 break-words">
            {{ toast.message }}
          </p>
          <RouterLink
            v-if="toast.to"
            :to="toast.to"
            class="inline-block mt-1 text-sm font-medium text-violet-500 hover:text-violet-600 dark:hover:text-violet-400"
            @click="notifications.dismiss(toast.id)"
          >
            View
          </RouterLink>
        </div>

        <button
          type="button"
          class="shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
          :data-testid="`toast-dismiss-${toast.id}`"
          @click="notifications.dismiss(toast.id)"
        >
          <span class="sr-only">Dismiss</span>
          <svg class="w-4 h-4 fill-current" viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M12.7 4.7 11.3 3.3 8 6.6 4.7 3.3 3.3 4.7 6.6 8l-3.3 3.3 1.4 1.4L8 9.4l3.3 3.3 1.4-1.4L9.4 8z"
            />
          </svg>
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>
