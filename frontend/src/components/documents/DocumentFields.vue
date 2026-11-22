<script setup lang="ts">
/**
 * A document's meta row: the Needs review chip and its reason first (always,
 * not one of the toggleable fields), then the owner's chosen fields in their
 * chosen order (`dashboard_fields`, the Fields menu). Used by the tile, row and
 * list layouts; the table lays the same fields out as columns instead.
 */
import { AppBadge } from '@/components/app'
import DocumentFieldValue from './DocumentFieldValue.vue'
import type { DocumentListItem } from '@/api/documents'
import type { DashboardField } from '@/api/settings'
import { summarizeReviewReasons } from '@/utils/validationReason'

withDefaults(
  defineProps<{
    item: DocumentListItem
    fields: readonly DashboardField[]
    compactDates?: boolean
    variant?: 'chips' | 'text'
    dateLabel?: boolean
  }>(),
  { compactDates: false, variant: 'chips', dateLabel: true },
)
</script>

<template>
  <p>
    <template v-if="item.review_status === 'needs_review'">
      <AppBadge colour="yellow" data-testid="review-badge">Needs review</AppBadge>
      <span
        v-if="item.review_findings.length"
        class="text-xs text-attention"
        data-testid="review-reason"
      >{{ summarizeReviewReasons(item.review_findings) }}</span>
    </template>
    <DocumentFieldValue
      v-for="field in fields"
      :key="field"
      :item="item"
      :field="field"
      :compact-dates="compactDates"
      :variant="variant"
      :date-label="dateLabel"
    />
  </p>
</template>
