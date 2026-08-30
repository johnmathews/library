<script setup lang="ts">
/**
 * The drill panel body for the folded `Other` band's second step (spec
 * §4.12 "`Other` drills in two steps").
 *
 * `/cell` takes a single `split_value`, so the folded bucket has no direct
 * drill target — clicking it opens this body listing the folded values and
 * their totals *for the clicked period only*. Those totals are already in
 * `/data`'s `cells` (one cell per period × split value), so **this body
 * issues no request of its own** — that is the entire reason the fold
 * "costs no request" per the spec.
 *
 * Only the clicked period is shown. A folded member's cells span every
 * period the chart covers, not just the one that was clicked, so summing
 * or listing across all of them would not add up to the bar segment that
 * opened this panel — the same "answer the same question the bar asked"
 * rule §4.6 holds `/cell` to.
 *
 * Picking a row emits the member's raw `value` (never its label) so the
 * parent can round-trip it straight into `fetchCell`.
 */
import { computed } from 'vue'
import type { Cell, SplitValue } from '@/api/spending'
import { formatMoney } from '@/spending/money'

const props = defineProps<{
  period: string
  members: SplitValue[]
  cells: Cell[]
  currency: string
}>()

const emit = defineEmits<{ pick: [value: string | null] }>()

interface Row {
  value: string | null
  label: string
  amount: string
}

const rows = computed<Row[]>(() =>
  props.members.map((member) => {
    const cell = props.cells.find(
      (c) => c.period === props.period && c.split_value === member.value,
    )
    return { value: member.value, label: member.label, amount: cell?.total ?? '0.00' }
  }),
)

function money(amount: string): string {
  return formatMoney(amount, props.currency)
}
</script>

<template>
  <div data-testid="drill-other-body">
    <ul v-if="rows.length > 0" class="flex flex-col gap-1">
      <li v-for="row in rows" :key="row.value ?? '__null__'">
        <button
          type="button"
          class="flex w-full items-baseline justify-between gap-3 rounded-lg px-2 py-1 text-left transition hover:bg-gray-100 dark:hover:bg-gray-700/50"
          data-testid="drill-other-row"
          @click="emit('pick', row.value)"
        >
          <span class="min-w-0 truncate text-gray-800 dark:text-gray-100" data-testid="drill-other-label">
            {{ row.label }}
          </span>
          <span
            class="shrink-0 tabular-nums text-gray-800 dark:text-gray-100"
            :data-amount="row.amount"
            data-testid="drill-other-amount"
          >
            {{ money(row.amount) }}
          </span>
        </button>
      </li>
    </ul>
    <p v-else class="text-sm text-gray-500 dark:text-gray-400" data-testid="drill-empty">
      Nothing folded into Other for this period.
    </p>
  </div>
</template>
