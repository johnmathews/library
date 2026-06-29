<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  id: string
  label: string
  name?: string
  type?: string
  hint?: string
  errorMessage?: string
  autocomplete?: string
  inputmode?: 'text' | 'numeric' | 'decimal' | 'email' | 'tel' | 'search' | 'url'
  spellcheck?: boolean
  widthClass?: string
  /** id of a `<datalist>` for native autocomplete suggestions. */
  list?: string
  /** Visually hide the label (kept for screen readers). Used where a
      surrounding element already shows the field name in the same position,
      so the inline editor doesn't duplicate it. */
  hideLabel?: boolean
  placeholder?: string
  /** Render an eye-icon button that toggles the value between hidden
      (password) and visible (text). Overrides `type`. */
  revealable?: boolean
}>()

const model = defineModel<string>({ default: '' })

// Local reveal state for `revealable` fields (e.g. secret tokens). It only
// affects what the user is currently typing — there is no stored secret to
// reveal, since secrets are never returned by the server.
const revealed = ref(false)

const inputType = computed(() =>
  props.revealable ? (revealed.value ? 'text' : 'password') : (props.type ?? 'text'),
)

const describedBy = computed(() => {
  const ids: string[] = []
  if (props.hint) ids.push(`${props.id}-hint`)
  if (props.errorMessage) ids.push(`${props.id}-error`)
  return ids.length ? ids.join(' ') : undefined
})
</script>

<template>
  <div>
    <label
      :class="props.hideLabel ? 'sr-only' : 'block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300'"
      :for="props.id"
    >{{ props.label }}</label>
    <p
      v-if="props.hint"
      :id="`${props.id}-hint`"
      class="text-sm text-gray-500 dark:text-gray-400 mb-1"
    >{{ props.hint }}</p>
    <div class="relative">
      <input
        v-model="model"
        class="form-input w-full"
        :class="[props.widthClass, { 'border-red-300': props.errorMessage, 'pr-10': props.revealable }]"
        :id="props.id"
        :name="props.name ?? props.id"
        :type="inputType"
        :placeholder="props.placeholder"
        :autocomplete="props.autocomplete"
        :inputmode="props.inputmode"
        :spellcheck="props.spellcheck"
        :list="props.list"
        :aria-describedby="describedBy"
        :aria-invalid="props.errorMessage ? 'true' : undefined"
      />
      <button
        v-if="props.revealable"
        type="button"
        class="absolute inset-y-0 right-0 flex items-center px-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
        :data-testid="`${props.id}-reveal`"
        :aria-label="revealed ? 'Hide value' : 'Show value'"
        :aria-pressed="revealed"
        @click="revealed = !revealed"
      >
        <svg
          v-if="!revealed"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 20 20"
          fill="currentColor"
          class="h-5 w-5"
          aria-hidden="true"
        >
          <path d="M10 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z" />
          <path
            fill-rule="evenodd"
            d="M.664 10.59a1.65 1.65 0 0 1 0-1.186A10.004 10.004 0 0 1 10 3c4.257 0 7.893 2.66 9.336 6.41.147.381.146.804 0 1.186A10.004 10.004 0 0 1 10 17c-4.257 0-7.893-2.66-9.336-6.41ZM14 10a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z"
            clip-rule="evenodd"
          />
        </svg>
        <svg
          v-else
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 20 20"
          fill="currentColor"
          class="h-5 w-5"
          aria-hidden="true"
        >
          <path
            fill-rule="evenodd"
            d="M3.28 2.22a.75.75 0 0 0-1.06 1.06l14.5 14.5a.75.75 0 1 0 1.06-1.06l-1.745-1.745a10.029 10.029 0 0 0 3.3-4.38 1.651 1.651 0 0 0 0-1.185A10.004 10.004 0 0 0 9.999 3a9.956 9.956 0 0 0-4.744 1.194L3.28 2.22ZM7.752 6.69l1.092 1.092a2.5 2.5 0 0 1 3.374 3.373l1.091 1.092a4 4 0 0 0-5.557-5.557Z"
            clip-rule="evenodd"
          />
          <path
            d="m10.748 13.93 2.523 2.523a9.987 9.987 0 0 1-3.27.547c-4.258 0-7.894-2.66-9.337-6.41a1.651 1.651 0 0 1 0-1.186A10.007 10.007 0 0 1 2.839 6.02L6.07 9.252a4 4 0 0 0 4.678 4.678Z"
          />
        </svg>
      </button>
    </div>
    <p
      v-if="props.errorMessage"
      :id="`${props.id}-error`"
      class="text-sm text-red-500 mt-1"
    >{{ props.errorMessage }}</p>
  </div>
</template>
