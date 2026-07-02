<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const props = defineProps<{
  sidebarOpen: boolean
}>()

const emit = defineEmits<{
  'close-sidebar': []
}>()

const sidebar = ref<HTMLDivElement | null>(null)
const route = useRoute()

// Expanded-by-default behaviour:
//
// - If localStorage has an explicit value, honour it — the user's
//   stated preference always wins.
// - Otherwise fall back to a viewport check: wide displays (the
//   Tailwind `lg` breakpoint, 1024px+) start expanded, narrower
//   viewports start collapsed. The mobile hamburger state is a
//   separate ref (`sidebarOpen`), so this only affects the
//   desktop layout.
//
// Guarded for SSR / test environments where `window.matchMedia`
// may be missing — fall back to `false` rather than crashing.
//
// Persisted under the app's `library:` key convention; the bare
// `sidebar-expanded` key predates it, so read it once as a fallback to
// preserve an existing preference on first load after this change.
const SIDEBAR_EXPANDED_KEY = 'library:sidebar-expanded'
const storedSidebarExpanded =
  localStorage.getItem(SIDEBAR_EXPANDED_KEY) ?? localStorage.getItem('sidebar-expanded')

function defaultSidebarExpanded(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(min-width: 1024px)').matches
}

const sidebarExpanded = ref<boolean>(
  storedSidebarExpanded === null
    ? defaultSidebarExpanded()
    : storedSidebarExpanded === 'true',
)

// close on click outside
const clickHandler = (event: MouseEvent) => {
  const target = event.target as Node | null
  if (!sidebar.value || !target) return
  if (!props.sidebarOpen || sidebar.value.contains(target)) return
  emit('close-sidebar')
}

// auto-close the mobile overlay whenever navigation happens
watch(
  () => route.fullPath,
  () => {
    if (props.sidebarOpen) emit('close-sidebar')
  },
)

// close if ESC pressed
const keyHandler = (event: KeyboardEvent) => {
  if (!props.sidebarOpen || event.key !== 'Escape') return
  emit('close-sidebar')
}

onMounted(() => {
  document.addEventListener('click', clickHandler)
  document.addEventListener('keydown', keyHandler)
})

onUnmounted(() => {
  document.removeEventListener('click', clickHandler)
  document.removeEventListener('keydown', keyHandler)
})

