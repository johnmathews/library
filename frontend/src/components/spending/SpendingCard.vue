<script setup lang="ts">
/**
 * One saved chart, as it appears on the `/charts` board (spec §4.13, §10.1).
 *
 * Anatomy is fixed and not open for reshaping here: name and overflow menu,
 * then the headline figure, then the compact chart, then the legend ribbon,
 * then a needs-attention line when the footer has one. The figure leads
 * because the board is scanned for values, not for shapes.
 *
 * This component fetches nothing about a chart's DATA — `data`/`error`/`busy`
 * are handed down by whatever polls the chart, and `delete` / `move-up` /
 * `move-down` are emitted for the parent to act on. That keeps every card
 * provably independent: one card's fetch failing renders that card's own
 * error line (`error`) without hiding the rest of the board, and a card
 * mid-refetch (`busy`) keeps showing its last render, dimmed, rather than
 * flashing a skeleton — `SpendingChart` deliberately carries no loading
 * signal of its own and never keys its `<Bar>` on `data`, so the card owns
 * that treatment.
 *
 * Renaming is the one exception: it is card-local (nothing polls a chart's
 * *name*, unlike its data), so this component issues its own
 * `PATCH /api/spending/{id}` via `updateChart` and emits `renamed` with the
 * server's response once it succeeds — the same precedent
 * `SpendingEmptyState` sets by owning its own `createChart` call, rather
 * than the emit-only edit/delete/move contract above.
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
 * Rename, delete, move up and move down all live behind the overflow menu
 * (`AppPopover`) — spec §10.3 #5 is that a card shows data, not six controls
 * each, and two always-visible reorder buttons on every card multiplied
 * across a whole board is exactly the defect that line names. This does not
 * weaken the reorder path: `AppPopover` is keyboard-operable, closes on
 * Escape and returns focus to its trigger, so move up / move down (real
 * `<button>`s, disabled at the ends) stay fully reachable by keyboard — it
 * just takes one activation to open the menu first. It is still the path
 * the e2e suite clicks on every viewport project (drag is the other one,
 * task 8's board), and on a phone a menu beats two more face buttons.
 *
 * Rename and delete are each **two-step, in place** — the overflow item
 * ARMS an inline affordance (a name input with Save/Cancel; a Confirm/Cancel
 * pair) that replaces the header row itself, rather than a menu item that
 * fires the action on one click. This is the same shape the app's most
 * recent overflow menu uses (`ThreadActionsMenu` + its hosts, e.g.
 * `ConversationSidebar.vue`: the menu only emits the intent, the host arms
 * an inline row), not the `ConfirmDialog.vue` modal — a card in a grid does
 * not need a second, wider, `<dialog>`-anchored surface on top of a popover
 * for a decision this small, and one click on the wrong menu item can no
 * longer destroy a chart with no way back (spec review finding 4). A chart's
 * NAME is also a real navigation target now: `spending-card-name` is a
 * `RouterLink` to `/charts/{id}`, the only route from the board into the
 * workspace, so replacing the old mislabelled "Edit" menu item (which
 * navigated there but changed nothing) with "Rename" does not remove that
 * navigation — it moves onto the name itself, where a reader would expect
 * to find it (spec review finding 5).
 */
