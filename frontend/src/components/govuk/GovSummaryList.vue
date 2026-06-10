<script setup lang="ts">
import type { SummaryListRow } from './types'

const props = defineProps<{
  rows: SummaryListRow[]
  noBorder?: boolean
}>()
</script>

<template>
  <dl class="govuk-summary-list" :class="{ 'govuk-summary-list--no-border': props.noBorder }">
    <div v-for="row in props.rows" :key="row.key" class="govuk-summary-list__row">
      <dt class="govuk-summary-list__key">{{ row.key }}</dt>
      <dd class="govuk-summary-list__value">{{ row.value }}</dd>
      <dd v-if="row.actions?.length" class="govuk-summary-list__actions">
        <ul v-if="row.actions.length > 1" class="govuk-summary-list__actions-list">
          <li
            v-for="action in row.actions"
            :key="action.href"
            class="govuk-summary-list__actions-list-item"
          >
            <a class="govuk-link" :href="action.href">
              {{ action.text
              }}<span v-if="action.visuallyHiddenText" class="govuk-visually-hidden">
                {{ action.visuallyHiddenText }}</span
              >
            </a>
          </li>
        </ul>
        <a v-else class="govuk-link" :href="row.actions[0]!.href">
          {{ row.actions[0]!.text
          }}<span v-if="row.actions[0]!.visuallyHiddenText" class="govuk-visually-hidden">
            {{ row.actions[0]!.visuallyHiddenText }}</span
          >
        </a>
      </dd>
    </div>
  </dl>
</template>
