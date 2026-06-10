<script setup lang="ts">
import { computed, ref, watch } from 'vue'

// GOV.UK date input pattern: three separate text fields (day / month /
// year). v-model is an ISO date string ("YYYY-MM-DD") or null while the
// fields are empty or do not form a valid date.
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
  <div class="govuk-form-group" :class="{ 'govuk-form-group--error': props.errorMessage }">
    <fieldset class="govuk-fieldset" role="group" :aria-describedby="describedBy">
      <legend class="govuk-fieldset__legend">{{ props.legend }}</legend>
      <div v-if="props.hint" :id="`${props.id}-hint`" class="govuk-hint">{{ props.hint }}</div>
      <p v-if="props.errorMessage" :id="`${props.id}-error`" class="govuk-error-message">
        <span class="govuk-visually-hidden">Error:</span> {{ props.errorMessage }}
      </p>
      <div class="govuk-date-input" :id="props.id">
        <div class="govuk-date-input__item">
          <div class="govuk-form-group">
            <label class="govuk-label govuk-date-input__label" :for="`${props.id}-day`">Day</label>
            <input
              v-model="day"
              class="govuk-input govuk-date-input__input govuk-input--width-2"
              :class="{ 'govuk-input--error': props.errorMessage }"
              :id="`${props.id}-day`"
              :name="`${props.id}-day`"
              type="text"
              inputmode="numeric"
              @input="onInput"
            />
          </div>
        </div>
        <div class="govuk-date-input__item">
          <div class="govuk-form-group">
            <label class="govuk-label govuk-date-input__label" :for="`${props.id}-month`">
              Month
            </label>
            <input
              v-model="month"
              class="govuk-input govuk-date-input__input govuk-input--width-2"
              :class="{ 'govuk-input--error': props.errorMessage }"
              :id="`${props.id}-month`"
              :name="`${props.id}-month`"
              type="text"
              inputmode="numeric"
              @input="onInput"
            />
          </div>
        </div>
        <div class="govuk-date-input__item">
          <div class="govuk-form-group">
            <label class="govuk-label govuk-date-input__label" :for="`${props.id}-year`">
              Year
            </label>
            <input
              v-model="year"
              class="govuk-input govuk-date-input__input govuk-input--width-4"
              :class="{ 'govuk-input--error': props.errorMessage }"
              :id="`${props.id}-year`"
              :name="`${props.id}-year`"
              type="text"
              inputmode="numeric"
              @input="onInput"
            />
          </div>
        </div>
      </div>
    </fieldset>
  </div>
</template>
