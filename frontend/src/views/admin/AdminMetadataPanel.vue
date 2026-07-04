<script setup lang="ts">
/**
 * Metadata tab: senders / recipients / kinds management (each a shared
 * {@link TaxonomyCrudPanel} driven by an entity descriptor), plus currency
 * normalisation and FX-rate seeding (kept inline — they are whole-store
 * operations, not per-row CRUD).
 *
 * Loads LAZILY: the parent passes `active` (true when the Metadata tab is
 * open). The taxonomy panels self-load on the first false→true transition; the
 * currency + FX lists load here in the same way, guarded by *Loaded flags.
 */
import { computed, reactive, ref, watch } from 'vue'
import { AppBadge, AppButton, AppSelect } from '@/components/app'
import type { SelectItem } from '@/components/app'
import { ApiError } from '@/api/client'
import {
  createRecipient,
  createSender,
  deleteRecipient,
  deleteSender,
  deleteKind,
  listCurrencies,
  listFxRates,
  listRecipients,
  normalizeCurrency,
  renameKind,
  renameRecipient,
  renameSender,
  seedFxRate,
  type CurrencyConflictItem,
  type CurrencyInUse,
  type CurrencyNormalizeResult,
  type FxRateStatus,
  type RecipientOption,
} from '@/api/admin'
import {
  createKind,
  listKinds,
  listSenders,
  type KindOption,
  type SenderOption,
} from '@/api/taxonomy'
import TaxonomyCrudPanel from './TaxonomyCrudPanel.vue'
import type { TaxonomyDescriptor, TaxonomyMergeTarget } from './taxonomyCrud'

const props = defineProps<{ active: boolean }>()

const cardClass = 'card p-6'

// --- Taxonomy descriptors ---------------------------------------------------
// Senders/recipients are id-keyed with rename-merge; kinds are slug-keyed with
// no merge (a name collision is a hard 409). The shared panel branches on
// `hasMerge` and uses `parseReassign`/`readMergeBody` for the remaining splits.

/** Read the 409 rename-merge body — shared by the two id-keyed entities. */
function readIdMergeBody(body: Record<string, unknown>): TaxonomyMergeTarget {
  return {
    target_id: Number(body.target_id),
    target_name: String(body.target_name),
    target_document_count: Number(body.target_document_count),
  }
}

const senderDescriptor: TaxonomyDescriptor<SenderOption> = {
  testid: 'sender',
  heading: 'Senders',
  addLabel: 'Add sender',
  renameLabel: 'Sender name',
  clearText: 'None (clear sender)',
  noun: 'sender',
  hasMerge: true,
  keyOf: (row) => row.id,
  list: listSenders,
  create: createSender,
  rename: (key, name, merge) => renameSender(key as number, name, merge),
  remove: (key, reassignTo) =>
    reassignTo === undefined
      ? deleteSender(key as number)
      : deleteSender(key as number, reassignTo as number | null),
  parseReassign: (value) => (value === '' ? null : Number(value)),
  readMergeBody: readIdMergeBody,
}

const recipientDescriptor: TaxonomyDescriptor<RecipientOption> = {
  testid: 'recipient',
  heading: 'Recipients',
  addLabel: 'Add recipient',
  renameLabel: 'Recipient name',
  clearText: 'None (clear recipient)',
  noun: 'recipient',
  hasMerge: true,
  keyOf: (row) => row.id,
  list: listRecipients,
  create: createRecipient,
  rename: (key, name, merge) => renameRecipient(key as number, name, merge),
  remove: (key, reassignTo) =>
    reassignTo === undefined
      ? deleteRecipient(key as number)
      : deleteRecipient(key as number, reassignTo as number | null),
  parseReassign: (value) => (value === '' ? null : Number(value)),
  readMergeBody: readIdMergeBody,
}

