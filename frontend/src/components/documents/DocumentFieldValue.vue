<script setup lang="ts">
/**
 * One dashboard field of one document, as every dashboard layout shows it.
 *
 * `chips` is the tile and row look: type, language and file type as neutral
 * chips. `text` is the quieter list and table look: the same values as plain
 * text. Status (Failed / Processing) stays a chip in both, because it asks for
 * attention. Renders nothing when the document has no value for the field.
 *
 * The class and data-testid hooks (`app-doc-card__sender`, `doc-date`,
 * `doc-tags`, …) are the same in every layout and variant, so a test or a style
 * rule that finds a field finds it under any layout.
 */
import { computed } from 'vue'
import { AppBadge } from '@/components/app'
import type { DocumentListItem } from '@/api/documents'
import type { DashboardField } from '@/api/settings'
import {
  EXTRA_DATE_FIELDS,
  MAX_TAGS,
  extraDate,
  fileTypeLabel,
  formatAmount,
  formatListDate,
  languageName,
} from '@/utils/documentFields'

const props = withDefaults(
  defineProps<{
    item: DocumentListItem
    field: DashboardField
    compactDates?: boolean
    variant?: 'chips' | 'text'
    /**
     * Which dates carry a prefix: `all` ("Date 17 Sep", "Due 1 Oct"), `extra`
     * (only the dates beyond the document's own, for the list), or `none` (the
     * table, whose column headings already say which date it is).
     */
    dateLabels?: 'all' | 'extra' | 'none'
  }>(),
  { compactDates: false, variant: 'chips', dateLabels: 'all' },
)

const chips = computed<boolean>(() => props.variant === 'chips')
const extra = computed(() => EXTRA_DATE_FIELDS[props.field])
const extraValue = computed<string | null>(() =>
  extra.value ? extraDate(props.field, props.item, props.compactDates) : null,
)
const hook = computed<string>(() => props.field.replace('_', '-'))
</script>

<template>
  <template v-if="field === 'kind' && item.kind">
    <AppBadge v-if="chips" colour="grey">{{ item.kind.name }}</AppBadge>
    <span v-else class="app-doc-card__kind text-sm text-ink-secondary">{{ item.kind.name }}</span>
  </template>
  <template v-else-if="field === 'language' && item.language !== 'unknown'">
    <AppBadge v-if="chips" colour="grey">
      {{ languageName(item.language) }}
    </AppBadge>
    <span v-else class="app-doc-card__language text-sm text-ink-secondary">{{
      languageName(item.language)
    }}</span>
  </template>
  <template v-else-if="field === 'status'">
    <AppBadge v-if="item.status === 'failed'" colour="red">Failed</AppBadge>
    <AppBadge v-else-if="item.status !== 'indexed'" colour="yellow">Processing</AppBadge>
  </template>
  <template v-else-if="field === 'file_type'">
    <AppBadge v-if="chips" colour="grey">{{ fileTypeLabel(item) }}</AppBadge>
    <span v-else class="app-doc-card__file-type text-sm text-ink-secondary">{{
      fileTypeLabel(item)
    }}</span>
  </template>
  <span
    v-else-if="field === 'sender' && item.sender"
    class="app-doc-card__sender text-sm text-ink-secondary"
  >
    {{ item.sender.name }}
  </span>
  <span
    v-else-if="field === 'date' && item.document_date"
    class="app-doc-card__date text-sm text-ink-secondary"
    data-testid="doc-date"
  >
    <span v-if="dateLabels === 'all'" class="text-ink-secondary">Date</span>
    {{ formatListDate(item.document_date, compactDates) }}
  </span>
  <span
    v-else-if="extra && extraValue"
    :class="`app-doc-card__${hook} text-sm text-ink-secondary`"
    :data-testid="`doc-${hook}`"
  >
    <span v-if="dateLabels !== 'none'" class="text-ink-secondary">{{ extra.label }}</span>
    {{ extraValue }}
  </span>
  <span
    v-else-if="field === 'amount' && formatAmount(item)"
    class="app-doc-card__amount text-sm text-ink tabular-nums"
  >
    {{ formatAmount(item) }}
  </span>
  <template v-else-if="field === 'tags' && item.tags.length">
    <span
      v-if="chips"
      class="inline-flex flex-wrap items-center gap-2 app-doc-card__tags"
      data-testid="doc-tags"
    >
      <AppBadge v-for="tag in item.tags.slice(0, MAX_TAGS)" :key="tag.slug" colour="grey">
        {{ tag.name }}
      </AppBadge>
      <span
        v-if="item.tags.length > MAX_TAGS"
        class="app-doc-card__tags-more text-sm text-ink-secondary"
      >
        +{{ item.tags.length - MAX_TAGS }}
      </span>
    </span>
    <!-- Capped and truncated so a long tag list cannot push a one-line
         layout onto two lines; the tooltip carries every tag. -->
    <span
      v-else
      class="app-doc-card__tags inline-block max-w-48 truncate align-bottom text-sm text-ink-secondary"
      :title="item.tags.map((tag) => tag.name).join(', ')"
      data-testid="doc-tags"
    >
      {{ item.tags.slice(0, MAX_TAGS).map((tag) => tag.name).join(', ') }}<span
        v-if="item.tags.length > MAX_TAGS"
        class="app-doc-card__tags-more"
      >
        +{{ item.tags.length - MAX_TAGS }}</span
      >
    </span>
  </template>
</template>
