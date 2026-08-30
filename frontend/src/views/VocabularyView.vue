<script setup lang="ts">
/**
 * Facet vocabulary console (route `/vocabulary`, authenticated but not
 * admin-gated — see router/index.ts).
 *
 * Tabs (in display order):
 *   - Facets: facet label values, counts and split colours.
 *   - Senders: sender facet assignment.
 *   - Suggestions: proposed facet-label merges/renames.
 * Tab selection is local state (no sub-routes).
 */
import { ref } from 'vue'
import { PageHeader } from '@/components/app'
import FacetsPanel from './vocabulary/FacetsPanel.vue'
import SendersPanel from './vocabulary/SendersPanel.vue'
import SuggestionsPanel from './vocabulary/SuggestionsPanel.vue'

type Tab = 'facets' | 'senders' | 'suggestions'
const tab = ref<Tab>('facets')

const TABS: { id: Tab; label: string }[] = [
  { id: 'facets', label: 'Facets' },
  { id: 'senders', label: 'Senders' },
  { id: 'suggestions', label: 'Suggestions' },
]

const tabClass = (active: boolean): string =>
  [
    'px-4 py-2 -mb-px text-sm font-medium border-b-2 transition cursor-pointer',
    active
      ? 'border-violet-500 text-violet-600'
      : 'border-transparent text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200',
  ].join(' ')
</script>

<template>
  <div id="vocabulary-page">
    <PageHeader title="Vocabulary" />

    <div
      role="tablist"
      class="flex gap-1 border-b border-gray-200 dark:border-gray-700/60 mb-6"
    >
      <button
        v-for="t in TABS"
        :key="t.id"
        role="tab"
        type="button"
        :aria-selected="tab === t.id"
        :tabindex="tab === t.id ? 0 : -1"
        :class="tabClass(tab === t.id)"
        :data-testid="`vocab-tab-${t.id}-btn`"
        @click="tab = t.id"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- Facets tab -->
    <section v-show="tab === 'facets'" role="tabpanel" data-testid="vocab-tab-facets">
      <FacetsPanel :active="tab === 'facets'" />
    </section>

    <!-- Senders tab -->
    <section v-show="tab === 'senders'" role="tabpanel" data-testid="vocab-tab-senders">
      <SendersPanel :active="tab === 'senders'" />
    </section>

    <!-- Suggestions tab -->
    <section v-show="tab === 'suggestions'" role="tabpanel" data-testid="vocab-tab-suggestions">
      <SuggestionsPanel :active="tab === 'suggestions'" />
    </section>
  </div>
</template>