const kindDescriptor: TaxonomyDescriptor<KindOption> = {
  testid: 'kind',
  heading: 'Kinds',
  addLabel: 'Add kind',
  renameLabel: 'Kind name',
  clearText: 'None (clear kind)',
  noun: 'kind',
  hasMerge: false,
  keyOf: (row) => row.slug,
  list: listKinds,
  create: createKind,
  rename: (key, name) => renameKind(key as string, name),
  remove: (key, reassignTo) =>
    reassignTo === undefined
      ? deleteKind(key as string)
      : deleteKind(key as string, reassignTo as string | null),
  parseReassign: (value) => (value === '' ? null : value),
}

// --- Metadata: currencies ---------------------------------------------------
// Currency is free-text (no reference table) but part of series identity, so
// "normalise" is a whole-store rewrite, not a per-row edit (see docs/api.md
// §1.18.6). There is a confirm step because the series-insight cache merge drops
// rows (they regenerate) and the rename spans every document.
const currencies = ref<CurrencyInUse[]>([])
const currenciesLoading = ref(false)
const currenciesLoaded = ref(false)
const currenciesError = ref<string | null>(null)

const normalizeFrom = ref('')
const normalizeTo = ref('')
const normalizeConfirming = ref(false)
const normalizePending = ref(false)
const normalizeError = ref<string | null>(null)
const normalizeConflicts = ref<CurrencyConflictItem[]>([])
const normalizeResult = ref<CurrencyNormalizeResult | null>(null)

const currencyItems = computed<SelectItem[]>(() => [
  { value: '', text: 'Select a code…' },
  ...currencies.value.map((c) => ({ value: c.code, text: `${c.code} (${c.document_count})` })),
])

async function loadCurrencies(): Promise<void> {
  currenciesLoading.value = true
  currenciesError.value = null
  try {
    currencies.value = await listCurrencies()
    currenciesLoaded.value = true
  } catch {
    currenciesError.value = 'Could not load currencies. Try refreshing the page.'
  } finally {
    currenciesLoading.value = false
  }
}

function startNormalize(): void {
  normalizeError.value = null
  normalizeConflicts.value = []
  normalizeResult.value = null
  if (!normalizeFrom.value || !normalizeTo.value.trim()) {
    normalizeError.value = 'Choose a source code and enter a 3-letter target code.'
    return
  }
  normalizeConfirming.value = true
}

function cancelNormalize(): void {
  normalizeConfirming.value = false
}

async function confirmNormalize(): Promise<void> {
  normalizePending.value = true
  normalizeError.value = null
  normalizeConflicts.value = []
  try {
    const result = await normalizeCurrency(normalizeFrom.value, normalizeTo.value.trim())
    normalizeResult.value = result
    normalizeConfirming.value = false
    normalizeFrom.value = ''
    normalizeTo.value = ''
    await loadCurrencies()
    await loadFxRates()
  } catch (error) {
    normalizeConfirming.value = false
    if (error instanceof ApiError && error.status === 409 && error.body) {
      normalizeConflicts.value = (error.body.conflicts as CurrencyConflictItem[]) ?? []
      normalizeError.value = error.detail
    } else {
      normalizeError.value =
        error instanceof ApiError ? error.detail : 'Could not normalise the currency. Try again.'
    }
  } finally {
    normalizePending.value = false
  }
}

// --- Metadata: FX rates -----------------------------------------------------
// Conversion needs one fx_rates row per currency; this subsection seeds them.
// A "Fetch rate" button pulls the live USD-per-unit rate; if the provider is
// down or lacks the code, a manual-entry fallback lets the admin type a rate.
const fxRates = ref<FxRateStatus[]>([])
const fxLoading = ref(false)
const fxLoaded = ref(false)
const fxError = ref<string | null>(null)
// The code currently being seeded (disables its row's buttons).
const fxBusyCode = ref<string | null>(null)
// Per-code UI state: manual-entry form open, its typed rate, and any error.
const fxManualOpen = reactive<Record<string, boolean>>({})
const fxManualRate = reactive<Record<string, string>>({})
const fxRowError = reactive<Record<string, string>>({})

async function loadFxRates(): Promise<void> {
  fxLoading.value = true
  fxError.value = null
  try {
    fxRates.value = await listFxRates()
    fxLoaded.value = true
  } catch {
    fxError.value = 'Could not load FX rates. Try refreshing the page.'
  } finally {
    fxLoading.value = false
  }
}