import { computed, nextTick, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { updateChart, type Chart, type ChartData, type Grain } from '@/api/spending'
import { ApiError } from '@/api/client'
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
  renamed: [chart: Chart]
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

// --- Overflow menu -----------------------------------------------------------

const menuOpen = ref(false)

function chooseMoveUp(): void {
  menuOpen.value = false
  emit('move-up')
}
function chooseMoveDown(): void {
  menuOpen.value = false
  emit('move-down')
}

// --- Rename (inline, two-step: the menu item ARMS the input row) -----------
//
// Owns its own `PATCH` (see the file docblock) — the one action this card
// fetches for itself rather than emitting up, since a chart's name has
// nothing to do with the data-polling loop `data`/`error`/`busy` exist for.

const renaming = ref(false)
const renameValue = ref('')
const renameBusy = ref(false)
const renameError = ref<string | null>(null)

const renameInputEl = ref<HTMLInputElement | null>(null)

function chooseRename(): void {
  menuOpen.value = false
  renameValue.value = props.chart.name
  renameError.value = null
  renaming.value = true
  // The overflow trigger this click came from is inside the AppPopover's
  // v-else branch and unmounts the same tick renaming flips true, so
  // without this a keyboard user is dropped to document.body and has to
  // tab from the top of the page (spec review round 2, finding N2).
  // Selected, not just focused, so typing immediately overwrites — same
  // idiom as ConversationSidebar.vue's own startRename.
  void nextTick(() => {
    renameInputEl.value?.focus()
    renameInputEl.value?.select()
  })
}
function cancelRename(): void {
  renaming.value = false
  renameError.value = null
}
async function saveRename(): Promise<void> {
  if (renameBusy.value) return
  const name = renameValue.value.trim()
  // A blank or unchanged name is a no-op — close the editor rather than
  // sending a doomed/pointless request (mirrors the Ask thread rename idiom,
  // ConversationSidebar.vue's `saveRename`).
  if (!name || name === props.chart.name) {
    cancelRename()
    return
  }
  renameBusy.value = true
  renameError.value = null
  try {
    const updated = await updateChart(props.chart.id, { name })
    renaming.value = false
    emit('renamed', updated)
  } catch (err) {
    // Keep the form open with exactly what was typed — a failed save must
    // never discard it (spec review finding 5).
    renameError.value = err instanceof ApiError ? err.detail : 'Could not rename this chart.'
  } finally {
    renameBusy.value = false
  }
}

// --- Delete (inline, two-step: the menu item ARMS a Confirm/Cancel pair) ---
//
// The parent still owns the actual `DELETE` (via the `delete` emit, same as
// before) — only the confirmation gate is new. A card mid-delete failure
// already has an established path: `SpendingBoardView.onDelete` sets this
// card's own `error` prop on a rejected request, same as a failed data load.

const confirmingDelete = ref(false)

const deleteConfirmButtonEl = ref<HTMLButtonElement | null>(null)

function chooseDelete(): void {
  menuOpen.value = false
  confirmingDelete.value = true
  // Same reasoning as chooseRename above: the trigger this click came from
  // is gone the instant confirmingDelete flips true, so focus must be
  // handed somewhere deliberately rather than left to fall to
  // document.body. The Confirm button, not Cancel — unlike ConfirmDialog.vue
  // (a destructive-default-avoidance dialog, where Cancel is focused on
  // open), this is a two-step confirm the owner already chose "Delete" to
  // reach; landing on Confirm lets Enter finish what they started, and
  // Cancel is still one Tab (or Escape via AppPopover, though the popover
  // itself is unmounted here) away.
  void nextTick(() => {
    deleteConfirmButtonEl.value?.focus()
  })
}
function cancelDeleteConfirm(): void {
  confirmingDelete.value = false
}
function confirmDeleteChart(): void {
  confirmingDelete.value = false
  emit('delete')
}
</script>

<template>
  <div class="card flex flex-col gap-3 p-5" data-testid="spending-card">
    <div class="flex items-start justify-between gap-2">
      <span class="min-w-0 flex-1">
        <!-- Inline rename: an editable input replaces the name+link while
             this card is being renamed. Enter saves, Esc cancels. `maxlength`
             matches the backend's own cap (ChartPatch.name, max_length=120,
             src/library/api/spending.py) — sending more than that is a
             guaranteed 422 whose validation-array detail renders as raw
             JSON (api/client.ts's readError JSON.stringifies it), so this
             must never drift ahead of the server's own limit. -->
        <input
          v-if="renaming"
          ref="renameInputEl"
          v-model="renameValue"
          type="text"
          maxlength="120"
          aria-label="Chart name"
          class="form-input w-full text-sm"
          data-testid="spending-card-rename-input"
          :disabled="renameBusy"
          @keydown.enter.prevent="saveRename"
          @keydown.esc.prevent="cancelRename"
        />
        <h3
          v-else
          class="min-w-0 truncate text-sm font-semibold text-gray-800 dark:text-gray-100"
        >
          <RouterLink
            :to="`/charts/${chart.id}`"
            class="hover:underline"
            data-testid="spending-card-name"
          >
            {{ chart.name }}
          </RouterLink>
        </h3>
      </span>

      <div class="flex shrink-0 items-center gap-2">
        <template v-if="renaming">
          <button
            type="button"
            class="text-xs font-medium text-violet-600 transition hover:text-violet-700 disabled:cursor-not-allowed disabled:opacity-50 dark:text-violet-400 dark:hover:text-violet-300"
            data-testid="spending-card-rename-save"
            :disabled="renameBusy || !renameValue.trim()"
            @click="saveRename"
          >
            {{ renameBusy ? 'Saving…' : 'Save' }}
          </button>
          <button
            type="button"
            class="text-xs text-gray-400 transition hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:text-gray-200"
            data-testid="spending-card-rename-cancel"
            :disabled="renameBusy"
            @click="cancelRename"
          >
            Cancel
          </button>
        </template>

        <template v-else-if="confirmingDelete">
          <button
            ref="deleteConfirmButtonEl"
            type="button"
            class="text-xs font-medium text-red-500 transition hover:text-red-600 dark:hover:text-red-400"
            data-testid="spending-card-delete-confirm"
            @click="confirmDeleteChart"
          >
            Confirm
          </button>
          <button
            type="button"
            class="text-xs text-gray-400 transition hover:text-gray-600 dark:hover:text-gray-200"
            data-testid="spending-card-delete-cancel"
            @click="cancelDeleteConfirm"
          >
            Cancel
          </button>
        </template>

        <AppPopover
          v-else
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
            class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent dark:text-gray-200 dark:hover:bg-gray-700/60"
            data-testid="spending-card-move-up"
            :disabled="!canMoveUp"
            @click="chooseMoveUp"
          >
            <svg class="h-3.5 w-3.5 shrink-0 fill-none stroke-current" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M5 15l7-7 7 7" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            Move up
          </button>
          <button
            type="button"
            role="menuitem"
            class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent dark:text-gray-200 dark:hover:bg-gray-700/60"
            data-testid="spending-card-move-down"
            :disabled="!canMoveDown"
            @click="chooseMoveDown"
          >
            <svg class="h-3.5 w-3.5 shrink-0 fill-none stroke-current" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M5 9l7 7 7-7" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            Move down
          </button>
          <div class="my-1 border-t border-gray-200 dark:border-gray-700/60" role="separator" />
          <button
            type="button"
            role="menuitem"
            class="flex w-full items-center rounded-lg px-3 py-2 text-left text-sm text-gray-700 transition hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700/60"
            data-testid="spending-card-rename"
            @click="chooseRename"
          >
            Rename
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

    <p
      v-if="renameError"
      class="text-xs text-red-600 dark:text-red-400"
      data-testid="spending-card-rename-error"
    >
      {{ renameError }}
    </p>

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
