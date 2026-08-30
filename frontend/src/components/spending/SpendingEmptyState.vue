<script setup lang="ts">
/**
 * `/charts` empty state (spec §4.9, §10.4, §2.2).
 *
 * "All spending" is **first and pinned** — an empty rule (`{ all: [] }`).
 * It is not a migration-seeded row (§2.2 rejects that: a display currency
 * nobody chose, and a one-shot that means "gone once deleted") — it is
 * created here, by the owner clicking it once, through the ordinary
 * `POST /api/spending` path every other chart uses.
 *
 * **Its split axis degrades to the vocabulary that actually exists.**
 * `default_split: 'category'` only works once the `category` facet has been
 * seeded (`library label-archive` — an operator step, never automatic on
 * migrate/startup); a genuinely fresh archive has no facets at all, and
 * `POST /api/spending` 422s on a split axis the vocabulary doesn't carry.
 * That is the single most important action on the first-run screen, so it
 * must not depend on an operator having run a CLI command first. Whether any
 * facet counts came back from `GET /api/facets/counts` (below) is the
 * signal: some means the vocabulary is populated enough to split by
 * `category` (today's behaviour); none means propose the total unsplit
 * instead — a perfectly good first chart, and one a fresh archive can
 * always draw. This guard is sound for a reason narrower than "any counts at
 * all implies a usable vocabulary" in general: `library/facets/seed.py`
 * seeds `category`, `scope` and `cost_type` (plus the value-less `vehicle` /
 * `property` / `person`) as ONE `SEED_VOCABULARY` tuple through one
 * `seed_vocabulary()` call — an all-or-nothing seed — and no route deletes a
 * facet, only values within one. So on THIS archive's seeding story, any
 * counts at all means `category` was seeded alongside them and still exists.
 * A hand-built vocabulary assembled value-by-value through `POST
 * /api/facets` without ever adding `category` would break this guard (it is
 * still sound in the sense of "never worse than checking `category` isn't
 * there", just not for the reason the property looks general enough to
 * suggest) — never probe for the `category` facet by name regardless, since
 * that would stay broken for an archive whose vocabulary exists but happens
 * to have no `category` VALUES yet, which "any counts at all" does not.
 *
 * Every other proposal comes from `GET /api/facets/counts`: the values with
 * the most documents, each shown with its count and date span, ranked
 * descending and capped at `MAX_PROPOSALS` (below) — the route itself
 * carries no limit, and a fully labelled archive can return 30+ values,
 * which would turn the first screen into a wall of equal-weight cards
 * instead of a scannable shortlist (spec §4.9: "the values with the *most*
 * documents"). That route counts over `spend_facts`, which deliberately
 * excludes amountless, soft-deleted and non-canonical documents (§3.4) — so
 * a value with no money behind it never reaches this component at all, and
 * this component does not second-guess that: whatever the route returns is
 * what gets proposed, nothing more (beyond the rank + cap here).
 *
 * Accepting a proposal costs the owner one click and saves; ignoring the
 * rest costs nothing and creates nothing — the difference from the old
 * "candidates" idea, which created series that persisted as noise even when
 * unwanted.
 */
import { computed, onMounted, ref } from 'vue'
import { createChart, type Chart, type Rule } from '@/api/spending'
import { fetchFacetCounts, type FacetCount } from '@/api/facets'
import { ApiError } from '@/api/client'
import { formatDate } from '@/utils/documentFormat'

const props = defineProps<{ currency: string }>()
const emit = defineEmits<{ created: [chart: Chart] }>()

interface Proposal {
  key: string
  label: string
  detail: string | null
  rule: Rule
  name: string
  defaultSplit: string | null
}

// A judgement call, not a derived number: this component imports no
// palette and a proposal card carries no per-value colour, so there is no
// mechanism that breaks past six (unlike `bands()`'s six-slot fold, which
// genuinely runs out of colours). Six is simply enough to read as a
// shortlist on a first screen without scrolling, on the phone-width layout
// spec §4.9 targets — a judgement call about how much fits, nothing more.
const MAX_PROPOSALS = 6

const counts = ref<FacetCount[]>([])
const loading = ref(true)
const loadError = ref<string | null>(null)

onMounted(async () => {
  try {
    counts.value = await fetchFacetCounts()
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.detail : 'Could not load proposed questions.'
  } finally {
    loading.value = false
  }
})

