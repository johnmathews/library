<script setup lang="ts">
/**
 * The drill panel body for a single bar (period × split value), spec §4.6.
 *
 * Owns its own fetch — the shell (`SpendingDrillPanel.vue`) never touches
 * `/cell`. Three contracts this component must hold, each traced to a
 * defect the engine's own review found:
 *
 * 1. `args` is `/data`'s echoed `grain`/`split`/`currency`/`since`→`from`/
 *    `until`→`to`, sent back to `/cell` verbatim alongside the cell's own
 *    `period` (never a user-picked date) — that round-trip is what proves
 *    the panel answers the same question the bar did. `cellArgs(data)`
 *    already builds `args`; this component does not rebuild or narrow it.
 * 2. An off-boundary `period` is a 422 whose `detail` names the correct
 *    boundary. That detail is rendered as the panel's content — an empty
 *    panel under a non-empty bar would read as "you spent nothing here",
 *    which is the silence this whole feature exists to remove.
 * 3. `CellPaymentOut.total` (`payments[].total`) is the only figure that
 *    sums to the bar; `payments[].total` values themselves are apportioned
 *    to sum exactly to `CellOutBody.total`, so listing and summing THOSE is
 *    safe. `documents[].amount` is never summed to reconstruct anything — a
 *    merged pair doubles it, a group member outside the period is still
 *    listed, and an unconvertible member is listed but not counted.
 *
 * `CellDocument.amount` / `.currency` are optional: a hand-made MERGE
 * override can pull an amountless document into a group, and that is
 * precisely the merge this panel exists to expose, so such a document is
 * still rendered, with "No amount recorded" standing in for the missing
 * figure rather than the row being dropped.
 *
 * The facet editor and the payment/merge controls already exist
 * (`FacetEditor.vue`, `PaymentGroup.vue`) and are reused inline per
 * document rather than reimplemented — the correction belongs where the
 * problem is noticed.
 */
import { onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { fetchCell, type CellBody, type ChartArgs } from '@/api/spending'
import { ApiError } from '@/api/client'
import { fetchDocumentLabels } from '@/api/facets'
import { useFacetVocabulary } from '@/composables/facetVocabulary'
import { formatMoney } from '@/spending/money'
import { formatDate } from '@/utils/documentFormat'
import FacetEditor from '@/components/facets/FacetEditor.vue'
import PaymentGroup from '@/components/payments/PaymentGroup.vue'

const props = defineProps<{
  chartId: number
  period: string
  splitValue: string | null
  args: ChartArgs
  chartName: string
}>()

const cell = ref<CellBody | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  cell.value = null
  try {
    cell.value = await fetchCell(props.chartId, props.period, props.splitValue, props.args)
  } catch (err) {
    // The 422 `detail` names the correct period boundary — that is the
    // whole point of surfacing it rather than falling back to a generic
    // "could not load" message or, worse, an empty state (contract #2).
    error.value = err instanceof ApiError ? err.detail : 'Could not load this cell.'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => [props.chartId, props.period, props.splitValue, props.args], load)

// The controlled facet vocabulary doesn't vary per document, and this is not
// the only consumer on the screen: the workspace's rule editor needs it too.
// Sharing one cache means one request and — the part that matters — ONE
// snapshot, so a value merged away mid-session cannot exist here and not
// there. Best-effort as before: FacetEditor renders no facets on failure.
const { facets, ensureLoaded: ensureVocabulary } = useFacetVocabulary()
onMounted(ensureVocabulary)

// Per-document labels, fetched lazily as documents appear in a loaded cell.
const labelsByDoc = ref<Record<number, Record<string, string>>>({})

async function ensureLabels(docId: number): Promise<void> {
  if (docId in labelsByDoc.value) return
  try {
    const labels = await fetchDocumentLabels(docId)
    labelsByDoc.value = { ...labelsByDoc.value, [docId]: labels }
  } catch {
    labelsByDoc.value = { ...labelsByDoc.value, [docId]: {} }
  }
}

watch(cell, (next) => {
  if (!next) return
  for (const payment of next.payments) {
    for (const doc of payment.documents) void ensureLabels(doc.id)
  }
})

function labelsFor(docId: number): Record<string, string> {
  return labelsByDoc.value[docId] ?? {}
}

function onSaved(docId: number, saved: Record<string, string>): void {
  labelsByDoc.value = { ...labelsByDoc.value, [docId]: saved }
}

function money(amount: string, currency: string | null): string {
  return formatMoney(amount, currency ?? props.args.currency ?? '')
}
</script>

<template>
  <div data-testid="drill-cell-body">
    <p class="mb-4 text-sm text-gray-500 dark:text-gray-400" data-testid="drill-cell-context">
      {{ chartName }}<span v-if="cell?.label">&nbsp;&middot; {{ cell.label }}</span>
    </p>

    <p v-if="error" role="alert" class="text-sm text-red-600 dark:text-red-400" data-testid="drill-error">
      {{ error }}
    </p>

    <p v-else-if="loading" class="text-sm text-gray-500 dark:text-gray-400" data-testid="drill-loading">
      Loading…
    </p>

    <template v-else-if="cell">
      <p
        class="mb-4 text-lg font-semibold tabular-nums text-gray-800 dark:text-gray-100"
        :data-amount="cell.total"
        data-testid="drill-cell-total"
      >
        {{ money(cell.total, args.currency ?? null) }}
      </p>

      <ul v-if="cell.payments.length > 0" class="flex flex-col gap-5">
        <li
          v-for="payment in cell.payments"
          :key="payment.payment_id"
          class="flex flex-col gap-3 border-t border-gray-200 pt-4 first:border-t-0 first:pt-0 dark:border-gray-700/60"
          data-testid="drill-payment"
        >
          <p
            class="text-sm font-medium tabular-nums text-gray-800 dark:text-gray-100"
            :data-amount="payment.total"
            data-testid="drill-payment-total"
          >
            {{ money(payment.total, args.currency ?? null) }}
          </p>

          <div
            v-for="doc in payment.documents"
            :key="doc.id"
            class="flex flex-col gap-2 rounded-lg bg-gray-50 p-3 dark:bg-gray-900/40"
            data-testid="drill-document"
          >
            <div class="flex items-baseline justify-between gap-3">
              <RouterLink :to="`/documents/${doc.id}`" class="min-w-0 truncate font-medium hover:underline">
                {{ doc.title ?? `Document #${doc.id}` }}
              </RouterLink>
              <span
                class="shrink-0 tabular-nums text-gray-800 dark:text-gray-100"
                :data-amount="doc.amount ?? undefined"
                data-testid="drill-document-amount"
              >
                <template v-if="doc.amount !== null">{{ money(doc.amount, doc.currency) }}</template>
                <template v-else>No amount recorded</template>
              </span>
            </div>
            <p class="text-xs text-gray-500 dark:text-gray-400">
              <span v-if="doc.amount_kind">{{ doc.amount_kind }}</span>
              <span v-if="formatDate(doc.date)"> &middot; {{ formatDate(doc.date) }}</span>
              <span v-if="doc.reference"> &middot; {{ doc.reference }}</span>
              <span v-if="!doc.is_canonical"> &middot; not the canonical document for this payment</span>
            </p>

            <FacetEditor
              :document-id="doc.id"
              :facets="facets"
              :labels="labelsFor(doc.id)"
              @saved="onSaved(doc.id, $event)"
            />
            <PaymentGroup :document-id="doc.id" />
          </div>
        </li>
      </ul>

      <p v-else class="text-sm text-gray-500 dark:text-gray-400" data-testid="drill-empty">
        No payments in this period.
      </p>
    </template>
  </div>
</template>
