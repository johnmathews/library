<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { SkipLink } from 'govuk-frontend'
import GovServiceNavigation from '@/components/govuk/GovServiceNavigation.vue'
import GovTag from '@/components/govuk/GovTag.vue'
import { useGovukComponent } from '@/components/govuk'
import type { ServiceNavigationItem } from '@/components/govuk'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const skipLink = ref<HTMLElement | null>(null)
useGovukComponent(skipLink, SkipLink)

const navItems = computed<ServiceNavigationItem[]>(() => {
  if (!auth.isAuthenticated) return []
  return [
    {
      text: 'Documents',
      to: '/',
      active: route.name === 'documents' || route.name === 'document-detail',
    },
    { text: 'Upload', to: '/upload', active: route.name === 'upload' },
    { text: 'Sign out' },
  ]
})

async function onNavSelect(item: ServiceNavigationItem): Promise<void> {
  if (item.text === 'Sign out') {
    await auth.logout()
    await router.push({ name: 'login' })
  }
}
</script>

<template>
  <a ref="skipLink" href="#main-content" class="govuk-skip-link" data-module="govuk-skip-link">
    Skip to main content
  </a>

  <header class="app-masthead">
    <div class="govuk-width-container">
      <div class="app-masthead__container">
        <RouterLink to="/" class="app-masthead__link">Library</RouterLink>
      </div>
    </div>
  </header>

  <GovServiceNavigation v-if="navItems.length" :items="navItems" @select="onNavSelect" />

  <div class="govuk-width-container">
    <div class="govuk-phase-banner">
      <p class="govuk-phase-banner__content">
        <GovTag class="govuk-phase-banner__content__tag">Beta</GovTag>
        <span class="govuk-phase-banner__text">This service is under active development.</span>
      </p>
    </div>

    <main class="govuk-main-wrapper" id="main-content">
      <RouterView />
    </main>
  </div>

  <footer class="app-footer">
    <div class="govuk-width-container">
      <p class="app-footer__text">Library — personal document archive.</p>
    </div>
  </footer>
</template>
