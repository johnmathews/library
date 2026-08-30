<script setup lang="ts">
/**
 * The drill panel body for a footer exclusion bucket (spec §4.5, §4.6).
 *
 * Owns its own fetch of `GET /api/spending/{id}/footer/{bucket}` — the shell
 * never touches it. `FooterDocuments.total` is the bucket's size *before*
 * paging (the server caps a page at `MAX_LIMIT`), so this renders "N of
 * total", never a silently truncated list, and offers a "Show more" action
 * to fetch the next page rather than hiding that more exists.
 *
 * `bucket === 'excluded'` requires `amountKind` (the footer route rejects it
 * without one, spec §2.4's per-`amount_kind` groups) — the other four
 * buckets take no `amount_kind` at all, so it is passed through only when
 * given, never defaulted.
 *
 * The footer route documents no `grain`/`split` query param (it always
 * resolves the chart's default split) — only `from`/`to`/`currency` are
 * read from `args`, so the wider `ChartArgs` this component receives is
 * narrowed rather than spread whole.
 *
 * A failed load surfaces `ApiError.detail` when there is one, exactly like
 * its sibling `DrillCellBody` — a generic "could not load" message would
 * swallow the server's own explanation of what went wrong, which for a real
 * 4xx/5xx here is the only account of the failure this panel shows.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { fetchFooterBucket, MAX_LIMIT, type ChartArgs, type FooterBucket, type FooterDocument } from '@/api/spending'
import { ApiError } from '@/api/client'
import { formatMoney } from '@/spending/money'
import { formatDate } from '@/utils/documentFormat'

const props = defineProps<{
  chartId: number
  bucket: FooterBucket
  amountKind?: string
  args: ChartArgs
}>()

const documents = ref<FooterDocument[]>([])
const total = ref<number | null>(null)
const loading = ref(false)
// Separate from moreError, the same distinction PaymentGroup.vue draws
// between loadError and actionError: a failed INITIAL load means there is
// nothing to show, so it replaces the panel. A failed "Show more" happens
// with a page already on screen, so it renders alongside those (still
// intact) rows rather than wiping them out from under the person who just
// clicked something.
const loadError = ref<string | null>(null)
const moreError = ref<string | null>(null)
const loadingMore = ref(false)

const hasMore = computed(() => total.value !== null && documents.value.length < total.value)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  moreError.value = null
  documents.value = []
  total.value = null
  try {
    const page = await fetchFooterBucket(props.chartId, props.bucket, {
      from: props.args.from,
      to: props.args.to,
      currency: props.args.currency,
      amount_kind: props.amountKind,
      limit: MAX_LIMIT,
      offset: 0,
    })
    documents.value = page.documents
    total.value = page.total
  } catch (err) {
    // Surface the server's own `detail` exactly as the sibling `DrillCellBody`
    // does, rather than swallowing it behind a generic message — its 422s
    // name the actual boundary problem, and an empty panel under a
    // non-empty bucket count would read as "nothing here" when something
    // went wrong instead.
    loadError.value = err instanceof ApiError ? err.detail : 'Could not load these documents.'
  } finally {
    loading.value = false
  }
}

async function loadMore(): Promise<void> {
  if (loadingMore.value || !hasMore.value) return
  loadingMore.value = true
  moreError.value = null
  try {
    const page = await fetchFooterBucket(props.chartId, props.bucket, {
      from: props.args.from,
      to: props.args.to,
      currency: props.args.currency,
      amount_kind: props.amountKind,
      limit: MAX_LIMIT,
      offset: documents.value.length,
    })
    documents.value = [...documents.value, ...page.documents]
    total.value = page.total
  } catch (err) {
    moreError.value = err instanceof ApiError ? err.detail : 'Could not load more of these documents.'
  } finally {
    loadingMore.value = false
  }
}

onMounted(load)
watch(() => [props.chartId, props.bucket, props.amountKind, props.args], load)

function money(amount: string, currency: string | null): string {
  // A null currency is still a real amount (the footer's own bareAmount
  // treatment, SpendingFooter.vue) — drop the prefix rather than guessing.
  return currency === null ? formatMoney(amount, '').trim() : formatMoney(amount, currency)
}
</script>

<template>
  <div data-testid="drill-bucket-body">
    <p v-if="loadError" role="alert" class="text-sm text-red-600 dark:text-red-400" data-testid="drill-error">
      {{ loadError }}
    </p>

    <p v-else-if="loading" class="text-sm text-gray-500 dark:text-gray-400" data-testid="drill-loading">
      Loading…
    </p>

    <template v-else>
      <p v-if="moreError" role="alert" class="mb-3 text-sm text-red-600 dark:text-red-400" data-testid="drill-more-error">
        {{ moreError }}
      </p>

      <p
        v-if="total !== null"
        class="mb-4 text-sm text-gray-500 dark:text-gray-400"
        data-testid="drill-bucket-count"
      >
        {{ documents.length }} of {{ total }}
      </p>

      <ul v-if="documents.length > 0" class="flex flex-col gap-2">
        <li
          v-for="doc in documents"
          :key="doc.id"
          class="flex flex-col gap-1 rounded-lg bg-gray-50 p-3 dark:bg-gray-900/40"
          data-testid="drill-document"
        >
          <div class="flex items-baseline justify-between gap-3">
            <RouterLink :to="`/documents/${doc.id}`" class="min-w-0 truncate font-medium hover:underline">
              {{ doc.title ?? `Document #${doc.id}` }}
            </RouterLink>
            <span
              class="shrink-0 tabular-nums text-gray-800 dark:text-gray-100"
              :data-amount="doc.amount"
              data-testid="drill-document-amount"
            >
              {{ money(doc.amount, doc.currency) }}
            </span>
          </div>
          <p class="text-xs text-gray-500 dark:text-gray-400">
            <span v-if="doc.amount_kind">{{ doc.amount_kind }}</span>
            <span v-if="formatDate(doc.date)"> &middot; {{ formatDate(doc.date) }}</span>
          </p>
        </li>
      </ul>
      <p v-else class="text-sm text-gray-500 dark:text-gray-400" data-testid="drill-empty">
        Nothing in this bucket.
      </p>

      <button
        v-if="hasMore"
        type="button"
        class="btn-sm mt-4 border-gray-200 text-gray-800 hover:border-gray-300 dark:border-gray-700/60 dark:text-gray-300"
        :disabled="loadingMore"
        data-testid="drill-load-more"
        @click="loadMore"
      >
        {{ loadingMore ? 'Loading…' : 'Show more' }}
      </button>
    </template>
  </div>
</template>