async function fetchFxLive(code: string): Promise<void> {
  fxBusyCode.value = code
  fxRowError[code] = ''
  try {
    await seedFxRate({ currency: code, source: 'live' })
    await loadFxRates()
  } catch (error) {
    fxRowError[code] =
      error instanceof ApiError
        ? `${error.detail} — you can enter a rate manually.`
        : 'Could not fetch a rate. Enter one manually.'
    fxManualOpen[code] = true
  } finally {
    fxBusyCode.value = null
  }
}

function toggleFxManual(code: string): void {
  fxManualOpen[code] = !fxManualOpen[code]
  fxRowError[code] = ''
}

async function seedFxManual(code: string): Promise<void> {
  // A number-typed input can yield a number or string; normalise to a string.
  const rate = String(fxManualRate[code] ?? '').trim()
  if (!rate || !(Number(rate) > 0)) {
    fxRowError[code] = 'Enter a positive rate (USD per one unit).'
    return
  }
  fxBusyCode.value = code
  fxRowError[code] = ''
  try {
    await seedFxRate({ currency: code, source: 'manual', rateToBase: rate })
    fxManualOpen[code] = false
    fxManualRate[code] = ''
    await loadFxRates()
  } catch (error) {
    fxRowError[code] =
      error instanceof ApiError ? error.detail : 'Could not save the rate. Try again.'
  } finally {
    fxBusyCode.value = null
  }
}

// Lazily load the currency + FX lists the first time the Metadata tab is opened
// (the taxonomy panels self-load via their own `active` prop).
watch(
  () => props.active,
  (active) => {
    if (!active) return
    if (!currenciesLoaded.value && !currenciesLoading.value) void loadCurrencies()
    if (!fxLoaded.value && !fxLoading.value) void loadFxRates()
  },
)
</script>

