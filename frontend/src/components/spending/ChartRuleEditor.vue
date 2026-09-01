<script setup lang="ts">
/**
 * Edit what a saved chart matches: its rule's clauses and its split axis.
 *
 * ## The invariant that shapes everything here
 *
 * Rows are seeded from `chart.rule.all` **verbatim**. The vocabulary is used
 * only to *label* values and to *offer* them — never to filter them. A chart
 * whose rule names a value that has since been deleted or merged still loads
 * (`GET /api/spending/{id}` validates the rule's shape, not its vocabulary),
 * and this editor is the tool for repairing it. Filtering the rows against the
 * live vocabulary is a one-line change that would silently drop exactly the
 * clause the owner came here to fix, so the unknown value is rendered as a
 * checked, flagged, removable chip instead.
 *
 * ## Owning its own write
 *
 * The component holds the draft rows and issues its own `PATCH`, the way
 * `SpendingCard` owns its rename. That is not a style preference: on a failed
 * save the edited rows must stay on screen, and only the component holding them
 * can guarantee that.
 *
 * ## Preview
 *
 * `postPreview` answers the *edited* rule without saving it, over the window
 * the workspace is currently showing — so the preview and the chart underneath
 * are answering the same question with different rules, which is the
 * comparison the owner needs. It costs no model call.
 *
 * ## Layout
 *
 * The workspace's content column measures 343px at a 375px viewport, so a
 * clause row cannot be three controls wide there. It stacks below
 * `@lg/workspace` (32rem) and becomes a row above it. `@container/workspace` is
 * declared by the view, so these are CONTAINER queries — the column is
 * viewport-minus-sidebar and a `lg:` breakpoint would measure the wrong box.
 * `min-w-0` on the flexible cells is load-bearing: a flex child defaults to
 * `min-width: auto`, which is the usual cause of the horizontal overflow the
 * e2e geometry specs assert against.
 */
import { computed, ref, nextTick, watch } from 'vue'
import { updateChart, postPreview, type Chart, type ChartData, type Grain, type Rule } from '@/api/spending'
import { ApiError } from '@/api/client'
import { useFacetVocabulary } from '@/composables/facetVocabulary'
import { bands, type Band } from '@/spending/palette'
import { ruleSummary } from '@/spending/ruleText'
import { AppButton, AppCheckboxes, AppSelect, FilterPill } from '@/components/app'
import type { ChoiceItem, SelectItem } from '@/components/app/types'
import SpendingChart from './SpendingChart.vue'
import SpendingLegend from './SpendingLegend.vue'

const props = defineProps<{
  chart: Chart
  /** The window the workspace is showing, so the preview answers it too. */
  grain: Grain
  from: string
  to: string
  currency: string
}>()

const emit = defineEmits<{ saved: [Chart]; cancel: [] }>()

const { facets, ensureLoaded } = useFacetVocabulary()
void ensureLoaded()

interface ClauseRow {
  /** Monotonic, never the index and never the facet key: two rows may share a
      facet mid-edit, and a reordering key would re-use DOM across rows. */
  id: number
  facet: string
  op: 'in' | 'not_in'
  values: string[]
}

let nextRowId = 0
const rows = ref<ClauseRow[]>(
  props.chart.rule.all.map((clause) => ({
    id: nextRowId++,
    facet: clause.facet,
    op: clause.op,
    values: [...clause.values],
  })),
)
const splitDraft = ref<string>(props.chart.default_split ?? '')

/** One values pill open at a time, as the document filter bar does. */
const openPill = ref<number | null>(null)

const previewData = ref<ChartData | null>(null)
const previewBusy = ref(false)
const previewError = ref('')
const saveBusy = ref(false)
const saveError = ref('')
const confirmingEmpty = ref(false)

const facetItems = computed<SelectItem[]>(() =>
  facets.value.map((facet) => ({ value: facet.key, text: facet.label })),
)

const splitItems = computed<SelectItem[]>(() => [
  { value: '', text: 'No split' },
  ...facetItems.value,
])

/**
 * A row's facet options. When the row names a facet the vocabulary no longer
 * has, that key is offered as a DISABLED option so the select tells the truth
 * about what is stored rather than silently showing some other facet.
 */
function facetItemsFor(row: ClauseRow): SelectItem[] {
  if (row.facet === '' || facets.value.some((facet) => facet.key === row.facet)) {
    return facetItems.value
  }
  return [
    { value: row.facet, text: `${row.facet} (no longer in the vocabulary)`, disabled: true },
    ...facetItems.value,
  ]
}

/**
 * A row's value options: the facet's live values, plus any value the row
 * already carries that the vocabulary has lost. The second half is the whole
 * point — `AppCheckboxes` reports an item checked because it is in the model,
 * so a lost value shows up ticked, explained by its hint, and removable.
 */
function valueItemsFor(row: ClauseRow): ChoiceItem[] {
  const facet = facets.value.find((candidate) => candidate.key === row.facet)
  const live = facet?.values ?? []
  const items: ChoiceItem[] = live.map((value) => ({ value: value.key, text: value.label }))
  for (const value of row.values) {
    if (!live.some((candidate) => candidate.key === value)) {
      items.push({ value, text: value, hint: 'No longer in the vocabulary' })
    }
  }
  return items
}

