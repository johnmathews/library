<script setup lang="ts">
/**
 * Dashboard "List" layout: one line per document, no cards. Hairlines between
 * lines on the page background; a small dot in the document type's colour, the
 * title, then the owner's chosen fields as quiet text on the right (under the
 * title on a phone). The table renders as this list where it has no room.
 *
 * Each line is one link (a stretched title link), and carries the tile's
 * `doc-card` hook so anything that opens a document works under any layout.
 */
import DocumentFields from './DocumentFields.vue'
import type { DocumentLayoutProps } from './layoutProps'

defineProps<DocumentLayoutProps>()
</script>

<template>
  <ul class="border-y border-line divide-y divide-line" data-testid="doc-list">
    <li
      v-for="item in items"
      :id="`doc-card-${item.id}`"
      :key="item.id"
      class="relative flex flex-col gap-0.5 px-1 py-2.5 transition-colors hover:bg-tile sm:flex-row sm:items-center sm:gap-6 sm:px-2"
      :data-kind="item.kind?.slug"
      data-testid="doc-card"
    >
      <div class="flex min-w-0 items-center gap-2.5 sm:flex-1">
        <span
          class="app-doc-list__dot"
          :class="{ 'app-doc-list__dot--accented': accents.get(item.id) }"
          :style="accents.get(item.id) ? { '--card-accent': accents.get(item.id)! } : undefined"
          aria-hidden="true"
        ></span>
        <RouterLink
          class="min-w-0 truncate text-[14px] font-medium text-ink hover:underline after:absolute after:inset-0 after:content-['']"
          :to="{ name: 'document-detail', params: { id: item.id }, query: detailQuery }"
        >
          {{ item.title ?? 'Untitled document' }}
        </RouterLink>
      </div>
      <DocumentFields
        :item="item"
        :fields="fields"
        :compact-dates="compactDates"
        variant="text"
        date-labels="extra"
        class="flex flex-wrap items-center gap-x-3 gap-y-0.5 pl-[1.125rem] sm:justify-end sm:pl-0 sm:max-w-[60%]"
      />
    </li>
  </ul>
</template>
