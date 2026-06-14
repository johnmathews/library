<script setup lang="ts">
import { computed, watch } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import DefaultLayout from '@/layouts/DefaultLayout.vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const isPublicRoute = computed(() => route.meta.public === true)

// Reflect the user's page-canvas tone onto <html data-canvas="…">, which
// assets/main.css maps to the body background (light mode). Immediate so the
// default tone is set on first paint; reactive so changing it in Settings
// re-paints instantly without a reload.
const auth = useAuthStore()
watch(
  () => auth.backgroundTone,
  (tone) => {
    document.documentElement.dataset.canvas = tone
  },
  { immediate: true },
)
</script>

<template>
  <RouterView v-if="isPublicRoute" />
  <DefaultLayout v-else>
    <RouterView />
  </DefaultLayout>
</template>
