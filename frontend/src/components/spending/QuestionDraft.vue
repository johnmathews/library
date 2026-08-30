<script setup lang="ts">
/**
 * "Ask a question" draft flow for the `/charts` board (spec §4.8).
 *
 * Posts free text to `POST /api/spending/draft` and renders exactly one of
 * **three** states — conflating the last two is the failure §7.5 names:
 *
 * | state | wire | render |
 * | --- | --- | --- |
 * | expressible | `expressible: true`, `rule`/`preview` present | rule, split, preview, save enabled |
 * | partly expressible | `expressible: false`, `rule`/`preview` **present** | the same, labelled an approximation, plus `unknown_terms` |
 * | collapsed | `expressible: false`, `rule`/`preview` **null** | `unknown_terms` + message only, **no preview**, save disabled |
 *
 * The collapsed case is the one that matters: every clause was dropped, so
 * the rule would be `Rule(all=[])`, which matches every row in the archive —
 * previewing it would answer a narrow question with the whole archive's
 * total, the most confidently wrong answer this feature can give. A null
 * rule also cannot be round-tripped into a save, which is what `canSave`
 * enforces below rather than trusting the caller not to click a disabled
 * button.
 *
 * `unknown_terms` is model-authored text — capped in count and length
 * server-side — and is rendered via ordinary text interpolation, never
 * `v-html`, so it can never execute as markup.
 *
 * `currency` is a prop, not a choice this component makes: the board reads
 * it from `useCurrencyOptions()` and hands it down, exactly as the seed
 * chart in the empty state does. Hardcoding one here would be the same
 * defect §2.2 rejected in the seed migration.
 */
import { computed, ref } from 'vue'
import { createChart, draftQuestion, type Chart, type Draft, type Rule } from '@/api/spending'
import { ApiError } from '@/api/client'
import { bands, type Band } from '@/spending/palette'
import { AppButton, AppInput } from '@/components/app'
import SpendingChart from './SpendingChart.vue'
import SpendingLegend from './SpendingLegend.vue'

const props = defineProps<{ currency: string }>()
const emit = defineEmits<{ saved: [chart: Chart] }>()

const question = ref('')
const draft = ref<Draft | null>(null)
const drafting = ref(false)
const draftError = ref<string | null>(null)

const saving = ref(false)
const saveError = ref<string | null>(null)

// The two guards the whole component turns on. `hasPreview` decides
// expressible-or-approximate vs collapsed; `canSave` is independent of it
// only in principle — today the API never sends a preview without a rule —
// but this reads the rule directly rather than trusting that coupling, so a
// future preview-without-rule response still cannot be saved.
//
// The mirror direction — `rule` present but `preview` null — is NOT one of
// the three documented wire states above (`preview: null` only ever
// accompanies `rule: null`, the collapsed row). If a future response broke
// that coupling, `canSave` would read true (a rule exists) while
// `hasPreview` reads false, landing in the `v-else` branch below: no chart,
// no legend, just `message`/`unknown_terms`, with an ENABLED Save button
// beside it — nothing rendered contradicts nothing disabled, so this is not
// a crash, but a save whose preview the user never saw. Not reachable under
// the current API contract, so left undocumented-as-a-branch rather than
// guarded outright; if the contract ever allows it, gate `canSave` on
// `hasPreview` too rather than trusting `rule` alone.
const hasPreview = computed(
  () => draft.value !== null && draft.value.rule !== null && draft.value.preview !== null,
)
const canSave = computed(() => draft.value !== null && draft.value.rule !== null)
const isApproximate = computed(() => hasPreview.value && draft.value?.expressible === false)

const previewBands = computed<Band[]>(() => {
  const preview = draft.value?.preview
  return preview ? bands(preview.splits, preview.cells) : []
})

function ruleSummary(rule: Rule): string {
  if (rule.all.length === 0) return 'Every document.'
  return rule.all
    .map((clause) => `${clause.facet} ${clause.op === 'in' ? 'is' : 'is not'} ${clause.values.join(' or ')}`)
    .join(' and ')
}

