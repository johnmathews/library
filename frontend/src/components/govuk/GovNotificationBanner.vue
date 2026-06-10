<script setup lang="ts">
import { computed, ref } from 'vue'
import { NotificationBanner } from 'govuk-frontend'
import { useGovukComponent } from './useGovukComponent'

const props = defineProps<{
  variant?: 'success'
  title?: string
}>()

const success = computed(() => props.variant === 'success')
const titleText = computed(() => props.title ?? (success.value ? 'Success' : 'Important'))

const root = ref<HTMLElement | null>(null)

// govuk-frontend NotificationBanner focuses banners with role="alert"
// (the success variant) so they are announced immediately.
useGovukComponent(root, NotificationBanner)
</script>

<template>
  <div
    ref="root"
    class="govuk-notification-banner"
    :class="{ 'govuk-notification-banner--success': success }"
    :role="success ? 'alert' : 'region'"
    aria-labelledby="govuk-notification-banner-title"
    data-module="govuk-notification-banner"
  >
    <div class="govuk-notification-banner__header">
      <h2 class="govuk-notification-banner__title" id="govuk-notification-banner-title">
        {{ titleText }}
      </h2>
    </div>
    <div class="govuk-notification-banner__content">
      <slot />
    </div>
  </div>
</template>
