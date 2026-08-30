<script setup lang="ts">
/**
 * One saved chart, as it appears on the `/charts` board (spec §4.13, §10.1).
 *
 * Anatomy is fixed and not open for reshaping here: name and overflow menu,
 * then the headline figure, then the compact chart, then the legend ribbon,
 * then a needs-attention line when the footer has one. The figure leads
 * because the board is scanned for values, not for shapes.
 *
 * This component fetches nothing — `data`/`error`/`busy` are handed down by
 * whatever polls the chart, and `edit` / `delete` / `move-up` / `move-down`
 * are emitted for the parent to act on. That keeps every card provably
 * independent: one card's fetch failing renders that card's own error line
 * (`error`) without hiding the rest of the board, and a card mid-refetch
 * (`busy`) keeps showing its last render, dimmed, rather than flashing a
 * skeleton — `SpendingChart` deliberately carries no loading signal of its
 * own and never keys its `<Bar>` on `data`, so the card owns that treatment.
 *
 * The headline is the most recent *complete* bucket, not the last one the
 * chart drew: the current period is always partial (it is still
 * accumulating), and a partial bucket compared against a full one is a
 * comparison that is always wrong and never looks it. "Complete" is decided
 * by comparing each cell's period against **today's period start**, computed
 * the way the server's `date_trunc(grain, ...)` computes it (`periodStart`
 * below) rather than by slicing the ISO string — a string slice gets `month`
 * right by accident and `week` / `quarter` wrong. `today` is a prop, not
 * `new Date()` read inline, purely so that "most recent complete bucket" is
 * testable: without pinning it, the assertion would name a different bucket
 * every day it runs.
 *
 * The delta between the headline and the bucket before it is **not
 * coloured**. Spending rising or falling is not, by itself, good or bad —
 * that depends on what was spent on — and this app reserves status colour
 * (red/green) for values that genuinely carry that meaning. The delta gets a
 * direction glyph in ordinary ink instead. It is also computed in integer
 * cents (`toCents`/`fromCents`), never `parseFloat`, because
 * `1284.50 - 1142.20` prints `142.29999999999998` in IEEE754.
 *
 * Edit and delete live behind the overflow menu (`AppPopover`), not on the
 * card face — a card shows data, not six controls each. Move up / move down
 * are ordinary, always-visible buttons, disabled at the ends: this is the
 * accessible reorder path (drag is the other one, task 8's board), and the
 * one the e2e suite can click on every viewport project without touching a
 * pointer-drag gesture.
 */
import { computed, ref } from 'vue'
import type { Chart, ChartData, Grain } from '@/api/spending'
import { bands, OTHER_VALUE, type Band } from '@/spending/palette'
import { formatMoney, fromCents, toCents } from '@/spending/money'
import AppPopover from '@/components/app/AppPopover.vue'
import SpendingChart from './SpendingChart.vue'
import SpendingLegend from './SpendingLegend.vue'

const props = withDefaults(
  defineProps<{
    chart: Chart
    data: ChartData | null
    error: string | null
    busy: boolean
    canMoveUp: boolean
    canMoveDown: boolean
    /** Defaults to the real current date; overridden in tests so "the most
     * recent complete bucket" names a fixed answer instead of today's. */
    today?: string
  }>(),
  { today: () => new Date().toISOString().slice(0, 10) },
)

const emit = defineEmits<{
  edit: []
  delete: []
  'move-up': []
  'move-down': []
}>()

// --- Period arithmetic ---------------------------------------------------
//
// Mirrors `_PERIOD_EXPR_TEMPLATE` in `charts/query.py`:
// `CAST(date_trunc(:grain, CAST(day AS timestamp)) AS date)`. Done here in
// plain integer year/month/day arithmetic — never via a parsed `Date` used
// for anything but the `week` weekday lookup — so there is no local-timezone
// step that could shift a date across midnight.

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
] as const

