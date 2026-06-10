<script setup lang="ts">
import { computed, ref } from 'vue'
import { FileUpload } from 'govuk-frontend'
import { useGovukComponent } from './useGovukComponent'

const props = defineProps<{
  id: string
  label: string
  name?: string
  hint?: string
  errorMessage?: string
  multiple?: boolean
  accept?: string
}>()

// v-model carries the selected files; null when nothing is selected.
const model = defineModel<File[] | null>({ default: null })

const wrapper = ref<HTMLElement | null>(null)

// govuk-frontend v6.2 FileUpload upgrades the wrapper into the enhanced
// drop-zone variant (drag and drop, "Choose file" button, status text).
useGovukComponent(wrapper, FileUpload)

const describedBy = computed(() => {
  const ids: string[] = []
  if (props.hint) ids.push(`${props.id}-hint`)
  if (props.errorMessage) ids.push(`${props.id}-error`)
  return ids.length ? ids.join(' ') : undefined
})

function onChange(event: Event): void {
  const input = event.target as HTMLInputElement
  model.value = input.files && input.files.length > 0 ? Array.from(input.files) : null
}
</script>

<template>
  <div class="govuk-form-group" :class="{ 'govuk-form-group--error': props.errorMessage }">
    <label class="govuk-label" :for="props.id">{{ props.label }}</label>
    <div v-if="props.hint" :id="`${props.id}-hint`" class="govuk-hint">{{ props.hint }}</div>
    <p v-if="props.errorMessage" :id="`${props.id}-error`" class="govuk-error-message">
      <span class="govuk-visually-hidden">Error:</span> {{ props.errorMessage }}
    </p>
    <div ref="wrapper" class="govuk-file-upload-wrapper" data-module="govuk-file-upload">
      <input
        class="govuk-file-upload"
        :class="{ 'govuk-file-upload--error': props.errorMessage }"
        :id="props.id"
        :name="props.name ?? props.id"
        type="file"
        :multiple="props.multiple"
        :accept="props.accept"
        :aria-describedby="describedBy"
        @change="onChange"
      />
    </div>
  </div>
</template>
