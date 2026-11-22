<script setup lang="ts">
/**
 * Dashboard "Rows" layout: one column of wide, thin cards with no page preview.
 * The title on its own line and the owner's chosen fields under it, both left
 * aligned so each reads down the page. The document type's colour is a 4px
 * band down the left edge, the row's version of the tile's top band. While
 * searching, the snippet follows.
 *
 * Each row is one link (a stretched title link), and carries the tile's
 * `doc-card` hook so anything that opens a document works under any layout.
 */
import DocumentFields from './DocumentFields.vue'
import type { DocumentLayoutProps } from './layoutProps'
import { renderSnippet } from '@/utils/snippet'

defineProps<DocumentLayoutProps>()
</script>

<template>
  <ul class="flex flex-col gap-1.5 sm:gap-2" data-testid="doc-rows">
    <li
      v-for="item in items"
      :id="`doc-card-${item.id}`"
      :key="item.id"
      class="relative app-doc-row px-3 py-2.5 sm:px-5 sm:py-3"
      :class="{ 'app-doc-row--accented': accents.get(item.id) }"
      :style="accents.get(item.id) ? { '--card-accent': accents.get(item.id)! } : undefined"
      :data-kind="item.kind?.slug"
      data-testid="doc-card"
    >
      <h2 class="text-[14px] sm:text-[15px] leading-snug">
        <RouterLink
          class="text-ink font-semibold hover:underline line-clamp-2 sm:line-clamp-1 after:absolute after:inset-0 after:content-['']"
          :to="{ name: 'document-detail', params: { id: item.id }, query: detailQuery }"
        >
          {{ item.title ?? 'Untitled document' }}
        </RouterLink>
      </h2>
      <DocumentFields
        :item="item"
        :fields="fields"
        :compact-dates="compactDates"
        class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1"
      />
      <!-- eslint-disable-next-line vue/no-v-html -- renderSnippet escapes everything except the ts_headline <b> markers (docs/api.md §1.3.3) -->
      <p
        v-if="searching && item.snippet"
        class="mt-1 text-sm text-ink-secondary leading-snug line-clamp-2 app-doc-card__snippet"
        v-html="renderSnippet(item.snippet)"
      ></p>
    </li>
  </ul>
</template>