interface Ymd {
  y: number
  m: number
  d: number
}

function parseIso(iso: string): Ymd {
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  return { y: y!, m: m!, d: d! }
}

function isoOf(y: number, m: number, d: number): string {
  return `${String(y).padStart(4, '0')}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

/** The start of the `date_trunc(grain, iso)` bucket `iso` falls in, as an ISO date. */
function periodStart(iso: string, grain: Grain): string {
  const { y, m, d } = parseIso(iso)
  switch (grain) {
    case 'year':
      return isoOf(y, 1, 1)
    case 'quarter':
      return isoOf(y, Math.floor((m - 1) / 3) * 3 + 1, 1)
    case 'month':
      return isoOf(y, m, 1)
    case 'week': {
      // Postgres `date_trunc('week', ...)` truncates to the Monday of the
      // ISO week. `getUTCDay()` is 0 (Sun) .. 6 (Sat); step back to Monday
      // in UTC so no local timezone can carry the date across midnight.
      const utcMs = Date.UTC(y, m - 1, d)
      const dow = new Date(utcMs).getUTCDay()
      const daysSinceMonday = (dow + 6) % 7
      const monday = new Date(utcMs - daysSinceMonday * 86_400_000)
      return isoOf(monday.getUTCFullYear(), monday.getUTCMonth() + 1, monday.getUTCDate())
    }
  }
}

/** A named label for a bucket's start, e.g. "July 2026" / "Q3 2026" / "2026". */
function periodLabel(iso: string, grain: Grain): string {
  const { y, m, d } = parseIso(iso)
  switch (grain) {
    case 'year':
      return `${y}`
    case 'quarter':
      return `Q${Math.floor((m - 1) / 3) + 1} ${y}`
    case 'month':
      return `${MONTH_NAMES[m - 1]} ${y}`
    case 'week':
      return `Week of ${MONTH_NAMES[m - 1]} ${d}, ${y}`
  }
}

// --- Headline: most recent COMPLETE bucket, vs the one before it ---------

/** Every distinct period the data carries, summed to a total in cents —
 * across split values, since the headline is the chart's whole-bar total. */
const periodCents = computed<Map<string, number>>(() => {
  const totals = new Map<string, number>()
  const data = props.data
  if (!data) return totals
  for (const cell of data.cells) {
    totals.set(cell.period, (totals.get(cell.period) ?? 0) + toCents(cell.total))
  }
  return totals
})

const sortedPeriods = computed<string[]>(() => Array.from(periodCents.value.keys()).sort())

const currentPeriodStart = computed<string | null>(() =>
  props.data ? periodStart(props.today, props.data.grain) : null,
)

// Strictly before the bucket containing `today` — the current bucket is
// always partial and is never a candidate, however the data happens to end.
const completePeriods = computed<string[]>(() => {
  const start = currentPeriodStart.value
  if (start === null) return []
  return sortedPeriods.value.filter((p) => p < start)
})

const headlinePeriod = computed<string | null>(() => completePeriods.value.at(-1) ?? null)
const previousPeriod = computed<string | null>(() => {
  const periods = completePeriods.value
  return periods.length >= 2 ? (periods[periods.length - 2] ?? null) : null
})

const headlineLabel = computed<string | null>(() =>
  headlinePeriod.value && props.data ? periodLabel(headlinePeriod.value, props.data.grain) : null,
)
const previousLabel = computed<string | null>(() =>
  previousPeriod.value && props.data ? periodLabel(previousPeriod.value, props.data.grain) : null,
)

const headlineCents = computed<number | null>(() =>
  headlinePeriod.value === null ? null : (periodCents.value.get(headlinePeriod.value) ?? 0),
)
const previousCents = computed<number | null>(() =>
  previousPeriod.value === null ? null : (periodCents.value.get(previousPeriod.value) ?? 0),
)

const headlineFigure = computed<string | null>(() =>
  headlineCents.value === null || !props.data ? null : formatMoney(fromCents(headlineCents.value), props.data.currency),
)

// Exact integer-cent subtraction — never a float — so a delta like
// `1284.50 - 1142.20` renders `142.30`, not `142.29999999999998`.
const deltaCents = computed<number | null>(() =>
  headlineCents.value === null || previousCents.value === null
    ? null
    : headlineCents.value - previousCents.value,
)

/** A direction glyph in ordinary ink — never colour, which this app
 * reserves for values that genuinely mean good or bad. */
const deltaGlyph = computed<string | null>(() => {
  if (deltaCents.value === null) return null
  if (deltaCents.value > 0) return '▲'
  if (deltaCents.value < 0) return '▼'
  return '–'
})

const deltaFigure = computed<string | null>(() =>
  deltaCents.value === null || !props.data
    ? null
    : formatMoney(fromCents(Math.abs(deltaCents.value)), props.data.currency),
)

// --- Chart + legend --------------------------------------------------------

const cardBands = computed<Band[]>(() => (props.data ? bands(props.data.splits, props.data.cells) : []))

// A display filter local to the card, exactly like the split legend's own
// isolate/exclude/reset contract (spec §4.7) — it never touches the
// headline above, which reads `data.cells` directly and does not consult it.
const hiddenSplitValues = ref<Set<string | null | typeof OTHER_VALUE>>(new Set())

function onIsolate(key: string | null | typeof OTHER_VALUE): void {
  const all = cardBands.value.map((b) => b.value)
  const alreadyIsolated = all.length - hiddenSplitValues.value.size === 1 && !hiddenSplitValues.value.has(key)
  hiddenSplitValues.value = alreadyIsolated ? new Set() : new Set(all.filter((v) => v !== key))
}
function onExclude(key: string | null | typeof OTHER_VALUE): void {
  const next = new Set(hiddenSplitValues.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  hiddenSplitValues.value = next
}
function onReset(): void {
  hiddenSplitValues.value = new Set()
}

// --- Needs-attention line --------------------------------------------------

interface AttentionRow {
  bucket: string
  documents: number
}

// Same four buckets `SpendingFooter` lists under "needs attention" —
// `excluded` is correctly-not-spending and never appears here, and
// `unaccounted` should always be empty but is not hidden if it is not.
const attentionRows = computed<AttentionRow[]>(() => {
  const footer = props.data?.footer
  if (!footer) return []
  const rows: AttentionRow[] = []
  if (footer.unclassified) rows.push({ bucket: 'unclassified', documents: footer.unclassified.documents })
  if (footer.uncategorised) rows.push({ bucket: 'uncategorised', documents: footer.uncategorised.documents })
  if (footer.undated) rows.push({ bucket: 'undated', documents: footer.undated.documents })
  if (footer.unaccounted) rows.push({ bucket: 'unaccounted', documents: footer.unaccounted.documents })
  return rows
})

const attentionText = computed<string | null>(() => {
  if (attentionRows.value.length === 0) return null
  return attentionRows.value
    .map((row) => `${row.documents} document${row.documents === 1 ? '' : 's'} ${row.bucket}`)
    .join(', ')
})

// --- Overflow menu ---------------------------------------------------------

const menuOpen = ref(false)

function chooseEdit(): void {
  menuOpen.value = false
  emit('edit')
}
function chooseDelete(): void {
  menuOpen.value = false
  emit('delete')
}
</script>

<template>
  <div class="card flex flex-col gap-3 p-5" data-testid="spending-card">
    <div class="flex items-start justify-between gap-2">
      <h3
        class="min-w-0 truncate text-sm font-semibold text-gray-800 dark:text-gray-100"
        data-testid="spending-card-name"
      >
        {{ chart.name }}
      </h3>

      <div class="flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          class="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 transition hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-400 dark:hover:bg-gray-700/60 dark:hover:text-gray-200"
          data-testid="spending-card-move-up"
          aria-label="Move up"
          :disabled="!canMoveUp"
          @click="emit('move-up')"
        >
          <svg class="h-3.5 w-3.5 fill-none stroke-current" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 15l7-7 7 7" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
        <button
          type="button"
          class="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 transition hover:bg-gray-100 hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-400 dark:hover:bg-gray-700/60 dark:hover:text-gray-200"
          data-testid="spending-card-move-down"
          aria-label="Move down"
          :disabled="!canMoveDown"
          @click="emit('move-down')"
        >
          <svg class="h-3.5 w-3.5 fill-none stroke-current" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 9l7 7 7-7" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>

        <AppPopover
          :open="menuOpen"
          align="right"
          panel-class="w-36 p-1"
          :panel-attrs="{ role: 'menu', 'aria-label': `${chart.name} actions`, 'data-testid': 'spending-card-menu-panel' }"
          @update:open="menuOpen = $event"
        >
          <template #trigger="{ open: isOpen, toggle, triggerRef }">
            <button
              :ref="triggerRef"
              type="button"
              class="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 transition hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700/60 dark:hover:text-gray-200"
              data-testid="spending-card-menu"
              :aria-label="`${chart.name} actions`"
              :aria-expanded="isOpen"
              aria-haspopup="menu"
              @click="toggle"
            >
              <svg class="h-4 w-4 fill-current" viewBox="0 0 16 16" aria-hidden="true">
                <circle cx="3" cy="8" r="1.5" />
                <circle cx="8" cy="8" r="1.5" />
                <circle cx="13" cy="8" r="1.5" />
              </svg>
            </button>
          </template>

          <button
            type="button"
            role="menuitem"
            class="flex w-full items-center rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700/60"
            data-testid="spending-card-edit"
            @click="chooseEdit"
          >
            Edit
          </button>
          <button
            type="button"
            role="menuitem"
            class="flex w-full items-center rounded-lg px-3 py-2 text-left text-sm text-red-500 transition hover:bg-red-50 dark:hover:bg-red-500/10"
            data-testid="spending-card-delete"
            @click="chooseDelete"
          >
            Delete
          </button>
        </AppPopover>
      </div>
    </div>

    <p v-if="error" class="text-sm text-red-600 dark:text-red-400" data-testid="spending-card-error">
      {{ error }}
    </p>

    <div
      v-else-if="data"
      class="flex flex-col gap-3"
      :class="{ 'opacity-50 transition-opacity': busy }"
      data-testid="spending-card-body"
    >
      <div data-testid="spending-card-headline">
        <p
          v-if="headlineLabel"
          class="filter-label"
          data-testid="spending-card-headline-label"
        >
          {{ headlineLabel }}
        </p>
        <p class="text-2xl font-semibold tabular-nums text-gray-900 dark:text-gray-50">
          {{ headlineFigure ?? '—' }}
        </p>
      </div>

      <p
        v-if="deltaGlyph"
        class="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400"
        data-testid="spending-card-delta"
      >
        <span aria-hidden="true">{{ deltaGlyph }}</span>
        <span>{{ deltaFigure }}</span>
        <span v-if="previousLabel" class="text-gray-400 dark:text-gray-500">vs {{ previousLabel }}</span>
      </p>

      <div class="h-28 w-full">
        <SpendingChart
          :data="data"
          :bands="cardBands"
          :hidden="hiddenSplitValues"
          compact
          @cell="() => {}"
        />
      </div>

      <SpendingLegend
        :bands="cardBands"
        :hidden="hiddenSplitValues"
        :currency="data.currency"
        compact
        @isolate="onIsolate"
        @exclude="onExclude"
        @reset="onReset"
      />

      <p
        v-if="attentionText"
        class="text-xs text-amber-700 dark:text-amber-300/90"
        data-testid="spending-card-attention"
      >
        {{ attentionText }}
      </p>
    </div>
  </div>
</template>
