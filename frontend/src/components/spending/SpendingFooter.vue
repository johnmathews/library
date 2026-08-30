<script setup lang="ts">
/**
 * The `/charts` footer accounting statement (spec §4.5).
 *
 * This is the feature's "nothing is excluded silently" promise made
 * concrete: every document a chart's rule touched but its total did not
 * count is accounted for here, in one of three labelled blocks. It is a
 * typeset statement, not a row of stat chips, because the point is to be
 * read line by line, not scanned as a single figure.
 *
 * Three things this component must never do, each traced to a defect the
 * engine's own review found:
 *
 * 1. Treat a refund as excluded. A refund is netted — it is INSIDE the
 *    total and lowers it — so it belongs in the header line, not in
 *    "excluded from the total", which would read as the opposite of what
 *    happened.
 * 2. Lump `unclassified` / `uncategorised` / `undated` / `unaccounted` in
 *    with `excluded`. `excluded` means correctly-not-spending (a rule
 *    matched and said "not this"); the other four mean not-yet-decided or
 *    (for `unaccounted`) a hole in the classification. They render under
 *    "needs attention" instead, and `unaccounted` carries that meaning
 *    explicitly — it should always be empty.
 * 3. Hide a block because its groups are null. An absent group and an
 *    empty block are different claims, and only an always-rendered block
 *    reading "nothing" makes the second claim. So the three body blocks —
 *    excluded / needs attention / could not be converted — always render,
 *    with a "nothing here" line standing in for no groups.
 *
 * `refund_count` and `unconvertible[].documents` are plain figures, never
 * buttons: neither has a bucket route (§2.4) and wiring one to
 * `/footer/{bucket}` is a 422. The five drillable counts — `excluded`'s
 * per-kind groups, `unclassified`, `uncategorised`, `undated`,
 * `unaccounted` — are real `<button>`s so they stay keyboard- and
 * screen-reader-operable.
 *
 * `documents` means three unrelated things across one API response
 * (`ChartData.documents` — payment-group members; a footer group's
 * `documents` — canonical rows; merged `unconvertible.documents` — a
 * summed upper bound) and this component never adds any two of them or
 * presents them as parts of one whole; each stays in its own block.
 */
import { computed } from 'vue'
import type { ChartData, ExcludedGroup, FooterBucket, Unconvertible } from '@/api/spending'
import { formatMoney } from '@/spending/money'

const props = defineProps<{ data: ChartData }>()

const emit = defineEmits<{ bucket: [bucket: FooterBucket, amountKind?: string] }>()

function money(amount: string): string {
  return formatMoney(amount, props.data.currency)
}

/** `formatMoney` needs a currency; a null-currency unconvertible group has
 * none, so the currency prefix is dropped and only the grouped digits kept —
 * still built from `formatMoney`'s own arithmetic, never `parseFloat`. */
function bareAmount(amount: string): string {
  return formatMoney(amount, '').trim()
}

const refundLabel = computed(() => {
  const n = props.data.footer.refund_count
  return `${n} refund${n === 1 ? '' : 's'} netted off`
})

interface AttentionRow {
  bucket: FooterBucket
  group: ExcludedGroup
}

// Only `excluded`, `unclassified`, `uncategorised`, `undated` and
// `unaccounted` are in FOOTER_BUCKETS / drillable — this list is exactly
// the needs-attention four, in the order the spec's diagram gives them.
const attentionRows = computed<AttentionRow[]>(() => {
  const footer = props.data.footer
  const rows: AttentionRow[] = []
  if (footer.unclassified) rows.push({ bucket: 'unclassified', group: footer.unclassified })
  if (footer.uncategorised) rows.push({ bucket: 'uncategorised', group: footer.uncategorised })
  if (footer.undated) rows.push({ bucket: 'undated', group: footer.undated })
  if (footer.unaccounted) rows.push({ bucket: 'unaccounted', group: footer.unaccounted })
  return rows
})

// `null` sorts last, and is labelled — an unconvertible payment and an
// equal unconvertible refund can net to `0.00` across two documents, which
// without a currency label and the document count reads as nothing missing.
const sortedUnconvertible = computed<Unconvertible[]>(() =>
  [...props.data.footer.unconvertible].sort((a, b) => {
    if (a.currency === null) return b.currency === null ? 0 : 1
    if (b.currency === null) return -1
    return a.currency.localeCompare(b.currency)
  }),
)

function onExcludedClick(group: ExcludedGroup): void {
  emit('bucket', 'excluded', group.amount_kind)
}

function onAttentionClick(bucket: FooterBucket): void {
  emit('bucket', bucket, undefined)
}
</script>

