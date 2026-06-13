<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import DefaultLayout from '@/layouts/DefaultLayout.vue'

const route = useRoute()
const isPublicRoute = computed(() => route.meta.public === true)
</script>

<template>
  <RouterView v-if="isPublicRoute" />
  <DefaultLayout v-else>
    <RouterView />
  </DefaultLayout>
</template>