function unknownValuesFor(row: ClauseRow): string[] {
  const facet = facets.value.find((candidate) => candidate.key === row.facet)
  const live = facet?.values ?? []
  return row.values.filter((value) => !live.some((candidate) => candidate.key === value))
}

/** Every key in the draft the vocabulary cannot resolve — facets and values. */
const unknownKeys = computed<string[]>(() => {
  const keys: string[] = []
  for (const row of rows.value) {
    if (row.facet !== '' && !facets.value.some((facet) => facet.key === row.facet)) {
      keys.push(row.facet)
    }
    keys.push(...unknownValuesFor(row))
  }
  return [...new Set(keys)]
})

/** Rows that cannot become a clause yet, so they are excluded rather than sent. */
function isIncomplete(row: ClauseRow): boolean {
  return row.facet === '' || row.values.length === 0
}

const draftRule = computed<Rule>(() => ({
  all: rows.value
    .filter((row) => !isIncomplete(row))
    .map((row) => ({ facet: row.facet, op: row.op, values: [...row.values] })),
}))

const summary = computed(() => ruleSummary(draftRule.value, facets.value))

/**
 * Two `is` rows on one facet. A document carries at most one value per facet,
 * so the rule can never match anything and the chart would read "you spent
 * nothing". Warned about rather than merged: merging turns the AND into an OR,
 * which answers a different question from the one being asked.
 */
const conflictFacet = computed<string | null>(() => {
  const seen = new Set<string>()
  for (const row of rows.value) {
    if (row.op !== 'in' || row.facet === '') continue
    if (seen.has(row.facet)) return row.facet
    seen.add(row.facet)
  }
  return null
})

const conflictLabel = computed(() => {
  const key = conflictFacet.value
  if (key === null) return ''
  return facets.value.find((facet) => facet.key === key)?.label ?? key
})

const isEmptyRule = computed(() => draftRule.value.all.length === 0)
const previewBands = computed<Band[]>(() =>
  previewData.value ? bands(previewData.value.splits, previewData.value.cells) : [],
)

// An empty draft has to be re-confirmed each time it is reached, not once per
// editor session.
watch(isEmptyRule, (empty) => {
  if (!empty) confirmingEmpty.value = false
})

function addRow(): void {
  const row: ClauseRow = { id: nextRowId++, facet: '', op: 'in', values: [] }
  rows.value = [...rows.value, row]
  void nextTick(() => focusById(`rule-row-${row.id}-facet`))
}

function removeRow(index: number): void {
  const previous = rows.value[index - 1]
  rows.value = rows.value.filter((_, position) => position !== index)
  void nextTick(() => {
    if (previous) focusById(`rule-row-${previous.id}-remove`)
    else focusById('rule-add-clause')
  })
}

function focusById(id: string): void {
  const element = document.querySelector<HTMLElement>(`[data-testid="${id}"]`)
  element?.focus()
}

/** Values are facet-scoped, so a facet change invalidates them. Keeping them
    would send a value the API matches by exact key and rejects with a 422. */
function onFacetChange(row: ClauseRow): void {
  row.values = []
}

async function runPreview(): Promise<void> {
  if (previewBusy.value) return
  previewBusy.value = true
  previewError.value = ''
  try {
    previewData.value = await postPreview({
      rule: draftRule.value,
      display_currency: props.currency,
      grain: props.grain,
      split: splitDraft.value || null,
      from: props.from || undefined,
      to: props.to || undefined,
    })
  } catch (error) {
    previewError.value =
      error instanceof ApiError ? error.detail : 'Could not preview this rule.'
  } finally {
    previewBusy.value = false
  }
}

async function apply(): Promise<void> {
  if (saveBusy.value) return
  if (isEmptyRule.value && !confirmingEmpty.value) {
    confirmingEmpty.value = true
    return
  }
  saveBusy.value = true
  saveError.value = ''
  try {
    // `default_split` is sent explicitly, including as null: ChartPatch is a
    // Partial, so omitting the key leaves the old axis in place and clearing
    // the split would be a silent no-op.
    const updated = await updateChart(props.chart.id, {
      rule: draftRule.value,
      default_split: splitDraft.value || null,
    })
    emit('saved', updated)
  } catch (error) {
    // Never reset the rows here: a failed save must leave the edit on screen.
    saveError.value = error instanceof ApiError ? error.detail : 'Could not save this rule.'
  } finally {
    saveBusy.value = false
  }
}
</script>

