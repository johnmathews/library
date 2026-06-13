<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppErrorMessage from './AppErrorMessage.vue'

// Replaces GovDateInput. Three separate fields (day / month / year);
// v-model is an ISO date string ("YYYY-MM-DD") or null while the fields are
// empty or do not form a valid date. The parse/format logic is preserved
// verbatim from GovDateInput.
const props = defineProps<{
  id: string
  legend: string
  hint?: string
  errorMessage?: string
}>()

const model = defineModel<string | null>({ default: null })

const day = ref('')
const month = ref('')
const year = ref('')

// Populate the parts from an externally supplied model value.
watch(
  model,
  (value) => {
    const match = value ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(value) : null
    if (match) {
      if (toIso(day.value, month.value, year.value) !== value) {
        year.value = match[1]!
        month.value = String(Number(match[2]))
        day.value = String(Number(match[3]))
      }
    } else if (value === null && !match) {
      // leave partial user input alone
    }
  },
  { immediate: true },
)

function toIso(d: string, m: string, y: string): string | null {
  const dayNum = Number(d)
  const monthNum = Number(m)
  const yearNum = Number(y)
  if (!/^\d{1,2}$/.test(d) || !/^\d{1,2}$/.test(m) || !/^\d{4}$/.test(y)) return null
  const date = new Date(Date.UTC(yearNum, monthNum - 1, dayNum))
  const valid =
    date.getUTCFullYear() === yearNum &&
    date.getUTCMonth() === monthNum - 1 &&
    date.getUTCDate() === dayNum
  if (!valid) return null
  return `${y}-${String(monthNum).padStart(2, '0')}-${String(dayNum).padStart(2, '0')}`
}

function onInput(): void {
  model.value = toIso(day.value, month.value, year.value)
}

const describedBy = computed(() => {
  const ids: string[] = []
  if (props.hint) ids.push(`${props.id}-hint`)
  if (props.errorMessage) ids.push(`${props.id}-error`)
  return ids.length ? ids.join(' ') : undefined
})
</script>

<template>
  <fieldset role="group" :aria-describedby="describedBy">
    <legend class="text-sm font-semibold mb-2">{{ props.legend }}</legend>
    <p
      v-if="props.hint"
      :id="`${props.id}-hint`"
      class="text-sm text-gray-500 dark:text-gray-400 mb-2"
    >
      {{ props.hint }}
    </p>
    <AppErrorMessage v-if="props.errorMessage" :id="`${props.id}-error`">
      {{ props.errorMessage }}
    </AppErrorMessage>
    <div class="flex gap-3" :id="props.id">
      <div>
        <label class="block text-xs font-medium mb-1" :for="`${props.id}-day`">Day</label>
        <input
          v-model="day"
          class="form-input w-14"
          :class="{ 'border-red-300': props.errorMessage }"
          :id="`${props.id}-day`"
          :name="`${props.id}-day`"
          type="text"
          inputmode="numeric"
          @input="onInput"
        />
      </div>
      <div>
        <label class="block text-xs font-medium mb-1" :for="`${props.id}-month`">Month</label>
        <input
          v-model="month"
          class="form-input w-14"
          :class="{ 'border-red-300': props.errorMessage }"
          :id="`${props.id}-month`"
          :name="`${props.id}-month`"
          type="text"
          inputmode="numeric"
          @input="onInput"
        />
      </div>
      <div>
        <label class="block text-xs font-medium mb-1" :for="`${props.id}-year`">Year</label>
        <input
          v-model="year"
          class="form-input w-20"
          :class="{ 'border-red-300': props.errorMessage }"
          :id="`${props.id}-year`"
          :name="`${props.id}-year`"
          type="text"
          inputmode="numeric"
          @input="onInput"
        />
      </div>
    </div>
  </fieldset>
</template>
