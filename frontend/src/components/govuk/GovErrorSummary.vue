<script setup lang="ts">
import { ref, watch } from 'vue'
import { ErrorSummary } from 'govuk-frontend'
import { useGovukComponent } from './useGovukComponent'
import type { ErrorSummaryItem } from './types'

const props = withDefaults(
  defineProps<{
    title?: string
    errors: ErrorSummaryItem[]
  }>(),
  { title: 'There is a problem' },
)

const root = ref<HTMLElement | null>(null)

// govuk-frontend ErrorSummary focuses the summary on init and makes the
// error links move focus to their target fields.
useGovukComponent(root, ErrorSummary)

// If the error list changes while the summary stays mounted (e.g. a second
// failed submit), re-focus it so screen readers re-announce the alert.
watch(
  () => props.errors,
  () => root.value?.focus(),
)
</script>

<template>
  <div ref="root" class="govuk-error-summary" data-module="govuk-error-summary" tabindex="-1">
    <div role="alert">
      <h2 class="govuk-error-summary__title">{{ props.title }}</h2>
      <div class="govuk-error-summary__body">
        <ul class="govuk-list govuk-error-summary__list">
          <li v-for="(error, index) in props.errors" :key="index">
            <a v-if="error.href" :href="error.href">{{ error.text }}</a>
            <template v-else>{{ error.text }}</template>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>