watch(
  sidebarExpanded,
  () => {
    localStorage.setItem(SIDEBAR_EXPANDED_KEY, String(sidebarExpanded.value))
    const body = document.querySelector('body')
    if (!body) return
    if (sidebarExpanded.value) {
      body.classList.add('sidebar-expanded')
    } else {
      body.classList.remove('sidebar-expanded')
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="min-w-fit">
    <!-- Sidebar backdrop (mobile only) -->
    <div
      class="fixed inset-0 bg-gray-900/30 z-40 lg:hidden lg:z-auto transition-opacity duration-200"
      :class="sidebarOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'"
      aria-hidden="true"
    ></div>

    <!-- Sidebar -->
    <div
      id="sidebar"
      ref="sidebar"
      class="flex lg:flex! flex-col absolute z-40 left-0 top-0 lg:static lg:left-auto lg:top-auto lg:translate-x-0 h-[100dvh] overflow-y-scroll lg:overflow-y-auto no-scrollbar w-64 max-[400px]:w-full lg:w-20 lg:sidebar-expanded:!w-64 shrink-0 bg-white dark:bg-gray-800 p-4 transition-all duration-200 ease-in-out rounded-r-lg shadow-xs"
      :class="sidebarOpen ? 'translate-x-0' : '-translate-x-full'"
    >
      <!-- Sidebar header -->
      <div class="flex justify-between mb-10 pr-3 sm:px-2">
        <!-- Logo / title — hidden when sidebar is collapsed on desktop -->
        <RouterLink class="block" to="/" aria-label="Library home">
          <div>
            <h1
              class="text-2xl md:text-xl md:pl-2 text-gray-800 dark:text-gray-100 font-bold lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200 lg:whitespace-nowrap lg:sidebar-expanded:whitespace-normal overflow-hidden"
            >
              LIBRARY
            </h1>
          </div>
        </RouterLink>
      </div>

      <!-- Links -->
      <div class="space-y-8">
        <div>
          <ul id="sidebar-nav" class="mt-3" @click="$emit('close-sidebar')">
            <!-- Documents link (home / `/`) -->
            <RouterLink v-slot="{ href, navigate, isActive }" to="/" custom>
              <li
                class="pl-4 pr-3 py-2 rounded-lg mb-0.5 last:mb-0 bg-linear-to-r"
                :class="
                  isActive &&
                  'from-violet-500/[0.12] dark:from-violet-500/[0.24] to-violet-500/[0.04]'
                "
              >
                <a
                  :href="href"
                  class="block truncate transition"
                  data-testid="sidebar-documents-link"
                  :class="
                    isActive
                      ? 'text-gray-900 dark:text-white'
                      : 'text-gray-800 dark:text-gray-100 hover:text-gray-900 dark:hover:text-white'
                  "
                  @click="navigate"
                >
                  <div class="flex items-center">
                    <svg
                      class="shrink-0 fill-current"
                      :class="
                        isActive
                          ? 'text-violet-500'
                          : 'text-gray-600 dark:text-gray-300'
                      "
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                    >
                      <path
                        d="M1 3a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H2a1 1 0 0 1-1-1Zm0 5a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H2a1 1 0 0 1-1-1Zm1 4a1 1 0 1 0 0 2h12a1 1 0 1 0 0-2H2Z"
                      />
                    </svg>
                    <span
                      class="text-base font-medium ml-4 lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200"
                      >Documents</span
                    >
                  </div>
                </a>
              </li>
            </RouterLink>

            <!-- Upload link -->
            <RouterLink v-slot="{ href, navigate, isActive }" to="/upload" custom>
              <li
                class="pl-4 pr-3 py-2 rounded-lg mb-0.5 last:mb-0 bg-linear-to-r"
                :class="
                  isActive &&
                  'from-violet-500/[0.12] dark:from-violet-500/[0.24] to-violet-500/[0.04]'
                "
              >
                <a
                  :href="href"
                  class="block truncate transition"
                  data-testid="sidebar-upload-link"
                  :class="
                    isActive
                      ? 'text-gray-900 dark:text-white'
                      : 'text-gray-800 dark:text-gray-100 hover:text-gray-900 dark:hover:text-white'
                  "
                  @click="navigate"
                >
                  <div class="flex items-center">
                    <svg
                      class="shrink-0 fill-current"
                      :class="
                        isActive
                          ? 'text-violet-500'
                          : 'text-gray-600 dark:text-gray-300'
                      "
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                    >
                      <path
                        d="M8 0a1 1 0 0 1 .7.3l4 4-1.4 1.4L9 3.4V11H7V3.4L4.7 5.7 3.3 4.3l4-4A1 1 0 0 1 8 0ZM1 13h14v2H1z"
                      />
                    </svg>
                    <span
                      class="text-base font-medium ml-4 lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200"
                      >Upload</span
                    >
                  </div>
                </a>
              </li>
            </RouterLink>

            <!-- New note link -->
            <RouterLink v-slot="{ href, navigate, isActive }" to="/notes/new" custom>
              <li
                class="pl-4 pr-3 py-2 rounded-lg mb-0.5 last:mb-0 bg-linear-to-r"
                :class="
                  isActive &&
                  'from-violet-500/[0.12] dark:from-violet-500/[0.24] to-violet-500/[0.04]'
                "
              >
                <a
                  :href="href"
                  class="block truncate transition"
                  data-testid="sidebar-notes-link"
                  :class="
                    isActive
                      ? 'text-gray-900 dark:text-white'
                      : 'text-gray-800 dark:text-gray-100 hover:text-gray-900 dark:hover:text-white'
                  "
                  @click="navigate"
                >
                  <div class="flex items-center">
                    <svg
                      class="shrink-0 fill-current"
                      :class="
                        isActive
                          ? 'text-violet-500'
                          : 'text-gray-600 dark:text-gray-300'
                      "
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                    >
                      <path
                        d="M11.7.3a1 1 0 0 1 1.4 0l2.6 2.6a1 1 0 0 1 0 1.4l-9 9a1 1 0 0 1-.46.26l-4 1a1 1 0 0 1-1.21-1.2l1-4a1 1 0 0 1 .26-.47l9-9Zm.7 2.1L4 10.8 3.5 12.5l1.7-.4 8.4-8.4-1.2-1.3Z"
                      />
                    </svg>
                    <span
                      class="text-base font-medium ml-4 lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200"
                      >New note</span
                    >
                  </div>
                </a>
              </li>
            </RouterLink>

            <!-- Charts link -->
            <RouterLink v-slot="{ href, navigate, isActive }" to="/charts" custom>
              <li
                class="pl-4 pr-3 py-2 rounded-lg mb-0.5 last:mb-0 bg-linear-to-r"
                :class="
                  isActive &&
                  'from-violet-500/[0.12] dark:from-violet-500/[0.24] to-violet-500/[0.04]'
                "
              >
                <a
                  :href="href"
                  class="block truncate transition"
                  data-testid="sidebar-charts-link"
                  :class="
                    isActive
                      ? 'text-gray-900 dark:text-white'
                      : 'text-gray-800 dark:text-gray-100 hover:text-gray-900 dark:hover:text-white'
                  "
                  @click="navigate"
                >
                  <div class="flex items-center">
                    <svg
                      class="shrink-0 fill-current"
                      :class="isActive ? 'text-violet-500' : 'text-gray-600 dark:text-gray-300'"
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                    >
                      <path
                        d="M0 0h2v14h14v2H0V0Zm4.5 9.5 3-3 2.5 2.5 4-4.5 1.5 1.3-5.4 6.1-2.6-2.5-2.9 2.9-1.6-1.6Z"
                      />
                    </svg>
                    <span
                      class="text-base font-medium ml-4 lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200"
                      >Charts</span
                    >
                  </div>
                </a>
              </li>
            </RouterLink>

            <!-- Ask link -->
            <RouterLink v-slot="{ href, navigate, isActive }" to="/ask" custom>
              <li
                class="pl-4 pr-3 py-2 rounded-lg mb-0.5 last:mb-0 bg-linear-to-r"
                :class="
                  isActive &&
                  'from-violet-500/[0.12] dark:from-violet-500/[0.24] to-violet-500/[0.04]'
                "
              >
                <a
                  :href="href"
                  class="block truncate transition"
                  data-testid="sidebar-ask-link"
                  :class="
                    isActive
                      ? 'text-gray-900 dark:text-white'
                      : 'text-gray-800 dark:text-gray-100 hover:text-gray-900 dark:hover:text-white'
                  "
                  @click="navigate"
                >
                  <div class="flex items-center">
                    <svg
                      class="shrink-0 fill-current"
                      :class="
                        isActive
                          ? 'text-violet-500'
                          : 'text-gray-600 dark:text-gray-300'
                      "
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                    >
                      <path
                        d="M8 0a8 8 0 0 0-7 11.9L0 16l4.1-1A8 8 0 1 0 8 0Zm0 12a1 1 0 1 1 0-2 1 1 0 0 1 0 2Zm1.3-4.2c-.3.2-.3.3-.3.5v.2H7v-.3c0-.9.4-1.4 1-1.8.5-.3.7-.5.7-.9 0-.5-.4-.8-1-.8-.5 0-.9.3-1 .8l-1.8-.3C5.1 3.9 6.3 3 8 3c1.8 0 3 1 3 2.4 0 1-.6 1.6-1.7 2.3Z"
                      />
                    </svg>
                    <span
                      class="text-base font-medium ml-4 lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200"
                      >Ask</span
                    >
                  </div>
                </a>
              </li>
            </RouterLink>

            <!-- Jobs link -->
            <RouterLink v-slot="{ href, navigate, isActive }" to="/jobs" custom>
              <li
                class="pl-4 pr-3 py-2 rounded-lg mb-0.5 last:mb-0 bg-linear-to-r"
                :class="
                  isActive &&
                  'from-violet-500/[0.12] dark:from-violet-500/[0.24] to-violet-500/[0.04]'
                "
              >
                <a
                  :href="href"
                  class="block truncate transition"
                  data-testid="sidebar-jobs-link"
                  :class="
                    isActive
                      ? 'text-gray-900 dark:text-white'
                      : 'text-gray-800 dark:text-gray-100 hover:text-gray-900 dark:hover:text-white'
                  "
                  @click="navigate"
                >
                  <div class="flex items-center">
                    <svg
                      class="shrink-0 fill-current"
                      :class="
                        isActive ? 'text-violet-500' : 'text-gray-600 dark:text-gray-300'
                      "
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                    >
                      <path
                        d="M8 0a8 8 0 1 0 0 16A8 8 0 0 0 8 0Zm0 14A6 6 0 1 1 8 2a6 6 0 0 1 0 12Zm1-9H7v4l3.3 2 1-1.7L9 7.9V5Z"
                      />
                    </svg>
                    <span
                      class="text-base font-medium ml-4 lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200"
                      >Jobs</span
                    >
                  </div>
                </a>
              </li>
            </RouterLink>

            <!-- Projects link -->
            <RouterLink v-slot="{ href, navigate, isActive }" to="/projects" custom>
              <li
                class="pl-4 pr-3 py-2 rounded-lg mb-0.5 last:mb-0 bg-linear-to-r"
                :class="
                  isActive &&
                  'from-violet-500/[0.12] dark:from-violet-500/[0.24] to-violet-500/[0.04]'
                "
              >
                <a
                  :href="href"
                  class="block truncate transition"
                  data-testid="sidebar-projects-link"
                  :class="
                    isActive
                      ? 'text-gray-900 dark:text-white'
                      : 'text-gray-800 dark:text-gray-100 hover:text-gray-900 dark:hover:text-white'
                  "
                  @click="navigate"
                >
                  <div class="flex items-center">
                    <svg
                      class="shrink-0 fill-current"
                      :class="isActive ? 'text-violet-500' : 'text-gray-600 dark:text-gray-300'"
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                    >
                      <path d="M1 2a1 1 0 0 1 1-1h4l2 2h6a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1V2Z" />
                    </svg>
                    <span
                      class="text-base font-medium ml-4 lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200"
                      >Projects</span
                    >
                  </div>
                </a>
              </li>
            </RouterLink>

            <!-- Settings link -->
            <RouterLink v-slot="{ href, navigate, isActive }" to="/settings" custom>
              <li
                class="pl-4 pr-3 py-2 rounded-lg mb-0.5 last:mb-0 bg-linear-to-r"
                :class="
                  isActive &&
                  'from-violet-500/[0.12] dark:from-violet-500/[0.24] to-violet-500/[0.04]'
                "
              >
                <a
                  :href="href"
                  class="block truncate transition"
                  data-testid="sidebar-settings-link"
                  :class="
                    isActive
                      ? 'text-gray-900 dark:text-white'
                      : 'text-gray-800 dark:text-gray-100 hover:text-gray-900 dark:hover:text-white'
                  "
                  @click="navigate"
                >
                  <div class="flex items-center">
                    <svg
                      class="shrink-0 fill-current"
                      :class="isActive ? 'text-violet-500' : 'text-gray-600 dark:text-gray-300'"
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                    >
                      <path
                        d="M6.7.3a1 1 0 0 1 2.6 0l.2 1.5a5.5 5.5 0 0 1 1.5.9l1.4-.6a1 1 0 0 1 1.3.5l1.3 2.2a1 1 0 0 1-.3 1.3l-1.2.9a5.5 5.5 0 0 1 0 1.8l1.2.9a1 1 0 0 1 .3 1.3l-1.3 2.2a1 1 0 0 1-1.3.5l-1.4-.6a5.5 5.5 0 0 1-1.5.9l-.2 1.5a1 1 0 0 1-2.6 0l-.2-1.5a5.5 5.5 0 0 1-1.5-.9l-1.4.6a1 1 0 0 1-1.3-.5L1 11.4a1 1 0 0 1 .3-1.3l1.2-.9a5.5 5.5 0 0 1 0-1.8l-1.2-.9A1 1 0 0 1 1 5.2l1.3-2.2a1 1 0 0 1 1.3-.5l1.4.6a5.5 5.5 0 0 1 1.5-.9L6.7.3ZM8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z"
                      />
                    </svg>
                    <span
                      class="text-base font-medium ml-4 lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200"
                      >Settings</span
                    >
                  </div>
                </a>
              </li>
            </RouterLink>

            <!-- Admin link (admins only) -->
            <RouterLink v-if="auth.isAdmin" v-slot="{ href, navigate, isActive }" to="/admin" custom>
              <li
                class="pl-4 pr-3 py-2 rounded-lg mb-0.5 last:mb-0 bg-linear-to-r"
                :class="
                  isActive &&
                  'from-violet-500/[0.12] dark:from-violet-500/[0.24] to-violet-500/[0.04]'
                "
              >
                <a
                  :href="href"
                  class="block truncate transition"
                  data-testid="sidebar-admin-link"
                  :class="
                    isActive
                      ? 'text-gray-900 dark:text-white'
                      : 'text-gray-800 dark:text-gray-100 hover:text-gray-900 dark:hover:text-white'
                  "
                  @click="navigate"
                >
                  <div class="flex items-center">
                    <svg
                      class="shrink-0 fill-current"
                      :class="isActive ? 'text-violet-500' : 'text-gray-600 dark:text-gray-300'"
                      xmlns="http://www.w3.org/2000/svg"
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                    >
                      <path
                        d="M8 0 1 3v5c0 3.5 3 6.5 7 8 4-1.5 7-4.5 7-8V3L8 0Zm0 2.2 5 2.1V8c0 2.5-2 4.8-5 6-3-1.2-5-3.5-5-6V4.3l5-2.1Z"
                      />
                    </svg>
                    <span
                      class="text-base font-medium ml-4 lg:opacity-0 lg:sidebar-expanded:opacity-100 duration-200"
                      >Admin</span
                    >
                  </div>
                </a>
              </li>
            </RouterLink>
          </ul>
        </div>
      </div>

      <!-- Expand / collapse button -->
      <div class="pt-3 hidden lg:inline-flex justify-end mt-auto">
        <div class="w-12 pl-4 pr-3 py-2">
          <button
            id="sidebar-collapse-toggle"
            class="text-gray-400 hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400"
            @click.prevent="sidebarExpanded = !sidebarExpanded"
          >
            <span class="sr-only">Expand / collapse sidebar</span>
            <svg
              class="shrink-0 fill-current text-gray-600 dark:text-gray-300 sidebar-expanded:rotate-180"
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 16 16"
            >
              <path
                d="M15 16a1 1 0 0 1-1-1V1a1 1 0 1 1 2 0v14a1 1 0 0 1-1 1ZM8.586 7H1a1 1 0 1 0 0 2h7.586l-2.793 2.793a1 1 0 1 0 1.414 1.414l4.5-4.5A.997.997 0 0 0 12 8.01M11.924 7.617a.997.997 0 0 0-.217-.324l-4.5-4.5a1 1 0 0 0-1.414 1.414L8.586 7M12 7.99a.996.996 0 0 0-.076-.373Z"
              />
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
