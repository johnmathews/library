<script setup lang="ts">
import type { SummaryListRow } from './types'

// Replaces GovSummaryList. Preserves the `rows` prop (SummaryListRow[]) and
// the optional `noBorder` flag, plus each row's optional `actions` link list
// (text / href / visuallyHiddenText).
const props = defineProps<{
  rows: SummaryListRow[]
  noBorder?: boolean
}>()
</script>

<template>
  <dl
    class="divide-y"
    :class="props.noBorder ? 'divide-transparent' : 'divide-gray-200 dark:divide-gray-700/60'"
  >
    <div v-for="row in props.rows" :key="row.key" class="flex justify-between gap-4 py-3">
      <dt class="text-sm font-medium text-gray-500">{{ row.key }}</dt>
      <dd class="text-sm text-gray-800 dark:text-gray-100">{{ row.value }}</dd>
      <dd v-if="row.actions?.length" class="text-sm">
        <ul v-if="row.actions.length > 1" class="flex flex-col gap-1">
          <li v-for="action in row.actions" :key="action.href">
            <a class="text-violet-500 hover:text-violet-600" :href="action.href">
              {{ action.text
              }}<span v-if="action.visuallyHiddenText" class="sr-only">
                {{ action.visuallyHiddenText }}</span
              >
            </a>
          </li>
        </ul>
        <a
          v-else
          class="text-violet-500 hover:text-violet-600"
          :href="row.actions[0]!.href"
        >
          {{ row.actions[0]!.text
          }}<span v-if="row.actions[0]!.visuallyHiddenText" class="sr-only">
            {{ row.actions[0]!.visuallyHiddenText }}</span
          >
        </a>
      </dd>
    </div>
  </dl>
</template>