<template>
  <div
    class="flex flex-col divide-y divide-gray-200 text-sm dark:divide-gray-700"
    data-testid="spending-footer"
  >
    <div
      class="flex flex-col gap-1 py-3"
      data-testid="spending-footer-header"
    >
      <p class="text-gray-800 dark:text-gray-100">
        <span class="font-semibold tabular-nums">{{ money(data.total) }}</span>
        across
        <span class="tabular-nums">{{ data.payments }}</span>
        payments from
        <span class="tabular-nums">{{ data.documents }}</span>
        documents
      </p>
      <p class="flex items-baseline justify-between gap-3 text-gray-500 dark:text-gray-400">
        <span data-testid="spending-footer-refund-label">including {{ refundLabel }}</span>
        <span
          class="shrink-0 tabular-nums"
          data-testid="spending-footer-refund-figure"
        >
          -{{ money(data.footer.netted_refunds) }}
        </span>
      </p>
    </div>

    <div
      class="flex flex-col gap-1 py-3"
      data-testid="spending-footer-excluded"
    >
      <h3 class="filter-label">Excluded from the total</h3>
      <ul
        v-if="data.footer.excluded.length > 0"
        class="flex flex-col gap-0.5"
      >
        <li
          v-for="group in data.footer.excluded"
          :key="group.amount_kind"
        >
          <button
            type="button"
            class="flex w-full items-baseline justify-between gap-3 rounded-lg px-2 py-1 text-left transition hover:bg-gray-100 dark:hover:bg-gray-700/50"
            :data-testid="`spending-footer-bucket-${group.amount_kind}`"
            @click="onExcludedClick(group)"
          >
            <span class="text-gray-800 dark:text-gray-100">{{ group.amount_kind }} &middot; {{ group.documents }} documents</span>
            <span class="shrink-0 tabular-nums text-gray-800 dark:text-gray-100">{{ money(group.amount) }}</span>
          </button>
        </li>
      </ul>
      <p
        v-else
        class="px-2 py-1 text-gray-500 dark:text-gray-400"
        data-testid="spending-footer-excluded-empty"
      >
        Nothing excluded
      </p>
    </div>

    <div
      class="flex flex-col gap-1 py-3"
      data-testid="spending-footer-attention"
    >
      <h3 class="filter-label">Needs attention</h3>
      <ul
        v-if="attentionRows.length > 0"
        class="flex flex-col gap-0.5"
      >
        <li
          v-for="row in attentionRows"
          :key="row.bucket"
        >
          <button
            type="button"
            class="flex w-full items-baseline justify-between gap-3 rounded-lg px-2 py-1 text-left transition hover:bg-gray-100 dark:hover:bg-gray-700/50"
            :data-testid="`spending-footer-bucket-${row.bucket}`"
            @click="onAttentionClick(row.bucket)"
          >
            <span class="text-gray-800 dark:text-gray-100">{{ row.bucket }} &middot; {{ row.group.documents }} documents</span>
            <span class="shrink-0 tabular-nums text-gray-800 dark:text-gray-100">{{ money(row.group.amount) }}</span>
          </button>
        </li>
      </ul>
      <p
        v-else
        class="px-2 py-1 text-gray-500 dark:text-gray-400"
        data-testid="spending-footer-attention-empty"
      >
        Nothing needs attention
      </p>
    </div>

    <div
      class="flex flex-col gap-1 py-3"
      data-testid="spending-footer-unconvertible"
    >
      <h3 class="filter-label">Could not be converted</h3>
      <ul
        v-if="sortedUnconvertible.length > 0"
        class="flex flex-col gap-0.5"
      >
        <li
          v-for="(group, index) in sortedUnconvertible"
          :key="group.currency ?? `__no_currency_${index}`"
          class="flex items-baseline justify-between gap-3 px-2 py-1"
          data-testid="spending-footer-unconvertible-row"
        >
          <span class="text-gray-800 dark:text-gray-100">
            {{ group.currency ?? 'No currency' }}
            &middot;
            <span data-testid="spending-footer-unconvertible-documents">{{ group.documents }} documents</span>
          </span>
          <span
            class="shrink-0 tabular-nums text-gray-800 dark:text-gray-100"
            data-testid="spending-footer-unconvertible-amount"
          >
            {{ group.currency === null ? bareAmount(group.amount) : money(group.amount) }}
          </span>
        </li>
      </ul>
      <p
        v-else
        class="px-2 py-1 text-gray-500 dark:text-gray-400"
        data-testid="spending-footer-unconvertible-empty"
      >
        Nothing unconverted
      </p>
    </div>
  </div>
</template>
