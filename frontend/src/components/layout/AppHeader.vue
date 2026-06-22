<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import ThemeToggle from './ThemeToggle.vue'
import { useAuthStore } from '@/stores/auth'
import { useJobsStore } from '@/stores/jobs'

defineProps<{
  sidebarOpen: boolean
}>()

defineEmits<{
  'toggle-sidebar': []
  'open-search': []
}>()

const authStore = useAuthStore()
const jobsStore = useJobsStore()
const router = useRouter()
const userMenuOpen = ref(false)
const jobsMenuOpen = ref(false)

const displayName = computed(
  () => authStore.user?.display_name || authStore.user?.username || 'User',
)

const STAGE_LABELS: Record<string, string> = {
  received: 'Received',
  ocr: 'OCR',
  extract: 'Extracting',
  markdown: 'Markdown',
  embed: 'Embedding',
}

function stageLabel(status: string): string {
  return STAGE_LABELS[status] ?? status
}

async function handleSignOut() {
  userMenuOpen.value = false
  await authStore.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header
    id="app-header"
    class="sticky top-0 before:absolute before:inset-0 before:backdrop-blur-md max-lg:before:bg-white/90 dark:max-lg:before:bg-gray-800/90 before:-z-10 z-30 max-lg:shadow-xs lg:before:bg-gray-100/90 dark:lg:before:bg-gray-900/90"
  >
    <div class="px-4 sm:px-6 lg:px-8">
      <div
        class="flex items-center justify-between h-16 lg:border-b border-gray-200 dark:border-gray-700/60"
      >
        <!-- Left: hamburger (mobile) -->
        <div class="flex">
          <button
            id="header-sidebar-toggle"
            class="text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 lg:hidden"
            aria-controls="sidebar"
            :aria-expanded="sidebarOpen"
            @click.stop="$emit('toggle-sidebar')"
          >
            <span class="sr-only">Open sidebar</span>
            <svg
              class="w-6 h-6 fill-current"
              viewBox="0 0 24 24"
              xmlns="http://www.w3.org/2000/svg"
            >
              <rect x="4" y="5" width="16" height="2" />
              <rect x="4" y="11" width="16" height="2" />
              <rect x="4" y="17" width="16" height="2" />
            </svg>
          </button>
        </div>

        <!-- Right: jobs indicator + search + theme toggle + user menu -->
        <div class="flex items-center space-x-3">
          <!-- Background-jobs indicator: only present while work is in flight -->
          <div
            v-if="jobsStore.activeCount > 0"
            id="header-jobs-indicator"
            class="relative inline-flex"
          >
            <button
              id="header-jobs-button"
              data-testid="header-jobs-button"
              class="relative w-8 h-8 flex items-center justify-center bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700/50 rounded-full"
              aria-haspopup="true"
              :aria-expanded="jobsMenuOpen"
              :aria-label="`${jobsStore.activeCount} background job(s) running`"
              @click.stop="jobsMenuOpen = !jobsMenuOpen"
            >
              <span class="sr-only">Background jobs running</span>
              <svg
                class="w-5 h-5 animate-spin text-violet-500"
                viewBox="0 0 24 24"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
                aria-hidden="true"
              >
                <circle
                  class="opacity-25"
                  cx="12"
                  cy="12"
                  r="9"
                  stroke="currentColor"
                  stroke-width="3"
                />
                <path
                  class="opacity-90"
                  fill="currentColor"
                  d="M12 3a9 9 0 0 1 9 9h-3a6 6 0 0 0-6-6V3Z"
                />
              </svg>
              <span
                data-testid="header-jobs-count"
                aria-live="polite"
                class="absolute -top-1 -right-1 min-w-4 h-4 px-1 flex items-center justify-center text-[10px] font-semibold text-white bg-violet-500 rounded-full"
                >{{ jobsStore.activeCount }}</span
              >
            </button>
            <Transition
              enter-active-class="transition ease-out duration-200"
              enter-from-class="opacity-0 -translate-y-1"
              enter-to-class="opacity-100 translate-y-0"
              leave-active-class="transition ease-out duration-200"
              leave-from-class="opacity-100 translate-y-0"
              leave-to-class="opacity-0 -translate-y-1"
            >
              <div
                v-show="jobsMenuOpen"
                id="header-jobs-dropdown"
                class="origin-top-right z-10 absolute top-full min-w-64 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700/60 py-1.5 rounded-lg shadow-lg overflow-hidden mt-1 right-0"
                @focusin="jobsMenuOpen = true"
                @focusout="jobsMenuOpen = false"
              >
                <div
                  class="pt-0.5 pb-2 px-3 mb-1 border-b border-gray-200 dark:border-gray-700/60 text-xs font-semibold uppercase text-gray-400 dark:text-gray-500"
                >
                  Processing
                </div>
                <ul class="max-h-64 overflow-y-auto">
                  <li v-for="doc in jobsStore.activeList" :key="doc.id" data-testid="header-jobs-item">
                    <RouterLink
                      class="flex items-center justify-between gap-3 text-sm py-1 px-3 hover:bg-gray-50 dark:hover:bg-gray-700/30"
                      :to="`/documents/${doc.id}`"
                      @click="jobsMenuOpen = false"
                    >
                      <span class="truncate text-gray-700 dark:text-gray-200">{{
                        doc.title || `Document #${doc.id}`
                      }}</span>
                      <span class="shrink-0 text-xs text-gray-400 dark:text-gray-500">{{
                        stageLabel(doc.status)
                      }}</span>
                    </RouterLink>
                  </li>
                </ul>
                <div class="border-t border-gray-200 dark:border-gray-700/60 mt-1 pt-1">
                  <RouterLink
                    data-testid="header-jobs-viewall"
                    class="font-medium text-sm text-violet-500 hover:text-violet-600 dark:hover:text-violet-400 flex items-center py-1 px-3"
                    to="/jobs"
                    @click="jobsMenuOpen = false"
                  >
                    View all jobs
                  </RouterLink>
                </div>
              </div>
            </Transition>
          </div>

          <!-- Search trigger -->
          <button
            id="header-search-button"
            class="w-8 h-8 flex items-center justify-center bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700/50 rounded-full"
            data-testid="header-search-button"
            @click="$emit('open-search')"
          >
            <span class="sr-only">Search</span>
            <svg
              class="fill-current text-gray-500/80 dark:text-gray-400/80"
              width="16"
              height="16"
              viewBox="0 0 16 16"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M7 14c-3.86 0-7-3.14-7-7s3.14-7 7-7 7 3.14 7 7-3.14 7-7 7ZM7 2C4.243 2 2 4.243 2 7s2.243 5 5 5 5-2.243 5-5-2.243-5-5-5Z"
              />
              <path
                d="M15.707 14.293 13.314 11.9a8.019 8.019 0 0 1-1.414 1.414l2.393 2.393a.997.997 0 0 0 1.414 0 .999.999 0 0 0 0-1.414Z"
              />
            </svg>
          </button>

          <ThemeToggle />

          <!-- User menu -->
          <div id="header-user-menu" class="relative inline-flex">
            <button
              id="header-user-menu-button"
              class="inline-flex items-center justify-center group"
              aria-haspopup="true"
              :aria-expanded="userMenuOpen"
              @click.stop="userMenuOpen = !userMenuOpen"
            >
              <div class="flex items-center truncate">
                <span
                  class="truncate ml-2 text-sm font-medium text-gray-600 dark:text-gray-100 group-hover:text-gray-800 dark:group-hover:text-white"
                  >{{ displayName }}</span
                >
                <svg
                  class="w-3 h-3 shrink-0 ml-1 fill-current text-gray-600 dark:text-gray-300"
                  viewBox="0 0 12 12"
                >
                  <path d="M5.9 11.4L.5 6l1.4-1.4 4 4 4-4L11.3 6z" />
                </svg>
              </div>
            </button>
            <Transition
              enter-active-class="transition ease-out duration-200"
              enter-from-class="opacity-0 -translate-y-1"
              enter-to-class="opacity-100 translate-y-0"
              leave-active-class="transition ease-out duration-200"
              leave-from-class="opacity-100 translate-y-0"
              leave-to-class="opacity-0 -translate-y-1"
            >
              <div
                v-show="userMenuOpen"
                id="header-user-menu-dropdown"
                class="origin-top-right z-10 absolute top-full min-w-44 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700/60 py-1.5 rounded-lg shadow-lg overflow-hidden mt-1 right-0"
                @focusin="userMenuOpen = true"
                @focusout="userMenuOpen = false"
              >
                <div
                  class="pt-0.5 pb-2 px-3 mb-1 border-b border-gray-200 dark:border-gray-700/60"
                >
                  <div class="font-medium text-gray-800 dark:text-gray-100">
                    {{ displayName }}
                  </div>
                  <div class="text-xs text-gray-600 dark:text-gray-300 italic">
                    {{ authStore.user?.username }}
                  </div>
                </div>
                <ul>
                  <li>
                    <RouterLink
                      class="font-medium text-sm text-violet-500 hover:text-violet-600 dark:hover:text-violet-400 flex items-center py-1 px-3"
                      to="/settings"
                      @click="userMenuOpen = false"
                    >
                      Settings
                    </RouterLink>
                  </li>
                  <li>
                    <button
                      class="font-medium text-sm text-violet-500 hover:text-violet-600 dark:hover:text-violet-400 flex items-center py-1 px-3 w-full text-left"
                      @click="handleSignOut"
                    >
                      Sign Out
                    </button>
                  </li>
                </ul>
              </div>
            </Transition>
          </div>
        </div>
      </div>
    </div>
  </header>
</template>
