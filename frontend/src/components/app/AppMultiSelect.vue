<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  id: string
  /** Accessible label for the text input (visually hidden). */
  label: string
  /** Existing values offered as suggestions. */
  options: string[]
  placeholder?: string
  errorMessage?: string
}>()

const emit = defineEmits<{ change: [] }>()

/** The selected values (full-replacement list). */
const model = defineModel<string[]>({ default: () => [] })

const query = ref('')
const focused = ref(false)

function isSelected(value: string): boolean {
  const lower = value.toLowerCase()
  return model.value.some((item) => item.toLowerCase() === lower)
}

/** Existing options not already selected, filtered by the current query. */
const suggestions = computed(() => {
  const q = query.value.trim().toLowerCase()
  return props.options.filter(
    (option) => !isSelected(option) && (!q || option.toLowerCase().includes(q)),
  )
})

/** Offer "Create …" when the typed text matches no existing option and isn't
 * already selected — typing a new name creates the project on save. */
const createCandidate = computed(() => {
  const value = query.value.trim()
  if (!value) return null
  const lower = value.toLowerCase()
  if (isSelected(value)) return null
  if (props.options.some((option) => option.toLowerCase() === lower)) return null
  return value
})

const open = computed(
  () => focused.value && (suggestions.value.length > 0 || createCandidate.value !== null),
)

function add(value: string): void {
  const trimmed = value.trim()
  if (!trimmed || isSelected(trimmed)) {
    query.value = ''
    return
  }
  model.value = [...model.value, trimmed]
  query.value = ''
  emit('change')
}

function remove(value: string): void {
  model.value = model.value.filter((item) => item !== value)
  emit('change')
}

/** Enter commits the typed text (selecting an existing option or creating a new
 * one — both are just a name the backend upserts). */
function onEnter(): void {
  if (query.value.trim()) add(query.value)
}

/** Backspace on an empty query removes the last chip (familiar token-input UX). */
function onBackspace(): void {
  if (!query.value && model.value.length) remove(model.value[model.value.length - 1]!)
}

const describedBy = computed(() => (props.errorMessage ? `${props.id}-error` : undefined))
</script>

<template>
  <div>
    <label class="sr-only" :for="props.id">{{ props.label }}</label>
    <div
      class="form-input flex flex-wrap items-center gap-1.5 w-full"
      :class="{ 'border-red-300': props.errorMessage }"
      :data-testid="`${props.id}`"
    >
      <span
        v-for="value in model"
        :key="value"
        class="inline-flex items-center gap-1 rounded bg-violet-100 dark:bg-violet-900/40 text-violet-800 dark:text-violet-200 text-sm px-2 py-0.5"
        :data-testid="`${props.id}-chip`"
      >
        {{ value }}
        <button
          type="button"
          class="text-violet-500 hover:text-violet-700 dark:hover:text-violet-100 leading-none"
          :aria-label="`Remove ${value}`"
          :data-testid="`${props.id}-remove`"
          @click="remove(value)"
        >
          ×
        </button>
      </span>
      <input
        :id="props.id"
        v-model="query"
        type="text"
        class="flex-1 min-w-[8rem] border-0 p-0 focus:ring-0 bg-transparent text-sm"
        :placeholder="props.placeholder ?? 'Add a project…'"
        :aria-describedby="describedBy"
        :aria-invalid="props.errorMessage ? 'true' : undefined"
        autocomplete="off"
        :data-testid="`${props.id}-input`"
        @focus="focused = true"
        @blur="focused = false"
        @keydown.enter.prevent="onEnter"
        @keydown.delete="onBackspace"
      />
    </div>

    <ul
      v-if="open"
      class="mt-1 max-h-48 overflow-auto rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm text-sm"
      :data-testid="`${props.id}-menu`"
    >
      <li v-for="option in suggestions" :key="option">
        <button
          type="button"
          class="block w-full text-left px-3 py-1.5 hover:bg-violet-50 dark:hover:bg-violet-900/30 text-gray-800 dark:text-gray-100"
          :data-testid="`${props.id}-option`"
          @mousedown.prevent="add(option)"
        >
          {{ option }}
        </button>
      </li>
      <li v-if="createCandidate !== null">
        <button
          type="button"
          class="block w-full text-left px-3 py-1.5 hover:bg-violet-50 dark:hover:bg-violet-900/30 text-violet-700 dark:text-violet-300"
          :data-testid="`${props.id}-create`"
          @mousedown.prevent="add(createCandidate)"
        >
          Create “{{ createCandidate }}”
        </button>
      </li>
    </ul>
  </div>
</template>