function splitSummary(split: string | null): string {
  return split ? `Split by ${split}.` : 'Not split.'
}

async function submit(): Promise<void> {
  const text = question.value.trim()
  if (!text || drafting.value) return
  drafting.value = true
  draftError.value = null
  draft.value = null
  // A save failure attached to the PREVIOUS draft must not survive onto a
  // new, unrelated one — otherwise a stale banner reappears beside a draft
  // it never applied to.
  saveError.value = null
  try {
    draft.value = await draftQuestion({ question: text, display_currency: props.currency })
  } catch (err) {
    draftError.value = err instanceof ApiError ? err.detail : 'Could not draft this question.'
  } finally {
    drafting.value = false
  }
}

async function save(): Promise<void> {
  const current = draft.value
  if (!current || current.rule === null || saving.value) return
  saving.value = true
  saveError.value = null
  try {
    const chart = await createChart({
      name: current.question,
      question_text: current.question,
      rule: current.rule,
      default_split: current.proposed_split,
      display_currency: props.currency,
    })
    emit('saved', chart)
    draft.value = null
    question.value = ''
  } catch (err) {
    saveError.value = err instanceof ApiError ? err.detail : 'Could not save this chart.'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="flex flex-col gap-3" data-testid="question-draft">
    <form class="flex items-end gap-2" @submit.prevent="submit">
      <AppInput
        id="question-draft-question"
        v-model="question"
        label="Ask a spending question"
        hide-label
        placeholder="e.g. How much do we spend on software?"
        testid="question-draft-input"
        width-class="min-w-0 flex-1"
      />
      <AppButton type="submit" size="sm" :disabled="drafting || question.trim().length === 0" data-testid="question-draft-ask">
        {{ drafting ? 'Asking…' : 'Ask' }}
      </AppButton>
    </form>

    <p v-if="draftError" class="text-sm text-red-600 dark:text-red-400" data-testid="question-draft-error">
      {{ draftError }}
    </p>

    <div v-if="draft" class="card flex flex-col gap-3 p-4" data-testid="question-draft-result">
      <template v-if="hasPreview">
        <p class="text-sm text-gray-700 dark:text-gray-200" data-testid="question-draft-rule">
          {{ ruleSummary(draft.rule!) }}
        </p>
        <p class="filter-label" data-testid="question-draft-split">
          {{ splitSummary(draft.proposed_split) }}
        </p>
        <p
          v-if="isApproximate"
          class="text-sm text-amber-700 dark:text-amber-300"
          data-testid="question-draft-approximate"
        >
          This is an approximation of your question.
        </p>
        <ul
          v-if="draft.unknown_terms.length > 0"
          class="flex flex-wrap gap-1 text-xs text-gray-500 dark:text-gray-400"
          data-testid="question-draft-unknown-terms"
        >
          <li v-for="term in draft.unknown_terms" :key="term">{{ term }}</li>
        </ul>
        <div class="h-40 w-full">
          <SpendingChart :data="draft.preview!" :bands="previewBands" @cell="() => {}" />
        </div>
        <SpendingLegend :bands="previewBands" :hidden="new Set()" :currency="currency" compact />
      </template>

      <template v-else>
        <p v-if="draft.message" class="text-sm text-gray-700 dark:text-gray-200" data-testid="question-draft-message">
          {{ draft.message }}
        </p>
        <ul
          v-if="draft.unknown_terms.length > 0"
          class="flex flex-wrap gap-1 text-xs text-gray-500 dark:text-gray-400"
          data-testid="question-draft-unknown-terms"
        >
          <li v-for="term in draft.unknown_terms" :key="term">{{ term }}</li>
        </ul>
      </template>

      <p v-if="saveError" class="text-sm text-red-600 dark:text-red-400" data-testid="question-draft-save-error">
        {{ saveError }}
      </p>

      <AppButton
        type="button"
        size="sm"
        class="self-start"
        :disabled="!canSave || saving"
        data-testid="question-draft-save"
        @click="save"
      >
        {{ saving ? 'Saving…' : 'Save chart' }}
      </AppButton>
    </div>
  </div>
</template>
