<script setup lang="ts">
import { computed } from 'vue'

// Replaces GovPagination. Emits `change` with the target page number instead
// of navigating, so the caller (SPA) controls what a page change means.
// Preserves GovPagination's first/last/current +/- 1 window with ellipses.
const props = defineProps<{
  page: number
  totalPages: number
}>()

const emit = defineEmits<{ change: [page: number] }>()

type Item = { kind: 'page'; page: number } | { kind: 'ellipsis' }

const items = computed<Item[]>(() => {
  const wanted = new Set<number>([1, props.totalPages, props.page - 1, props.page, props.page + 1])
  const pages = [...wanted].filter((p) => p >= 1 && p <= props.totalPages).sort((a, b) => a - b)
  const result: Item[] = []
  let previous = 0
  for (const page of pages) {
    if (previous && page - previous > 1) result.push({ kind: 'ellipsis' })
    result.push({ kind: 'page', page })
    previous = page
  }
  return result
})

const onFirst = computed(() => props.page <= 1)
const onLast = computed(() => props.page >= props.totalPages)

function goTo(page: number): void {
  if (page >= 1 && page <= props.totalPages && page !== props.page) emit('change', page)
}

const btnBase =
  'btn bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700/60 text-gray-600 dark:text-gray-300'
const activeBtn = 'btn bg-violet-500 text-white border-violet-500'
const disabledBtn = 'opacity-50 cursor-not-allowed'
</script>

<template>
  <nav v-if="props.totalPages > 1" class="flex items-center gap-1" aria-label="Pagination">
    <button
      type="button"
      :class="[btnBase, { [disabledBtn]: onFirst }]"
      :disabled="onFirst"
      @click="goTo(props.page - 1)"
    >
      Previous<span class="sr-only"> page</span>
    </button>
    <template v-for="(item, index) in items" :key="item.kind === 'page' ? 'p' + item.page : 'gap' + index">
      <span v-if="item.kind === 'ellipsis'" class="px-2 text-gray-400">&ctdot;</span>
      <button
        v-else
        type="button"
        :class="item.page === props.page ? activeBtn : btnBase"
        :aria-label="`Page ${item.page}`"
        :aria-current="item.page === props.page ? 'page' : undefined"
        @click="goTo(item.page)"
      >
        {{ item.page }}
      </button>
    </template>
    <button
      type="button"
      :class="[btnBase, { [disabledBtn]: onLast }]"
      :disabled="onLast"
      @click="goTo(props.page + 1)"
    >
      Next<span class="sr-only"> page</span>
    </button>
  </nav>
</template>
