<script setup lang="ts">
import { computed } from 'vue'

// Emits `change` with the target page number instead of navigating, so the
// caller (SPA) controls what a page change means.
const props = defineProps<{
  page: number
  totalPages: number
}>()

const emit = defineEmits<{ change: [page: number] }>()

type Item = { kind: 'page'; page: number } | { kind: 'ellipsis' }

// GOV.UK pagination pattern: first page, last page, current +/- 1, with
// ellipses for any gaps.
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

function goTo(page: number): void {
  if (page >= 1 && page <= props.totalPages && page !== props.page) emit('change', page)
}
</script>

<template>
  <nav v-if="props.totalPages > 1" class="govuk-pagination" aria-label="Pagination">
    <div v-if="props.page > 1" class="govuk-pagination__prev">
      <a class="govuk-link govuk-pagination__link" href="#" rel="prev" @click.prevent="goTo(props.page - 1)">
        <svg class="govuk-pagination__icon govuk-pagination__icon--prev" xmlns="http://www.w3.org/2000/svg" height="13" width="15" aria-hidden="true" focusable="false" viewBox="0 0 15 13">
          <path d="m6.5938-0.0078125-6.7266 6.7266 6.7441 6.4062 1.377-1.449-4.1856-3.9768h12.896v-2h-12.984l4.2931-4.293-1.414-1.414z"></path>
        </svg>
        <span class="govuk-pagination__link-title">
          Previous<span class="govuk-visually-hidden"> page</span>
        </span>
      </a>
    </div>
    <ul class="govuk-pagination__list">
      <template v-for="(item, index) in items" :key="index">
        <li v-if="item.kind === 'ellipsis'" class="govuk-pagination__item govuk-pagination__item--ellipses">
          &ctdot;
        </li>
        <li
          v-else
          class="govuk-pagination__item"
          :class="{ 'govuk-pagination__item--current': item.page === props.page }"
        >
          <a
            class="govuk-link govuk-pagination__link"
            href="#"
            :aria-label="`Page ${item.page}`"
            :aria-current="item.page === props.page ? 'page' : undefined"
            @click.prevent="goTo(item.page)"
          >
            {{ item.page }}
          </a>
        </li>
      </template>
    </ul>
    <div v-if="props.page < props.totalPages" class="govuk-pagination__next">
      <a class="govuk-link govuk-pagination__link" href="#" rel="next" @click.prevent="goTo(props.page + 1)">
        <span class="govuk-pagination__link-title">
          Next<span class="govuk-visually-hidden"> page</span>
        </span>
        <svg class="govuk-pagination__icon govuk-pagination__icon--next" xmlns="http://www.w3.org/2000/svg" height="13" width="15" aria-hidden="true" focusable="false" viewBox="0 0 15 13">
          <path d="m8.107-0.0078125-1.4136 1.414 4.2926 4.293h-12.986v2h12.896l-4.1855 3.9766 1.377 1.4492 6.7441-6.4062-6.7246-6.7266z"></path>
        </svg>
      </a>
    </div>
  </nav>
</template>