<template>
  <section class="card p-4 flex flex-col gap-4" data-testid="chart-rule-editor">
    <div>
      <h2 class="text-sm font-semibold text-gray-900 dark:text-gray-100">What this chart matches</h2>
      <p class="text-sm text-gray-500 dark:text-gray-400" data-testid="rule-editor-summary">
        {{ summary }}
      </p>
    </div>

    <div class="w-full @lg/workspace:w-64">
      <AppSelect
        id="rule-editor-split"
        v-model="splitDraft"
        label="Split by"
        testid="rule-editor-split"
        :items="splitItems"
      />
    </div>

    <ul class="flex flex-col gap-3" data-testid="rule-editor-rows">
      <li
        v-for="(row, index) in rows"
        :key="row.id"
        class="flex flex-col gap-2 @lg/workspace:flex-row @lg/workspace:items-end @lg/workspace:gap-3"
        data-testid="rule-editor-row"
      >
        <div class="min-w-0 flex-1">
          <AppSelect
            :id="`rule-row-${row.id}-facet`"
            v-model="row.facet"
            label="Filter on"
            :testid="`rule-row-${row.id}-facet`"
            :items="facetItemsFor(row)"
            @update:model-value="onFacetChange(row)"
          />
        </div>

        <div class="@lg/workspace:w-32 shrink-0">
          <AppSelect
            :id="`rule-row-${row.id}-op`"
            v-model="row.op"
            label="Match"
            :testid="`rule-row-${row.id}-op`"
            :items="[
              { value: 'in', text: 'is' },
              { value: 'not_in', text: 'is not' },
            ]"
          />
        </div>

        <div class="min-w-0">
          <FilterPill
            :label="`Values${row.values.length ? ` (${row.values.length})` : ''}`"
            :open="openPill === row.id"
            :active="row.values.length > 0"
            @update:open="openPill = $event ? row.id : null"
          >
            <AppCheckboxes
              :id="`rule-row-${row.id}-values`"
              v-model="row.values"
              legend="Values"
              legend-size="s"
              small
              :items="valueItemsFor(row)"
            />
          </FilterPill>
          <p
            v-if="unknownValuesFor(row).length"
            class="text-sm text-amber-700 dark:text-amber-300"
            data-testid="rule-row-unknown-value"
          >
            No longer in the vocabulary: {{ unknownValuesFor(row).join(', ') }}
          </p>
        </div>

        <button
          type="button"
          class="shrink-0 self-end text-sm text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400"
          :data-testid="`rule-row-${row.id}-remove`"
          :aria-label="`Remove filter ${index + 1}`"
          @click="removeRow(index)"
        >
          Remove
        </button>
      </li>
    </ul>

    <p class="sr-only" aria-live="polite" data-testid="rule-editor-row-count">
      {{ rows.length }} filter{{ rows.length === 1 ? '' : 's' }}
    </p>

    <div>
      <AppButton size="sm" variant="secondary" data-testid="rule-add-clause" @click="addRow">
        Add a filter
      </AppButton>
    </div>

    <p
      v-if="conflictFacet"
      class="text-sm text-amber-700 dark:text-amber-300"
      data-testid="rule-editor-conflict-warning"
    >
      Two “is” filters both match on {{ conflictLabel }}. A document has only one
      {{ conflictLabel }}, so nothing can satisfy both.
    </p>

    <p
      v-if="unknownKeys.length"
      class="text-sm text-amber-700 dark:text-amber-300"
      data-testid="rule-editor-unknown-warning"
    >
      Not in the vocabulary any more: {{ unknownKeys.join(', ') }}. Remove or
      replace them before saving.
    </p>

    <div class="flex flex-wrap items-center gap-3">
      <AppButton
        size="sm"
        variant="secondary"
        data-testid="rule-editor-preview"
        :disabled="previewBusy"
        @click="runPreview"
      >
        {{ previewBusy ? 'Previewing…' : 'Preview' }}
      </AppButton>
      <AppButton
        size="sm"
        data-testid="rule-editor-apply"
        :disabled="saveBusy"
        @click="apply"
      >
        {{ confirmingEmpty ? 'Apply anyway' : 'Apply' }}
      </AppButton>
      <button
        type="button"
        class="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
        data-testid="rule-editor-cancel"
        @click="emit('cancel')"
      >
        Cancel
      </button>
    </div>

    <p
      v-if="confirmingEmpty"
      class="text-sm text-amber-700 dark:text-amber-300"
      data-testid="rule-editor-empty-warning"
    >
      This chart has no filters left, so it will match all spending in the
      archive. Press Apply again to confirm.
    </p>

    <p
      v-if="previewError"
      class="text-sm text-red-600 dark:text-red-400"
      data-testid="rule-editor-preview-error"
    >
      {{ previewError }}
    </p>
    <p
      v-if="saveError"
      class="text-sm text-red-600 dark:text-red-400"
      data-testid="rule-editor-save-error"
    >
      {{ saveError }}
    </p>

    <div v-if="previewData" data-testid="rule-editor-preview-region">
      <p class="text-sm text-gray-500 dark:text-gray-400">
        This rule would total
        <span data-testid="rule-editor-preview-total">{{ previewData.total }}</span>
        {{ previewData.currency }}.
      </p>
      <div class="h-40 w-full">
        <SpendingChart :data="previewData" :bands="previewBands" @cell="() => {}" />
      </div>
      <SpendingLegend
        :bands="previewBands"
        :hidden="new Set()"
        :currency="previewData.currency"
        compact
      />
    </div>
  </section>
</template>
