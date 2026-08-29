<script setup lang="ts">
/**
 * "This and N other documents describe one payment" (docs/money-facts.md).
 *
 * Roughly a quarter of this archive's amount-bearing documents are one real
 * payment documented twice — an emailed invoice and a downloaded receipt, a
 * booking confirmation and its payment confirmation. This card is what makes
 * that collapse visible on the document it happened to, and offers the split
 * that corrects it when the rules got it wrong.
 *
 * Renders nothing when the group is just this document: a "1 document" panel
 * on every page would be noise. It appears only when there is a collapse to
 * explain. A load failure is surfaced rather than swallowed into an empty
 * panel — staying silent here would defeat the point of the component.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { fetchPayment, splitPayment, type PaymentRef } from '@/api/payments'
import { formatDate } from '@/utils/documentFormat'

const props = defineProps<{ documentId: number }>()

const payment = ref<PaymentRef | null>(null)
// Separate from actionError: a load failure means there is nothing to show,
// so it replaces the panel. A split failure happens with a group already on
// screen, so it is shown alongside the (still-intact) rows rather than
// wiping them out from under the person who just clicked something.
const loadError = ref<string | null>(null)
const actionError = ref<string | null>(null)
const splittingId = ref<number | null>(null)

const collapsed = computed<boolean>(() => (payment.value?.documents.length ?? 0) > 1)

async function load(): Promise<void> {
  loadError.value = null
  actionError.value = null
  payment.value = null
  try {
    payment.value = await fetchPayment(props.documentId)
  } catch {
    loadError.value = 'Could not load this payment.'
  }
}

async function split(otherId: number): Promise<void> {
  if (splittingId.value !== null) return
  splittingId.value = otherId
  actionError.value = null
  try {
    payment.value = await splitPayment(props.documentId, otherId)
  } catch {
    actionError.value = 'Could not split these documents. Try again.'
  } finally {
    splittingId.value = null
  }
}

onMounted(load)
watch(() => props.documentId, load)
</script>

<template>
  <p
    v-if="loadError"
    role="alert"
    class="text-sm text-red-600 dark:text-red-400"
    data-testid="payment-error"
  >
    {{ loadError }}
  </p>

  <div v-else-if="collapsed" class="card p-5 @container" data-testid="payment-group">
    <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-1">Payment</h2>
    <p class="text-sm text-gray-500 dark:text-gray-400 mb-3">
      One payment, documented across {{ payment!.documents.length }} documents.
    </p>
    <p v-if="actionError" role="alert" class="text-sm text-red-600 dark:text-red-400 mb-3">
      {{ actionError }}
    </p>
    <ul class="space-y-2">
      <li
        v-for="doc in payment!.documents"
        :key="doc.id"
        data-testid="payment-group-row"
        class="flex flex-col gap-2 border-t border-gray-100 pt-2 first:border-t-0 first:pt-0 @sm:flex-row @sm:items-center @sm:justify-between dark:border-gray-700/60"
      >
        <div class="min-w-0">
          <RouterLink :to="`/documents/${doc.id}`" class="truncate font-medium hover:underline">
            {{ doc.title ?? `Document #${doc.id}` }}
          </RouterLink>
          <p class="text-xs text-gray-500 dark:text-gray-400">
            <span>{{ doc.amount_kind ?? 'unclassified' }}</span>
            <span v-if="formatDate(doc.document_date)"> · {{ formatDate(doc.document_date) }}</span>
          </p>
        </div>
        <button
          v-if="doc.id !== documentId"
          type="button"
          class="btn-sm shrink-0 self-start border-gray-200 text-gray-800 hover:border-gray-300 @sm:self-auto dark:border-gray-700/60 dark:text-gray-300"
          data-testid="payment-split"
          :disabled="splittingId !== null"
          @click="split(doc.id)"
        >
          {{ splittingId === doc.id ? 'Splitting…' : 'Not the same payment' }}
        </button>
      </li>
    </ul>
  </div>
</template>
