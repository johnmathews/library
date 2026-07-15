<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import SearchModal from '@/components/SearchModal.vue'
import { ToastContainer } from '@/components/app'
import { useJobsStore } from '@/stores/jobs'

const sidebarOpen = ref(false)
const searchModal = ref<InstanceType<typeof SearchModal> | null>(null)

// The default layout renders only for authenticated routes, so this is the
// right place to open the live job stream (and tear it down on sign-out).
const jobs = useJobsStore()
onMounted(() => jobs.connect())
onUnmounted(() => jobs.disconnect())
</script>

<template>
  <div id="app-shell" class="flex h-[100dvh] overflow-hidden">
    <AppSidebar
      :sidebar-open="sidebarOpen"
      @close-sidebar="sidebarOpen = false"
      @open-search="searchModal?.open()"
    />
    <div id="app-content" class="relative flex flex-col flex-1 overflow-y-auto overflow-x-hidden">
      <AppHeader
        :sidebar-open="sidebarOpen"
        @toggle-sidebar="sidebarOpen = !sidebarOpen"
        @open-search="searchModal?.open()"
      />
      <main id="app-main" class="grow">
        <div id="app-page" class="px-4 sm:px-6 lg:px-8 py-8 w-full max-w-[96rem] mx-auto">
          <slot />
        </div>
      </main>
    </div>
  </div>
  <SearchModal ref="searchModal" />
  <ToastContainer />
</template>