<template>
  <div class="space-y-6">
    <TaxonomyCrudPanel :descriptor="senderDescriptor" :active="props.active" />
    <TaxonomyCrudPanel :descriptor="recipientDescriptor" :active="props.active" />
    <TaxonomyCrudPanel :descriptor="kindDescriptor" :active="props.active" />

    <!-- Currencies -->
    <div :class="cardClass">
      <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-1">Currencies</h2>
      <p class="mb-4 text-sm text-gray-500 dark:text-gray-400">
        Currency codes aren't a reference table, but they're part of series
        identity. Normalising rewrites a code across every document and series
        — merging duplicate cached insights (they regenerate) and refusing if
        it would collide with your series overrides.
      </p>

      <p
        v-if="currenciesLoading"
        data-testid="currencies-loading"
        class="text-sm text-gray-500 dark:text-gray-400"
      >
        Loading…
      </p>
      <p
        v-else-if="currenciesError"
        data-testid="currencies-error"
        class="text-sm text-red-600 dark:text-red-400"
      >
        {{ currenciesError }}
      </p>

      <template v-else>
        <!-- Normalise form -->
        <div class="mb-4 flex flex-wrap items-end gap-2">
          <AppSelect
            id="currency-normalize-from"
            v-model="normalizeFrom"
            label="From"
            :items="currencyItems"
            data-testid="currency-normalize-from"
          />
          <div>
            <label
              for="currency-normalize-to"
              class="block text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"
            >
              To
            </label>
            <input
              id="currency-normalize-to"
              v-model="normalizeTo"
              type="text"
              maxlength="3"
              autocomplete="off"
              placeholder="EUR"
              class="form-input w-24 uppercase"
              data-testid="currency-normalize-to"
            />
          </div>
          <AppButton
            type="button"
            data-testid="currency-normalize-button"
            :disabled="normalizePending"
            @click="startNormalize()"
          >
            Normalise
          </AppButton>
        </div>

        <!-- Confirm step -->
        <div
          v-if="normalizeConfirming"
          data-testid="currency-normalize-confirm-box"
          class="mb-4 border-l-4 border-amber-500 bg-amber-50 dark:bg-amber-500/10 rounded-lg px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
        >
          <p class="mb-2">
            Rename <strong>{{ normalizeFrom }}</strong> to
            <strong>{{ normalizeTo.toUpperCase() }}</strong> across all documents and series?
            Duplicate cached insights are merged; this isn't a per-document undo.
          </p>
          <div class="flex gap-2">
            <AppButton
              type="button"
              data-testid="currency-normalize-confirm"
              :disabled="normalizePending"
              @click="confirmNormalize()"
            >
              {{ normalizePending ? 'Normalising…' : 'Confirm' }}
            </AppButton>
            <AppButton
              type="button"
              variant="secondary"
              data-testid="currency-normalize-cancel"
              @click="cancelNormalize()"
            >
              Cancel
            </AppButton>
          </div>
        </div>

        <!-- Result summary -->
        <div
          v-if="normalizeResult"
          data-testid="currency-normalize-result"
          class="mb-4 rounded-lg border border-green-200 bg-green-50 dark:border-green-500/30 dark:bg-green-500/10 px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
        >
          <p>
            Renamed <strong>{{ normalizeResult.from_code }}</strong> →
            <strong>{{ normalizeResult.to_code }}</strong> ·
            {{ normalizeResult.counts.documents ?? 0 }} document(s).
          </p>
          <p
            v-if="normalizeResult.fx_rate_missing"
            data-testid="currency-fx-warning"
            class="mt-1 text-amber-700 dark:text-amber-400"
          >
            No FX rate exists for {{ normalizeResult.to_code }} — FX conversion for it is
            unavailable until a rate is seeded. Seed one in the FX rates section below.
          </p>
        </div>

        <!-- Override-collision refusal -->
        <div
          v-if="normalizeConflicts.length"
          data-testid="currency-conflict"
          class="mb-4 rounded-lg border border-red-200 bg-red-50 dark:border-red-500/30 dark:bg-red-500/10 px-3 py-2 text-sm text-gray-700 dark:text-gray-200"
        >
          <p class="mb-1">
            Refused: this would collide with {{ normalizeConflicts.length }} series
            override(s). Resolve them first (nothing was changed).
          </p>
          <ul class="list-disc pl-5">
            <li v-for="(c, i) in normalizeConflicts" :key="i">
              {{ c.table }} · sender {{ c.sender_id ?? '—' }} · kind {{ c.kind_id ?? '—' }}
            </li>
          </ul>
        </div>

        <p
          v-else-if="normalizeError"
          data-testid="currency-normalize-error"
          class="mb-4 text-sm text-red-600 dark:text-red-400"
        >
          {{ normalizeError }}
        </p>

        <!-- Codes in use -->
        <ul v-if="currencies.length" class="divide-y divide-gray-100 dark:divide-gray-700/60">
          <li
            v-for="c in currencies"
            :key="c.code"
            :data-testid="`currency-row-${c.code}`"
            class="flex items-center justify-between py-2 text-sm"
          >
            <span class="font-medium text-gray-800 dark:text-gray-100">{{ c.code }}</span>
            <AppBadge colour="grey">{{ c.document_count }}</AppBadge>
          </li>
        </ul>
        <p v-else class="text-sm text-gray-500 dark:text-gray-400">
          No currencies are set on any document yet.
        </p>

        <!-- FX rates subsection: seed a rate so conversion resolves -->
        <div class="mt-6 border-t border-gray-100 dark:border-gray-700/60 pt-4">
          <h3 class="text-base font-semibold text-gray-800 dark:text-gray-100 mb-1">
            FX rates
          </h3>
          <p class="mb-3 text-sm text-gray-500 dark:text-gray-400">
            Cross-currency series convert via a stored USD rate. Fetch a live rate per code, or
            enter one manually (the value of one unit in USD). USD is the base (1.0).
          </p>

          <p
            v-if="fxLoading"
            data-testid="fx-loading"
            class="text-sm text-gray-500 dark:text-gray-400"
          >
            Loading…
          </p>
          <p
            v-else-if="fxError"
            data-testid="fx-error"
            class="text-sm text-red-600 dark:text-red-400"
          >
            {{ fxError }}
          </p>

          <ul
            v-else-if="fxRates.length"
            class="divide-y divide-gray-100 dark:divide-gray-700/60"
          >
            <li
              v-for="fx in fxRates"
              :key="fx.code"
              :data-testid="`fx-row-${fx.code}`"
              class="py-2 text-sm"
            >
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="font-medium text-gray-800 dark:text-gray-100">
                  {{ fx.code }}
                  <span class="ml-1 text-xs font-normal text-gray-400">
                    · {{ fx.document_count }} doc(s)
                  </span>
                </span>

                <!-- Status + actions -->
                <span
                  v-if="fx.is_base"
                  :data-testid="`fx-status-${fx.code}`"
                  class="text-xs text-gray-500 dark:text-gray-400"
                >
                  Base currency (1.0)
                </span>
                <span
                  v-else-if="fx.has_rate"
                  :data-testid="`fx-status-${fx.code}`"
                  class="flex flex-wrap items-center gap-2 text-xs"
                >
                  <AppBadge colour="green">Rate {{ fx.rate_to_base }}</AppBadge>
                  <span class="text-gray-400">as of {{ fx.as_of }}</span>
                  <button
                    type="button"
                    :data-testid="`fx-fetch-${fx.code}`"
                    class="text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 disabled:opacity-50"
                    :disabled="fxBusyCode === fx.code"
                    @click="fetchFxLive(fx.code)"
                  >
                    {{ fxBusyCode === fx.code ? 'Fetching…' : 'Refresh' }}
                  </button>
                </span>
                <span
                  v-else
                  :data-testid="`fx-status-${fx.code}`"
                  class="flex flex-wrap items-center gap-2 text-xs"
                >
                  <AppBadge colour="yellow">No rate</AppBadge>
                  <button
                    type="button"
                    :data-testid="`fx-fetch-${fx.code}`"
                    class="font-medium text-violet-600 hover:text-violet-700 dark:text-violet-400 dark:hover:text-violet-300 disabled:opacity-50"
                    :disabled="fxBusyCode === fx.code"
                    @click="fetchFxLive(fx.code)"
                  >
                    {{ fxBusyCode === fx.code ? 'Fetching…' : 'Fetch rate' }}
                  </button>
                  <button
                    type="button"
                    :data-testid="`fx-manual-toggle-${fx.code}`"
                    class="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                    @click="toggleFxManual(fx.code)"
                  >
                    Enter manually
                  </button>
                </span>
              </div>

              <!-- Manual-entry fallback form -->
              <form
                v-if="fxManualOpen[fx.code]"
                :data-testid="`fx-manual-form-${fx.code}`"
                class="mt-2 flex flex-wrap items-center gap-2"
                @submit.prevent="seedFxManual(fx.code)"
              >
                <label :for="`fx-manual-input-${fx.code}`" class="sr-only">
                  {{ fx.code }} rate to USD
                </label>
                <input
                  :id="`fx-manual-input-${fx.code}`"
                  v-model="fxManualRate[fx.code]"
                  type="text"
                  inputmode="decimal"
                  autocomplete="off"
                  placeholder="USD per 1 unit"
                  class="form-input w-40 text-sm"
                  :data-testid="`fx-manual-input-${fx.code}`"
                />
                <AppButton
                  type="submit"
                  :data-testid="`fx-seed-submit-${fx.code}`"
                  :disabled="fxBusyCode === fx.code"
                >
                  {{ fxBusyCode === fx.code ? 'Saving…' : 'Save rate' }}
                </AppButton>
              </form>

              <p
                v-if="fxRowError[fx.code]"
                :data-testid="`fx-row-error-${fx.code}`"
                class="mt-1 text-xs text-red-600 dark:text-red-400"
              >
                {{ fxRowError[fx.code] }}
              </p>
            </li>
          </ul>
          <p v-else class="text-sm text-gray-500 dark:text-gray-400">
            No currencies are set on any document yet.
          </p>
        </div>
      </template>
    </div>
  </div>
</template>
