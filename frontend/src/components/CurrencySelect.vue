<script setup lang="ts">
/**
 * Currency picker for the charts create form: a dropdown of built-in codes
 * (EUR / GBP / USD) plus any the user has added, with an inline "Add another…"
 * affordance that appends a new 3-letter code (persisted via useCurrencyOptions)
 * and selects it. An empty selection means "no currency" (the chart currency is
 * optional).
 */
import { ref } from 'vue'
import { useCurrencyOptions } from '@/composables/useCurrencyOptions'

const model = defineModel<string>({ default: '' })

const { options, addOption } = useCurrencyOptions()

const adding = ref(false)
const draft = ref('')
const addError = ref<string | null>(null)

// The sentinel value the <select> uses to trigger the add-a-code flow.
const ADD_SENTINEL = '__add__'

function onSelect(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  if (value === ADD_SENTINEL) {
    adding.value = true
    draft.value = ''
    addError.value = null
    return
  }
  model.value = value
}

function confirmAdd(): void {
  const code = addOption(draft.value)
  if (code === null) {
    addError.value = 'Enter a 3-letter code, e.g. CHF.'
    return
  }
  model.value = code
  adding.value = false
  draft.value = ''
  addError.value = null
}

function cancelAdd(): void {
  adding.value = false
  draft.value = ''
  addError.value = null
}
</script>

<template>
  <div>
    <select
      v-if="!adding"
      :value="model"
      data-testid="currency-select"
      class="form-select w-full text-sm"
      aria-label="Chart currency"
      @change="onSelect"
    >
      <option value="">Currency (optional)</option>
      <option v-for="code in options" :key="code" :value="code">{{ code }}</option>
      <option :value="ADD_SENTINEL">＋ Add another…</option>
    </select>

    <div v-else class="space-y-1">
      <div class="flex gap-2">
        <input
          v-model="draft"
          type="text"
          maxlength="3"
          data-testid="currency-add-input"
          placeholder="e.g. CHF"
          class="form-input w-full text-sm uppercase"
          aria-label="New currency code"
          @keydown.enter.prevent="confirmAdd"
        />
        <button
          type="button"
          data-testid="currency-add-confirm"
          class="btn bg-violet-600 hover:bg-violet-700 text-white text-sm shrink-0"
          @click="confirmAdd"
        >
          Add
        </button>
        <button
          type="button"
          data-testid="currency-add-cancel"
          class="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 shrink-0"
          @click="cancelAdd"
        >
          Cancel
        </button>
      </div>
      <p
        v-if="addError"
        data-testid="currency-add-error"
        role="alert"
        class="text-xs text-red-600 dark:text-red-400"
      >
        {{ addError }}
      </p>
    </div>
  </div>
</template>