function documentsLabel(n: number): string {
  return `${n} document${n === 1 ? '' : 's'}`
}

/** e.g. "4 May 2026 – 12 July 2026", or a single date when the span collapses
 * to one day. `null` when either bound is absent (the route sends both
 * together, but this does not assume that). */
function dateSpan(first: string | null, last: string | null): string | null {
  const from = formatDate(first)
  const to = formatDate(last)
  if (!from || !to) return null
  return from === to ? from : `${from} – ${to}`
}

// Ranked by document count, descending — "the values with the most
// documents" (§4.9) — ties broken by key so the order is deterministic
// rather than whatever the route happened to return.
const facetProposals = computed<Proposal[]>(() =>
  [...counts.value]
    .sort((a, b) => b.documents - a.documents || a.value_key.localeCompare(b.value_key))
    .slice(0, MAX_PROPOSALS)
    .map((count) => {
      const span = dateSpan(count.first_date, count.last_date)
      return {
        key: `${count.facet_key}:${count.value_key}`,
        label: count.value_key,
        detail: span ? `${documentsLabel(count.documents)} · ${span}` : documentsLabel(count.documents),
        rule: { all: [{ facet: count.facet_key, op: 'in' as const, values: [count.value_key] }] },
        name: count.value_key,
        defaultSplit: null,
      }
    }),
)

// Pinned first regardless of load state — it renders even before the counts
// fetch below resolves (or if it fails), just unsplit until proven otherwise.
// See the file header: split-by-`category` only once `counts` shows the
// vocabulary actually has something to split by.
const allSpendingProposal = computed<Proposal>(() => {
  const hasFacetData = counts.value.length > 0
  return {
    key: '__all_spending__',
    label: 'All spending',
    detail: hasFacetData ? 'Every document, split by category.' : 'Every document, as one total.',
    rule: { all: [] },
    name: 'All spending',
    defaultSplit: hasFacetData ? 'category' : null,
  }
})

const proposals = computed<Proposal[]>(() => [allSpendingProposal.value, ...facetProposals.value])

const savingKey = ref<string | null>(null)
const saveError = ref<string | null>(null)

async function choose(proposal: Proposal): Promise<void> {
  if (savingKey.value !== null) return
  savingKey.value = proposal.key
  saveError.value = null
  try {
    const chart = await createChart({
      name: proposal.name,
      rule: proposal.rule,
      default_split: proposal.defaultSplit,
      display_currency: props.currency,
    })
    emit('created', chart)
  } catch (err) {
    saveError.value = err instanceof ApiError ? err.detail : 'Could not create this chart.'
  } finally {
    savingKey.value = null
  }
}
</script>

<template>
  <div class="flex flex-col gap-4" data-testid="spending-empty-state">
    <p class="text-sm text-gray-500 dark:text-gray-400">
      No charts yet. Start with the aggregate view below, or chart one of these.
    </p>

    <p v-if="loadError" class="text-sm text-red-600 dark:text-red-400" data-testid="spending-empty-load-error">
      {{ loadError }}
    </p>
    <p v-if="saveError" class="text-sm text-red-600 dark:text-red-400" data-testid="spending-empty-save-error">
      {{ saveError }}
    </p>

    <ul class="flex flex-col gap-2" data-testid="spending-empty-proposals">
      <li v-for="proposal in proposals" :key="proposal.key">
        <button
          type="button"
          class="card flex w-full items-center justify-between gap-3 p-4 text-left transition hover:border-violet-300 disabled:cursor-not-allowed disabled:opacity-60"
          data-testid="spending-empty-proposal"
          :disabled="savingKey === proposal.key"
          @click="choose(proposal)"
        >
          <span class="min-w-0">
            <span
              class="block truncate font-medium text-gray-800 dark:text-gray-100"
              data-testid="spending-empty-proposal-label"
            >
              {{ proposal.label }}
            </span>
            <span v-if="proposal.detail" class="block text-xs text-gray-500 dark:text-gray-400">
              {{ proposal.detail }}
            </span>
          </span>
          <span class="shrink-0 text-xs font-medium text-violet-600 dark:text-violet-400">
            {{ savingKey === proposal.key ? 'Saving…' : 'Chart it' }}
          </span>
        </button>
      </li>
    </ul>
  </div>
</template>
