<script setup lang="ts">
import { ref } from 'vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import SearchModal from '@/components/SearchModal.vue'

const sidebarOpen = ref(false)
const searchModal = ref<InstanceType<typeof SearchModal> | null>(null)
</script>

<template>
  <div id="app-shell" class="flex h-[100dvh] overflow-hidden">
    <AppSidebar :sidebar-open="sidebarOpen" @close-sidebar="sidebarOpen = false" />
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
</template>
